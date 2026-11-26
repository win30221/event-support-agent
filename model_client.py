"""LangChain model integrations; one provider-neutral request and observation seam."""
import asyncio
import json
import os
from copy import deepcopy
from time import perf_counter

import httpx
from langchain_core.messages import AIMessage, convert_to_messages
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from ollama import ResponseError
from openai import APIConnectionError, APIStatusError

from request_pacing import note_rate_limit, pace_request
from transport_observation import record_transport


class TransientModelError(RuntimeError):
    """Only transport/rate-limit/server failures are eligible for node retry."""


PROVIDERS = ("ollama", "openai", "nvidia")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"


def configured_model(provider):
    defaults = {"openai": ("OPENAI_MODEL", "gpt-4o-mini"),
                "ollama": ("OLLAMA_MODEL", "qwen3:8b"),
                "nvidia": ("NVIDIA_MODEL", NVIDIA_DEFAULT_MODEL)}
    if provider not in defaults:
        raise ValueError("未知模型服務。")
    return os.getenv(*defaults[provider])


def model_for(provider, *, num_predict=None):
    if provider == "openai":
        return ChatOpenAI(model=configured_model(provider),
                          timeout=120, max_retries=0, use_responses_api=False)
    if provider == "nvidia":
        credential = os.getenv("NVIDIA_API_KEY", "").strip()
        if not credential:
            raise ValueError("請先設定 NVIDIA_API_KEY。")
        name = configured_model(provider).strip()
        if not name:
            raise ValueError("NVIDIA_MODEL 不可為空白。")
        return ChatOpenAI(model=name, base_url=NVIDIA_BASE_URL, api_key=credential,
                          timeout=120, max_retries=0, use_responses_api=False)
    if provider != "ollama":
        raise ValueError("未知模型服務。")
    setting = os.getenv("EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS", "120").strip()
    if not setting.isdecimal():
        raise ValueError("EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS 請填 0 或正整數秒數。")
    # 舊 /v1 設定相容轉換；ChatOllama 使用 Ollama SDK 的 /api/chat。
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    options = {"model": configured_model(provider),
               "base_url": base, "temperature": 0,
               "client_kwargs": {"timeout": int(setting) or None}}
    context = os.getenv("EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX", "").strip()
    if context:
        if not context.isdecimal() or int(context) <= 0:
            raise ValueError("EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX 須為正整數。")
        options["num_ctx"] = int(context)
    reasoning = os.getenv("EVENT_SUPPORT_AGENT_OLLAMA_REASONING", "").strip().lower()
    if reasoning:
        if reasoning not in ("true", "false", "low", "medium", "high"):
            raise ValueError("EVENT_SUPPORT_AGENT_OLLAMA_REASONING 設定無效。")
        options["reasoning"] = {"true": True, "false": False}.get(reasoning, reasoning)
    if num_predict is not None:
        options["num_predict"] = num_predict
    return ChatOllama(**options)


def bound_model(provider, tools, response_schema, *, num_predict=None):
    if tools and response_schema is not None:
        raise ValueError("工具選擇與最終結構化回答分開請求。")
    model = model_for(provider) if num_predict is None else model_for(provider, num_predict=num_predict)
    if tools:
        # 同一份 registry 定義，不用文字 JSON 模擬 function calling。
        if provider == "nvidia":
            return model.bind_tools(tools, tool_choice="auto")
        return model.bind_tools(tools, **({"strict": True} if provider == "openai" else {}))
    if response_schema is not None:
        if provider == "nvidia":
            return model.bind(response_format={"type": "json_schema", "json_schema": {
                "name": "agent_response", "schema": response_schema}})
        options = {"method": "json_schema", "include_raw": True}
        if provider == "openai":
            options["strict"] = True
        return model.with_structured_output(response_schema, **options)
    return model


