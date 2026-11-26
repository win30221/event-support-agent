"""Single-agent terminal output contract; the host still owns evidence validation."""
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


class FinalAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    results: list[FinalPart] = Field(description="各獨立需求的最後回答；不可補造需求或替換指定對象。")


# 兩條輸出路徑共用一個 Pydantic 契約；展開非遞迴子物件供工具介接。
FINAL_SCHEMA = FinalAnswer.model_json_schema()
FINAL_SCHEMA["properties"]["results"]["items"] = FINAL_SCHEMA.pop("$defs")["FinalPart"]
FINAL_DEFINITION = {"type": "function", "function": {
    "name": FINAL_TOOL,
    "description": "取得必要證據、或已知無法合法查詢後，提交最後回答並結束本輪。只單獨呼叫一次；不執行資料查詢，不與資料工具同批。",
    "parameters": FINAL_SCHEMA}}


def decode_final(raw, *, evidence, original_question, observe):
    try:
        data = FinalAnswer.model_validate_json(raw) if isinstance(raw, str) else FinalAnswer.model_validate(raw)
    except ValidationError as error:
        raise ValueError("最終回答契約不符：" + contract_error(error)) from error
    # action/missing 是歷史呈現介面的主機欄位，不再讓模型決定。
    return decode_response({"action": "answer", "missing": "", **data.model_dump()},
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
