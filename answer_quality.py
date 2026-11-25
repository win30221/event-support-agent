"""Bounded, evidence-only response review; never a new retrieval or authorization step.

A coverage candidate is a signal for review, not a requirement to cite all
retrieved text. The model judges relevance; the host enforces its explicit
coverage commitments and the existing citation contract.
"""
import json
import os
from collections import Counter
from copy import deepcopy
from urllib.error import URLError

from answering import PART, decode_response, question_key, validate_part
from model_client import model_step


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["keep", "revise"]},
        "reason": {"type": "string", "description": "具體說明是否有重要資訊遺漏、無關細節或重複分段。"},
        "missing_ids": {"type": "array", "items": {"type": "string"},
                        "description": "本輪已有、原回答未引用、且回答確實需要的段落 ID；沒有則空陣列。"},
        "results": {"type": "array", "items": PART,
                    "description": "keep 時為空陣列；revise 時提供完整修訂回答，不只是補丁。"},
    },
    "required": ["decision", "reason", "missing_ids", "results"],
    "additionalProperties": False,
}

REVIEW_PROMPT = """你是客服回答的品質審查員。只根據玩家最新需求、可信對話指涉、本輪有效證據和已驗證的回答草稿審查，不執行工具。
先理解玩家要完成的事，再核對草稿是否涵蓋所有必要步驟、條件、結果及所問的獨立需求。文件章節可能互相補充；未引用的同文件章節只是候選，只有真正回答問題所需的才算遺漏。
核心需求優先，直接相關的細節才補充。不要以不相干的例外取代主流程，不必覆述全部檢索結果，不把每個來源當成一個子問題。若要修訂，合併重複問題成連貫的回答。
若已有足夠、相關的回答，選 keep，missing_ids 及 results 為空陣列。若存在實質遺漏、無關內容或重複拆段，選 revise，列出真正缺漏的已有證據 ID，再生成完整的修訂 results。沒有遺漏證據時 missing_ids 可空。
修訂須保留原本各獨立問題及有效回答；不可省略原有帳號限制或服務範圍拒答。可將同一問題的多筆結果整合。每一事實只能依據本輪證據，引用 ID 及逐字摘錄；非 supported 的 answer、citations 留空。原問題欄位保留既有各獨立問題標籤，避免修訂遺失需求。不得將未確認事項當成已確認，也不得要求補查或猜測。輸出繁體中文 JSON。"""


def validated_parts(data, evidence):
    """Return citation-validated parts or raise, never trust status alone."""
    for part in data["results"]:
        validate_part(part, evidence)
    return data["results"]


def cited_ids(parts):
    return {cite["id"] for part in parts if part["status"] == "supported"
            for cite in part["citations"]}


# 同文件未採用、重複問題、未採用的工具組是審查候選，不是強制引用清單。
def review_candidates(data, evidence, sources):
    """Find structural risk signals, without classifying any natural language."""
    if data["action"] != "answer":
        return None
    try:
        parts = validated_parts(data, evidence)
    except ValueError:
        # Do not let a quality rewrite launder an invalid citation.
        return None
    used = cited_ids(parts)
    if not used:
        return None
    counts = Counter(question_key(part["question"]) for part in parts)
    duplicates = any(count > 1 for count in counts.values())

    def document_key(row):
        if row.get("文件ID"):
            return (row.get("檔期"), row["文件ID"])
        return None

    documents = {document_key(evidence[key]) for key in used}
    documents.discard(None)
    siblings = {key for key, row in evidence.items()
                if key not in used and document_key(row) in documents}
    # A retrieved but entirely unrepresented data source may be important for
    # a separate request or a calculation. Group by tool + edition so that
    # multiple searches of the same document do not create artificial gaps.
    groups = {}
    for source in sources:
        key = (source.get("工具"), source.get("檔期"))
        groups.setdefault(key, set()).update(row["段落ID"] for row in source["段落"])
    unrepresented = set().union(*(ids for ids in groups.values() if ids and ids.isdisjoint(used)))
    candidates = siblings | unrepresented
    if not duplicates and not candidates:
        return None
    return {"重複問題": duplicates, "已採用": sorted(used),
            "待檢查段落": sorted(candidates),
            "同文件未採用": sorted(siblings), "未採用工具資料": sorted(unrepresented)}


