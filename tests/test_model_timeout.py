import os
import unittest
from unittest.mock import patch

from model_client import model_for


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