def normalize_reply(value):
    """Keep native tool calls, parsed JSON and safe metadata; never expose reasoning."""
    parsed = None
    parsing_error = None
    if isinstance(value, dict) and "raw" in value:
        parsed, parsing_error, value = value.get("parsed"), value.get("parsing_error"), value["raw"]
    if not isinstance(value, AIMessage):
        raise ValueError("模型回傳不是 AIMessage。")
    content = value.content
    if isinstance(content, list):
        content = "\n".join(block if isinstance(block, str) else block.get("text", "")
                            for block in content if isinstance(block, str)
                            or isinstance(block, dict) and block.get("type") == "text")
    if parsed is not None:
        if hasattr(parsed, "model_dump"):
            parsed = parsed.model_dump(mode="json")
        content = json.dumps(parsed, ensure_ascii=False)
    calls = []
    for item in value.tool_calls:
        calls.append({"id": item.get("id"), "type": "function",
                      "function": {"name": item["name"], "arguments":
                                   json.dumps(item["args"], ensure_ascii=False)}})
    for item in value.invalid_tool_calls:
        calls.append({"id": item.get("id"), "type": "function",
                      "function": {"name": item.get("name"), "arguments": item.get("args")}})
    meta = value.response_metadata or {}
    usage = value.usage_metadata or {}
    safe = {key: meta[key] for key in (
        "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
        "eval_count", "eval_duration", "done_reason") if key in meta}
    safe.update({key: val for key, val in {
        "model": meta.get("model_name") or meta.get("model"),
        "finish_reason": meta.get("finish_reason") or meta.get("done_reason"),
        "usage": {"prompt_tokens": usage.get("input_tokens"),
                  "completion_tokens": usage.get("output_tokens"),
                  "total_tokens": usage.get("total_tokens")} if usage else None,
        "parsing_error": type(parsing_error).__name__ if parsing_error else None,
    }.items() if val is not None})
    reply = {"role": "assistant", "content": content, "tool_calls": calls}
    if safe:
        reply["_provider_meta"] = safe
    return reply


def transport_error(error, provider=None):
    if provider == "nvidia":
        status = getattr(error, "status_code", None)
        if status in (401, 403):
            return RuntimeError("NVIDIA API 驗證或授權失敗；請確認 NVIDIA_API_KEY 與模型存取權。")
        if status == 404:
            return RuntimeError("NVIDIA 模型或端點不可用；請確認 NVIDIA_MODEL。")
        if status == 429:
            return TransientModelError("NVIDIA API 達速率限制；請檢查帳號配額後重試。")
        if status in (400, 422):
            return RuntimeError(f"NVIDIA API 拒絕請求（HTTP {status}）；請核對工具及 JSON Schema 相容性。")
    if isinstance(error, (APIConnectionError, httpx.TransportError, ConnectionError)):
        return TransientModelError("模型服務連線或等待逾時。")
    status = getattr(error, "status_code", None)
    if status == 429 or isinstance(status, int) and status >= 500:
        return TransientModelError("模型服務暫時無法使用。")
    return RuntimeError("模型服務請求失敗；請檢查模型、服務設定及供應商日誌。")


def completion(provider, messages, tools, *, response_schema=None):
    """Synchronous public adapter; tests can inject a scripted completion instead."""
    try:
        model = bound_model(provider, tools, response_schema)
        pace_request(provider, kind="chat")
        converted = convert_to_messages(messages)
        record_transport(provider, "chat", "attempt")
        value = model.invoke(converted)
        record_transport(provider, "chat", "success")
        return normalize_reply(value)
    except (APIConnectionError, APIStatusError, ResponseError, httpx.HTTPError, ConnectionError) as error:
        record_transport(provider, "chat", "failure", http_status=getattr(error, "status_code", None),
                         error_type=type(error).__name__)
        if getattr(error, "status_code", None) == 429:
            note_rate_limit(provider, getattr(getattr(error, "response", None), "headers", None), kind="chat")
        raise transport_error(error, provider) from error