# 修訂不得遺失已支持的獨立需求或原有權限拒答；失敗交由呼叫端保留原答。
def validate_review(raw, original, evidence, observe, *, original_question=""):
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict) or data.keys() != REVIEW_SCHEMA["properties"].keys():
        raise ValueError("審查格式不符。")
    decision, reason, missing, results = (data[k] for k in ("decision", "reason", "missing_ids", "results"))
    if (decision not in ("keep", "revise") or not isinstance(reason, str) or not reason.strip()
            or not isinstance(missing, list) or any(not isinstance(key, str) for key in missing)
            or not isinstance(results, list)):
        raise ValueError("審查欄位型別不符。")
    original_parts = validated_parts(original, evidence)
    original_ids = cited_ids(original_parts)
    if len(missing) != len(set(missing)) or any(key not in evidence or key in original_ids for key in missing):
        raise ValueError("遺漏來源必須是本輪已取得且尚未採用的段落。")
    if decision == "keep":
        if missing:
            raise ValueError("keep 不可包含遺漏來源。")
        if results:
            # A keep decision cannot consume replacement content. Discard it
            # instead of retrying or treating the original as a revision.
            observe("Python 捨棄審查非作答欄位", {"判定": "keep"})
        return original, reason, []
    replacement = decode_response({"action": "answer", "missing": "", "results": results}, observe=observe,
                                  evidence=evidence, original_question=original_question)
    parts = validated_parts(replacement, evidence)
    replacement_ids = cited_ids(parts)
    if not set(missing) <= replacement_ids:
        raise ValueError("修訂未採用自己判定必要的遺漏來源。")

    def statuses_by_question(rows):
        grouped = {}
        for part in rows:
            grouped.setdefault(question_key(part["question"]), set()).add(part["status"])
        return grouped

    before, after = statuses_by_question(original_parts), statuses_by_question(parts)
    for key, statuses in before.items():
        if key not in after:
            raise ValueError("修訂遺失原本的獨立需求。")
        if "supported" in statuses and "supported" not in after[key]:
            raise ValueError("修訂丟棄原本有依據的需求。")
        if not (statuses & {"account_limit", "service_help"}) <= after[key]:
            raise ValueError("修訂不可移除帳號或服務範圍限制。")
        if "account_limit" in statuses and "supported" not in statuses and "supported" in after[key]:
            raise ValueError("修訂不可將他人資料拒答改成有資料的回答。")
    return replacement, reason, missing


# 審查最多一次且不開放工具；模型審查仍不等於完整語意驗證。
async def review_answer(provider, data, evidence, packet, conversation, complete, observe):
    """At most one request, and only after tool use is finished."""
    mode = os.getenv("EVENT_SUPPORT_AGENT_ANSWER_REVIEW", "auto").strip().lower()
    if mode not in ("auto", "off"):
        raise ValueError("EVENT_SUPPORT_AGENT_ANSWER_REVIEW 請填 auto 或 off。")
    if mode == "off":
        return data
    risk = review_candidates(data, evidence, packet["工具結果"])
    if not risk:
        return data
    observe("Python 啟動回答品質審查", risk)
    # Only filtered current-turn tool results are included. Earlier prose is
    # available for reference resolution, not as evidence.
    review_packet = {"玩家原問": packet["玩家原問"], "服務上下文": packet["服務上下文"],
                     "工具結果": packet["工具結果"], "回答草稿": deepcopy(data),
                     "審查線索": risk}
    messages = [{"role": "system", "content": REVIEW_PROMPT + "\n格式：" +
                 json.dumps(REVIEW_SCHEMA, ensure_ascii=False, separators=(",", ":"))},
                *conversation, {"role": "user", "content": json.dumps(review_packet, ensure_ascii=False)}]
    try:
        reply = await model_step(provider, messages, [], complete, observe, "回答品質審查",
                                 response_schema=REVIEW_SCHEMA)
        revised, reason, missing = validate_review(reply.get("content"), data, evidence, observe,
                                                   original_question=packet["玩家原問"])
    except (ValueError, TypeError, OSError, URLError, RuntimeError) as error:
        # Keep the existing independently validated answer on an invalid or
        # unavailable review. Do not leak transport error details to players.
        observe("Python 回答品質審查結果", {"判定": "review_failed", "錯誤類型": type(error).__name__})
        return data
    observe("Python 回答品質審查結果", {
        "判定": "kept" if revised is data else "revised", "原因": reason,
        "補足段落": missing, "採用段落": sorted(cited_ids(revised["results"])),
    })
    return revised
