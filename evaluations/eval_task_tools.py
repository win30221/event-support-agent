"""獨立評測第二層任務感知原生 Tool Calling；不執行資料工具。"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

from guide import CATALOG, normalize_item_name
from intent_analysis import INTENT_PROMPT
from task_tool_planning import TASK_TOOL_PROMPT, plan_tools


CASE_FILE = Path(__file__).with_name("task_tool_cases.json")
METRICS = ("status_match", "tool_match", "argument_match")


def load_cases(case_id=None):
    cases = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    selected = [c for c in cases if case_id is None or c["id"] == case_id]
    if not selected:
        raise ValueError(f"找不到案例：{case_id}")
    return selected


def product_key(value, edition):
    wanted = normalize_item_name(value)
    matches = {item["id"] for item in CATALOG.editions.get(edition, {}).get("shop_items", [])
               if wanted in {normalize_item_name(v) for v in
                             (item["id"], item["name"], *item.get("aliases", []))}}
    return next(iter(matches)) if len(matches) == 1 else wanted


def same_call(actual, expected, *, check_arguments):
    if actual["tool"] != expected["tool"]:
        return False
    if not check_arguments:
        return True
    args = actual["arguments"]
    for key, value in expected["arguments"].items():
        if key == "name":
            if product_key(args.get(key, ""), args.get("edition", "")) != product_key(value, args.get("edition", "")):
                return False
        elif args.get(key) != value:
            return False
    if sorted(actual.get("fields", [])) != sorted(expected.get("fields", [])):
        return False
    if actual.get("account_scope") != expected.get("account_scope"):
        return False
    # 檢索改寫不是逐字答案；每組概念至少符合一種等價詞，另保留原始內容供人工核對。
    query = args.get("query", "")
    return all(any(term in query for term in group.split("|"))
               for group in expected.get("query_terms", []))


def call_set_matches(actual, expected, *, check_arguments):
    if len(actual) != len(expected):
        return False
    remaining = list(actual)
    # 小型批次的一對一配對，不依工具呼叫順序；不可用同一呼叫滿足兩個預期。
    def match(index, available):
        if index == len(expected):
            return not available
        return any(same_call(call, expected[index], check_arguments=check_arguments)
                   and match(index + 1, available[:i] + available[i+1:])
                   for i, call in enumerate(available))
    return match(0, remaining)


def compare(result, expected):
    rows = {t["task_id"]: t for t in result.get("tasks", [])}
    comparisons = []
    for task in expected:
        row = rows.get(task["task_id"], {})
        status_match = row.get("status") == task["status"]
        tool_match = call_set_matches(row.get("calls", []), task["calls"], check_arguments=False)
        argument_match = call_set_matches(row.get("calls", []), task["calls"], check_arguments=True)
        comparisons.append({"task_id": task["task_id"], "status_match": status_match,
                            "tool_match": tool_match, "argument_match": argument_match,
                            "exact_match": status_match and argument_match and not row.get("errors")})
    identity_match = (len(result.get("tasks", [])) == len(expected)
                      and set(rows) == {t["task_id"] for t in expected})
    return {"task_comparisons": comparisons, "identity_match": identity_match,
            "exact_match": bool(identity_match and result.get("contract_valid")
                                and all(t["exact_match"] for t in comparisons))}


async def evaluate_cases(cases, provider, repeat):
    results = []
    for run in range(1, repeat + 1):
        for case in cases:
            started = perf_counter()
            try:
                result = await plan_tools(case["question"], case["intents"], provider, case.get("history", []))
            except (RuntimeError, ValueError, OSError) as error:
                result = {"contract_valid": False, "tasks": [], "errors": [str(error)], "model_calls": None}
            row = {"case": case["id"], "group": case["group"], "run": run,
                   "question": case["question"], "first_layer": case["intents"],
                   "expected": case["expected"], "rationale": case["rationale"],
                   "elapsed_seconds": round(perf_counter() - started, 3), **result,
                   **compare(result, case["expected"])}
            results.append(row)
            print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
    tasks = [t for row in results for t in row["task_comparisons"]]
    summary = {"total": len(results), "contract_valid": sum(bool(r["contract_valid"]) for r in results),
               "exact_match": sum(r["exact_match"] for r in results), "total_tasks": len(tasks),
               "task_exact_match": sum(t["exact_match"] for t in tasks),
               **{metric: sum(t[metric] for t in tasks) for metric in METRICS},
               "groups": {group: {"total": sum(r["group"] == group for r in results),
                                  "exact_match": sum(r["group"] == group and r["exact_match"] for r in results)}
                          for group in sorted({r["group"] for r in results})}}
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("此評測只驗證首輪工具提案與主機核定，不執行工具或驗證最終回答；限制理由及檢索語意仍需人工複核。", flush=True)
    model = os.getenv("OLLAMA_MODEL", "qwen3:8b") if provider == "ollama" else os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return {"provider": provider, "configured_model": model, "case_file": CASE_FILE.name,
            "prompt_sha256": hashlib.sha256(TASK_TOOL_PROMPT.encode()).hexdigest(),
            "first_layer_prompt_sha256": hashlib.sha256(INTENT_PROMPT.encode()).hexdigest(),
            "repeat": repeat, "summary": summary, "results": results}


async def main(args):
    cases = load_cases(args.case)
    print(f"第二層工具規劃：{len(cases)} 題，重跑 {args.repeat} 次。", flush=True)
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
    mode.add_argument("--cases", action="store_true")
    mode.add_argument("--case", help="單一案例 ID")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--report", help="本機 JSON 報告")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat 必須為正整數。")
    try:
        raise SystemExit(asyncio.run(main(args)))
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"執行失敗：{error}\n")
