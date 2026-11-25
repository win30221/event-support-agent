import os
import unittest
from io import BytesIO
from unittest.mock import patch

from agent import completion


class ModelTimeoutTests(unittest.TestCase):
    def test_ollama_timeout_setting_reaches_http_client(self):
        for provider, settings, expected in (
            ("ollama", {}, 120),
            ("ollama", {"EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS": "300"}, 300),
            ("ollama", {"EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS": "0"}, None),
            ("openai", {"EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS": "0", "OPENAI_API_KEY": "test-only"}, 120),
        ):
            with self.subTest(provider=provider, settings=settings), patch.dict(os.environ, settings, clear=True):
                response = BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')
                with patch("urllib.request.urlopen", return_value=response) as send:
                    self.assertEqual(completion(provider, [{"role": "user", "content": "test"}], []), {"content": "{}"})
                self.assertEqual(send.call_args.kwargs["timeout"], expected)

    def test_invalid_ollama_timeout_is_reported_before_http_request(self):
        for invalid in ("-1", "unlimited"):
            with self.subTest(setting=invalid), \
                 patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS": invalid}), \
                 patch("urllib.request.urlopen") as send:
                with self.assertRaisesRegex(ValueError, "EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS"):
                    completion("ollama", [], [])
                send.assert_not_called()
