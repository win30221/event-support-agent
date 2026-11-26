import io
import json
import os
import unittest
from unittest.mock import patch

from embedding_client import EmbeddingError, embed, resolve_embedding
from backend import ToolRegistry


class EmbeddingTests(unittest.TestCase):
    def test_auto_and_override_do_not_share_global_state(self):
        with patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_EMBED_PROVIDER": "auto",
                                    "EVENT_SUPPORT_AGENT_EMBED_MODEL": "bge-m3"}, clear=True):
            local = ToolRegistry("A", provider="ollama")
            cloud = ToolRegistry("B", provider="nvidia")
            self.assertEqual(local.embedding.model, "bge-m3")
            self.assertEqual(cloud.embedding.model, "nvidia/nemotron-3.5-embed-1b")
            self.assertEqual(resolve_embedding("nvidia", "openai").model, "text-embedding-3-small")
            self.assertEqual(local.embedding.provider, "ollama")

    def test_nvidia_query_and_passage_contract_and_response_order(self):
        payload = {"data": [{"index": 1, "embedding": [0.0, 1.0]},
                            {"index": 0, "embedding": [1.0, 0.0]}]}
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-only"}, clear=True):
            settings = resolve_embedding(selection="nvidia")
            for mode in ("passage", "query"):
                with patch("embedding_client.urllib.request.urlopen",
                           return_value=io.BytesIO(json.dumps(payload).encode())) as request:
                    self.assertEqual(embed(["a", "b"], settings=settings, input_type=mode),
                                     [[1.0, 0.0], [0.0, 1.0]])
                    req = request.call_args.args[0]
                    self.assertEqual(json.loads(req.data)["input_type"], mode)
                    self.assertEqual(json.loads(req.data)["truncate"], "NONE")
                    self.assertEqual(req.full_url, "https://integrate.api.nvidia.com/v1/embeddings")

    def test_openai_payload_does_not_include_nvidia_parameters(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}, clear=True):
            payload = {"data": [{"index": 0, "embedding": [1.0]}]}
            with patch("embedding_client.urllib.request.urlopen",
                       return_value=io.BytesIO(json.dumps(payload).encode())) as request:
                self.assertEqual(embed(["a"], settings=resolve_embedding(selection="openai")), [[1.0]])
                body = json.loads(request.call_args.args[0].data)
                self.assertNotIn("input_type", body)
                self.assertNotIn("truncate", body)

    def test_missing_key_fails_before_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("embedding_client.urllib.request.urlopen") as request:
                with self.assertRaises(EmbeddingError):
                    embed(["a"], settings=resolve_embedding(selection="nvidia"))
                request.assert_not_called()

    def test_bad_response_index_is_rejected(self):
        payload = {"data": [{"index": 2, "embedding": [1.0]}]}
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-only"}, clear=True):
            with patch("embedding_client.urllib.request.urlopen",
                       return_value=io.BytesIO(json.dumps(payload).encode())):
                with self.assertRaises(EmbeddingError):
                    embed(["a"], settings=resolve_embedding(selection="nvidia"))
