"""獨立驗證正式第二層意圖審查與業務綁定，不執行第一層或後續資料工具。"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

from guide import CATALOG, normalize_item_name
from intent_analysis import AUDIT_PROMPT, INTENT_PROMPT, SimpleIntentPlan, anchor_intents
from model_client import completion
from task_runtime import IntentRuntime


CASE_FILE = Path(__file__).with_name("audit_cases.json")
STAGE = "意圖與業務範圍驗證"
BINDING_METRICS = ("identity_match", "kind_match", "edition_match", "product_match",
                   "account_match", "status_match")


def load_cases(case_id=None):
    cases = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    if case_id is not None:
        cases = [case for case in cases if case["id"] == case_id]
    if not cases:
        raise ValueError(f"找不到第二層案例：{case_id or CASE_FILE.name}")
    return cases


async def review(question, first_layer, provider="ollama", history=(), complete=completion):
    # 只檢查第一層輸入的結構和原問片段；故意錯分類、漏拆、多拆亦可測。
    intents = anchor_intents(SimpleIntentPlan.model_validate({"intents": first_layer}), question)
    events = []
    runtime = IntentRuntime("A", provider, list(history), question, complete,
                            lambda title, data: events.append((title, data)))
    started = perf_counter()
    reviewed, error = None, None
    try:
        reviewed = await runtime.audit_intents(runtime.intent_packet({}), intents)
    except (RuntimeError, ValueError, OSError) as exc:
        error = str(exc)
    responses = [d for title, d in events if title.startswith(f"收到模型回傳 · {STAGE}")]
    requests = [d for title, d in events if title.startswith(f"送出模型請求 · {STAGE}")]
    audit, tasks, states = None, [], {}
    if reviewed is not None:
        result, plan = reviewed
        audit = result.model_dump()
        if plan is not None:
            tasks, states = plan
    return {"question": question, "first_layer_intents": intents, "audit": audit,
            "tasks": tasks, "task_states": states, "contract_valid": reviewed is not None,
            "model_calls": len(requests), "elapsed_seconds": round(perf_counter() - started, 3),
            "provider_meta": [r.get("_provider_meta") for r in responses],
            "raw_outputs": [r.get("content") for r in responses],
            "contract_errors": [d["錯誤"] for t, d in events if t == "Python 任務契約驗證失敗"],
            "error": error}


def canonical_product(name, editions):
    # 只接受同一檔期商品的既有名稱、ID 或精確別名等價；未知商品保留原詞。
    wanted = normalize_item_name(name) if name else ""
    if not wanted:
        return ""
    matches = {item["id"] for edition in editions
               for item in CATALOG.editions.get(edition, {}).get("shop_items", [])
               if wanted in {normalize_item_name(v) for v in
                             (item["id"], item["name"], *item.get("aliases", []))}}
    return next(iter(matches)) if len(matches) == 1 else wanted


def compare(result, expected):
    valid = bool(result.get("contract_valid"))
    audit = result.get("audit") or {}
    decision = bool(valid and audit.get("decision") == expected["decision"])
    checks = {"decision_match": decision}
    if expected["decision"] == "revise":
        # revise 不應有 binding，且主機已檢查具體 issues。問題文字需人工複核。
        checks.update({key: None for key in BINDING_METRICS})
        checks["exact_match"] = bool(decision and audit.get("issues") and
                                     not audit.get("bindings") and not result.get("tasks"))
        return checks
    actual = result.get("tasks") or []
    expected_tasks = expected["tasks"]
    states = result.get("task_states") or {}
    by_id = {task["id"]: task for task in actual}
    raw_by_id = {binding["task_id"]: binding for binding in audit.get("bindings", [])}
    ids = [task["task_id"] for task in expected_tasks]
    intents = {intent["task_id"]: intent for intent in result.get("first_layer_intents", [])}
    complete = bool(decision and [task["id"] for task in actual] == ids and
                    set(states) == set(ids) and set(raw_by_id) == set(ids))
    checks["identity_match"] = bool(complete and all(
        task["id"] in intents and task["goal"] == intents[task["id"]]["request"]
        and task["intent_type"] == intents[task["id"]]["type"] for task in actual))
    for key, field in (("kind_match", "kind"), ("account_match", "account")):
        checks[key] = bool(complete and all(by_id[t["task_id"]][field] == t[field]
                                            for t in expected_tasks))
    # 比較主機核定的檔期 ID；順序不影響同一組檔期的資料範圍。
    checks["edition_match"] = bool(complete and all(
        sorted(by_id[t["task_id"]]["editions"]) == sorted(t["editions"]) for t in expected_tasks))
    checks["product_match"] = bool(complete and all(
        canonical_product(by_id[t["task_id"]]["product"], t["editions"]) ==
        canonical_product(t["product"], t["editions"]) ==
        canonical_product(raw_by_id[t["task_id"]]["product"], t["editions"])
        for t in expected_tasks))
    checks["status_match"] = bool(complete and all(
        states[t["task_id"]]["status"] == t["status"] for t in expected_tasks))
    checks["exact_match"] = all(checks[key] for key in ("decision_match", *BINDING_METRICS))
    return checks


def print_result(result):
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


async def evaluate_cases(cases, provider, repeat):
    results = []
    for run in range(1, repeat + 1):
        for case in cases:
            try:
                result = await review(case["question"], case["intents"], provider, case.get("history", []))
            except (RuntimeError, ValueError, OSError) as error:
                result = {"question": case["question"], "first_layer_input": case["intents"],
                          "contract_valid": False, "audit": None, "tasks": [], "task_states": {},
                          "model_calls": None, "elapsed_seconds": None, "error": str(error)}
            row = {**result, "case": case["id"], "run": run, "expected": case["expected"],
                   "rationale": case.get("rationale", ""), **compare(result, case["expected"])}
            results.append(row)
            print_result(row)
    accept_rows = [r for r in results if r["expected"]["decision"] == "accept"]
    revise_rows = [r for r in results if r["expected"]["decision"] == "revise"]
    summary = {"total": len(results), "accept_cases": len(accept_rows), "revise_cases": len(revise_rows),
               "contract_valid": sum(bool(r["contract_valid"]) for r in results),
               "decision_match": sum(bool(r["decision_match"]) for r in results),
               "accept_decision_match": sum(bool(r["decision_match"]) for r in accept_rows),
               "revise_decision_match": sum(bool(r["decision_match"]) for r in revise_rows),
               **{k: sum(bool(r[k]) for r in accept_rows) for k in BINDING_METRICS},
               "exact_match": sum(bool(r["exact_match"]) for r in results)}
    print_result(summary)
    print("綁定欄位的分母為 accept_cases；revise 的 issues 語意與可接受等價答案須人工複核。", flush=True)
    model = os.getenv("OLLAMA_MODEL", "qwen3:8b") if provider == "ollama" else os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return {"provider": provider, "configured_model": model,
            "prompt_sha256": hashlib.sha256(AUDIT_PROMPT.encode("utf-8")).hexdigest(),
            "first_layer_prompt_sha256": hashlib.sha256(INTENT_PROMPT.encode("utf-8")).hexdigest(),
            "case_file": CASE_FILE.name, "repeat": repeat, "summary": summary, "results": results}


async def main(args):
    cases = load_cases(args.case)
    print(f"第二層題庫：{CASE_FILE.name}，{len(cases)} 題，重跑 {args.repeat} 次。", flush=True)
    report = await evaluate_cases(cases, args.provider, args.repeat)
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"評測結果：{path}", flush=True)
    return 0 if report["summary"]["exact_match"] == report["summary"]["total"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "openai"), default="ollama")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--cases", action="store_true", help="執行第二層完整標記題集")
    mode.add_argument("--case", help="只執行指定第二層案例 ID")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--report", help="本機 JSON 報告路徑")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat 必須為正整數。")
    try:
        raise SystemExit(asyncio.run(main(args)))
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"執行失敗：{error}\n")
