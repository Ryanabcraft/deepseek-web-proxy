const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Exercise the actual userscript parser without starting its browser hooks.
const source = fs.readFileSync("deepseek-bridge.user.js", "utf8");
const parserCode = source.slice(source.indexOf("  class SSEParser {"), source.indexOf("  let _pendingCapture"));
const Parser = vm.runInNewContext(`${parserCode}\nSSEParser`, { log() {} });
const event = (payload) => `data: ${JSON.stringify(payload)}\n\n`;

const parser = new Parser();
const stream = event({ v: { response: { status: "WIP", fragments: [{ type: "THINKING", content: "Reasoning" }] } } })
  + event({ p: "response", o: "BATCH", v: [
    { p: "fragments", o: "APPEND", v: [{ type: "RESPONSE", content: "Final" }] },
    { p: "fragments/-1/content", o: "APPEND", v: " answer" },
    { p: "accumulated_token_usage", o: "SET", v: 42 },
    { p: "status", o: "SET", v: "FINISHED" },
  ] }) + "event: close\n\n";
for (let i = 0; i < stream.length; i += 7) parser.feed(stream.slice(i, i + 7));
assert.equal(parser.getResult().text, "Final answer");
assert.equal(parser.getResult().reasoning, "Reasoning");
assert.equal(parser.getResult().accumulatedTokens, 42);
assert.equal(parser.finished, true);

// Live DeepSeek uses THINK, value-only appends, and an array for the final answer.
const liveFormat = new Parser();
liveFormat.feed(event({ v: { response: {
  status: "WIP", thinking_enabled: true,
  fragments: [{ id: 2, type: "THINK", content: "Checking" }],
} } }));
liveFormat.feed(event({ p: "response/fragments/-1/content", o: "APPEND", v: " arithmetic" }));
liveFormat.feed(event({ v: "." }));
liveFormat.feed(event({ p: "response/fragments", o: "APPEND", v: [{ id: 3, type: "RESPONSE", content: "391" }] }));
liveFormat.feed(event({ p: "response/status", o: "SET", v: "FINISHED" }));
assert.equal(liveFormat.finished, false);
liveFormat.feed("event: close\ndata: {\"auto_resume\":false}\n\n");
assert.equal(liveFormat.getResult().text, "391");
assert.equal(liveFormat.getResult().reasoning, "Checking arithmetic.");
assert.equal(liveFormat.finished, true);

// Captured live: DeepSeek omits o when the previous APPEND operation still applies.
const compressed = new Parser();
compressed.feed(event({ p: "response/fragments", o: "APPEND", v: [{ type: "RESPONSE", content: "B" }] }));
compressed.feed(event({ p: "response/fragments/-1/content", v: "ora" }));
compressed.feed(event({ v: "! 🚀" }));
assert.equal(compressed.getResult().text, "Bora! 🚀");
compressed.feed(event({ p: "response/fragments/0/content", o: "SET", v: "replacement" }));
compressed.feed(event({ v: "complete replacement" }));
assert.equal(compressed.getResult().text, "complete replacement");

const batchScope = new Parser();
batchScope.feed(event({ p: "response", o: "BATCH", v: [
  { p: "fragments", o: "APPEND", v: [{ type: "RESPONSE", content: "full" }] },
  { p: "fragments/-1/content", o: "APPEND", v: " words" },
] }));
assert.equal(batchScope.lastPath, "response");
assert.equal(batchScope.lastOperation, "BATCH");
assert.equal(batchScope.getResult().text, "full words");

const update = new Parser();
update.feed(event({ p: "response/fragments", o: "SET", v: [{ type: "RESPONSE", content: "old" }] }));
update.feed(event({ p: "response/fragments/0/content", o: "SET", v: "new" }));
assert.equal(update.getResult().text, "new");
update.feed(event({ p: "response/status", o: "SET", v: "WIP" }));
assert.equal(update.isIncomplete, false);
update.feed(event({ p: "response/status", o: "SET", v: "INCOMPLETE" }));
assert.equal(update.feed("event: close\n\n"), false);
assert.equal(update.isIncomplete, true);
console.log("Bridge parser checks passed: batched reasoning/answer, split SSE, status, token usage, SET.");

const uploadCode = source.slice(source.indexOf("  let pendingUploadFiles"), source.indexOf("  const origOpen"));
const input = { disabled: false, parentElement: null, dispatchEvent() {} };
class Transfer {
  constructor() { this.files = []; this.items = { add: (file) => this.files.push(file) }; }
}
const uploads = vm.runInNewContext(`${uploadCode}\n({uploadImages, recordUploadResponse, pending: () => pendingUploadFiles})`, {
  UPLOAD_TIMEOUT_MS: 1000, document: { querySelector: () => input },
  DataTransfer: Transfer, File, Event, atob, Uint8Array, setTimeout,
});
const uploaded = (file, status) => ({ code: 0, data: { biz_code: 0, biz_data: {
  files: [{ file_name: file.name, status, id: "file-test" }],
} } });
(async () => {
  input.dispatchEvent = () => input.files.forEach((file) => uploads.recordUploadResponse(uploaded(file, "SUCCESS")));
  await uploads.uploadImages([{ mimeType: "image/png", data: "aGVsbG8=" }], "test");
  assert.equal(input.files[0].name, "opencode-test-1.png");
  assert.equal(input.files[0].type, "image/png");
  assert.equal(uploads.pending(), null);
  input.dispatchEvent = () => input.files.forEach((file) => uploads.recordUploadResponse(uploaded(file, "FAILED")));
  await assert.rejects(uploads.uploadImages([{ mimeType: "image/png", data: "aGVsbG8=" }], "test"), /failed to process/);
  assert.equal(uploads.pending(), null);
  input.dispatchEvent = () => uploads.recordUploadResponse({ code: 0, data: { biz_code: 1, biz_msg: "Rejected" } });
  await assert.rejects(uploads.uploadImages([{ mimeType: "image/png", data: "aGVsbG8=" }], "test"), /Rejected/);
  assert.equal(uploads.pending(), null);
  input.parentElement = { querySelector: () => ({}), querySelectorAll: () => [{ textContent: "personal-draft.lua" }] };
  await assert.rejects(uploads.uploadImages([{ mimeType: "image/png", data: "aGVsbG8=" }], "test"), /existing draft attachments/);
  input.parentElement = null;
  console.log("Image upload checks passed: readiness, failed processing, business errors, cleanup.");
})().catch((err) => { console.error(err); process.exitCode = 1; });