async def acompletion(provider, messages, tools, *, response_schema=None, num_predict=None):
    try:
        model = (bound_model(provider, tools, response_schema) if num_predict is None else
                 bound_model(provider, tools, response_schema, num_predict=num_predict))
        if provider == "nvidia":
            await asyncio.to_thread(pace_request, provider, kind="chat")
        converted = convert_to_messages(messages)
        record_transport(provider, "chat", "attempt")
        value = await model.ainvoke(converted)
        record_transport(provider, "chat", "success")
        return normalize_reply(value)
    except (APIConnectionError, APIStatusError, ResponseError, httpx.HTTPError, ConnectionError) as error:
        record_transport(provider, "chat", "failure", http_status=getattr(error, "status_code", None),
                         error_type=type(error).__name__)
        if getattr(error, "status_code", None) == 429:
            note_rate_limit(provider, getattr(getattr(error, "response", None), "headers", None), kind="chat")
        raise transport_error(error, provider) from error


def ignore_event(title, data):
    pass


def request_metrics(provider, messages, tools, response_schema, num_predict):
    # 字元數不是 Token 數；num_ctx 是應用設定，不代表伺服器實際配置。
    context = os.getenv("EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX", "").strip() if provider == "ollama" else ""
    return {"服務": provider, "配置模型": configured_model(provider),
            "配置Context上限": int(context) if context.isdecimal() and int(context) > 0 else None,
            "輸出上限": num_predict, "訊息數": len(messages), "工具數": len(tools),
            "工具結果字元數": sum(len(m["content"]) for m in messages
                                  if m.get("role") == "tool" and isinstance(m.get("content"), str)),
            "工具定義字元數": len(json.dumps(tools, ensure_ascii=False)),
            "回答Schema字元數": len(json.dumps(response_schema, ensure_ascii=False)) if response_schema else 0}


def response_metrics(reply):
    meta = reply.get("_provider_meta") or {}
    usage = meta.get("usage") or {}
    reason = meta.get("finish_reason") or meta.get("done_reason")
    return {"供應商模型": meta.get("model"),
            "輸入Token": usage.get("prompt_tokens") if usage.get("prompt_tokens") is not None else meta.get("prompt_eval_count"),
            "輸出Token": usage.get("completion_tokens") if usage.get("completion_tokens") is not None else meta.get("eval_count"),
            "停止原因": reason,
            "輸出達長度上限": reason in ("length", "max_tokens", "max_output_tokens") if reason else None}


async def model_step(provider, messages, tools, complete, observe, stage, *, response_schema=None,
                     num_predict=None):
    request = {"服務": provider, "messages": deepcopy(messages), "tools": deepcopy(tools)}
    if response_schema is not None:
        request["預期回答格式"] = response_schema
    if num_predict is not None:
        request["num_predict"] = num_predict
    observe(f"送出模型請求 · {stage}", request)
    metrics = request_metrics(provider, messages, tools, response_schema, num_predict)
    started = perf_counter()
    try:
        if complete is completion:
            options = {"response_schema": response_schema}
            if num_predict is not None:
                options["num_predict"] = num_predict
            reply = await acompletion(provider, messages, tools, **options)
        else:
            def invoke():
                try:
                    if response_schema is None:
                        return complete(provider, deepcopy(messages), tools)
                    return complete(provider, deepcopy(messages), tools, response_schema=response_schema)
                except StopIteration as error:
                    raise RuntimeError("模型回傳序列意外結束。") from error
            reply = await asyncio.to_thread(invoke)
        if not isinstance(reply, dict):
            raise ValueError("模型回傳不是有效訊息。")
        metrics.update(response_metrics(reply))
    except Exception as error:
        metrics["錯誤類型"] = type(error).__name__
        raise
    finally:
        metrics["耗時毫秒"] = round((perf_counter() - started) * 1000, 2)
        observe(f"Python 模型請求指標 · {stage}", metrics)
    output = {key: reply[key] for key in ("role", "content", "tool_calls", "_provider_meta") if key in reply}
    if response_schema is not None:
        try:
            output["回答 JSON"] = json.loads(reply["content"])
        except (KeyError, TypeError, ValueError):
            pass
    observe(f"收到模型回傳 · {stage}", deepcopy(output))
    return reply
