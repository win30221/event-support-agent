"""Send the complete registered tool schema to Ollama's native chat endpoint."""
import json

import httpx
from ollama import ResponseError


def chat_messages(messages):
    # LangChain 使用 OpenAI 訊息封套；Ollama 的呼叫參數是物件，工具結果以名稱配對。
    output, names = [], {}
    for message in messages:
        row = {"role": message["role"], "content": message.get("content") or ""}
        if row["role"] == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                name, args = call["function"]["name"], call["function"]["arguments"]
                args = json.loads(args) if isinstance(args, str) else args
                if not isinstance(args, dict):
                    raise ValueError("工具回放參數必須是物件。")
                names[call["id"]] = name
                calls.append({"id": call["id"], "function": {"name": name, "arguments": args}})
            row["tool_calls"] = calls
        elif row["role"] == "tool":
            row["tool_name"] = names[message["tool_call_id"]]
            row["tool_call_id"] = message["tool_call_id"]
        output.append(row)
    return output


async def native_tool_reply(settings, messages, tools):
    options = {k: settings[k] for k in ("temperature", "num_ctx", "num_predict") if k in settings}
    body = {"model": settings["model"], "messages": chat_messages(messages), "tools": tools,
            "stream": False, "options": options}
    if "reasoning" in settings:
        body["think"] = settings["reasoning"]
    url = settings["base_url"] + "/api/chat"
    async with httpx.AsyncClient(**settings["client_kwargs"]) as client:
        response = await client.post(url, json=body)
    if response.is_error:
        raise ResponseError("Ollama chat request failed", response.status_code)
    data = response.json()
    if data.get("done") is not True or not isinstance(data.get("message"), dict):
        raise ValueError("Ollama 未回傳完整訊息。")
    msg = data["message"]
    prompt, generated = data.get("prompt_eval_count"), data.get("eval_count")
    meta = {k: data[k] for k in ("model", "done_reason", "total_duration", "load_duration",
            "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration") if k in data}
    meta["usage"] = {"prompt_tokens": prompt, "completion_tokens": generated,
                     "total_tokens": prompt + generated if type(prompt) is int and type(generated) is int else None}
    # 只接受原生 tool_calls。工具以外的內容及模型思考不能作為呼叫指令。
    return {"role": "assistant", "content": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls", []), "_provider_meta": meta}
