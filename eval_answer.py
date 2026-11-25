"""Replay the answer stage of a locally saved engineering log (no RAG/tool calls)."""
import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from answering import ANSWER_PROMPT, ANSWER_SCHEMA, ANSWER_VERIFICATION_FAILED, decode_response, render_response
from backend import compact_json
from evidence import model_sources
from eval_rag import report_metadata, turn_metrics
from model_client import completion, model_step


def load_log(path):
    events = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    # First answer request; do not upload logs to GitHub.
    request = next(e["data"] for e in events if e["title"] == "送出模型請求 · 結構化回答")
    packet = json.loads(request["messages"][-1]["content"])
    rows = {row["段落ID"]: row for group in packet["工具結果"] for row in group["段落"]}
    if not rows:
        raise ValueError("Log 沒有可重播的證據。")
    mapping = next((e["data"]["來源對照"] for e in events if e["title"] == "Python 回答上下文"), {})
    if mapping:
        for group in packet["工具結果"]:
            for row in group["段落"]:
                key = mapping.get(row["段落ID"])
                if key is None:
                    raise ValueError("Log 的來源對照不完整。")
                row["段落ID"] = key
                if "運算依據" in row:
                    row["運算依據"] = [mapping.get(i, i) for i in row["運算依據"]]
            if "已回傳段落ID" in group:
                group["已回傳段落ID"] = [mapping.get(i, i) for i in group["已回傳段落ID"]]
        rows = {r["段落ID"]: r for group in packet["工具結果"] for r in group["段落"]}
    return request, packet, rows


def make_request(request, packet, variant):
    if variant == "legacy":
        schema = request["預期回答格式"]
        if '"quote"' not in json.dumps(schema):
            raise ValueError("legacy 需要舊版逐字引用的 Log。")
        return request["messages"], schema
    groups = model_sources(packet["工具結果"], aliases=True, compact=variant == "compact")
    new_packet = {**packet, "工具結果": groups}
    history = request["messages"][1:-1]
    if history and history[-1].get("content") == packet["玩家原問"]:
        history = history[:-1]
    return [{"role": "system", "content": ANSWER_PROMPT}, *history,
            {"role": "user", "content": compact_json(new_packet)}], ANSWER_SCHEMA


def legacy_check(content, evidence):
    data = json.loads(content)
    if data.get("action") != "answer" or not data.get("results"):
        return False
    supported = [p for p in data["results"] if p.get("status") == "supported"]
    return bool(supported) and all(p.get("answer") and p.get("citations") and all(
        c.get("id") in evidence and isinstance(c.get("quote"), str) and c["quote"].strip()
        and c["quote"].strip() in evidence[c["id"]]["原文"] for c in p["citations"]) for p in supported)


async def run_once(request, packet, evidence, provider, variant):
    messages, schema = make_request(request, packet, variant)
    events = []
    started = perf_counter()
    reply = await model_step(provider, messages, [], completion,
                             lambda t, d: events.append((t, d)), "獨立回答重播", response_schema=schema)
    elapsed = perf_counter() - started
    raw = reply.get("content")
    try:
        if variant == "legacy":
            passed = legacy_check(raw, evidence)
            text, checks = raw, []
        else:
            data = decode_response(raw, evidence=evidence, original_question=packet["玩家原問"])
            text = render_response(data, evidence, lambda t, d: events.append((t, d)))
            checks = [d for t, d in events if t == "Python 檢查回答依據"]
            passed = bool(checks and checks[-1]["判定"] == "supported"
                          and ANSWER_VERIFICATION_FAILED not in text and data["action"] == "answer")
        error = None
    except (ValueError, TypeError, KeyError) as exc:
        passed, text, checks, error = False, None, [], type(exc).__name__
    return {"variant": variant, "structural_pass": passed, "answer": text, "checks": checks,
            "raw": raw, "error": error, "metrics": turn_metrics(events, [], elapsed),
            "input_characters": sum(len(m["content"]) for m in messages), "semantic_review": "not_run"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--provider", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--variant", choices=("legacy", "ids", "compact"), default="compact")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20:
        parser.error("--repeat 必須為 1 至 20")
    request, packet, evidence = load_log(args.log)
    reports = []
    for trial in range(1, args.repeat + 1):
        row = asyncio.run(run_once(request, packet, evidence, args.provider, args.variant))
        row["trial"] = trial
        reports.append(row)
        print("trial", trial, "structural_pass", row["structural_pass"], row["metrics"])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"run": report_metadata(args.provider), "question": packet["玩家原問"],
        "trials": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("此工具以可回答問題的固定證據比較；語意驗收尚未執行。")
    if not all(row["structural_pass"] for row in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
