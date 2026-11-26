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


def expand_evidence_dependencies(identifiers, evidence):
    """Return the current-turn dependency closure in stable first-visit order.

    Only host evidence supplies edges. Reject invalid references or cycles
    instead of treating an incomplete provenance chain as verified.
    """
    ordered, active, visited = [], set(), set()
    # 以顯式堆疊展開 DAG；不信任模型補寫依賴，也不修改既有證據。
    stack = [(identifier, False) for identifier in reversed(list(identifiers))]
    while stack:
        identifier, leaving = stack.pop()
        if leaving:
            active.remove(identifier)
            visited.add(identifier)
            continue
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("證據依賴 ID 格式不合法。")
        if identifier in active:
            raise ValueError("本輪證據存在循環依賴。")
        if identifier in visited:
            continue
        row = evidence.get(identifier)
        if not isinstance(row, dict):
            raise ValueError("證據依賴不屬於本輪有效來源。")
        original = row.get("原文")
        if not isinstance(original, str) or not original.strip():
            raise ValueError("證據依賴缺少有效原文。")
        dependencies = row.get("運算依據", ())
        if ("運算依據" in row and
                (not isinstance(dependencies, (list, tuple)) or not dependencies
                 or any(not isinstance(i, str) or not i.strip() for i in dependencies))):
            raise ValueError("證據依賴格式不合法。")
        ordered.append(identifier)
        active.add(identifier)
        stack.append((identifier, True))
        stack.extend((dependency, False) for dependency in reversed(dependencies))
    return ordered


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
