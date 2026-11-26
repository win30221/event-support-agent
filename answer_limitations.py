"""Turn-scoped, host-owned descriptions of data availability, not semantic proofs."""
from dataclasses import dataclass


# 只使用主機實際記錄的工具名稱及狀態；不呈現參數、原文、私人值、例外或模型解釋。
_NOTES = {
    "rules_found": "本輪已取得活動公告資料，但目前的資料不足以確認尚未解答的需求；不能據此推論規則不存在。",
    "rules_context_only": "本輪有公告查詢只取得活動背景資料，沒有取得更具體的可採用搜尋結果。",
    "rules_no_match": "本輪有公告查詢未取得可採用段落，這不代表相關規則不存在。",
    "catalogue_scope": "商城查詢的資料範圍為品項、價格及用途，不包含個人訂單明細或完整交易政策。",
    "catalogue_not_found": "本輪有指定品項查詢回傳查無；查無只適用於該次查詢的品名及檔期。",
    "catalogue_ambiguous": "本輪有商城查詢尚未確認唯一品項。",
    "catalogue_unavailable": "本輪有商城查詢未能確認清單資料。",
    "catalogue_need_name": "本輪有商城查詢缺少品項名稱。",
    "progress_scope": "活動進度查詢提供紀錄日期、星星幣餘額、紀錄當日場次及參加狀態，不包含累計遊玩天數或個人訂單明細。",
    "progress_unavailable": "本輪有本人活動進度查詢未取得紀錄，不能據此推論餘額或遊玩情形。",
    "tool_error": "本輪有資料工具未能完成查詢；這不代表查無資料。",
}
_RULE_STATES = {"found": "rules_found", "context_only": "rules_context_only", "no_match": "rules_no_match"}
_SHOP_STATES = {"found": "catalogue_scope", "listed": "catalogue_scope",
                "not_found": "catalogue_not_found", "ambiguous": "catalogue_ambiguous",
                "unavailable": "catalogue_unavailable", "need_name": "catalogue_need_name"}
_PROGRESS_STATES = {"found": "progress_scope", "unavailable": "progress_unavailable"}


@dataclass(frozen=True)
class LimitationContext:
    codes: tuple[str, ...]

    def summary(self):
        # 同輪多需求未必對應同一次工具；只陳述全輪狀態，不聲稱是個別拒答的原因。
        notes = [_NOTES[code] for code in self.codes if code in _NOTES]
        return "本輪查詢情況：\n\n" + "\n".join("- " + note for note in notes) if notes else ""


def build_limitations(sources, errors):
    """Use only successful source envelopes and actual execution errors of this turn."""
    codes = set()
    for source in sources:
        name, status = source.get("工具"), source.get("狀態")
        if name == "find_rules":
            code = _RULE_STATES.get(status)
        elif name in ("find_shop_item", "list_shop_items"):
            code = _SHOP_STATES.get(status)
        elif name == "get_my_event_progress":
            code = _PROGRESS_STATES.get(status)
        else:
            code = None
        if code:
            codes.add(code)
    # invalid_arguments 是主機拒絕提案，並非已執行查詢或服務失敗。
    if any(error.get("狀態") == "tool_error" and error.get("工具") in
           ("find_rules", "find_shop_item", "list_shop_items", "get_my_event_progress", "calculate")
           for error in errors):
        codes.add("tool_error")
    if not codes:
        # 無法確定個別原因時，維持原本通用拒答，不聲稱已搜尋或取得資料。
        return LimitationContext(("no_observed_data_context",))
    return LimitationContext(tuple(code for code in _NOTES if code in codes))
