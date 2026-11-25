"""Request-scoped context. Prior model prose is never a source of verified facts."""
from dataclasses import dataclass

from guide import CATALOG, mentions_identifier, normalize_item_name, shop_catalog


@dataclass(frozen=True)
class RequestContext:
    question: str
    history: tuple
    previous_editions: tuple
    product_refs: tuple
    previous_user: str

    @classmethod
    # 近期歷史只供解析指涉；可信商品及檔期候選來自上一輪實際工具 trace。
    def build(cls, question, history):
        turns = [turn for turn in history if turn.get("role") in ("user", "assistant")
                 and isinstance(turn.get("content"), str)][-8:]
        recent = next((turn for turn in reversed(turns) if turn["role"] == "assistant"), {})
        previous_user = next((turn["content"] for turn in reversed(turns) if turn["role"] == "user"), "")
        periods, refs = [], []
        for call in recent.get("trace") or []:
            args = call.get("參數") or {}
            edition = args.get("edition")
            if edition not in CATALOG.editions:
                continue
            if edition not in periods:
                periods.append(edition)
            # Only IDs emitted by the local public tools are accepted. Recover
            # canonical names and aliases from the catalogue, not model prose.
            ids = call.get("商品參照") or []
            for item in shop_catalog(edition)["items"]:
                if item["id"] in ids:
                    ref = {"edition": edition, "id": item["id"], "name": item["name"],
                           "aliases": item["aliases"]}
                    if ref not in refs:
                        refs.append(ref)
        messages = tuple({"role": turn["role"], "content": turn["content"]} for turn in turns)
        return cls(question, messages, tuple(periods), tuple(refs), previous_user)

    def resolve_edition(self, reference):
        if not isinstance(reference, str):
            raise ValueError("edition 必須是檔期文字。")
        if reference == "context":
            if len(self.previous_editions) != 1:
                raise ValueError("前一輪沒有唯一可延續的檔期，請選明確檔期。")
            return self.previous_editions[0]
        key = CATALOG.resolve(reference)
        if key is None:
            raise ValueError(f"查不到指定檔期 {reference}；不得以其他檔期替代。")
        return key

    # 核對原問、上一輪玩家原句及可信商品參照，防止模型自行替換商品。
    def grounded_product(self, name, edition):
        if not isinstance(name, str) or not name.strip():
            return False
        if mentions_identifier(self.question, name) or mentions_identifier(self.previous_user, name):
            return True
        wanted = normalize_item_name(name)
        for ref in self.product_refs:
            if ref["edition"] == edition and wanted in {
                normalize_item_name(value) for value in (ref["id"], ref["name"], *ref["aliases"])
            }:
                return True
        # A canonical name/ID can be resolved from an exact registered alias
        # in the user's current message, not inferred from a model rewrite.
        for item in shop_catalog(edition)["items"]:
            names = (item["id"], item["name"], *item["aliases"])
            if wanted in {normalize_item_name(value) for value in names} and any(
                    mentions_identifier(self.question, value) for value in names):
                return True
        return False

    def public_summary(self):
        return {"活動": CATALOG.name, "服務日期": CATALOG.as_of.isoformat(),
                "檔期": [{k: row[k] for k in ("id", "title", "start", "end")}
                         for row in CATALOG.editions.values()],
                "本期": CATALOG.current, "上期": CATALOG.previous,
                "前一輪檔期": list(self.previous_editions),
                "前一輪可信商品候選": list(self.product_refs)}
