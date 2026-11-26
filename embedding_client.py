"""Request-scoped embedding selection, independent of chat completion."""
from dataclasses import dataclass, replace
import json
import math
import os
import urllib.error
import urllib.request

PROVIDERS = ("ollama", "openai", "nvidia")
DEFAULT_MODELS = {"ollama": "bge-m3", "openai": "text-embedding-3-small",
                  "nvidia": "nvidia/nemotron-3.5-embed-1b"}
KEY_VARIABLES = {"openai": "OPENAI_API_KEY", "nvidia": "NVIDIA_API_KEY"}
ENDPOINTS = {"openai": "https://api.openai.com/v1/embeddings",
             "nvidia": "https://integrate.api.nvidia.com/v1/embeddings"}


@dataclass(frozen=True)
class EmbeddingSettings:
    provider: str
    model: str
    endpoint: str

    def public_metadata(self):
        return {"provider": self.provider, "model": self.model}


class EmbeddingError(RuntimeError):
    """Only a safe diagnostic is logged; never include credentials or request bodies."""


def resolve_embedding(chat_provider=None, selection=None):
    selected = (selection if selection is not None else
                os.getenv("EVENT_SUPPORT_AGENT_EMBED_PROVIDER", "auto")).strip().lower()
    provider = (chat_provider or "ollama") if selected == "auto" else selected
    if provider not in PROVIDERS:
        raise ValueError("Embedding provider must be auto, ollama, openai or nvidia.")
    # Existing generic model/URL variables remain Ollama-only for backward compatibility.
    default = (os.getenv("EVENT_SUPPORT_AGENT_EMBED_MODEL", DEFAULT_MODELS[provider])
               if provider == "ollama" else DEFAULT_MODELS[provider])
    model = os.getenv(f"EVENT_SUPPORT_AGENT_{provider.upper()}_EMBED_MODEL", default).strip()
    if not model:
        raise ValueError("Embedding model cannot be empty.")
    endpoint = (os.getenv("EVENT_SUPPORT_AGENT_EMBED_URL", "http://localhost:11434").rstrip("/") + "/api/embed"
                if provider == "ollama" else ENDPOINTS[provider])
    return EmbeddingSettings(provider, model, endpoint)


def embed(texts, model=None, *, settings=None, input_type="query"):
    settings = settings or resolve_embedding()
    if model is not None:
        settings = replace(settings, model=model)
    if input_type not in ("passage", "query"):
        raise ValueError("Embedding input type must be passage or query.")
    if not texts:
        return []
    headers = {"Content-Type": "application/json"}
    body = {"model": settings.model, "input": texts}
    if settings.provider == "ollama":
        body["truncate"] = False
    else:
        key_variable = KEY_VARIABLES[settings.provider]
        key = os.getenv(key_variable, "").strip()
        if not key:
            raise EmbeddingError(f"Missing {key_variable}.")
        headers["Authorization"] = f"Bearer {key}"
        body["encoding_format"] = "float"
        if settings.provider == "nvidia":
            # NVIDIA uses distinct document and query encoders/prefixes.
            body.update(input_type=input_type, truncate="NONE")
    req = urllib.request.Request(settings.endpoint, json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = json.load(response)
        if settings.provider == "ollama":
            vectors = payload["embeddings"]
        else:
            data = sorted(payload["data"], key=lambda item: item["index"])
            if [item["index"] for item in data] != list(range(len(texts))):
                raise ValueError("Invalid embedding response indices.")
            vectors = [item["embedding"] for item in data]
    except urllib.error.HTTPError as error:
        raise EmbeddingError(f"{settings.provider} embedding HTTP {error.code}.") from None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise EmbeddingError(f"{settings.provider} embedding failed: {type(error).__name__}.") from None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise EmbeddingError("Embedding count does not match input.")
    for vector in vectors:
        if not isinstance(vector, list) or not vector or len(vector) != len(vectors[0]):
            raise EmbeddingError("Embedding dimensions differ.")
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector) or not any(vector):
            raise EmbeddingError("Invalid embedding vector.")
    return vectors
