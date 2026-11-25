"""只執行正式第一層意圖分析；不啟動 Graph、Audit、資料工具或回答生成。"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter

from intent_analysis import INTENT_PROMPT
from model_client import completion
from task_runtime import IntentRuntime


async def classify(question, provider="ollama", history=(), complete=completion):
    events = []
    runtime = IntentRuntime("A", provider, list(history), question, complete,
                            lambda title, data: events.append((title, data)))
    started = perf_counter()
    intents = await runtime.classify_intents(runtime.intent_packet({}))
    responses = [data for title, data in events if title.startswith("收到模型回傳 · 精簡意圖分析")]
    return {"question": question, "intents": intents, "contract_valid": intents is not None,
            "model_calls": len(responses), "elapsed_seconds": round(perf_counter() - started, 3),
            "provider_meta": [r.get("_provider_meta") for r in responses],
            "raw_outputs": [r.get("content") for r in responses],
            "contract_errors": [d["錯誤"] for t, d in events if t == "Python 任務契約驗證失敗"]}


def compare(result, expected):
    # 類型序列及逐字片段分開比對；不同切分未必代表語意錯誤，須人工複核。
    actual = result.get("intents")
    valid = result.get("contract_valid", False) and actual is not None
    count = bool(valid and len(actual) == len(expected))
    types = bool(count and [i["type"] for i in actual] == [i["type"] for i in expected])
    spans = bool(count and [i["request"] for i in actual] == [i["request"] for i in expected])
    return {"count_match": count, "type_sequence_match": types, "span_match": spans,
            "exact_match": types and spans}


def matches(result, expected):
    return compare({**result, "contract_valid": result.get("contract_valid", result.get("intents") is not None)},
                   expected)["exact_match"]


def print_result(result):
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def load_cases(case_id=None):
    cases = json.loads(Path(__file__).with_name("intent_cases.json").read_text(encoding="utf-8"))
    if case_id is not None:
        cases = [c for c in cases if c["id"] == case_id]
        if not cases:
            raise ValueError(f"找不到案例：{case_id}；請查看 intent_cases.json。")
    return cases


async def evaluate_cases(cases, provider, repeat):
    results = []
    for run in range(1, repeat + 1):
        for case in cases:
            try:
                result = await classify(case["question"], provider, case.get("history", []))
            except (RuntimeError, ValueError) as error:
                # 模型連線錯誤也算失敗，不能漏掉或假稱呼叫次數為零。
                result = {"question": case["question"], "intents": None, "contract_valid": False,
                          "model_calls": None, "elapsed_seconds": None, "error": str(error)}
            row = {**result, "case": case["id"], "run": run, "expected": case["expected"],
                   "rationale": case.get("rationale", ""), **compare(result, case["expected"])}
            results.append(row)
            print_result(row)
    summary = {"total": len(results),
               **{key: sum(bool(r.get(key)) for r in results)
                  for key in ("contract_valid", "count_match", "type_sequence_match", "span_match", "exact_match")}}
    print_result(summary)
    print("逐字比對不等同完整語意證明；切分差異及未見問句須人工複核。")
    model = os.getenv("OLLAMA_MODEL", "qwen3:8b") if provider == "ollama" else os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return {"provider": provider, "configured_model": model,
            "prompt_sha256": hashlib.sha256(INTENT_PROMPT.encode("utf-8")).hexdigest(),
            "repeat": repeat, "summary": summary, "results": results}


async def main(args):
    if args.cases or args.case is not None:
        report = await evaluate_cases(load_cases(args.case), args.provider, args.repeat)
        if args.report:
            path = Path(args.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"評測結果：{path}", flush=True)
        summary = report["summary"]
        return 0 if summary["exact_match"] == summary["total"] else 1
    if args.question is not None:
        result = await classify(args.question, args.provider)
        print_result(result)
        return 0 if result["contract_valid"] else 1
    print("第一層意圖測試（每題獨立、無歷史；空白行結束）。", flush=True)
    while True:
        try:
            question = input("玩家問題 > ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question.strip():
            return 0
        try:
            print_result(await classify(question, args.provider))
        except (RuntimeError, ValueError) as error:
            print(f"模型請求失敗：{error}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "openai"), default="ollama")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--question", help="單題分類；不指定則進入互動模式")
    mode.add_argument("--cases", action="store_true", help="執行 intent_cases.json 標記題集")
    mode.add_argument("--case", help="只執行指定案例 ID")
    parser.add_argument("--repeat", type=int, default=1, help="標記題集重跑次數")
    parser.add_argument("--report", help="標記題集的本機 JSON 結果路徑")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat 必須為正整數。")
    if (args.repeat != 1 or args.report) and not (args.cases or args.case is not None):
        parser.error("--repeat 與 --report 需搭配 --cases 或 --case。")
    try:
        raise SystemExit(asyncio.run(main(args)))
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"執行失敗：{error}\n")
