"""Independent structured response validation. A valid citation is not a semantic proof."""
import json
import unicodedata

from guide import mentions_identifier


DIRECT_REPLIES = {
    "service_help": "我可以協助查詢遊戲活動玩法、商城獎勵和目前帳號的活動進度。",
    "account_limit": "我只能查詢目前登入帳號的活動紀錄。",
    "clarify": "還需要更多資訊才能確認，請補充要查的活動、品項或畫面提示。",
    "no_evidence": "目前資料不足以確認這個問題，無法據此推論。",
    "unavailable": "目前無法取得或確認所需的資料。",
}
REDIRECT = DIRECT_REPLIES["service_help"]
ANSWER_VERIFICATION_FAILED = "目前無法驗證這部分回答與資料的一致性，請重新查詢。"
UNRESOLVED = "其餘需求目前未能取得足夠證據，無法可靠確認。"
STATUSES = ["supported", *DIRECT_REPLIES]
CITATION = {"type": "object", "properties": {"id": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["id", "quote"], "additionalProperties": False}
# One presentation contract for both the answer and review schemas. These are
# generation instructions, not a new semantic validator or retry condition.
ANSWER_MARKDOWN_GUIDANCE = (
    "回答正文使用可讀的 Markdown：簡短問答直接以自然文字回答；有順序的步驟使用有序清單，"
    "並列項目使用項目清單。每個清單項目各自另起一行，清單前及段落之間留空行；"
    "較長說明可用簡短小標題分組，但不要為排版拆分獨立需求。"
    "JSON 解碼後必須是真正的換行，不要把所有編號擠在同一行或顯示換行跳脫字面值。"
    "僅整理版面，不增刪或改寫必要事實、適用條件、日期、數值及代碼。"
    "引用留在 citations；正文不重複列來源，也不要用程式碼區塊包住整個回答。"
)

PART = {"type": "object", "properties": {
    "question": {"type": "string", "description": "一個獨立玩家需求，不是公告段落；同一需求只建立一筆，保留原意。"},
    "status": {"type": "string", "enum": STATUSES,
               "description": "supported 表示有可驗證事實，包含成功查詢但指定資料不存在；no_evidence 表示證據不足。"},
    "answer": {"type": "string", "description": "supported 時整合必要證據，先回答核心需求，再提供直接相關細節；其他狀態填空字串。"
               + ANSWER_MARKDOWN_GUIDANCE},
    "citations": {"type": "array", "items": CITATION},
}, "required": ["question", "status", "answer", "citations"], "additionalProperties": False}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["answer", "retrieve"],
                   "description": "只有確實還有必要且可查的新證據才 retrieve；否則 answer。"},
        "missing": {"type": "string", "description": "retrieve 時說明需要的證據及原因；answer 時留空。"},
        "results": {"type": "array", "items": PART},
    },
    "required": ["action", "missing", "results"], "additionalProperties": False,
}

ANSWER_PROMPT = """你是遊戲活動客服。針對玩家最新問題及對話指涉，以本輪工具資料為唯一活動事實依據。舊回答僅供指涉，不是新證據。
按玩家的獨立需求（不是來源段落）建立結果，同一需求整合成一筆連貫回答。先理解玩家要完成的事，核對相關文件各章節能否完整涵蓋所需步驟、條件和結果；先說核心答案，再補直接相關細節。不要把不相干的例外當主流程，也不必逐段覆述全部資料。完整回答有證據的部分；其他部分獨立標示 no_evidence、clarify、unavailable、account_limit 或 service_help，不因其中一項失敗而捨棄其餘答案。
資料存在不等於能回答問題。基礎公告與檢索命中的細節都只能支持其實際涵蓋的內容。確認對象、檔期、時間、前提及所需欄位；未知品項不得當成存在，未確認品項不得套用商品專屬處理建議。一般規則與個人實際紀錄不可互相替代。本人紀錄只能用於目前登入者。
需要數值計算時以工具的運算結果及原始資料為準；只達價格門檻不代表所有資格都符合。不可自行推測不存在的資料或未提供的實際狀態。
supported 的每個子問題均引用本輪來源 ID 與逐字摘錄，摘錄必須是原文連續文字；所有說法均須有依據。成功查詢且有明確 not_found 結果，屬於可驗證的否定事實，應以 supported 回覆並引用完整查詢狀態原文；不得推論實際交易或其他檔期的狀態。查詢失敗或沒有足夠證據則不是確認不存在。其他狀態的 answer 與 citations 留空。
若仍有必要且可取得的新證據，且允許補查，選 action=retrieve，missing 說明欠缺的資料；已能回答的部分仍放入 results。已查過且沒有資料、真正需要玩家補充、或超出服務範圍時，不要重複查詢。
不允許補查時必須以現有資料作部分回答或拒答。使用繁體中文，輸出指定 JSON 格式。"""


