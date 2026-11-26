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
from embedding_client import TransientEmbeddingError
from request_pacing import DEFAULT_BATCH_RPM, ENV as NVIDIA_RPM_ENV, configured_rpm
from transport_observation import capture_transport


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
        combined = offline_hybrid(case)
    return old, equal_k, new, combined


def offline_hybrid(case):
    combined = set()
    if case["mode"] not in ("outside-account", "progress", "shop"):
        question = case.get("retrieval_query", case["question"])
        mode = "search" if case["mode"] == "unanswerable" else case["mode"]
        for key in case["editions"]:
            combined.update(p["段落ID"] for p in search_rules(question, key, mode, retriever="hybrid"))
    return combined


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
    hybrid_errors = []
    for case in cases:
        required = set(case["sources"])
        old, equal_k, new, combined = offline(case)
        failed = False
        if hybrid:
            try:
                combined = offline_hybrid(case)
            except TransientEmbeddingError as error:
                # 供應商服務失敗不是「沒有找到證據」；不可混入召回率或隱藏。
                failed = True
                hybrid_errors.append((case["id"], str(error)))
        if required:
            old_trials.append((required, old))
            new_trials.append((required, new))
            if case["mode"] == "search":
                old_search.append((required, old))
                equal_search.append((required, equal_k))
            if hybrid and not failed:
                hybrid_trials.append((required, combined))
            missing = sorted(required - (combined if hybrid else new))
            verdict = "已找齊" if not missing else "漏掉 " + "、".join(missing)
        else:
            verdict = "人工核對回答、工具範圍或資料不足處理"
        if failed:
            verdict = "Hybrid 執行失敗：" + hybrid_errors[-1][1]
        print(f"{case['id']:20} {verdict}", flush=True)
        if detail:
            print("玩家提問", case["question"])
            print("離線檔期、模式", case["editions"], case["mode"])
            print("舊版證據", sorted(old))
            print("BM25 方案證據", sorted(new))
            if hybrid:
                print("混合方案證據", "未完成檢索" if failed else sorted(combined))
            print("驗收要點", case["reference"])
    summary("同一語料 · 舊 bigram 與舊取段方式", old_trials)
    summary("同一語料 · BM25 與新取段方式", new_trials)
    summary("同一語料 · pgvector + BM25 與新取段方式（成功完成的有標記案例）", hybrid_trials)
    summary("僅聚焦問題／相同 top 2 · 舊 bigram", old_search)
    summary("僅聚焦問題／相同 top 2 · BM25", equal_search)
    if hybrid:
        print(f"Hybrid 完成案例：{len(cases) - len(hybrid_errors)}/{len(cases)}")
        if hybrid_errors:
            print("Hybrid 評測不完整，不可直接與完整 BM25 召回率比較。")
            print("失敗案例：" + "、".join(case_id for case_id, _ in hybrid_errors))
            raise SystemExit(1)


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
    model_metrics = [d for t, d in events if t.startswith("Python 模型請求指標 · ")]
    tool_metrics = [d for t, d in events if t == "Python 工具執行指標"]
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
            "elapsed_seconds": round(elapsed, 3),
            "model_request_ms": round(sum(d["耗時毫秒"] for d in model_metrics), 2) if model_metrics else None,
            "tool_execution_ms": round(sum(d["耗時毫秒"] for d in tool_metrics), 2) if tool_metrics else None,
            "model_steps": model_metrics,
            "tool_steps": tool_metrics,
            "provider_timing": [{k: m[k] for k in ("total_duration", "load_duration",
                "prompt_eval_duration", "eval_duration", "prompt_eval_count", "eval_count") if k in m}
                for m in metadata]}


