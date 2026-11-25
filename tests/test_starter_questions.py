"""Quick-question UI contract, independent of model/provider availability."""
import json
import unittest
from pathlib import Path

from starter_questions import render_starter_questions, starter_cases


class ClickableUI:
    def __init__(self, clicked=None):
        self.clicked = clicked
        self.buttons = {}

    def columns(self, count, **kwargs):
        return [self] * count

    def button(self, label, *, key, **kwargs):
        if key in self.buttons:
            raise AssertionError("Duplicate widget key")
        self.buttons[key] = label
        return key == self.clicked


class StarterQuestionTests(unittest.TestCase):
    def setUp(self):
        rows = json.loads((Path(__file__).resolve().parents[1] / "rag_cases.json").read_text(encoding="utf-8"))
        self.cases = {row["label"]: row for row in rows}

    def test_every_rendered_button_submits_its_visible_text(self):
        for player in ("A", "B", "C"):
            for provider in ("ollama", "openai"):
                chat_key = f"chat-{player}-{provider}"
                ui = ClickableUI()
                self.assertIsNone(render_starter_questions(ui, self.cases, player, chat_key))
                self.assertEqual(len(ui.buttons), 4)
                for button_key, displayed in ui.buttons.items():
                    with self.subTest(player=player, provider=provider, button=button_key):
                        clicked = ClickableUI(button_key)
                        actual = render_starter_questions(clicked, self.cases, player, chat_key)
                        self.assertEqual(actual, displayed)
                        self.assertEqual(clicked.buttons, ui.buttons)

    def test_all_accounts_show_the_same_four_questions(self):
        expected = ["first-night", "current-balance", "shop-catalog", "nov-weekend"]
        for player in ("A", "B", "C"):
            with self.subTest(player=player):
                self.assertEqual([row["id"] for row in starter_cases(self.cases, player)], expected)

    def test_evaluation_question_does_not_replace_displayed_question(self):
        cases = {"different": {"id": "different", "starter_label": "短問句？",
                              "question": "完整的評測問題？"}}
        ui = ClickableUI("starter-chat-A-ollama-different")
        result = render_starter_questions(ui, cases, "A", "chat-A-ollama")
        self.assertEqual(result, cases["different"]["starter_label"])
        self.assertNotEqual(result, cases["different"]["question"])
