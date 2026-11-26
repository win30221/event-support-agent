"""Typed, read-only local function tools; application code owns authorization."""
import os
import ast
import hashlib
import json
import operator
import re
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from functools import partial
from concurrent.futures import Future
from threading import Lock
from typing import Callable

from guide import CATALOG, mentions_identifier, progress, search_rules, shop_catalog, shop_item


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    handler: Callable

    def definition(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.properties,
                           "required": list(self.properties), "additionalProperties": False},
        }}


EDITION = {"type": "string", "enum": ["current", "previous", "context", "unavailable", *CATALOG.editions],
           "description": "本期、上期、前一輪唯一檔期或明確 ID；查不到的指定檔期用 unavailable，不得替換成別期。"}
TEXT = {"type": "string"}
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
             ast.Div: operator.truediv}


def calculate(expression):
    """Evaluate bounded decimal arithmetic without eval, names or function calls."""
    if not isinstance(expression, str) or len(expression) > 120 or not re.fullmatch(r"[\d.()+*/ \t-]+", expression):
        raise ValueError("只允許數字、括號和四則運算。")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 40:
            raise ValueError("運算式過長。")

        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                value = Decimal(str(node.value))
            elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
                value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            elif isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
                value = OPERATORS[type(node.op)](visit(node.left), visit(node.right))
            else:
                raise ValueError("不支援此運算。")
            if not value.is_finite() or abs(value) > Decimal("1000000000000"):
                raise ValueError("運算數值超出範圍。")
            return value

        value = visit(tree.body)
        return format(value.normalize(), "f") if value else "0"
    except (SyntaxError, DecimalException, OverflowError) as error:
        raise ValueError("運算式無效。") from error


class QueryMemo:
    """Request-scoped single-flight cache for identical retrieval subqueries."""
    def __init__(self):
        self.lock = Lock()
        self.pending = {}

    def get(self, key, compute):
        with self.lock:
            leader = key not in self.pending
            future = self.pending.setdefault(key, Future())
        if leader:
            try:
                future.set_result(compute())
            except BaseException as error:
                future.set_exception(error)
                with self.lock:
                    self.pending.pop(key, None)
        return future.result()


# 工具登錄表同時定義模型可見契約及後端處理器；帳號綁定於建立時的 player。
class ToolRegistry:
    """A new registry per player request. No tool accepts an account ID."""

    def __init__(self, player, *, provider=None, embedding=None):
        from embedding_client import resolve_embedding
        self.retrieval = QueryMemo()
        self.embedding = embedding or resolve_embedding(provider)
        self.retriever = os.getenv("EVENT_SUPPORT_AGENT_RETRIEVAL", "bm25")
        self._tools = {tool.name: tool for tool in (
            Tool("find_rules", "查單期公開公告。overview：核心參加、玩法及兌換流程；search：具體規則、條件或代碼，包含核心背景與相關細節；compare：單期全部有效公告，供完整盤點。跨期分別查詢。只選符合資料需求的一種模式。",
                 {"edition": EDITION, "query": {"type": "string", "description": "搜尋文字；保留明示對象、日期、條件及代碼。"},
                  "mode": {"type": "string", "enum": ["search", "overview", "compare"]}},
                 search_rules),
            Tool("list_shop_items", "讀單期完整公開商城清單、各商品名稱、價格與用途。不依個人餘額過濾。",
                 {"edition": EDITION}, shop_catalog),
            Tool("find_shop_item", "核對單一指定品名、別名或 SKU 是否存在。多商品可個別呼叫，或查完整清單。not_found 不代表其他商品。",
                 {"edition": EDITION, "name": {"type": "string", "description": "玩家原用的完整單一品名，或可信上下文中的名稱；勿自行縮短修飾詞或合併多個名稱。"}}, shop_item),
            Tool("get_my_event_progress", "只讀目前登入者的活動餘額、紀錄當日場次、參加狀態。不含其他帳號或累計遊玩天數、訂單明細。",
                 {"edition": EDITION}, partial(progress, player)),
            Tool("calculate", "執行確定性四則運算；輸入必須以本輪已取得資料為依據。運算結果不是獨立的遊戲事實。",
                 {"expression": TEXT, "source_ids": {"type": "array", "items": TEXT}}, calculate),
        )}

    def definitions(self, names=None):
        return [tool.definition() for name, tool in self._tools.items()
                if names is None or name in names]

    # 信任邊界：拒絕未知欄位、無效檔期、未核對的商品指涉與非本輪計算來源。
    def validate(self, name, arguments, context, evidence):
        if not isinstance(name, str) or name not in self._tools or not isinstance(arguments, dict):
            raise ValueError("未知工具或參數不是 JSON 物件。")
        tool = self._tools[name]
        args = dict(arguments)
        if args.keys() != tool.properties.keys():
            raise ValueError("工具參數不符合契約；不可加入未宣告欄位。")
        for key, value in args.items():
            spec = tool.properties[key]
            if spec["type"] == "string":
                if not isinstance(value, str) or len(value) > 2000:
                    raise ValueError(f"工具參數 {key} 格式不符。")
                if "enum" in spec and value not in spec["enum"]:
                    raise ValueError(f"工具參數 {key} 超出允許範圍。")
                args[key] = value.strip()
            elif not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"工具參數 {key} 必須是文字陣列。")
        if name == "calculate":
            ids = args["source_ids"]
            if not ids or len(ids) > 8 or len(ids) != len(set(ids)) or any(i not in evidence for i in ids):
                raise ValueError("計算依據必須是本輪已驗證的來源 ID。")
            args["source_ids"] = sorted(ids)
            return args
        args["edition"] = context.resolve_edition(args["edition"])
        if name == "find_shop_item" and not context.grounded_product(args["name"], args["edition"]):
            raise ValueError("商品參數無法從玩家原問或可信上下文核對；請重新選擇資料或確認指涉。")
        if name == "find_rules":
            if args["mode"] == "search" and not args["query"]:
                raise ValueError("特定規則檢索需要非空查詢。")
            if args["mode"] in ("overview", "compare"):
                args["query"] = ""  # These modes ignore query text.
            else:
                if args["query"] != context.question.strip() and context.question.strip():
                    args["alternate_queries"] = [context.question.strip()]
        return args

    # 執行層使用已正規化的參數；公告查詢額外附上同一期活動背景。
    def execute(self, name, arguments):
        """Execute only normalized arguments produced by validate()."""
        tool = self._tools.get(name)
        if tool is None or not isinstance(arguments, dict):
            raise ValueError("未知工具。")
        args = dict(arguments)
        if name == "calculate":
            if args.keys() != {"expression", "source_ids"}:
                raise ValueError("運算參數不符。")
            return {"expression": args["expression"], "result": calculate(args["expression"]),
                    "source_ids": args["source_ids"]}
        alternates = args.pop("alternate_queries", []) if name == "find_rules" else []
        if args.keys() != tool.properties.keys() or args.get("edition") not in CATALOG.editions:
            raise ValueError("工具參數未通過驗證。")
        for key, value in args.items():
            if not isinstance(value, str):
                raise ValueError("工具參數型別不符。")
        if name == "find_rules":
            if args["mode"] not in tool.properties["mode"]["enum"]:
                raise ValueError("公告模式不符。")
            return tool.handler(**args, alternate_queries=alternates, include_overview=True,
                                retrieve_once=self.retrieval.get, retriever=self.retriever,
                                embedding=self.embedding)
        return tool.handler(**args)


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def result_id(prefix, value):
    return prefix + "/" + hashlib.sha256(compact_json(value).encode()).hexdigest()[:20]


