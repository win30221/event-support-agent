"""Compare retrieval on the same corpus, or replay a support scenario through the chat agent."""
import argparse
import asyncio
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from time import perf_counter
from collections import Counter
from math import sqrt
from pathlib import Path

from guide import published_passages, rank_passages, search_rules


def legacy_search(question, passages):
    """Reproduce the previous bigram scoring and unconditional top two on the expanded corpus."""
    def grams(text):
        chars = "".join(text.lower().split())
        return Counter(chars[index:index + 2] for index in range(len(chars) - 1))

    query = grams(question)

    def score(passage):
        tokens = grams(passage["章節"] + passage["原文"])
        return sum(query[w] * n for w, n in tokens.items()) / sqrt(sum(n * n for n in tokens.values()) or 1)

    return sorted(passages, key=score, reverse=True)[:2]


def offline(case, hybrid=False):
    question = case.get("retrieval_query", case["question"])
    mode = case["mode"]
    old, equal_k, new, combined = set(), set(), set(), set()
    if mode not in ("outside-account", "progress", "shop"):
        for key in case["editions"]:
            docs = published_passages(key)
            older = search_rules(question, key, "overview") if mode == "overview" else legacy_search(question, docs)
            old.update(p["段落ID"] for p in older)
            equal_k.update(p["段落ID"] for p in rank_passages(question, docs, limit=2))
            tool_mode = "search" if mode == "unanswerable" else mode
            new.update(p["段落ID"] for p in search_rules(question, key, tool_mode, retriever="bm25"))
            if hybrid:
                combined.update(p["段落ID"] for p in search_rules(question, key, tool_mode, retriever="hybrid"))
    return old, equal_k, new, combined


def summary(label, trials):
    if not trials:
        return
    complete = sum(required <= retrieved for required, retrieved in trials)
    recall = sum(len(required & retrieved) / len(required) for required, retrieved in trials) / len(trials)
    print(f"{label}：必要段落找齊 {complete}/{len(trials)}，平均證據召回率 {recall:.1%}")


# 使用相同語料與固定檔期/模式比較純檢索；不經模型，不代表整體回答準確率。
def benchmark(cases, detail=False, hybrid=False):
    old_trials, new_trials, old_search, equal_search = [], [], [], []
    hybrid_trials = []
    for case in cases:
        required = set(case["sources"])
        old, equal_k, new, combined = offline(case, hybrid)
        if required:
            old_trials.append((required, old))
            new_trials.append((required, new))
            if case["mode"] == "search":
                old_search.append((required, old))
                equal_search.append((required, equal_k))
            if hybrid:
                hybrid_trials.append((required, combined))
            missing = sorted(required - (combined if hybrid else new))
            verdict = "已找齊" if not missing else "漏掉 " + "、".join(missing)
        else:
            verdict = "人工核對回答、工具範圍或資料不足處理"
        print(f"{case['id']:20} {verdict}")
        if detail:
            print("玩家提問", case["question"])
            print("離線檔期、模式", case["editions"], case["mode"])
            print("舊版證據", sorted(old))
            print("BM25 方案證據", sorted(new))
            if hybrid:
                print("混合方案證據", sorted(combined))
            print("驗收要點", case["reference"])
    summary("同一語料 · 舊 bigram 與舊取段方式", old_trials)
    summary("同一語料 · BM25 與新取段方式", new_trials)
    summary("同一語料 · pgvector + BM25 與新取段方式", hybrid_trials)
    summary("僅聚焦問題／相同 top 2 · 舊 bigram", old_search)
    summary("僅聚焦問題／相同 top 2 · BM25", equal_search)


def live_source_coverage(required, events):
    """Measure retrieved and actually adopted required sources separately.

    This is a citation-coverage metric, not an LLM semantic-quality score.
    Empty ground-truth lists are not counted as successful coverage.
    """
    required = set(required)
    retrieved, withheld = set(), set()
    for title, data in events:
        if title == "收到工具回傳 · find_rules" and not data.get("isError"):
            retrieved.update(row["段落ID"] for row in (data.get("結構化資料") or {}).get("result", []))
        elif title == "Python 核對公告適用條件":
            withheld.update(row["段落ID"] for row in data.get("未採用段落", []))
    checks = [data for title, data in events if title == "Python 檢查回答依據"]
    adopted = {cite["id"] for part in (checks[-1]["子問題"] if checks else [])
               if part["判定"] == "supported" for cite in part["採用段落"]}
    usable = retrieved - withheld
    return {"required": sorted(required), "retrieved": sorted(retrieved),
            "usable": sorted(usable), "adopted": sorted(adopted),
            "missing_retrieval": sorted(required - usable),
            "missing_answer": sorted(required - adopted),
            "retrieval_coverage": len(required & usable) / len(required) if required else None,
            "answer_coverage": len(required & adopted) / len(required) if required else None}


