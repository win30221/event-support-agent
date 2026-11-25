"""UI quick prompts are separate from evaluation questions."""


def starter_cases(cases, player, limit=4):
    """Show featured scenarios, respecting any account-specific examples."""
    return [
        case for case in cases.values()
        if case.get("starter_label")
        and case.get("starter_player", player) == player
    ][:limit]


# 可見問句同時是送出的問句；評測 fixture 的完整 question 不參與 UI 替換。
def render_starter_questions(ui, cases, player, chat_key):
    """Submit exactly the visible question, not the longer evaluation fixture."""
    selected = None
    examples = starter_cases(cases, player)
    for start in range(0, len(examples), 2):
        cols = ui.columns(2, gap="small")
        for col, example in zip(cols, examples[start:start + 2]):
            question = example["starter_label"]
            if col.button(
                question,
                key=f"starter-{chat_key}-{example['id']}",
                use_container_width=True,
            ):
                selected = question
    return selected
