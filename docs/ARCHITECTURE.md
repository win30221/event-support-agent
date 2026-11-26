# 系統架構與執行流程

[返回 README](../README.md) · [資料與工具](DATA_AND_TOOLS.md) · [設計決策](DESIGN_DECISIONS.md)


## Single Agent Loop 分支（目前預設）

本分支 `refactor/single-agent-loop` 從 `refactor/intent-task-simple` 的 8b6fdd0 建立。預設 `agent.answer` 改為 `single_agent.py`：同一模型角色以 native tool_calls 取得工具結果，或直接輸出最終回答。保留 LangGraph ToolNode、ToolRegistry、資料庫、RAG、參數驗證、快取、適用條件及引用驗證；不呼叫原分類、Audit、獨立證據評估、停止審查或回答品質審查。舊 `intent_answer`、`legacy_answer` 與歷史題庫保留，以下舊流程章節只供歷史參考。

工具輪數 3、單批 6、累計嘗試 12、模型請求 5（含最多一次格式修正）、Ollama 每次 num_predict 2048。這是本專案實驗上限，不是官方推薦通用數值。原生工具結果以短 ID 回傳，calculate 的來源 ID 經主機對照。正常路徑以同一 Agent 的原生終止工具 submit_answer（JSON Schema）提交 results，移除 action/missing，將 unavailable 合併至 no_evidence；不計入資料工具預算。終止工具必須單獨提交才可能被接受；混合批次保留全部呼叫 ID，資料工具各自依原有權限、參數及預算核准，終止提案回傳拒絕結果而不執行、不計入資料工具嘗試。整批封套不合法、截斷或超額仍不執行。只有回傳契約失敗、重複查詢停止或達到預算時，使用無工具的供應商 JSON Schema 結構化回答，仍受原有模型呼叫上限及一次格式修正限制。主機驗證引用、運算依據及格式，不代表語意完整性。帳號仍由主機選定，不接受任意帳號 ID；明示 other/unclear 拒絕，但模型誤標 self、漏查商品或漏答的語意問題尚未被確定性驗證。商品依賴順序由 Agent 指引，不再有舊的逐需求槽位強制控制。正式認證與語意驗收仍需補足。

工程觀測新增每次模型的配置/回報名稱、配置 Context（非實際伺服器容量）、訊息與工具結果字元數、供應商 Token、停止原因及耗時；新增實際資料工具耗時及公開 RAG 候選排序、分數、採用狀態。缺少供應商資訊時不猜測；不將字元數換算為 Token，不新增模型請求或調整檢索策略。主機格式修復與停止指令只附加於該次 system 請求，合法終止封套驗證失敗則回傳對應 ToolMessage。現有完整工程紀錄仍屬本機 Demo，正式環境須限制存取、遮罩及保留期限。

測試（已啟動原有 PostgreSQL/Ollama 並沿用原 DSN，無需重新建立資料庫）：

```powershell
 git fetch origin
 git switch --track origin/refactor/single-agent-loop
 .\.venv\Scripts\python.exe -m unittest tests.test_single_agent -v
 .\.venv\Scripts\python.exe -m evaluations.eval_rag --live ollama --case redemption-gap --report reports/single-agent-gap.json
 .\.venv\Scripts\python.exe -m evaluations.eval_rag --live ollama --report reports/single-agent-all.json
```

已存在本機分支時用 `git switch refactor/single-agent-loop` 與 `git pull --ff-only`。macOS/Linux 改用 `./.venv/bin/python`。新單元測試使用腳本模型，不代表實機理解正確；實機以既有 RAG/業務端到端題庫驗證，包含實際查詢及最終回答、呼叫次數、耗時、來源及人工驗收要點。原第一、二層獨立評測不必重跑；其分數不可與端到端結果直接比較。實機結果尚未驗證。

