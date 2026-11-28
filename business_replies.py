"""Domain answers backed by the current turn's approved, read-only tool results."""
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from answering import contract_error
from task_business import inspect_slots


FACT_TOOL = "answer_event_fact"


class FactRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    question: str = Field(min_length=1, max_length=2000, description="逐字複製最新原問中這一個獨立需求，保留連接詞。")
    kind: Literal["balance", "item_price", "catalog", "redemption_gap", "unsupported"] = Field(
        description="本人餘額、指定商品價格、完整商城清單、本人與指定商品的價格差額、或要求代辦寫入。")
    edition: str = Field(description="current、previous、context 或明確檔期 ID。")
    product: str = Field(max_length=2000, description="指定品名照原句。沿用上輪唯一商品時填空並用 context；無商品也填空。")
    account: Literal["self", "public", "other", "unclear"] = Field(description="本人、公開資訊、其他帳號或指涉不明。")


FACT_DEFINITION = {"type": "function", "function": {
    "name": FACT_TOOL, "description": "直接回答一個固定業務需求；多個需求可同批各呼叫一次。"
    "主機會查本輪資料、計算價格差與產生答案。資格、失敗原因、假設或公告問題請用資料工具。",
    "parameters": FactRequest.model_json_schema()}}

# 只核對逐字涵蓋範圍。語意及同一句裡有幾個需求仍由模型判斷。
SENTENCE_END = re.compile(r"[？?！!。；;\n]+")
SEPARATORS = " \t\n\r、，,。！？!?；;：:"


def remaining_questions(question, labels):
    masked = list(question)
    for label in labels:
        index = question.find(label) if label else -1
        if index >= 0:
            masked[index:index + len(label)] = [" "] * len(label)
    return [s.strip(SEPARATORS) for s in SENTENCE_END.split("".join(masked)) if s.strip(SEPARATORS)]


def prepare_fact(raw, context):
    req = FactRequest.model_validate(raw)
    label = req.question.strip()
    if label not in context.question or len(remaining_questions(label, [])) != 1:
        raise ValueError("question 請逐字複製本輪的一個獨立需求，多個問句請分開。")
    task = {"goal": label, "kind": req.kind, "product": req.product.strip(), "account": req.account,
            "editions": [], "status": "", "missing_slot": ""}
    if req.kind == "unsupported":
        task["status"] = "unsupported"
        return task, []
    private = req.kind in {"balance", "redemption_gap"}
    if private and req.account != "self":
        task["status"] = "account_limit" if req.account == "other" else "clarify"
        task["missing_slot"] = "account"
        return task, []
    try:
        edition = context.resolve_edition(req.edition)
    except ValueError:
        task.update(status="clarify", missing_slot="edition")
        return task, []
    task["editions"] = [edition]
    queries = []
    if req.kind in {"item_price", "redemption_gap"}:
        name = task["product"]
        if not name and req.edition == "context":
            name = context.previous_product(edition)
        if not name or not context.product_for_request(name, edition, label, follow_up=req.edition == "context"):
            task.update(status="clarify", missing_slot="product")
            return task, []
        task["product"] = name
        queries.append(("find_shop_item", {"edition": edition, "name": name}))
    elif req.product.strip():
        raise ValueError("此類需求不接受商品參數。商品差額請選 redemption_gap。")
    if private:
        queries.append(("get_my_event_progress", {"edition": edition, "account_scope": "self",
                        "required_item": task["product"] if req.kind == "redemption_gap" else ""}))
    if req.kind == "catalog":
        queries.append(("list_shop_items", {"edition": edition}))
    return task, queries


def fact_part(task, sources, evidence):
    status, missing = task["status"], task["missing_slot"]
    text, ids = "", []
    if not status:
        result = inspect_slots(task, sources, evidence)
        if result["kind"] == "direct":
            status, text, ids = "supported", result["answer"], result["ids"]
        elif result["status"] == "clarify":
            status, missing = "clarify", "product"
        else:
            status = "no_evidence"
    return {"question": task["goal"], "status": status, "answer": text, "missing_slot": missing,
            "citations": [{"id": key, "quote": evidence[key]["原文"]} for key in ids]}


def expand_fact_calls(calls, context):
    from agent_runtime import raw_arguments
    reads, facts = [], {}
    for call in calls:
        if call["function"]["name"] != FACT_TOOL:
            reads.append(call)
            continue
        cid = call["id"]
        try:
            task, queries = prepare_fact(raw_arguments(call), context)
        except ValidationError as error:
            facts[cid] = {"error": contract_error(error)}
            continue
        except (ValueError, TypeError, KeyError) as error:
            facts[cid] = {"error": str(error)}
            continue
        facts[cid] = {"task": task}
        for i, (name, args) in enumerate(queries):
            reads.append({"id": f"{cid}/read{i}", "type": "function",
                          "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}})
    return reads, facts
