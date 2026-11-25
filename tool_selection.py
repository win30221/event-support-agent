"""Provider-neutral tool selection; registry schemas remain authoritative."""
import json
import re

from backend import compact_json
from model_client import model_step


# 限制單輪查詢成本及循環深度；批次、輪數與累計工具嘗試分別設上限。
MAX_TOOL_ROUNDS = 3
MAX_TOOL_CALLS = 12
MAX_BATCH = 6

AGENT_PROMPT = """你是遊戲活動客服。根據玩家最新問題及近期對話，選擇最小必要的唯讀工具集合；需要多項獨立資料可一次呼叫多個工具。
保留明示品名、條件、日期、實際狀態及代碼，不能用猜測補造。使用可信上下文解析指涉；有多個可能對象而無法判斷時不要任選。
公開資料與本人紀錄用途不同，只在玩家需求確實依賴本人資料時才查，目前登入者以外的帳號不可查。活動事實必須來自工具，舊回答只供指涉。
依工具實際回傳判斷前提是否成立；對指定實體的操作或失敗診斷，先核對該實體是否存在，再查依賴該實體的條件。查無不代表改問其他實體。獨立需求可同批查詢，不因個別需求不足而阻擋。
本階段負責取得證據，不負責產生最終回答。若無需查資料，或沒有有意義的新查詢，停止呼叫工具。"""


# 正規化供應商回傳格式與呼叫識別碼；真正的參數檢查由 ToolRegistry 執行。
def normalized_calls(reply, round_number):
    """Normalize provider envelopes, never execute raw model content."""
    calls = reply.get("tool_calls") or []
    if not isinstance(calls, list):
        return []
    normalized = []
    for index, call in enumerate(calls[:MAX_BATCH]):
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            continue
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        normalized.append({"id": f"call_{round_number}_{index}", "type": "function",
                           "function": {"name": name, "arguments": args}})
    return normalized


# 以同一份工具宣告生成結構化選擇 Schema，避免維護兩套工具契約。
def tool_request_schema(tools):
    """One source of truth: derive fallback requests from the actual registry."""
    variants = []
    for tool in tools:
        function = tool["function"]
        variants.append({"type": "object", "properties": {
            "name": {"type": "string", "enum": [function["name"]], "description": function["description"]},
            "arguments": function["parameters"],
        }, "required": ["name", "arguments"], "additionalProperties": False})
    return {"type": "object", "properties": {"calls": {"type": "array",
            "items": {"anyOf": variants}}}, "required": ["calls"], "additionalProperties": False}


async def structured_tool_selection(provider, conversation, context, tools, sources, errors,
                                    reason, complete, observe, round_number, *, recovery=False):
    schema = tool_request_schema(tools)
    observe("Python 工具選擇備援" if recovery else "Python 結構化工具選擇",
            {"原因": reason, "輪次": round_number})
    messages = [{"role": "system", "content": AGENT_PROMPT
                 + "\n此次使用 JSON 表達工具請求，不使用原生 tool_calls。"
                 "依最新問題及已取得資料選擇必要工具；無需查詢時 calls 為空陣列。"
                 "不得猜測參數。格式：" + compact_json(schema)},
                *conversation, {"role": "user", "content": compact_json({
                    "服務上下文": context.public_summary(), "工具結果": sources,
                    "工具錯誤": errors, "重新選擇原因": reason})}]
    try:
        reply = await model_step(provider, messages, [], complete, observe, "結構化工具選擇",
                                 response_schema=schema)
        data = json.loads(reply.get("content") or "")
        if (not isinstance(data, dict) or data.keys() != {"calls"}
                or not isinstance(data["calls"], list) or len(data["calls"]) > MAX_BATCH):
            raise ValueError("結構化工具請求格式不符。")
        for request in data["calls"]:
            if (not isinstance(request, dict) or request.keys() != {"name", "arguments"}
                    or not isinstance(request["name"], str) or not isinstance(request["arguments"], dict)):
                raise ValueError("結構化工具請求參數格式不符。")
        calls = normalized_calls({"tool_calls": [{"function": request} for request in data["calls"]]}, round_number)
        if len(calls) != len(data["calls"]):
            raise ValueError("結構化工具名稱格式不符。")
        return calls
    except (TypeError, ValueError) as error:
        observe("Python 結構化工具選擇失敗", {"原因": str(error)})
        errors.append({"狀態": "selection_error", "原因": "無法取得合法工具選擇。"})
        return []
