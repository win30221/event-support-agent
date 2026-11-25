"""Published event announcements and the demo player's read-only progress."""
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import date
from math import log1p
from pathlib import Path

from player_progress import read_progress


# 從示範資料固定服務日期與檔期；不以真實當天日期改變測試語料。
class EventCatalog:
    """Resolve time-relative slots against a reproducible date in the fixture."""

    def __init__(self, data):
        self.name = data["name"]
        self.as_of = date.fromisoformat(data["as_of"])
        self.editions = {}
        self.current = None
        self.previous = None
        previous_end = None
        seen = set()
        for row in sorted(data["editions"], key=lambda row: row["start"]):
            key = row["id"]
            start = date.fromisoformat(row["start"])
            end = date.fromisoformat(row["end"])
            if not isinstance(key, str) or not key or key in {"current", "previous", "context", "unavailable", "本期", "上期"}:
                raise ValueError("檔期需要獨立的 ID。")
            if key in seen or start > end or (previous_end is not None and start <= previous_end):
                raise ValueError("檔期 ID 必須唯一，起訖日期須有效且各期不得重疊。")
            seen.add(key)
            previous_end = end
            if start > self.as_of:
                continue
            self.editions[key] = row
            if end < self.as_of:
                self.previous = key
            else:
                self.current = key

    def resolve(self, reference):
        if reference in ("current", "本期"):
            return self.current
        if reference in ("previous", "上期"):
            return self.previous
        return reference if reference in self.editions else None

    def index(self):
        editions = []
        for key, row in self.editions.items():
            headings = {}
            for passage in published_passages(key, self):
                headings.setdefault(passage["文件"], []).append(passage["章節"])
            editions.append({**{k: row[k] for k in ("id", "title", "start", "end")},
                             "公告章節": headings})
        return editions


CATALOG = EventCatalog(json.loads(Path(__file__).with_name("events.json").read_text(encoding="utf-8")))


