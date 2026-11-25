# 系統架構與執行流程

[返回 README](../README.md) · [資料與工具](DATA_AND_TOOLS.md) · [設計決策](DESIGN_DECISIONS.md)

## 1. 設計目標

LLM 負責理解意圖、選擇工具與生成回答；Python 負責工具權限、參數、業務條件、證據與停止預算。實際採用 LangGraph 1.2.24 編排既有 Agent，不是完整 Pydantic AI Agent、Codex SDK 或 Claude Agent SDK。參考來源及採用範圍見 [REFERENCES](REFERENCES.md)。

## 2. 模組責任

| 模組 | 責任 |
| --- | --- |
| app.py / starter_questions.py | UI、依玩家與 provider 分隔對話、快速提問、背景執行及工程檢視 |
| agent.py | Graph 節點、狀態轉移、answer 入口 |
| agent_runtime.py | 每輪 TurnState、預算、工具、證據快取及降級 |
| tool_selection.py | 原生工具呼叫正規化 |
| model_client.py | LangChain ChatOpenAI / ChatOllama、原生 Structured Output、紀錄 |
| request_context.py | 最近 8 則訊息；上一輪可信工具檔期及商品參照 |
| backend.py | Tool Registry、參數/帳號驗證、證據轉換及適用條件 |
| guide.py / vector_search.py | 公開公告、版本、BM25、hybrid、商城及進度契約 |
| database.py / player_progress.py | PostgreSQL 連線、示範初始化、參數化唯讀查詢 |
| answering.py | 結構化回答、引用、部分回答、Markdown 及參考資料 |
| answer_quality.py | 審查候選、修訂及保護既有回答 |
| eval_rag.py / tests | 檢索評測、腳本回歸、實機重播 |

## 3. Agent 生命週期

LangGraph StateGraph / MessagesState 管理每輪狀態與訊息；獨立節點透過 conditional edges 路由，不直接操作 Pydantic Graph。每輪建立獨立 registry，ToolNode 並行執行主機核准且去重的呼叫。

1. Select：首次、補查及空回應重試均從同一 registry 取得工具定義，透過 ChatOpenAI/ChatOllama.bind_tools 使用原生 tool_calls。
2. Prepare tools：主機檢查名稱、參數、檔期、可信指涉、計算依據與預算；相同正規化參數在本輪共用結果。ToolNode 只接收已核准的唯一呼叫。
3. ToolNode：並行執行獨立唯讀工具；需要既有證據的運算不得使用尚未完成的同批結果。
4. Collect：按原呼叫順序合併結果、查詢紀錄、適用條件及證據；模型上下文去重，保留合法 tool_call_id 對應。
5. Assess：以 with_structured_output(method="json_schema") 獨立生成回答；Pydantic 驗證欄位型別與狀態，保留逐需求引用驗證。answer 時捨棄無作用的 missing，retrieve 必須提供原因。
6. Stop Gate：依證據與預算決定補查或停止，維持既有矛盾拒答有限複查。格式修復回饋實際驗證原因及原草稿。
7. Finalize：可選的一次品質審查；修訂失敗保留可驗證草稿，依子問題產生回答與參考資料。

| 上限 | 數值 |
| --- | ---: |
| 工具輪數 | 3 |
| 單批工具 | 6 |
| 每次玩家請求工具嘗試 | 12（含無效參數、快取命中） |
| 回答格式修復 | 全輪 1 次 |
| 額外矛盾停止檢查 | 1 次 |
| 最終審查 | 1 次 |
| LangGraph recursion_limit | 24 |

Select 對暫時性傳輸錯誤使用 RetryPolicy（至多 2 次嘗試）；其他業務結果不是傳輸重試。原生工具選擇與回答分開，常見成功路徑兩次模型步驟，不承諾所有模型及問題都只有兩次。尚未證實遷移後實機延遲或語意品質改善。

## 4. 信任邊界與限制

工具均唯讀或純運算；進度工具不接受任意帳號 ID。demo 帳號由 UI 選擇，不是正式登入驗證。RAG 不包含玩家資料，公開與本人紀錄各有來源。PostgreSQL 的 player_event_progress 與 event_support_agent_embeddings 分表；demo 共用 DSN，尚未配置獨立最小權限角色。商品查無是查詢事實，不等於查明交易失敗原因。計算來源 ID 有驗證，但並未保證每個運算元都語意受支持。引用驗證是本輪 ID、逐字摘錄與狀態核對，非完整語意證明。

品質審查觸發條件為重複需求、未引用同文件章節或未採用的工具組；候選不等於必要證據，可能有過度審查問題。保留 auto/off 作實測比較，不以單一案例修改流程。

工程檢視顯示 Graph 節點、模型請求/回傳、用量、工具、證據與停止原因。正式系統需補認證、授權、日誌遮罩及保留政策。正文透過共用 Markdown 指引與 Streamlit 渲染；沒有新增排版模型呼叫或正規表示式重排。
