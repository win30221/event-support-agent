"""Provider-neutral tool selection; registry schemas remain authoritative."""
import json
import re


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