def terms(text):
    """Keep literal issue codes intact; use overlapping character pairs for Chinese."""
    text = unicodedata.normalize("NFKC", text).casefold()
    tokens = Counter()
    for word in re.findall(r"[a-z0-9]+(?:[-:][a-z0-9]+)*|[㐀-鿿]+", text):
        if word.isascii() or len(word) == 1:
            tokens[word] += 1
        else:
            tokens.update(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


def mentions_identifier(text, identifier):
    """Match a configured literal, including full-width spelling and adjacent Chinese text."""
    text = unicodedata.normalize("NFKC", text).casefold()
    name = unicodedata.normalize("NFKC", identifier).casefold().strip()
    if name.isascii():
        return re.search(rf"(?<![a-z0-9_-]){re.escape(name)}(?![a-z0-9_-])", text) is not None
    return name in text


# 公開索引僅取選定檔期、已發布且有效的公告版本，不包含私人玩家資料。
def published_passages(edition, catalog=CATALOG):
    """One public paragraph per chunk. Select the latest published revision of each notice."""
    key = catalog.resolve(edition)
    if key is None:
        return []
    row = catalog.editions[key]
    launch = {"id": "launch", "title": row["title"] + "開幕公告", "published": row["start"],
              "sections": row["sections"]}
    documents = {"launch": launch}
    for notice in sorted(row.get("notices", []), key=lambda item: item["published"]):
        if date.fromisoformat(notice["published"]) <= catalog.as_of:
            doc_id = notice["id"]
            if doc_id == "launch" or (doc_id in documents and documents[doc_id]["published"] == notice["published"]):
                raise ValueError("附加公告須有獨立文件 ID，同一文件同日只能發布一個版本。")
            documents[doc_id] = notice

    label = row["title"]
    if key == catalog.current:
        label = "本期"
    elif key == catalog.previous:
        label = "上期"

    passages = []
    for doc in documents.values():
        prefix = f"{label}公告" if doc["id"] == "launch" else doc["title"]
        for heading, section in doc["sections"].items():
            passage = {
                "段落ID": f"{key}/{doc['id']}/{heading}", "檔期": key,
                "文件ID": doc["id"], "文件": doc["title"], "發布日": doc["published"],
                "章節": heading, "來源": f"{prefix}／{heading}",
            }
            if isinstance(section, str):
                passage["原文"] = section
            else:
                if not isinstance(section, dict):
                    raise ValueError(f"公告段落需要原文：{passage['段落ID']}")
                body, requirements = section.get("text"), section.get("requires")
                if not isinstance(body, str) or not isinstance(requirements, list) or not requirements:
                    raise ValueError(f"公告段落的原文或適用條件無效：{passage['段落ID']}")
                for condition in requirements:
                    if not isinstance(condition, dict):
                        raise ValueError(f"公告適用條件的格式不符：{passage['段落ID']}")
                    label, aliases = condition.get("label"), condition.get("mentions_any")
                    if (not isinstance(label, str) or not label.strip() or not isinstance(aliases, list)
                            or not aliases or not all(isinstance(v, str) and v.strip() for v in aliases)):
                        raise ValueError(f"公告適用條件需要標籤及可辨識的名稱或代碼：{passage['段落ID']}")
                passage.update({"原文": body, "適用條件": requirements})
            passages.append(passage)
    return passages


# 詞彙 BM25 排名衡量查詢相關性，排名本身並不是答案正確性的證明。
def rank_passages(query, passages, limit=3):
    """BM25 within the selected edition; return only passages with a positive match."""
    words = set(terms(query))
    if not words or not passages:
        return []
    documents = [terms(" ".join((p["文件"], p["章節"], p["原文"]))) for p in passages]
    document_frequency = Counter(word for doc in documents for word in doc.keys() & words)
    average_length = sum(doc.total() for doc in documents) / len(documents) or 1
    scores = []
    k1, b = 1.2, 0.75
    for passage, doc in zip(passages, documents):
        norm = k1 * (1 - b + b * doc.total() / average_length)
        score = 0.0
        for word in sorted(doc.keys() & words):
            df = document_frequency[word]
            idf = log1p((len(documents) - df + 0.5) / (df + 0.5))
            score += idf * doc[word] * (k1 + 1) / (doc[word] + norm)
        if score > 0:
            scores.append((score, passage))

    scores.sort(key=lambda item: item[0], reverse=True)
    return [{**passage, "BM25": round(score, 3)} for score, passage in scores[:limit]]


def search_rules(query, edition="本期", mode="search", catalog=CATALOG, *, retriever=None,
                 alternate_queries=(), include_overview=False, retrieve_once=None):
    """Rank details inside one edition; optionally attach its canonical activity context.

    The raw retrieval benchmark can still measure lexical/hybrid ranking alone.
    Agent retrieval supplies the short, canonical launch document independently
    of keyword overlap, without crossing editions or expanding private scope.
    """
    passages = published_passages(edition, catalog)
    if mode == "compare":
        return passages

    launch = [p for p in passages if p["文件ID"] == "launch"]
    if mode == "overview":
        return launch
    if mode not in ("search", "eligibility"):
        raise ValueError("未知的公告檢索方式。")
    if mode == "eligibility":
        required = launch
    elif include_overview:
        required = [{**p, "檢索角色": "activity_context"} for p in launch]
    else:
        required = []
    candidates = [p for p in passages if p["文件ID"] != "launch"] if required else passages
    if not candidates or not query.strip():
        return required
    method = retriever or os.getenv("EVENT_SUPPORT_AGENT_RETRIEVAL", "bm25")
    def retrieve(text):
        if method == "bm25":
            return rank_passages(text, candidates)
        if method == "hybrid":
            from vector_search import hybrid_search
            lexical = rank_passages(text, candidates, limit=6)
            return hybrid_search(text, candidates, passages, lexical, limit=3)
        raise ValueError("EVENT_SUPPORT_AGENT_RETRIEVAL 可使用 bm25 或 hybrid。")

    queries = list(dict.fromkeys(text.strip() for text in (query, *alternate_queries) if text.strip()))
    # 相同原問常被多個改寫工具帶入；同一請求共享相同候選範圍的檢索。
    scope = (method, tuple(p['段落ID'] for p in candidates))
    rankings = [retrieve_once((scope, text), lambda text=text: retrieve(text))
                if retrieve_once is not None else retrieve(text) for text in queries]
    if len(rankings) == 1:
        return required + rankings[0]
    # Reciprocal rank fusion: scores from different queries or retrievers
    # are not directly comparable. Keep a bounded, deduplicated candidate set.
    rows, scores = {}, Counter()
    for ranking in rankings:
        for rank, row in enumerate(ranking, start=1):
            identifier = row["段落ID"]
            rows.setdefault(identifier, row)
            scores[identifier] += 1 / (60 + rank)
    ordered = sorted(scores, key=lambda identifier: -scores[identifier])[:6]
    return required + [{**rows[identifier], "多查詢融合分數": round(scores[identifier], 6)}
                       for identifier in ordered]


def normalize_item_name(name):
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


# 完整清單、空清單及缺少清單有不同語意；不可將資料缺失視為商品不存在。
def shop_catalog(edition="本期", catalog=CATALOG):
    """Treat a declared list as that edition's complete catalogue, including an empty list."""
    key = catalog.resolve(edition)
    row = catalog.editions.get(key, {})
    result = {"edition": key, "title": row.get("title"), "status": "unavailable", "items": []}
    items = row.get("shop_items")
    if not isinstance(items, list):
        return result

    ids, public_items = set(), []
    for item in items:
        if not isinstance(item, dict):
            return result
        identifier, name, price = item.get("id"), item.get("name"), item.get("price")
        aliases = item.get("aliases", [])
        description = item.get("description", "")
        if (not isinstance(identifier, str) or not identifier.strip() or identifier in ids
                or not isinstance(name, str) or not name.strip()
                or type(price) is not int or price < 0
                or not isinstance(aliases, list)
                or not all(isinstance(alias, str) and alias.strip() for alias in aliases)
                or not isinstance(description, str)):
            return result
        ids.add(identifier)
        public_items.append({"id": identifier, "name": name, "price": price,
                             "aliases": aliases, "description": description})
    result.update(status="listed", items=public_items)
    return result


def shop_item(name, edition="本期", catalog=CATALOG):
    listing = shop_catalog(edition, catalog)
    if listing["status"] == "unavailable":
        return listing

    query = normalize_item_name(name)
    if not query:
        return {**listing, "status": "need_name", "items": []}

    exact, similar = [], []
    for item in listing["items"]:
        names = [normalize_item_name(value) for value in (item["id"], item["name"], *item.get("aliases", []))]
        if query in names:
            exact.append(item)
        elif any(query in candidate for candidate in names):
            similar.append(item)

    if len(exact) == 1:
        return {**listing, "status": "found", "items": exact}
    matches = exact or similar
    return {**listing, "status": "ambiguous" if matches else "not_found", "items": matches}


# 保留既有工具回傳契約；資料庫讀取失敗交由 Agent 工具錯誤處理，不假裝查無紀錄。
def progress(player, edition="本期", catalog=CATALOG):
    key = catalog.resolve(edition)
    row = read_progress(player, key) if key is not None else None
    if row is None:
        return {"檔期": key, "狀態": "沒有這個檔期的玩家進度紀錄"}
    recorded, coins, rounds, joined = row
    recorded = recorded.isoformat()
    joined_label = "已參加本期" if key == catalog.current else "已參加該期"
    rounds_label = "今日已計入場次" if recorded == catalog.as_of.isoformat() else "紀錄當日已計入場次"
    return {"檔期": key, "紀錄日期": recorded, "星星幣": coins, rounds_label: rounds, joined_label: joined}
