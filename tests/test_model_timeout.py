import asyncio
import os
import unittest
from unittest.mock import patch

from model_client import model_for, model_step


class ModelTimeoutTests(unittest.TestCase):
    def test_ollama_timeout_setting_reaches_sdk(self):
        for settings, expected in (
            ({}, 120),
            ({"EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS": "300"}, 300),
            ({"EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS": "0"}, None),
        ):
            with self.subTest(settings=settings), patch.dict(os.environ, settings, clear=True):
                with patch("model_client.ChatOllama") as client:
                    model_for("ollama")
                    self.assertEqual(client.call_args.kwargs["client_kwargs"]["timeout"], expected)

    def test_openai_timeout_does_not_use_ollama_setting(self):
        with patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS": "0"}, clear=True):
            with patch("model_client.ChatOpenAI") as client:
                model_for("openai")
                self.assertEqual(client.call_args.kwargs["timeout"], 120)
                self.assertEqual(client.call_args.kwargs["max_retries"], 0)

    def test_invalid_ollama_timeout_is_reported_before_sdk_creation(self):
        for invalid in ("-1", "unlimited"):
            with self.subTest(setting=invalid), patch.dict(os.environ,
                    {"EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS": invalid}, clear=True):
                with patch("model_client.ChatOllama") as client:
                    with self.assertRaisesRegex(ValueError, "EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS"):
                        model_for("ollama")
                    client.assert_not_called()

    def test_legacy_base_url_and_context_size(self):
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://localhost:11434/v1",
                "EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX": "8192",
                "EVENT_SUPPORT_AGENT_OLLAMA_REASONING": "false"}, clear=True):
            with patch("model_client.ChatOllama") as client:
                model_for("ollama")
                self.assertEqual(client.call_args.kwargs["base_url"], "http://localhost:11434")
                self.assertEqual(client.call_args.kwargs["num_ctx"], 8192)
                self.assertIs(client.call_args.kwargs["reasoning"], False)

    def test_request_metrics_use_configured_context_and_provider_reported_usage(self):
        events = []
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "question"},
                    {"role": "tool", "content": "private-result"}]
        def complete(provider, messages, tools):
            return {"content": "", "_provider_meta": {"model": "reported", "prompt_eval_count": 123,
                    "eval_count": 45, "done_reason": "length"}}
        with patch.dict(os.environ, {"OLLAMA_MODEL": "configured",
                "EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX": "8192"}, clear=True):
            asyncio.run(model_step("ollama", messages, [], complete,
                                   lambda t, d: events.append((t, d)), "test", num_predict=2048))
        metrics = dict(events)["Python 模型請求指標 · test"]
        self.assertEqual(metrics["配置模型"], "configured")
        self.assertEqual(metrics["供應商模型"], "reported")
        self.assertEqual(metrics["配置Context上限"], 8192)
        self.assertEqual(metrics["輸入Token"], 123)
        self.assertEqual(metrics["輸出Token"], 45)
        self.assertTrue(metrics["輸出達長度上限"])
        self.assertEqual(metrics["工具結果字元數"], len("private-result"))
        self.assertNotIn("private-result", str(metrics))

    def test_missing_provider_metrics_remain_unknown(self):
        events = []
        with patch.dict(os.environ, {}, clear=True):
            asyncio.run(model_step("ollama", [{"role": "user", "content": "question"}], [],
                lambda *args: {"content": ""}, lambda t, d: events.append((t, d)), "test"))
        metrics = dict(events)["Python 模型請求指標 · test"]
        for field in ("配置Context上限", "輸入Token", "輸出Token", "輸出達長度上限"):
            self.assertIsNone(metrics[field])