# 引用資料形狀驗證與語意正確性不同，後續仍須核對本輪原文。
def valid_citation(cite):
    return (isinstance(cite, dict) and cite.keys() == {"id", "quote"}
            and all(isinstance(cite[key], str) and cite[key].strip() for key in ("id", "quote")))


# 只恢復可信查無事實，不採用模型拒答中的任意文字或推測。
def recover_verified_absence(part, evidence, original_question):
    """Normalize a mislabeled, explicit lookup outcome without trusting model prose.

    Only current-turn, host-produced not_found rows can be recovered. Require
    complete exact quotes and the actual lookup identifier in both the latest
    user message and this subquestion. This conservative recovery is not a
    semantic entailment check; implicit references and all other abstentions
    retain their existing behavior. Never infer a transaction's actual cause.
    """
    if part["status"] != "no_evidence" or not part["citations"] or not original_question:
        return False
    rows = []
    for cite in part["citations"]:
        if not valid_citation(cite):
            return False
        row = evidence.get(cite["id"])
        if not row or row.get("資料狀態") != "not_found":
            return False
        identifier = row.get("查詢對象")
        if (not isinstance(identifier, str) or not identifier.strip()
                or not mentions_identifier(original_question, identifier)
                or not mentions_identifier(part["question"], identifier)
                or cite["quote"].strip() != row["原文"].strip()):
            return False
        rows.append(row)
    # The validated lookup result is the entire assertion. Discard the
    # generated answer even when it happens to be correct.
    part["status"] = "supported"
    part["answer"] = "\n".join(dict.fromkeys(row["原文"] for row in rows))
    return True


# 解析輸出契約；非作答狀態丟棄正文，僅留下必要的停止檢查線索。
def decode_response(raw, *, observe=None, evidence=None, original_question="", stop_candidates=None):
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as error:
        raise ValueError("最終回答不是合法 JSON。") from error
    if not isinstance(data, dict) or data.keys() != ANSWER_SCHEMA["properties"].keys():
        raise ValueError("最終回答缺少必要欄位或欄位不符。")
    action, missing, parts = data["action"], data["missing"], data["results"]
    if action not in ("answer", "retrieve") or not isinstance(missing, str) or not isinstance(parts, list):
        raise ValueError("回答動作或結果型別不符。")
    if (action == "retrieve") != bool(missing.strip()) or (action == "answer" and not parts):
        raise ValueError("補查原因或回答結果不完整。")
    if len(parts) > 16:
        raise ValueError("子問題數量超出上限。")
    for part in parts:
        if not isinstance(part, dict) or part.keys() != PART["properties"].keys():
            raise ValueError("子問題結構不符。")
        if (not isinstance(part["question"], str) or not part["question"].strip()
                or part["status"] not in STATUSES or not isinstance(part["answer"], str)
                or not isinstance(part["citations"], list)):
            raise ValueError("子問題欄位型別不符。")
        if evidence is not None and recover_verified_absence(part, evidence, original_question):
            if observe is not None:
                observe("Python 正規化查詢狀態", {"問題": part["question"],
                        "原狀態": "no_evidence", "新狀態": "supported",
                        "採用段落": [cite["id"] for cite in part["citations"]],
                        "原因": "本輪明確指定對象的查無結果已核對；只採用工具原文。"})
        if part["status"] == "supported":
            if not part["answer"].strip() or not part["citations"]:
                raise ValueError("有依據的回答必須提供正文與引用。")
        elif part["answer"].strip() or part["citations"]:
            # The status already determines the safe host-owned response.
            # Discard fields that are never consumed, rather than retrying an
            # otherwise valid abstention and repeating the same decision.
            # A contradictory abstention with real current evidence may signal
            # a missing prerequisite. Keep only a diagnostic, never the prose.
            if (stop_candidates is not None and evidence is not None
                    and part["status"] == "no_evidence" and part["answer"].strip()
                    and part["citations"] and all(
                        valid_citation(cite) and cite["id"] in evidence
                        and cite["quote"].strip() in evidence[cite["id"]]["原文"]
                        for cite in part["citations"])):
                stop_candidates.append({"問題": part["question"],
                                        "已引用段落": sorted({c["id"] for c in part["citations"]})})
            if observe is not None:
                observe("Python 捨棄非作答欄位", {"狀態": part["status"]})
            part["answer"], part["citations"] = "", []
        for cite in part["citations"]:
            if not valid_citation(cite):
                raise ValueError("引用格式不符。")
    return data


