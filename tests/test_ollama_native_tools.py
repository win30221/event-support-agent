"""Check the exact Ollama wire contract without running a model server."""
import json
import unittest
from unittest.mock import patch

import httpx

from business_replies import FACT_DEFINITION
from model_client import TransientModelError, acompletion
from ollama_native_tools import native_tool_reply


class NativeOllamaTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_preserves_schema_scope_and_usage(self):
        inbox = []
        def endpoint(req):
            inbox.append(json.loads(req.content))
            return httpx.Response(200, json={"model": "local", "done": True, "done_reason": "stop",
                "prompt_eval_count": 12, "eval_count": 3, "message": {"role": "assistant", "content": "",
                    "thinking": "private", "tool_calls": [{"function": {"name": "submit_answer", "arguments": {}}}]}})
        settings = {"base_url": "http://localhost:11434", "model": "local", "temperature": 0,
                    "num_ctx": 8192, "num_predict": 2048, "reasoning": False,
                    "client_kwargs": {"transport": httpx.MockTransport(endpoint), "timeout": 5}}
        msgs = [{"role": "user", "content": "我想查活動規則"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "a", "function": {"name": "find_rules", "arguments": '{"edition":"current"}'}},
                    {"id": "b", "function": {"name": "find_rules", "arguments": {"edition": "previous"}}}]},
                {"role": "tool", "tool_call_id": "a", "content": "first"},
                {"role": "tool", "tool_call_id": "b", "content": "second"}]
        row = await native_tool_reply(settings, msgs, [FACT_DEFINITION])
        body = inbox[0]
        self.assertEqual(body["tools"], [FACT_DEFINITION])
        self.assertFalse(body["stream"])
        self.assertFalse(body["think"])
        self.assertEqual(body["options"], {"temperature": 0, "num_ctx": 8192, "num_predict": 2048})
        self.assertEqual(body["messages"][1]["tool_calls"][0]["function"]["arguments"], {"edition": "current"})
        self.assertEqual([(m["tool_name"], m["tool_call_id"]) for m in body["messages"][2:]],
                         [("find_rules", "a"), ("find_rules", "b")])
        self.assertEqual(row["tool_calls"][0]["function"]["name"], "submit_answer")
        self.assertEqual(row["_provider_meta"]["usage"]["total_tokens"], 15)
        self.assertNotIn("thinking", row)
        self.assertNotIn("private", str(row))

    async def test_provider_failure_uses_existing_transport_error(self):
        settings = {"base_url": "http://localhost:11434", "model": "local",
                    "client_kwargs": {"transport": httpx.MockTransport(lambda req: httpx.Response(503))}}
        with patch("model_client.ollama_settings", return_value=settings):
            with self.assertRaises(TransientModelError):
                await acompletion("ollama", [], [FACT_DEFINITION], native_ollama=True)
