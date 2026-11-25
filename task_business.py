"""Host-owned capability/slot checks and deterministic rendering of trusted data."""
from answering import DIRECT_REPLIES
from guide import CATALOG, normalize_item_name, published_passages
from evidence import source_alias
from task_contracts import (PRIVATE_KINDS, PRODUCT_KINDS, SELF_REQUIRED, SINGLE_EDITION,
                            TERMINAL, record)


def materialize_plan(proposals, context):
    """Resolve scopes and dependencies, never grant privileges from model output."""
    if not 1 <= len(proposals) <= 16:
        raise ValueError("需求數量須為 1 至 16。")
    tasks, states = [], {}
    for index, proposed in enumerate(proposals, 1):
        task = proposed.model_dump()
        goal = task["goal"].strip()
        if not goal or len(goal) > 2000:
            raise ValueError("需求說明不可為空或過長。")
        deps = task["depends_on"]
        if len(deps) != len(set(deps)) or any(i < 1 or i >= index for i in deps):
            raise ValueError("需求依賴必須指向先前需求，不能循環。")
        tid = f"T{index}"
        task.update(id=tid, goal=goal, product=task["product"].strip(),
                    depends_on=[f"T{i}" for i in deps])
        kind, account = task["kind"], task["account"]
        initial = None
        if account == "other":
            initial = record("account_limit")
        elif kind == "operation":
            initial = record("unsupported")
        elif kind == "other":
            initial = record("service_help")
        elif kind == "clarification" or account == "unclear":
            initial = record("clarify")
        elif kind in SELF_REQUIRED and account != "self":
            initial = record("clarify", reason="本人資料指涉不明。")
        editions = []
        if initial is None:
            try:
                editions = list(dict.fromkeys(context.resolve_edition(e) for e in task["editions"]))
            except ValueError:
                initial = record("unavailable" if "unavailable" in task["editions"] else "clarify",
                                 reason="檔期無法唯一確認。")
            if not editions or kind in SINGLE_EDITION and len(editions) != 1:
                initial = initial or record("clarify", reason="需確認單一檔期。")
            if kind in PRODUCT_KINDS and not task["product"]:
                initial = initial or record("clarify", reason="需確認商品。")
            if task["product"] and any(not context.grounded_product(task["product"], e) for e in editions):
                initial = initial or record("clarify", reason="商品指涉無法核對。")
        task["editions"] = editions
        tasks.append(task)
        states[tid] = initial or record("pending")
    return tasks, states


def need(tool, edition, detail):
    return {"tool": tool, "edition": edition, "detail": detail}


def slot(kind, *, ids=(), answer="", needs=(), status="", reason="", required=()):
    return {"kind": kind, "ids": list(dict.fromkeys(ids)), "answer": answer, "needs": list(needs),
            "status": status, "reason": reason, "required": list(dict.fromkeys(required))}


def matching_item(product, item):
    wanted = normalize_item_name(product)
    return bool(wanted) and wanted in {normalize_item_name(v)
                                      for v in (item["id"], item["name"], *item.get("aliases", []))}


def shop_lookup(task, sources):
    """Accept a verified exact match or a status for the same requested entity."""
    product = task["product"]
    if not product:
        return None, None
    for source in sources:
        if source.get("工具") != "find_shop_item" or source.get("檔期") not in task["editions"]:
            continue
        raw = source.get("商城", {})
        if raw.get("status") == "found" and len(raw.get("items", [])) == 1:
            item = raw["items"][0]
            if matching_item(product, item):
                return source, item
        requested = source.get("查詢參數", {}).get("name", "")
        if normalize_item_name(product) == normalize_item_name(requested):
            return source, None
    return None, None


def scoped_evidence(task, sources, evidence):
    """Scope by trusted source metadata, not by model-supplied ID or prose."""
    allowed, computations = set(), []
    for source in sources:
        name = source.get("工具")
        if name == "calculate":
            computations.extend(source.get("段落", []))
            continue
        if source.get("檔期") not in task["editions"]:
            continue
        if name == "get_my_event_progress" and task["account"] != "self":
            continue
        allowed.update(r["段落ID"] for r in source.get("段落", []) if r["段落ID"] in evidence)
    # Calculation dependencies must themselves be in scope; support chained computations.
    for _ in range(len(computations)):
        for row in computations:
            dependencies = row.get("運算依據", [])
            if dependencies and set(dependencies) <= allowed and row["段落ID"] in evidence:
                allowed.add(row["段落ID"])
    return {key: row for key, row in evidence.items() if key in allowed}


