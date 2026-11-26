"""Single-agent terminal output contract; the host still owns evidence validation."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from answering import AnswerPart, contract_error, decode_response, validate_part

FINAL_TOOL = "submit_answer"


class FinalPart(AnswerPart):
    # 舊 unavailable 與 no_evidence 合併；沒有證據不代表查無。
    status: Literal["supported", "clarify", "no_evidence", "account_limit",
                    "unsupported", "service_help"] = Field(
        description="supported：本輪證據支持肯定或否定結論（含明確查無）；clarify：缺玩家資訊；"
                    "no_evidence：資料不足或工具未能確認；account_limit：其他帳號；"
                    "unsupported：不支援的操作；service_help：非活動客服。")


class LegacyFinalAnswer(BaseModel):
    """Strict compatibility decoder for previously recorded terminal envelopes."""
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[FinalPart]


class SupportedClaim(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    question: str = Field(description="原問的一個獨立需求；同需求的各主張使用相同標題")
    statement: str = Field(description="一項可獨立核對的回答；繁體中文 Markdown，不在正文寫來源 ID")
    condition: str = Field(description="結論成立的必要條件或假設，須有證據；直接事實填空字串，不保證未核對的全部資格")
    source_ids: list[str] = Field(description="同時支持這項主張與條件的本輪短來源 ID")


class UnresolvedNeed(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    question: str = Field(description="保留原問中未能解決的獨立需求，不補造事實")
    status: Literal["clarify", "no_evidence", "account_limit", "unsupported", "service_help"] = Field(
        description="缺玩家資訊、缺證據、其他帳號、不支援操作、非活動客服；依實際原因選擇")


class FinalAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    supported: list[SupportedClaim] = Field(description="有本輪證據的主張與必要條件；沒有則空陣列")
    unresolved: list[UnresolvedNeed] = Field(description="未能解決的需求；沒有則空陣列，不含正文或來源")


# 兩路徑共用 Pydantic 契約；以兩個型別明確的陣列取代 oneOf/anyOf、
# discriminator、條件 Schema。避免假設所有託管端點的聯合型別支援相同。
FINAL_SCHEMA = FinalAnswer.model_json_schema()
_definitions = FINAL_SCHEMA.pop("$defs")
for _key, _class in (("supported", "SupportedClaim"), ("unresolved", "UnresolvedNeed")):
    FINAL_SCHEMA["properties"][_key]["items"] = _definitions[_class]
FINAL_DEFINITION = {"type": "function", "function": {
    "name": FINAL_TOOL,
    "description": ("足夠證據或無可取得的必要新證據後單獨提交一次。"
                    "supported 每項為一個有來源的主張及條件；未知需求用 no_evidence 放入 unresolved。"
                    "兩陣列必填，沒有則空陣列；不與資料工具同批。"),
    "parameters": FINAL_SCHEMA}}


def decode_final(raw, *, evidence, original_question, observe):
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict) and set(payload) == {"results"}:
            # 舊封套仍走原嚴格驗證（含查無恢復），不採信非 supported 的正文。
            # 新模型只收到新 Schema；保留此路徑供舊回傳相容及回歸，明確記錄使用。
            parts = LegacyFinalAnswer.model_validate(payload).model_dump()["results"]
            if observe is not None:
                observe("Python 終止契約版本", {"version": 1, "claims": None})
        else:
            data = FinalAnswer.model_validate(payload)
            parts = []
            for claim in data.supported:
                statement, condition = claim.statement.strip(), claim.condition.strip()
                answer = ("**成立條件：** " + condition + "\n\n" + statement
                          if statement and condition else statement)
                parts.append({"question": claim.question, "status": "supported",
                              "answer": answer, "source_ids": claim.source_ids})
            for need in data.unresolved:
                parts.append({"question": need.question, "status": need.status,
                              "answer": "", "source_ids": []})
            if observe is not None:
                observe("Python 終止契約版本", {"version": 2, "claims": len(data.supported),
                        "conditional_claims": sum(bool(c.condition.strip()) for c in data.supported),
                        "unresolved": len(data.unresolved)})
    except ValidationError as error:
        raise ValueError("最終回答契約不符：" + contract_error(error)) from error
    except (TypeError, ValueError) as error:
        raise ValueError("最終回答不是合法 JSON。") from error
    # 主機轉換至同一驗證及 renderer；每個主張仍獨立驗證所有本輪來源與依賴。
    return decode_response({"action": "answer", "missing": "", "results": parts},
                           evidence=evidence, original_question=original_question, observe=observe,
                           expand_dependencies=True, strict_terminal=True)


def final_issues(data, evidence):
    """終止前使用與呈現相同的主機驗證；只檢查契約與可信來源，不聲稱語意正確。"""
    issues = []
    for index, part in enumerate(data["results"]):
        try:
            validate_part(part, evidence)
        except ValueError as error:
            reason = str(error)
            if reason == "未確認的品項與其他規則須分開回答。":
                reason += " 查無只適用於其查詢對象及檔期；無關檔期勿引用。若原問確需不同結果，分成獨立需求各自引用。"
            issues.append(f"results[{index}]：{reason}")
    return issues
