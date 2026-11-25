"""Exercise the shared presentation contract in the actual Graph requests.

Scripted replies verify wiring and content preservation, not a live LLM's
ability to follow the format instruction.
"""
import asyncio
import os
from unittest.mock import patch

from agent import answer
from answering import ANSWER_MARKDOWN_GUIDANCE, ANSWER_SCHEMA
from answer_quality import REVIEW_SCHEMA
from guide import CATALOG, published_passages
from tests.test_bounded_agent import select, response, rules, scripted, supported
from tests.test_answer_quality import review
from tests.test_guide import OfflineTests


class MarkdownContractTests(OfflineTests):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"EVENT_SUPPORT_AGENT_STOP_GATE": "auto", "EVENT_SUPPORT_AGENT_ANSWER_REVIEW": "auto"})
        env.start()
        self.addCleanup(env.stop)

    def check_contract(self, messages, tools, kwargs, schema):
        self.assertEqual(tools, [])
        self.assertEqual(kwargs["response_schema"], schema)
        self.assertIn(ANSWER_MARKDOWN_GUIDANCE, messages[0]["content"])
        self.assertIn(ANSWER_MARKDOWN_GUIDANCE,
            schema["$defs"][schema["properties"]["results"]["items"]["$ref"].split("/")[-1]]["properties"]["answer"]["description"])

    def test_initial_and_revised_markdown_through_existing_graph(self):
        q = "請說明活動的操作步驟。"
        rows = [r for r in published_passages(CATALOG.current) if r["文件ID"] == "launch"]
        cites = [(r["段落ID"], r["原文"]) for r in rows]
        draft_text = "參加方式：\n\n1. " + rows[0]["原文"] + "\n2. " + rows[1]["原文"]
        final_text = draft_text + "\n3. " + rows[2]["原文"]

        def initial(provider, messages, tools, **kwargs):
            self.check_contract(messages, tools, kwargs, ANSWER_SCHEMA)
            return response(supported(q, draft_text, *cites[:2]))

        def revised(provider, messages, tools, **kwargs):
            self.check_contract(messages, tools, kwargs, REVIEW_SCHEMA)
            return review("revise", supported(q, final_text, *cites), missing=[cites[2][0]])

        model = scripted(select(rules(mode="overview")), initial, revised)
        events = []
        text, trace = asyncio.run(answer("A", "ollama", [], q, model,
                                    lambda title, data: events.append((title, data))))
        self.assertEqual(text, final_text)
        self.assertEqual(len(trace), 1)
        self.assertEqual(model.call_count, 3)
        self.assertEqual([d["節點"] for t, d in events if t == "Python Harness 節點"],
                         ["select", "execute", "assess", "stop_gate", "finalize"])
        self.assertEqual(dict(events)["Python 回答品質審查結果"]["判定"], "revised")
        final = dict(events)["Python 檢查回答依據"]
        self.assertEqual([c["id"] for c in final["子問題"][0]["採用段落"]],
                         [c[0] for c in cites])
