# DeepSeek Web Proxy

**Versão adaptada — Feito por RYANAB, GPT 6.1 SOL e DEEPSEEK V4.1.**

OpenAI-compatible local API proxy for DeepSeek Web (`chat.deepseek.com`).

Allows you to use DeepSeek models (**DeepSeek Chat** and **DeepSeek Reasoner / R1**) with OpenCode or any OpenAI-compatible client.

---

## Disclaimer

This project is for educational and research purposes only. It is not affiliated with or endorsed by DeepSeek.

---

## Features

- **OpenAI API Compatibility**: Exposes `http://127.0.0.1:1337/v1/chat/completions` and `/v1/models`.
- **DeepSeek Reasoner (R1)**: Returns captured thinking as `reasoning_content` delta chunks, followed by the final answer.
- **Tool Calling**: Translates tool schemas and parses `<tool_call>` outputs into OpenAI function call structures for agent tools (`write`, `edit`, `bash`, `read`).
- **Images**: Accepts inline OpenAI `image_url` content and uploads PNG, JPEG, WebP or GIF through the logged-in browser before sending the prompt.
- **Token Tracking**: Reports native accumulated usage when available, otherwise estimates token usage.
- **Chat Management**: Send `/clear`, `/reset`, or `/new` in chat to start a clean conversation session.
- **Lightweight**: Pure Python (`aiohttp`) + Tampermonkey script with no heavy automation frameworks.

---

## Quick Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Userscript

1. Install [Tampermonkey](https://www.tampermonkey.net/) or Violentmonkey in your browser.
2. Create a new userscript and paste the contents of [`deepseek-bridge.user.js`](./deepseek-bridge.user.js).
3. Open [chat.deepseek.com](https://chat.deepseek.com/) and log in.
4. You will see a badge at the bottom-right: **Bridge: Connected (Ready)** once the proxy is running.

### 3. Start the Proxy

```bash
python deepseek-proxy.py
```

Options:
- `--host 127.0.0.1`: Listening host (default: `127.0.0.1`).
- `--port 1337`: Listening port (default: `1337`).
- `--reset-threshold 150000`: Auto-reset chat session if tokens exceed limit (default: `150000`, `0` to disable).

---

## OpenCode Configration

Add this provider to your OpenCode config (`opencode.jsonc`):

```json
{
  "provider": {
    "deepseek-proxy": {
      "api": "openai",
      "name": "DeepSeek Web Proxy",
      "options": {
        "baseURL": "http://127.0.0.1:1337/v1",
        "apiKey": "nah",
        "timeout": 3600000,
        "chunkTimeout": 3600000
      },
      "models": {
        "deepseek-chat": {
          "id": "deepseek-chat",
          "name": "DeepSeek Chat (via Web Proxy)",
          "tool_call": true,
          "temperature": true
        },
        "deepseek-reasoner": {
          "id": "deepseek-reasoner",
          "name": "DeepSeek Reasoner (via Web Proxy)",
          "tool_call": true,
          "reasoning": true,
          "temperature": true
        }
      }
    }
  }
}
```

---

## Other Clients (Cline, Cursor, etc.)

- **Base URL**: `http://127.0.0.1:1337/v1`
- **API Key**: `nah`
- **Models**:
  - `deepseek-chat`: Standard chat
  - `deepseek-reasoner`: DeepSeek R1 reasoning

---

## Python Example

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:1337/v1",
    api_key="nah",
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Hello DeepSeek"}],
    stream=True,
)

for chunk in response:
    reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
    if reasoning:
        print(reasoning, end="", flush=True)
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

---

## Notes & Chat Management

- Keep the browser tab open while using the proxy.
- Updating `deepseek-bridge.user.js` on disk does not update Tampermonkey: replace its installed script, save, and reload the DeepSeek tab. Current userscript version: **1.0.6**.
- Any function tool supplied by OpenCode (including MCP tools) is transported by name and JSON schema; execution and permissions remain in OpenCode. The proxy never executes commands itself.
- Browser-backed tool calling is prompted and parsed, not native API tool calling. Model compliance is not guaranteed. Non-image file uploads, native Responses API, and real-time token streaming are not implemented.
- SSE responses are delivered after browser generation completes. Large tool catalogs are retained; system/history context may be truncated. The web chat retains earlier turns, including title-generation requests.
- An empty browser answer returns an explicit API error instead of silently completing with no text.
- If the badge shows disconnected, click it to reconnect immediately.
- To reset manually, send `/clear`, `/reset`, or `/new` directly from your client prompt (or run `window.deleteCurrentChat()` in the browser console).

---

## License

MIT

## Regression tests

```bash
python -m unittest test_proxy -v
node test_bridge.cjs
```

These tests cover parsing and API transport using synthetic browser responses. Live browser/OpenCode checks are still needed after userscript or website updates.

## Image input

Send images in OpenAI chat-completions format:

```json
{"role":"user","content":[{"type":"text","text":"Describe this image"},{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]}
```

- OpenCode V2: set `capabilities.input` to `["text", "image"]` on the two web-proxy models. Leave other providers unchanged.
- Limits: 10 images in the current input, 5 MiB per image, 10 MiB combined. Remote URLs and local file paths are rejected; the server never fetches them.
- Images are sent to DeepSeek using your logged-in account. Do not attach secrets. The browser waits up to 120 seconds for processing and reports failures explicitly.
- Keep the DeepSeek composer free of draft attachments. After failed uploads, remove the failed draft attachments before retrying; the bridge does not delete your drafts automatically.
- Only images from the latest user message or the latest batch of tool results are uploaded. Historical attachments are not re-uploaded; they remain in the existing DeepSeek chat. After resetting that chat, re-attach an older image if it is needed again.