# 依實際事件統計模型呼叫、工具與耗時；缺少 token 計數時保留未知。
def turn_metrics(events, trace, elapsed):
    """Observed calls/usage; missing provider counters stay unknown, not zero."""
    requests = [d for t, d in events if t.startswith("送出模型請求 · ")]
    responses = [d for t, d in events if t.startswith("收到模型回傳 · ")]
    metadata = [d.get("_provider_meta") or {} for d in responses]
    usage = [m.get("usage") or {} for m in metadata]
    def token_sum(key):
        values = [u.get(key) for u in usage]
        return sum(values) if len(values) == len(requests) and values and all(
            type(v) is int for v in values) else None
    return {"model_requests": len(requests), "model_responses": len(responses),
            "models": sorted({m["model"] for m in metadata if m.get("model")}),
            "prompt_tokens": token_sum("prompt_tokens"),
            "completion_tokens": token_sum("completion_tokens"),
            "total_tokens": token_sum("total_tokens"),
            "tool_attempts": sum(t.startswith("呼叫工具 · ") or t in (
                "Python 工具驗證失敗", "Python 重用工具結果") for t, _ in events),
            "tool_successes": len(trace),
            "stop_rechecks": sum(t == "Python 停止檢查" for t, _ in events),
            "elapsed_seconds": round(elapsed, 3)}


def report_metadata(provider):
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
            capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "commit": commit,
            "python": platform.python_version(), "os": platform.platform(), "provider": provider,
            "configured_model": os.getenv("OLLAMA_MODEL", "qwen3:8b") if provider == "ollama"
                                else os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "retrieval": os.getenv("EVENT_SUPPORT_AGENT_RETRIEVAL", "bm25"),
            "tool_selection": "native",
            "answer_review": os.getenv("EVENT_SUPPORT_AGENT_ANSWER_REVIEW", "auto"),
            "stop_gate": os.getenv("EVENT_SUPPORT_AGENT_STOP_GATE", "auto"),
            "semantic_review": "not_run"}


# 真實模型重播同一題集及多輪歷史，記錄證據覆蓋、工具及人工驗收要點。
async def replay(case, provider, report_turns=None):
    from agent import answer

    player = case.get("player", "A")
    history = []
    for question in [*case.get("before", []), case["question"]]:
        events = []
        print(f"\n玩家 {player}：{question}")
        def observe(title, data):
            events.append((title, data))
            if title in ("Python 工具批次", "Python 工具驗證失敗", "Python 接受補查需求",
                         "Python 啟動回答品質審查", "Python 回答品質審查結果", "Python 檢查回答依據"):
                print(title, json.dumps(data, ensure_ascii=False))
            if title.startswith("呼叫工具"):
                print(title, json.dumps(data, ensure_ascii=False))
            if title == "收到工具回傳 · find_rules" and not data.get("isError"):
                passages = (data.get("結構化資料") or {}).get("result", [])
                print("實際段落", [p["段落ID"] for p in passages])

        started = perf_counter()
        reply, trace = await answer(player, provider, history[-8:], question, observe=observe)
        metrics = turn_metrics(events, trace, perf_counter() - started)
        print("助手：", reply)
        print("實機呼叫統計", json.dumps(metrics, ensure_ascii=False))
        if report_turns is not None:
            report_turns.append({"question": question, "reply": reply, "metrics": metrics,
                "tools": [{"name": t["工具"], "arguments": t["參數"]} for t in trace],
                "checks": [d for t, d in events if t == "Python 檢查回答依據"]})
        history.extend([{"role": "user", "content": question},
                        {"role": "assistant", "content": reply, "trace": trace}])
    print("期望來源", case["sources"])
    print("人工驗收", case["reference"])
    coverage = live_source_coverage(case["sources"], events)
    print("實際證據採用評估", json.dumps(coverage, ensure_ascii=False))
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="用案例 ID 查看來源與驗收要點")
    parser.add_argument("--hybrid", action="store_true", help="用已建好的 pgvector 索引額外比較混合檢索")
    parser.add_argument("--live", choices=("ollama", "openai"), help="透過指定模型重播所選情境")
    parser.add_argument("--strict", action="store_true", help="實機重播時必要來源未被採用則回傳非零結束碼；不等於語意驗收")
    parser.add_argument("--report", type=Path, help="實機重播結果輸出 JSON；含模擬玩家問答與引用")
    args = parser.parse_args()
    if args.report and not args.live:
        parser.error("--report 需要搭配 --live")
    cases = json.loads(Path(__file__).with_name("rag_cases.json").read_text(encoding="utf-8"))
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            parser.error("找不到案例 ID，直接執行程式可列出全部案例")
    if args.live and args.hybrid:
        parser.error("--hybrid 用於離線題集比對；聊天實測請設定 EVENT_SUPPORT_AGENT_RETRIEVAL=hybrid")
    if args.strict and not args.live:
        parser.error("--strict 需要搭配 --live")
    if args.live:
        reports, details = [], []
        for case in cases:
            turns = []
            result = asyncio.run(replay(case, args.live, report_turns=turns))
            reports.append(result)
            details.append({"case": case["id"], "player": case.get("player", "A"),
                            "expected_editions": case["editions"], "reference": case["reference"],
                            "coverage": result, "turns": turns})
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({"run": report_metadata(args.live), "cases": details},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
            print("實機評測報告", args.report)
        labelled = [row for row in reports if row["required"]]
        summary("實機可用檢索證據", [(set(r["required"]), set(r["usable"])) for r in labelled])
        summary("實機回答採用證據", [(set(r["required"]), set(r["adopted"])) for r in labelled])
        print(f"無指定必要段落、需另行人工驗收：{len(reports) - len(labelled)} 題")
        if args.strict and any(r["missing_answer"] or r["missing_retrieval"] for r in labelled):
            raise SystemExit(1)
    else:
        benchmark(cases, detail=bool(args.case), hybrid=args.hybrid)


if __name__ == "__main__":
    main()
