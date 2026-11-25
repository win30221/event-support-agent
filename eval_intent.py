"""只執行正式第一層意圖分析；不啟動 Graph、Audit、資料工具或回答生成。"""
import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

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
            "contract_errors": [d["錯誤"] for t, d in events if t == "Python 任務契約驗證失敗"]}


def matches(result, expected):
    actual = result["intents"]
    return actual is not None and [
        {"type": item["type"], "request": item["request"]} for item in actual
    ] == expected


def print_result(result):
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


async def main(args):
    if args.cases:
        cases = json.loads(Path(__file__).with_name("intent_cases.json").read_text(encoding="utf-8"))
        passed = 0
        for case in cases:
            result = await classify(case["question"], args.provider, case.get("history", []))
            correct = matches(result, case["expected"])
            passed += int(correct)
            print_result({**result, "case": case["id"], "expected": case["expected"], "exact_match": correct})
        print(f"第一層嚴格比對：{passed}/{len(cases)}；語意等價的拆分仍須人工核對。")
        return 0 if passed == len(cases) else 1
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
    try:
        raise SystemExit(asyncio.run(main(parser.parse_args())))
    except (RuntimeError, ValueError) as error:
        parser.exit(1, f"模型請求失敗：{error}\n")