# 將實際工具結果轉成有來源 ID 的證據；適用條件由玩家本輪原問核對。
def evidence_for(name, args, result, question):
    """Create citation-bearing evidence and public context references."""
    edition = args.get("edition", "")
    rows, refs, withheld = [], [], []
    source = {"工具": name, "檔期": edition, "段落": rows}
    if name == "find_rules":
        for passage in result:
            requirements = passage.get("適用條件", [])
            missing = [condition["label"] for condition in requirements if not any(
                mentions_identifier(question, value) for value in condition["mentions_any"])]
            if missing:
                withheld.append({"段落ID": passage["段落ID"], "缺少": missing})
                continue
            row = {field: passage[field] for field in ("段落ID", "檔期", "文件ID", "文件", "章節", "來源", "發布日", "原文")}
            if requirements:
                row["適用條件"] = requirements
            if "檢索角色" in passage:
                row["檢索角色"] = passage["檢索角色"]
            rows.append(row)
        # Context candidates come only from returned, applicable public text.
        for item in shop_catalog(edition)["items"]:
            if any(mentions_identifier(row["原文"], item["name"]) for row in rows):
                refs.append(item["id"])
        source["狀態"] = ("no_match" if not rows else
                          "context_only" if all(row.get("檢索角色") == "activity_context" for row in rows)
                          else "found")
    elif name in ("list_shop_items", "find_shop_item"):
        source["商城"] = result
        status, title = result["status"], result["title"] or edition
        source["狀態"] = status
        if status in ("listed", "found"):
            for item in result["items"]:
                refs.append(item["id"])
                rows.append({"段落ID": f"{edition}/shop-items/{item['id']}", "來源": f"{title}商城清單",
                             "原文": f"「{item['name']}」需要 {item['price']} 枚星星幣。"
                                     + (f" {item['description']}" if item.get("description") else "")})
        requested = args.get("name", "")
        if status == "not_found":
            text = f"「{title}」商城清單查無「{requested}」。"
        elif status == "ambiguous":
            text = f"「{title}」商城查詢「{requested}」有多個或不確定的候選：" + "、".join(i["name"] for i in result["items"]) + "；尚未確認品項。"
        elif status == "unavailable":
            text = f"目前無法確認「{title}」的商城清單。"
        elif status == "need_name":
            text = "缺少指定商品名稱。"
        elif status == "listed":
            text = f"「{title}」商城清單共 {len(result['items'])} 件商品。"
        else:
            text = ""
        if text:
            row = {"段落ID": result_id(f"{edition}/shop-status", [status, requested]),
                   "來源": f"{title}商城查詢狀態", "原文": text, "資料狀態": status}
            # Provenance from the successful local lookup, never from model
            # answer text. A missing catalogue or ambiguous result is distinct.
            if name == "find_shop_item" and status == "not_found" and requested:
                row.update({"查詢對象": requested, "檔期": edition})
            rows.append(row)
    elif name == "get_my_event_progress":
        source["本人紀錄"] = result
        source["狀態"] = "unavailable" if "狀態" in result else "found"
        row = {"段落ID": f"{edition}/my-progress", "來源": f"目前登入帳號的活動紀錄（{edition}）",
               "原文": json.dumps(result, ensure_ascii=False, indent=2)}
        # 查無進度紀錄是查詢狀態，不是餘額、參加狀態或其他個人事實的依據。
        # 與商城 unavailable 使用同一既有驗證路徑，保留可信原文、拒絕混合推論。
        if source["狀態"] == "unavailable":
            row["資料狀態"] = "unavailable"
        rows.append(row)
    else:
        rows.append({"段落ID": result_id("calculation", result), "來源": "依本輪資料執行四則運算",
                     "原文": f"{result['expression']} = {result['result']}",
                     "運算依據": result["source_ids"]})
        source["狀態"] = "computed"
    return source, refs, withheld
