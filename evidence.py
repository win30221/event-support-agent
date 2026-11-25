"""Trusted current-turn evidence and a small model-facing projection."""
from hashlib import sha256


def source_alias(identifier: str) -> str:
    # Short deterministic display ID; validity requires membership in this turn.
    return "E" + sha256(identifier.encode("utf-8")).hexdigest()[:12]


def source_map(evidence):
    aliases = {source_alias(key): key for key in evidence}
    if len(aliases) != len(evidence):
        raise ValueError("本輪來源別名衝突。")
    return aliases


def model_sources(sources, *, aliases=False, compact=True, seen=None):
    """Project without mutating full host evidence; preserve scope and conditions.

    Tool selection/calculation uses canonical IDs; final answers use short IDs.
    """
    if seen is None:
        seen = set()

    def key(identifier):
        return source_alias(identifier) if aliases else identifier

    output = []
    for source in sources:
        group = {k: source[k] for k in ("工具", "檔期", "狀態", "原因") if k in source}
        if not compact:
            group = {k: v for k, v in source.items() if k not in ("段落", "已回傳段落ID")}
        elif "商城" in source:
            # Exact SKU/aliases matter; prices and descriptions are already in rows.
            group["商品識別"] = [{k: item[k] for k in ("id", "name", "aliases") if k in item}
                                 for item in source["商城"].get("items", [])]
        elif "商品識別" in source:
            group["商品識別"] = source["商品識別"]
        rows, omitted = [], []
        for row in source.get("段落", []):
            identifier = row["段落ID"]
            if identifier in seen:
                omitted.append(key(identifier))
                continue
            seen.add(identifier)
            fields = ("來源", "檔期", "發布日", "原文", "適用條件",
                      "資料狀態", "查詢對象", "運算依據", "檢索角色")
            projected = {k: row[k] for k in fields if k in row} if compact else dict(row)
            projected["段落ID"] = key(identifier)
            if "運算依據" in projected:
                projected["運算依據"] = [key(i) for i in projected["運算依據"]]
            rows.append(projected)
        if "段落" in source:
            group["段落"] = rows
        if omitted:
            group["已回傳段落ID"] = list(dict.fromkeys(omitted))
        output.append(group)
    return output


def host_draft_for_model(data):
    """Do not send the host-generated full-text quotations back to the model."""
    return {**data, "results": [
        {**{k: p[k] for k in ("question", "status", "answer")},
         "source_ids": [source_alias(c["id"]) for c in p["citations"]]}
        for p in data["results"]]}