def report_metadata(provider):
    from hashlib import sha256
    from model_client import configured_model
    from embedding_client import resolve_embedding
    from single_agent import MAX_MODEL_CALLS, NUM_PREDICT, SINGLE_AGENT_PROMPT
    from single_output import FINAL_SCHEMA
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "commit": commit,
            "python": platform.python_version(), "os": platform.platform(), "provider": provider,
            "configured_model": configured_model(provider),
            "nvidia_request_pacing_rpm": configured_rpm(),
            "retrieval": os.getenv("EVENT_SUPPORT_AGENT_RETRIEVAL", "bm25"),
            "embedding": resolve_embedding(provider).public_metadata()
                         if os.getenv("EVENT_SUPPORT_AGENT_RETRIEVAL", "bm25") == "hybrid" else None,
            "tool_selection": "native", "agent_framework": "langgraph",
            "answer_review": "off",
            "ollama_num_ctx": os.getenv("EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX"),
            "ollama_reasoning": os.getenv("EVENT_SUPPORT_AGENT_OLLAMA_REASONING"),
            "ollama_timeout": os.getenv("EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS", "120"),
            "stop_gate": "not_used",
            "semantic_review": "not_run", "workflow": "single-agent-loop",
            "online_verifier": "off", "max_model_calls": MAX_MODEL_CALLS,
            "num_predict": NUM_PREDICT if provider == "ollama" else None,
            "prompt_sha256": sha256(SINGLE_AGENT_PROMPT.encode()).hexdigest(),
            "final_output": "native_submit_answer_with_schema_fallback",
            "final_schema_sha256": sha256(json.dumps(FINAL_SCHEMA, sort_keys=True).encode()).hexdigest()}


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
                         "Python 啟動回答品質審查", "Python 回答品質審查結果", "Python 檢查回答依據",
                         "Python 單一 Agent 回傳驗證失敗", "Python 查詢進展", "Python 檢索快取統計"):
                print(title, json.dumps(data, ensure_ascii=False))
            if title == "Python 單一 Agent 輪次" and not data.get("允許工具"):
                print(title, json.dumps(data, ensure_ascii=False))
            if title == "Python 呈現資料限制":
                print(title, json.dumps(data, ensure_ascii=False))
            if title.startswith("呼叫工具"):
                print(title, json.dumps(data, ensure_ascii=False))
            if title == "收到工具回傳 · find_rules" and not data.get("isError"):
                passages = (data.get("結構化資料") or {}).get("result", [])
                print("實際段落", [p["段落ID"] for p in passages])

        started = perf_counter()
        with capture_transport() as transport:
            try:
                reply, trace = await answer(player, provider, history[-8:], question, observe=observe)
            except Exception as error:
                if report_turns is not None:
                    metrics = turn_metrics(events, [], perf_counter() - started)
                    metrics["tool_successes"] = None  # 尚未取得最後的成功 trace。
                    report_turns.append({"question": question, "reply": "", "metrics": metrics,
                        "tools": [], "checks": [d for t, d in events if t == "Python 檢查回答依據"],
                        "events": [{"title": t, "data": d} for t, d in events],
                        "transport": transport, "execution_error": type(error).__name__})
                raise
        metrics = turn_metrics(events, trace, perf_counter() - started)
        print("助手：", reply)
        print("實機呼叫統計", json.dumps(metrics, ensure_ascii=False))
        if report_turns is not None:
            report_turns.append({"question": question, "reply": reply, "metrics": metrics,
                "tools": [{"name": t["工具"], "arguments": t["參數"]} for t in trace],
                "checks": [d for t, d in events if t == "Python 檢查回答依據"],
                "events": [{"title": t, "data": d} for t, d in events], "transport": transport})
        history.extend([{"role": "user", "content": question},
                        {"role": "assistant", "content": reply, "trace": trace}])
    print("期望來源", case["sources"])
    print("人工驗收", case["reference"])
    coverage = live_source_coverage(case["sources"], events)
    print("實際證據採用評估", json.dumps(coverage, ensure_ascii=False))
    return coverage