參考：[OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)、[OpenAI Agents SDK: running agents](https://openai.github.io/openai-agents-python/running_agents/)、[Anthropic 客服範例](https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/customer_service_agent.ipynb)、[Anthropic Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)。參考工具循環而非複製特定 SDK；現有 LangChain / LangGraph 依賴不變。

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
| evaluations/eval_rag.py / tests | 檢索評測、腳本回歸、實機重播 |

## 3. 歷史 Intent-first Agent 生命週期（intent_answer）

`agent.intent_answer` 建立 IntentRuntime，沿用共用 ToolNode、registry、證據與傳輸，改用以下節點：

1. Intent：第一個 LLM 呼叫依最終目的分類五類 type，逐字保留 request；主機驗證片段並配置 T1 等 ID。第二個 LLM 呼叫 Audit 驗證需求覆蓋及業務綁定；主機驗證業務能力、檔期、商品指涉及帳號。寫入操作為 unsupported、其他帳號為 account_limit。第一層不接收工具能力及權限清單；原能力資訊留給 Audit。第一層可由 evaluations/eval_intent.py 獨立執行，不進入第二層或工具。
2. Select / Prepare tools / ToolNode / Collect：根據未完成需求選原生工具；主機核對工具範圍及必要資料，先核對依賴的指定商品再查本人紀錄。保留本輪原始結果及來源。以新增來源判斷檢索進展。
3. Evidence assessment：主機檢查必要欄位，模型獨立評估是否足以回答每項需求。補查須有具體工具及資料需求、有效預算；不能僅因非空文字補查。
4. Generate：已確認的結構化事實以主機呈現，知識任務由 LLM 生成並核對本輪來源。活動總覽的重要條款由資料設定保留。
5. Verify：核對原問與需求覆蓋及各草稿；最多一次需求重規劃、一次具體語意修正。終止需求不再任意執行工具。模型驗證不等於語意保證。
6. Finalize：逐需求整合已確認的回答及限制；來源與最終採用內容對照。

`task_contracts.py` 定義各階段 Pydantic 契約；`task_business.py` 管理能力、槽位及結構化呈現；`task_runtime.py` 負責逐需求狀態轉移。工具上限仍為 3 輪、12 次嘗試、單批 6；每次結構化階段最多一次契約修正；模型傳輸可有限重試；recursion_limit=64。新增階段預期增加延遲，尚未有實測效益。

## 3A. 基準 Agent 生命週期（legacy_answer，僅供比較）

LangGraph StateGraph / MessagesState 管理每輪狀態與訊息；獨立節點透過 conditional edges 路由，不直接操作 Pydantic Graph。每輪建立獨立 registry，ToolNode 並行執行主機核准且去重的呼叫。

1. Select：首次、補查及空回應重試均從同一 registry 取得工具定義，透過 ChatOpenAI/ChatOllama.bind_tools 使用原生 tool_calls。
2. Prepare tools：主機檢查名稱、參數、檔期、可信指涉、計算依據與預算；相同正規化參數在本輪共用結果。ToolNode 只接收已核准的唯一呼叫。
3. ToolNode：並行執行獨立唯讀工具；需要既有證據的運算不得使用尚未完成的同批結果。
4. Collect：按原呼叫順序合併結果、查詢紀錄、適用條件及證據；模型上下文去重及投影，保留完整主機資料與合法 tool_call_id 對應。
5. Assess：以 with_structured_output(method="json_schema") 獨立生成回答；Pydantic 產生 Schema 並驗證欄位；模型僅回傳短 source_ids，各需求獨立驗證並回填真實原文。answer 時捨棄無作用的 missing，retrieve 必須提供原因。
6. Stop Gate：依證據與預算決定補查或停止，維持既有矛盾拒答有限複查。格式修復回饋實際驗證原因及原草稿。
7. Finalize：預設關閉、可選的一次品質審查；修訂失敗保留可驗證草稿，依子問題產生回答與參考資料。

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

工具均唯讀或純運算；進度工具不接受任意帳號 ID。demo 帳號由 UI 選擇，不是正式登入驗證。RAG 不包含玩家資料，公開與本人紀錄各有來源。PostgreSQL 的 player_event_progress 與 event_support_agent_embeddings 分表；demo 共用 DSN，尚未配置獨立最小權限角色。商品查無是查詢事實，不等於查明交易失敗原因。計算來源 ID 有驗證，但並未保證每個運算元都語意受支持。引用驗證是本輪短 ID 對照、主機原文與狀態核對，非完整語意證明。

基準工作流的品質審查觸發條件為重複需求、未引用同文件章節或未採用的工具組；候選不等於必要證據，可能有過度審查問題。預設 off，保留 auto 作實測比較，不以單一案例修改流程。

工程檢視顯示 Graph 節點、模型請求/回傳、用量、工具、證據與停止原因。正式系統需補認證、授權、日誌遮罩及保留政策。正文透過共用 Markdown 指引與 Streamlit 渲染；沒有新增排版模型呼叫或正規表示式重排。

模型選工具時使用完整來源 ID（供 calculate 引用）；回答及審查階段使用本輪短 ID，由主機對照完整 ID。短 ID 只有本輪證據集合中存在才有效。完整工具資料留存工程紀錄；模型只取得必要原文、狀態、檔期、日期及條件。單一子問題格式或來源錯誤不會抹除其他有效子問題。

第二層可透過 `python -m evaluations.eval_audit --cases` 注入固定第一層輸出，與正式流程共用 `IntentRuntime.audit_intents` 和 `bind_intents`，不啟動後續節點。LLM 提案 kind、editions、product、account；Python 核定初始狀態。跨期公開比較為 analysis/calculation；他人紀錄保留 query 及具體 kind，account=other。模型提案不構成資料授權。

## 新第二層候選（尚未接入正式 Graph）

固定第一層需求 → Python route_tasks → task_definitions（每個工具的 task_id enum 依任務允許範圍）→ 一次原生 tool_calls → validate_batch（逐任務驗證及現有 ToolRegistry 參數正規化）。不重分類、不建立 18 種 kind，不執行資料工具；限制提案經 report_task_status 控制契約處理。首輪不提供 calculate。舊 Graph、Audit、證據評估與回答流程保留，待獨立驗收後再設計整合與跨輪取證。

### 第二層契約精簡實驗

初次 Qwen3:8b 實機基準：15/20 輪、25/31 任務，17/20 契約有效。保留原題庫、預期結果及 exact_match。唯一待辦任務由主機綁定 ID；多任務仍要求 task_id。公告 overview/compare 不使用 query，缺欄位時主機補空字串；search 仍需非空 query。為維持 OpenAI strict schema，模型可見 query 仍宣告必填，但描述指出非 search 填空字串。僅傳待辦任務與服務、歷史上下文，不重複傳完整原問；原問仍用於主機錨定。工具描述集中在候選契約，不增加工具或模型呼叫。若終止任務含待辦所需指涉，可能須補問，未驗證跨任務指涉。Ollama 候選獨立預設 num_predict=2048，CLI --num-predict -1 可做不限制的對照；OpenAI 不套用此參數。長度截斷批次不核准，無自動重試。未實機驗證新版。


## Single Agent：證據依賴展開

最終回答可只引用已取得的計算結果短 ID。主機依本輪可信證據內的 `運算依據` 展開多層來源，去重並保留穩定順序，再執行原有引用及適用條件驗證。未知 ID、缺失原文、循環或不合法依賴會使受影響的子問題降級；不新增模型呼叫，不自動引入未被引用的計算。舊 `legacy_answer`、`intent_answer` 的引用契約不變。此機制僅保證來源追溯，不證明運算式選擇、數字來源或回答語意正確。測試：`python -m unittest tests.test_evidence_dependencies tests.test_single_agent -v`。
