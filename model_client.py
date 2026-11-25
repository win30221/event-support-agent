"""Provider transport and observable model calls."""
import asyncio
import json
import os
import urllib.error
import urllib.request

from copy import deepcopy


# 統一 OpenAI-compatible API；原生工具呼叫與結構化回答分開傳送。
def completion(provider, messages, tools, *, response_schema=None):
    timeout = 120
    if provider == "openai":
        url = "https://api.openai.com/v1"
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}
    else:
        url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
        headers = {}
        setting = os.getenv("EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS", str(timeout)).strip()
        if not setting.isdecimal():
            raise ValueError("EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS 請填 0 或正整數秒數。")
        # urllib uses None to wait without a Python socket timeout.
        timeout = int(setting) or None
    body = {"model": model, "messages": messages, "stream": False}
    if tools and response_schema is not None:
        raise ValueError("工具選擇與最終結構化回答分開請求。")
    if tools:
        body["tools"] = [
            {**tool, "function": {**tool["function"], "strict": True}}
            if provider == "openai" else tool for tool in tools
        ]
    elif response_schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "activity_answer", "strict": True, "schema": response_schema,
        }}
    if provider == "ollama":
        body["temperature"] = 0

    def send():
        req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.load(response)
            choice = payload["choices"][0]
            message = dict(choice["message"])
            metadata = {key: value for key, value in {
                "model": payload.get("model"), "finish_reason": choice.get("finish_reason"),
                "message_fields": sorted(message) if "finish_reason" in choice else None,
                "usage": payload.get("usage"),
            }.items() if value is not None}
            if metadata:
                message["_provider_meta"] = metadata
            return message

    try:
        return send()
    except urllib.error.HTTPError as error:
        # Older compatible deployments may only offer JSON object mode.
        if response_schema is None or error.code != 400:
            raise
        reason = error.read().decode(errors="replace").lower()
        if "json_schema" not in reason or not any(s in reason for s in ("not supported", "unsupported")):
            raise
        body["response_format"] = {"type": "json_object"}
        return send()


def ignore_event(title, data):
    pass


# 保留階段、請求及供應商回傳 metadata；阻塞的 HTTP 呼叫交給執行緒。
async def model_step(provider, messages, tools, complete, observe, stage, *, response_schema=None):
    request = {"服務": provider, "messages": deepcopy(messages), "tools": deepcopy(tools)}
    if response_schema is not None:
        request["預期回答格式"] = response_schema
    observe(f"送出模型請求 · {stage}", request)
    def invoke():
        # StopIteration must not cross an asyncio Future boundary (e.g. an
        # exhausted scripted model in tests). Fail explicitly instead of hanging.
        try:
            if response_schema is None:
                return complete(provider, deepcopy(messages), tools)
            return complete(provider, deepcopy(messages), tools, response_schema=response_schema)
        except StopIteration as error:
            raise RuntimeError("模型回傳序列意外結束。") from error
    reply = await asyncio.to_thread(invoke)
    if not isinstance(reply, dict):
        raise ValueError("模型回傳不是有效訊息。")
    output = {key: reply[key] for key in ("role", "content", "tool_calls", "_provider_meta") if key in reply}
    if response_schema is not None:
        try:
            output["回答 JSON"] = json.loads(reply["content"])
        except (KeyError, TypeError, ValueError):
            pass
    observe(f"收到模型回傳 · {stage}", deepcopy(output))
    return reply