def structural_gate(case, turns, coverage):
    """Deterministic live acceptance only; semantic correctness needs separate review."""
    from answering import ANSWER_VERIFICATION_FAILED
    if not turns:
        return ["no turns"]
    final = turns[-1]
    issues = []
    checks = final["checks"][-1]["子問題"] if final["checks"] else []
    statuses = {part["判定"] for part in checks}
    names = {tool["name"] for tool in final["tools"]}
    editions = {tool["arguments"]["edition"] for tool in final["tools"] if "edition" in tool["arguments"]}
    if not checks or any(ANSWER_VERIFICATION_FAILED in t["reply"] for t in turns) or "rejected" in statuses:
        issues.append("answer rejected or missing validation")
    if final["checks"] and final["checks"][-1].get("需求覆蓋", "complete") != "complete":
        issues.append("intent coverage not verified")
    if not editions <= set(case["editions"]):
        issues.append("unexpected edition")
    if coverage["missing_retrieval"] or coverage["missing_answer"]:
        issues.append("required evidence missing")
    expected = set(case.get("expected_statuses", []))
    if expected:
        if not expected <= statuses:
            issues.append("expected task status missing")
        if names & set(case.get("forbid_tools", [])):
            issues.append("unexpected tool for terminal task")
    elif case["mode"] == "outside-account":
        if any(tool["name"] == "get_my_event_progress" for turn in turns for tool in turn["tools"]):
            issues.append("private lookup outside account")
        if "account_limit" not in statuses:
            issues.append("missing account boundary")
    elif case["mode"] == "unanswerable" or case["id"] == "unrecorded-days":
        if "no_evidence" not in statuses or "supported" in statuses:
            issues.append("missing evidence limitation")
    elif "supported" not in statuses:
        issues.append("answerable case not supported")
    if case.get("progress") and "get_my_event_progress" not in names:
        issues.append("required personal lookup missing")
    if case["mode"] == "shop" and "list_shop_items" not in names:
        issues.append("required catalogue lookup missing")
    return issues