def progress_source(task, sources):
    return next((s for s in sources if s.get("工具") == "get_my_event_progress"
                 and s.get("檔期") in task["editions"] and task["account"] == "self"), None)


def source_ids(source, evidence):
    return [r["段落ID"] for r in source.get("段落", []) if r["段落ID"] in evidence]


def overview_requirements(task, catalog=CATALOG):
    return [p["段落ID"] for edition in task["editions"]
            for p in published_passages(edition, catalog) if p["文件ID"] == "launch"]


def overview_clauses(task, evidence, catalog=CATALOG):
    """Data-authored critical clauses, checked against effective retrieved source text.

    These are display/coverage requirements, not an executable eligibility engine.
    """
    clauses = []
    for edition in task["editions"]:
        for item in catalog.editions[edition].get("overview_required_clauses", []):
            key = f"{edition}/launch/{item['section']}"
            row = evidence.get(key)
            if row is None or item["text"] not in row["原文"]:
                raise ValueError("必要公告條款未取得或與有效公告不一致。")
            clauses.append((key, item["text"]))
    return clauses


def inspect_slots(task, sources, evidence, catalog=CATALOG):
    """Determine only typed data requirements; open-ended sufficiency stays semantic."""
    kind, editions = task["kind"], task["editions"]
    scoped = scoped_evidence(task, sources, evidence)
    edition = editions[0] if editions else ""
    product_source, item = shop_lookup(task, sources)
    product_required = kind in PRODUCT_KINDS or (kind in {"eligibility", "diagnosis"} and task["product"])
    if product_required:
        if product_source is None:
            return slot("missing", needs=[need("find_shop_item", edition, f"核對指定商品 {task['product']}")])
        status = product_source.get("狀態")
        ids = source_ids(product_source, scoped)
        if status == "not_found" and ids:
            # Never use a generic rule or another product to explain a failed transaction.
            return slot("direct", ids=ids, answer="\n".join(scoped[i]["原文"] for i in ids))
        if status in ("ambiguous", "need_name"):
            return slot("blocked", status="clarify", reason="商品尚未唯一確認。")
        if status != "found" or item is None:
            return slot("blocked", status="unavailable", reason="無法確認指定商品。")
        item_key = f"{product_source['檔期']}/shop-items/{item['id']}"
        if item_key not in scoped:
            return slot("blocked", status="unavailable", reason="缺少已核對商品證據。")
        if kind in ("item_price", "item_details"):
            text = f"「{item['name']}」需要 {item['price']} 枚星星幣。"
            if kind == "item_details" and item.get("description"):
                text += " " + item["description"]
            return slot("direct", ids=[item_key], answer=text)
    if kind in ("catalog", "catalog_details"):
        source = next((s for s in sources if s.get("工具") == "list_shop_items"
                       and s.get("檔期") == edition), None)
        if source is None:
            return slot("missing", needs=[need("list_shop_items", edition, "取得完整商城清單")])
        listing = source.get("商城", {})
        if listing.get("status") != "listed":
            return slot("blocked", status="unavailable", reason="完整商城清單未取得。")
        ids = source_ids(source, scoped)
        items = listing["items"]
        keys = [f"{edition}/shop-items/{i['id']}" for i in items]
        if not ids or not set(keys) <= set(ids):
            return slot("blocked", status="unavailable", reason="商城清單證據不完整。")
        text = f"{listing['title']}商城共 {len(items)} 件商品。"
        if items:
            lines = []
            for i in items:
                line = f"- {i['name']}：{i['price']} 枚星星幣"
                if kind == "catalog_details" and i.get("description"):
                    line += f"（{i['description']}）"
                lines.append(line)
            text += "\n\n" + "\n".join(lines)
        return slot("direct", ids=ids, answer=text)

    progress_needed = kind in SELF_REQUIRED or (kind == "eligibility" and task["account"] == "self")
    progress = progress_source(task, sources)
    if progress_needed:
        if progress is None:
            return slot("missing", needs=[need("get_my_event_progress", edition, "取得本人活動紀錄")])
        raw = progress.get("本人紀錄", {})
        if progress.get("狀態") != "found" or raw.get("檔期") != edition:
            return slot("blocked", status="unavailable", reason="該期本人紀錄未取得。")
        ids = source_ids(progress, scoped)
        if not ids:
            return slot("blocked", status="unavailable", reason="缺少本人紀錄來源。")
        dated = raw.get("紀錄日期", "")
        if kind in ("balance", "redemption_gap"):
            coins = raw.get("星星幣")
            if type(coins) is not int or coins < 0:
                return slot("blocked", status="no_evidence", reason="紀錄未提供有效餘額。")
            if kind == "redemption_gap":
                gap = max(0, item["price"] - coins)
                text = (f"本人於 {edition} 的紀錄餘額為 {coins} 枚星星幣；"
                        f"「{item['name']}」價格為 {item['price']} 枚，")
                text += (f"距離價格門檻還差 {gap} 枚。" if gap else "已達價格門檻。")
                text += "這只代表價格門檻，不代表已符合所有兌換資格。"
                return slot("direct", ids=[item_key, *ids], answer=text)
            return slot("direct", ids=ids, answer=f"本人於 {edition} 的紀錄餘額為 {coins} 枚星星幣（紀錄日期：{dated}）。")
        if kind in ("counted_matches", "recorded_matches"):
            field = "今日已計入場次" if kind == "counted_matches" else (
                "紀錄當日已計入場次" if "紀錄當日已計入場次" in raw else "今日已計入場次")
            value = raw.get(field)
            if (type(value) is not int or value < 0 or
                    kind == "counted_matches" and dated != catalog.as_of.isoformat()):
                return slot("blocked", status="no_evidence", reason="紀錄未提供所問日期的活動計次。")
            return slot("direct", ids=ids, answer=f"{dated} 已計入活動的場次為 {value} 場，不代表所有遊戲場次。")
        if kind == "participation":
            field = "已參加本期" if edition == catalog.current else "已參加該期"
            value = raw.get(field)
            if type(value) is not bool:
                return slot("blocked", status="no_evidence", reason="紀錄未提供參加狀態。")
            return slot("direct", ids=ids, answer=f"依 {edition} 本人紀錄，{'已' if value else '尚未'}參加該期活動。")
        # Other personal queries and eligibility still require semantic assessment.
    required = []
    if product_required:
        required.append(item_key)
    if progress_needed and progress:
        required.extend(source_ids(progress, scoped))
    if kind == "overview":
        required.extend(overview_requirements(task, catalog))
        missing_editions = [e for e in editions if any(k.startswith(e + "/") and k not in scoped for k in required)]
        if missing_editions:
            return slot("missing", needs=[need("find_rules", e, "取得核心活動公告完整章節") for e in missing_editions])
        try:
            overview_clauses(task, scoped, catalog)
        except ValueError as error:
            return slot("blocked", status="unavailable", reason=str(error))
    return slot("semantic", required=required)