# 逐項確認引用與運算依據；否定結果、適用條件等特殊情況保留工具原文。
def validate_part(part, evidence):
    if part["status"] != "supported":
        return DIRECT_REPLIES[part["status"]], []
    adopted = []
    citations = part["citations"]
    ids = {cite["id"] for cite in citations}
    for cite in citations:
        row = evidence.get(cite["id"])
        if row is None or cite["quote"].strip() not in row["原文"]:
            raise ValueError("引用 ID 或摘錄不屬於本輪有效來源。")
        if not set(row.get("運算依據", ())) <= ids:
            raise ValueError("計算結果必須同時引用其原始依據。")
        adopted.append(row)
    if any(row.get("資料狀態") in ("not_found", "ambiguous", "unavailable", "need_name") for row in adopted):
        # Negative lookup results are facts only about the lookup. Never blend
        # them with a speculative treatment for that unverified entity.
        if any(not row.get("資料狀態") for row in adopted):
            raise ValueError("未確認的品項與其他規則須分開回答。")
        answer = "\n".join(dict.fromkeys(row["原文"] for row in adopted))
        if all(row.get("資料狀態") == "not_found" and row.get("查詢對象") for row in adopted):
            answer += "\n\n請確認商品名稱或要查詢的活動檔期。"
    elif any(row.get("適用條件") for row in adopted):
        # Preserve the complete applicable clause, including its prerequisites.
        answer = "\n\n".join(dict.fromkeys(row["原文"] for row in adopted))
    else:
        answer = part["answer"].strip()
    return answer, citations


def question_key(question):
    return " ".join(unicodedata.normalize("NFKC", question).casefold().split())


def answer_references(decisions, evidence):
    """Build a stable, source-level list from validated final citations only.

    Multiple citations to a single catalogue or announcement section share
    one display entry. Full citation IDs and quotes remain in the decisions.
    """
    references = {}
    for decision in decisions:
        if decision["判定"] != "supported":
            continue
        for cite in decision["採用段落"]:
            row = evidence[cite["id"]]
            key = (row["來源"], row.get("發布日", ""))
            reference = references.setdefault(key, {
                "來源": key[0], "發布日": key[1], "段落ID": [],
            })
            if cite["id"] not in reference["段落ID"]:
                reference["段落ID"].append(cite["id"])
    return list(references.values())


# 先驗證再分組與呈現；參考資料只來自最終採用的有效引用。
def render_response(data, evidence, observe, *, unresolved=False):
    rendered, decisions, groups = [], [], {}
    parts = data["results"]
    for part in parts:
        status = part["status"]
        try:
            text, citations = validate_part(part, evidence)
            detail = {"問題": part["question"], "判定": status, "採用段落": citations}
        except ValueError as error:
            text = ANSWER_VERIFICATION_FAILED
            detail = {"問題": part["question"], "判定": "rejected", "原因": str(error), "採用段落": []}
        decisions.append(detail)
        key = question_key(part["question"])
        group = groups.setdefault(key, {"question": part["question"], "answers": []})
        entry = (detail["判定"], text)
        if entry not in group["answers"]:
            group["answers"].append(entry)
    for group in groups.values():
        entries = group["answers"]
        has_supported = any(status == "supported" for status, _ in entries)
        texts = [text for status, text in entries if status == "supported"]
        for status, text in entries:
            if status == "supported":
                continue
            if has_supported and status == "no_evidence":
                text = "這個問題仍有部分內容缺乏足夠依據，無法完整確認。"
            if text not in texts:
                texts.append(text)
        text = "\n\n".join(texts)
        if len(groups) > 1:
            text = f"**{group['question']}**\n\n{text}"
        rendered.append(text)
    if unresolved or data["action"] == "retrieve":
        rendered.append(UNRESOLVED)
    observe("Python 檢查回答依據", {"子問題": decisions,
            "參考資料": answer_references(decisions, evidence), "驗證範圍": "來源 ID、逐字摘錄與資料狀態，不代表語意完整性",
            "判定": "supported" if decisions and all(d["判定"] == "supported" for d in decisions) else "partial"})
    return "\n\n".join(rendered) or UNRESOLVED