def save_report(path, run, details, expected_trials):
    """Write a complete JSON checkpoint after each trial, including failures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps({"run": run, "expected_trials": expected_trials,
                                "completed_trials": len(details),
                                "complete": len(details) == expected_trials,
                                "cases": details}, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def main():
    from model_client import PROVIDERS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="指定案例 ID；可重複指定")
    parser.add_argument("--suite", choices=("baseline", "holdout"), default="baseline",
                        help="baseline 保留原 31 題；holdout 為另行凍結的新問句，僅實機")
    parser.add_argument("--hybrid", action="store_true", help="用已建好的 pgvector 索引額外比較混合檢索")
    parser.add_argument("--live", choices=PROVIDERS, help="透過指定模型重播所選情境")
    parser.add_argument("--strict", action="store_true", help="實機重播時必要來源未被採用則回傳非零結束碼；不等於語意驗收")
    parser.add_argument("--report", type=Path, help="實機重播結果輸出 JSON；含模擬玩家問答與引用")
    parser.add_argument("--gate", action="store_true", help="實機結構性驗收；所有選定試驗通過才回傳 0，仍需人工語意驗收")
    parser.add_argument("--repeat", type=int, default=1, help="每個案例重跑次數")
    parser.add_argument("--nvidia-rpm", type=int, default=None,
                        help="NVIDIA 聊天、Embedding 及重試共用每分鐘請求上限；預設 20 或環境變數，0 停用")
    args = parser.parse_args()
    if args.nvidia_rpm is not None and args.nvidia_rpm < 0:
        parser.error("--nvidia-rpm 必須為非負整數")
    if args.live or args.hybrid:
        # 評測程序設定一次；不修改使用者的永久環境變數。
        if args.nvidia_rpm is not None:
            os.environ[NVIDIA_RPM_ENV] = str(args.nvidia_rpm)
        else:
            os.environ.setdefault(NVIDIA_RPM_ENV, str(DEFAULT_BATCH_RPM))
        try:
            rpm = configured_rpm()
        except ValueError as error:
            parser.error(str(error))
        print(f"NVIDIA API pacing: {rpm} RPM (0 = disabled; chat + embeddings + retries)", flush=True)
    if args.repeat < 1 or args.repeat > 20:
        parser.error("--repeat 必須為 1 至 20")
    if (args.gate or args.repeat != 1) and not args.live:
        parser.error("--gate/--repeat 需要 --live")
    if args.report and not args.live:
        parser.error("--report 需要搭配 --live")
    if args.suite == "holdout":
        if not args.live:
            parser.error("--suite holdout 需要 --live")
        cases = json.loads(Path(__file__).with_name("holdout_cases.json").read_text(encoding="utf-8"))
    else:
        cases = json.loads(Path(__file__).with_name("rag_cases.json").read_text(encoding="utf-8"))
        if args.live:
            cases += json.loads(Path(__file__).with_name("task_cases.json").read_text(encoding="utf-8"))
    if args.case:
        known = {case["id"] for case in cases}
        missing = set(args.case) - known
        if missing:
            parser.error("找不到案例 ID：" + "、".join(sorted(missing)))
        cases = [case for case in cases if case["id"] in args.case]
    if args.live and args.hybrid:
        parser.error("--hybrid 用於離線題集比對；聊天實測請設定 EVENT_SUPPORT_AGENT_RETRIEVAL=hybrid")
    if args.strict and not args.live:
        parser.error("--strict 需要搭配 --live")
    if args.live:
        reports, details = [], []
        run = report_metadata(args.live)
        run["suite"] = args.suite
        run["transport_observation_version"] = 1
        expected_trials = len(cases) * args.repeat
        for case in cases:
            for trial in range(1, args.repeat + 1):
                print(f"\n[{len(details) + 1}/{expected_trials}] {case['id']} · trial {trial}", flush=True)
                turns = []
                error_type = None
                try:
                    result = asyncio.run(replay(case, args.live, report_turns=turns))
                    issues = structural_gate(case, turns, result)
                    reports.append(result)
                except Exception as error:
                    # 記錄失敗類型，不將可能含有連線憑證的例外原文寫入報告。
                    result = None
                    error_type = type(error).__name__
                    issues = ["evaluation execution error: " + error_type]
                details.append({"case": case["id"], "trial": trial, "player": case.get("player", "A"),
                                "expected_editions": case["editions"], "reference": case["reference"],
                                "coverage": result, "turns": turns, "gate_issues": issues,
                                "execution_error": error_type, "semantic_review": "not_run"})
                print("結構驗收", "PASS" if not issues else "FAIL: " + "; ".join(issues))
                if args.report:
                    save_report(args.report, run, details, expected_trials)
        if args.report:
            print("實機評測報告", args.report)
        labelled = [row for row in reports if row["required"]]
        summary("實機可用檢索證據", [(set(r["required"]), set(r["usable"])) for r in labelled])
        summary("實機回答採用證據", [(set(r["required"]), set(r["adopted"])) for r in labelled])
        print(f"無指定必要段落、需另行人工驗收：{len(reports) - len(labelled)} 題")
        print("結構驗收通過", sum(not d["gate_issues"] for d in details), "/", len(details))
        print("語意驗收尚未執行：請逐題對照 reference，不將 PASS 視為語意正確。")
        if args.report:
            print("可使用 python -m evaluations.eval_report 重新評分，不重新呼叫模型。")
        if any(d["execution_error"] for d in details):
            raise SystemExit(1)
        if args.gate and any(d["gate_issues"] for d in details):
            raise SystemExit(1)
        if args.strict and any(r["missing_answer"] or r["missing_retrieval"] for r in labelled):
            raise SystemExit(1)
    else:
        benchmark(cases, detail=bool(args.case), hybrid=args.hybrid)


if __name__ == "__main__":
    main()