def permits_tool(task, tool, args, sources):
    # Typed capabilities have bounded data dependencies. Open knowledge/diagnosis
    # remain semantic; the registry and task scope still validate every call.
    typed = {
        "catalog": {"list_shop_items"}, "catalog_details": {"list_shop_items"},
        "item_price": {"find_shop_item"}, "item_details": {"find_shop_item"},
        "balance": {"get_my_event_progress"}, "counted_matches": {"get_my_event_progress"},
        "recorded_matches": {"get_my_event_progress"}, "participation": {"get_my_event_progress"},
        "progress_query": {"get_my_event_progress"},
        "redemption_gap": {"find_shop_item", "get_my_event_progress"},
        "overview": {"find_rules"},
    }
    allowed = typed.get(task["kind"])
    if allowed is not None and tool not in allowed:
        return False
    if tool == "calculate":
        return task["kind"] in {"calculation", "eligibility", "diagnosis"}
    if args.get("edition") not in task["editions"]:
        return False
    if tool == "get_my_event_progress":
        if task["account"] != "self" or task["kind"] not in PRIVATE_KINDS:
            return False
        # 依賴指定商品的本人查詢，先驗證該商品；獨立餘額需求可同批。
        if task["product"] and task["kind"] in {"redemption_gap", "eligibility", "diagnosis"}:
            _, item = shop_lookup(task, sources)
            return item is not None
    return True


def terminal_message(state):
    if state["status"] == "unsupported":
        return "目前客服僅提供唯讀查詢與運算，不能代為發放或贈送獎勵、兌換、扣款或修改紀錄。"
    if state["status"] == "rejected":
        from answering import ANSWER_VERIFICATION_FAILED
        return ANSWER_VERIFICATION_FAILED
    return DIRECT_REPLIES[state["status"]]


def model_task_states(tasks, states):
    result = []
    for task in tasks:
        state = states[task["id"]]
        result.append({**task, "state": state["status"], "response_type": state["response_type"],
                       "answer": state["answer"] or (terminal_message(state) if state["status"] in TERMINAL
                                                     and state["status"] != "completed" else ""),
                       "source_ids": [source_alias(i) for i in state["source_ids"]],
                       "reason": state["reason"], "needs": state["needs"]})
    return result
