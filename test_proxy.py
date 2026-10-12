import runpy
import unittest
import json
import base64
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer


proxy = runpy.run_path("deepseek-proxy.py", run_name="test")
PNG_URL = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimage-test").decode()


class ToolProtocolTests(unittest.TestCase):
    def test_inline_image_validation(self):
        message = {"role": "user", "content": [{"type": "text", "text": "Describe"},
                   {"type": "image_url", "image_url": {"url": PNG_URL}}]}
        images = proxy["collect_images"]([message])
        self.assertEqual(images[0]["mimeType"], "image/png")
        prompt, _ = proxy["build_prompt"]([message], None)
        self.assertIn("Image attachment", prompt)
        self.assertNotIn("base64", prompt)
        for url in ("https://127.0.0.1/private.png", "file:///private.png",
                    "data:image/png;base64,AAAA", "data:image/png;base64,===="):
            with self.assertRaises(ValueError):
                proxy["collect_images"]([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}])

    def test_image_count_and_size_limits(self):
        block = {"type": "image_url", "image_url": {"url": PNG_URL}}
        self.assertEqual(len(proxy["collect_images"]([{"role": "user", "content": [block] * 10}])), 10)
        with self.assertRaisesRegex(ValueError, "At most 10"):
            proxy["collect_images"]([{"role": "user", "content": [block] * 11}])
        huge = "data:image/png;base64," + "A" * (8 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "at most 5 MiB"):
            proxy["collect_images"]([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": huge}}]}])

    def test_only_current_image_is_uploaded_not_history(self):
        second_url = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nsecond-image").decode()
        def message(url):
            return {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        history = [message(PNG_URL), {"role": "assistant", "content": "First image received"}]
        images = proxy["collect_images"](history + [message(second_url)])
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["data"], second_url.split(",", 1)[1])
        self.assertEqual(proxy["collect_images"](history + [{"role": "user", "content": "Now explain"}]), [])

    def test_tool_followup_does_not_upload_user_image_again(self):
        history = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": PNG_URL}}]},
                   {"role": "assistant", "content": "Calling a tool"}]
        self.assertEqual(proxy["collect_images"](history + [{"role": "tool", "content": "Tool result"}]), [])
        images = proxy["collect_images"](history + [{"role": "tool", "content": [
            {"type": "image_url", "image_url": {"url": PNG_URL}}
        ]}])
        self.assertEqual(len(images), 1)

    def test_action_instructions_survive_large_context(self):
        prompt, mode = proxy["build_prompt"](
            [{"role": "system", "content": "x" * 30000},
             {"role": "user", "content": "Execute Get-Date"}],
            [{"type": "function", "function": {"name": "shell"}}],
        )
        self.assertEqual(mode, "native_tools")
        self.assertIn("Execute Get-Date", prompt)
        self.assertIn("<tool_call>", prompt.split("# Current API turn")[1])
        self.assertIn("wait for a Tool result", prompt)

    def test_only_explicit_tool_calls_are_executable(self):
        parse = proxy["parse_tool_calls"]
        self.assertEqual(parse("shell: Get-Date", ["shell"]), [])
        self.assertEqual(
            parse('<tool_call><name>shell</name><arguments>{"command":"Get-Date"}</arguments></tool_call>', ["shell"]),
            [{"name": "shell", "arguments": {"command": "Get-Date"}}],
        )

    def test_dsml_preserves_strings_and_typed_arguments(self):
        raw = ('<｜｜DSML｜｜ invoke name="edit">'
               '<｜｜DSML｜｜ parameter name="oldString" string="true">  true\n</｜｜DSML｜｜ parameter>'
               '<｜｜DSML｜｜ parameter name="replaceAll" string="false">false</｜｜DSML｜｜ parameter>'
               '</｜｜DSML｜｜ invoke>')
        self.assertEqual(proxy["parse_tool_calls"](raw, ["edit"]), [
            {"name": "edit", "arguments": {"oldString": "  true\n", "replaceAll": False}}
        ])

    def test_unknown_tools_are_not_executable(self):
        raw = '<tool_call><name>unknown</name><arguments>{}</arguments></tool_call>'
        self.assertEqual(proxy["parse_tool_calls"](raw, ["read"]), [])

    def test_code_mode_discovery_survives_truncation(self):
        prompt, _ = proxy["build_prompt"](
            [{"role": "system", "content": "x" * 20000 + "\n# Code Mode\nCatalog is partial\n" + "x" * 20000},
             {"role": "user", "content": "List scripts from a requested MCP server"}],
            [{"type": "function", "function": {"name": "execute"}}],
        )
        self.assertIn("# MCP / Code Mode routing", prompt)
        self.assertIn('return search({namespace: "SERVER", query: "ACTION", limit: 5});', prompt)
        self.assertIn("Empty resources do not prove a server is disconnected", prompt)
        self.assertIn("exact path and signature", prompt)

    def test_no_code_mode_api_is_invented_for_other_clients(self):
        prompt, _ = proxy["build_prompt"](
            [{"role": "user", "content": "List scripts"}],
            [{"type": "function", "function": {"name": "execute"}}],
        )
        self.assertNotIn("# MCP / Code Mode routing", prompt)

    def test_large_tool_catalog_and_developer_instructions_are_preserved(self):
        tools = [{"function": {"name": f"mcp_tool_{i}", "description": "x" * 1000,
                               "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
                 for i in range(30)]
        prompt, _ = proxy["build_prompt"](
            [{"role": "developer", "content": "Respect user permissions"},
             {"role": "user", "content": "Read a file"}], tools,
        )
        for tool in tools:
            self.assertIn(tool["function"]["name"], prompt)
        self.assertIn("Respect user permissions", prompt)

    def test_tool_choice_and_tool_results(self):
        messages = [{"role": "user", "content": "read"},
                    {"role": "tool", "tool_call_id": "call_123", "content": "result"}]
        tools = [{"type": "function", "function": {"name": "read"}}]
        prompt, mode = proxy["build_prompt"](messages, tools, "none")
        self.assertEqual(mode, "text")
        self.assertNotIn("# Current API turn", prompt)
        prompt, _ = proxy["build_prompt"](messages, tools, "required")
        self.assertIn("requires at least one tool call", prompt)
        self.assertIn("Tool result (call_123)", prompt)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(proxy["make_app"]()))
        await self.client.start_server()
        self.globals = proxy["handle_chat_completions"].__globals__

    async def asyncTearDown(self):
        await self.client.close()

    async def post(self, body, result):
        with patch.dict(self.globals, {"run_browser_job": AsyncMock(return_value=result)}):
            response = await self.client.post("/v1/chat/completions", json=body)
            return response.status, await response.text()

    async def test_streamed_multiple_tool_calls_and_usage(self):
        body = {"messages": [{"role": "user", "content": "Read both files"}],
                "stream": True, "stream_options": {"include_usage": True},
                "tools": [{"type": "function", "function": {"name": "read"}}]}
        raw = ''.join('<tool_call><name>read</name><arguments>' + json.dumps({"path": path})
                      + '</arguments></tool_call>' for path in ("á.py", "b.py"))
        status, text = await self.post(body, {"text": raw})
        self.assertEqual(status, 200)
        self.assertTrue(text.endswith("data: [DONE]\n\n"))
        events = [json.loads(line[6:]) for line in text.splitlines()
                  if line.startswith("data: ") and line != "data: [DONE]"]
        calls = events[0]["choices"][0]["delta"]["tool_calls"]
        self.assertEqual([call["index"] for call in calls], [0, 1])
        self.assertNotEqual(calls[0]["id"], calls[1]["id"])
        args = {0: "", 1: ""}
        for event in events[1:]:
            for choice in event["choices"]:
                for call in choice["delta"].get("tool_calls", []):
                    args[call["index"]] += call["function"]["arguments"]
        self.assertEqual(json.loads(args[0]), {"path": "á.py"})
        self.assertEqual(json.loads(args[1]), {"path": "b.py"})
        self.assertEqual(events[-2]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(events[-1]["choices"], [])
        self.assertIn("total_tokens", events[-1]["usage"])

    async def test_reasoning_and_answer_stream(self):
        status, text = await self.post(
            {"model": "deepseek-reasoner", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            {"text": "Olá", "reasoning": "Thought"},
        )
        self.assertEqual(status, 200)
        self.assertIn('"reasoning_content": "Thought"', text)
        self.assertIn('"content": "Olá"', text)

    async def test_empty_answer_is_not_silent_success(self):
        status, text = await self.post({"messages": [{"role": "user", "content": "hi"}]}, {"text": ""})
        self.assertEqual(status, 502)
        self.assertIn("no final response", json.loads(text)["error"]["message"])

    async def test_images_are_forwarded_and_must_be_acknowledged(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": PNG_URL}},
            {"type": "text", "text": "Describe this image"},
        ]}]}
        job = AsyncMock(return_value={"text": "Image received", "imageCount": 1})
        with patch.dict(self.globals, {"run_browser_job": job}):
            response = await self.client.post("/v1/chat/completions", json=body)
            self.assertEqual(response.status, 200)
            await response.read()
            self.assertEqual(job.call_args.kwargs["images"][0]["mimeType"], "image/png")
        status, text = await self.post(body, {"text": "I did not see an image"})
        self.assertEqual(status, 502)
        self.assertIn("confirm image upload", text)

    async def test_bad_images_fail_before_browser_submission(self):
        job = AsyncMock(return_value={"text": "unused"})
        with patch.dict(self.globals, {"run_browser_job": job}):
            response = await self.client.post("/v1/chat/completions", json={"messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://localhost/private"}}]}
            ]})
            self.assertEqual(response.status, 400)
            await response.read()
            job.assert_not_called()

    async def test_websocket_job_carries_image_data(self):
        state = self.globals

        class Browser:
            closed = False
            packet = None

            async def send_str(self, text):
                self.packet = json.loads(text)
                state["pending_jobs"][self.packet["id"]].set_result({"text": "Seen", "imageCount": 1})

        browser = Browser()
        images = proxy["collect_images"]([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": PNG_URL}}]}])
        with patch.dict(state, {"active_ws": browser, "_job_lock": None, "_last_job_time": 0}):
            result = await proxy["run_browser_job"]("Describe", True, "deepseek-reasoner", images)
        self.assertEqual(result["imageCount"], 1)
        self.assertEqual(browser.packet["images"], images)
        self.assertTrue(browser.packet["thinkingEnabled"])
        self.assertEqual(len(state["pending_jobs"]), 0)

    async def test_reasoning_is_preserved_with_tool_calls(self):
        body = {"messages": [{"role": "user", "content": "read"}],
                "tools": [{"function": {"name": "read"}}]}
        result = {"reasoning": "Need file", "text":
                  '<tool_call><name>read</name><arguments>{"path":"x"}</arguments></tool_call>'}
        status, text = await self.post(body, result)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text)["choices"][0]["message"]["reasoning_content"], "Need file")
        status, text = await self.post({**body, "stream": True}, result)
        self.assertEqual(status, 200)
        self.assertLess(text.index('"reasoning_content"'), text.index('"tool_calls"'))

    async def test_invalid_requests_return_400(self):
        for body in ([], {"messages": [1]}, {"messages": [{"role": 1}]},
                     {"messages": [{"role": "user"}], "tools": [1]},
                     {"messages": [{"role": "user"}], "model": 1}):
            status, text = await self.post(body, {"text": "unused"})
            self.assertEqual(status, 400, text)

    async def test_required_tool_cannot_be_simulated_as_text(self):
        status, _ = await self.post(
            {"messages": [{"role": "user", "content": "execute"}], "tool_choice": "required",
             "tools": [{"function": {"name": "shell"}}]}, {"text": "shell: Get-Date"},
        )
        self.assertEqual(status, 502)


if __name__ == "__main__":
    unittest.main()
