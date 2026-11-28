# 系統架構與執行流程

[返回 README](../README.md) · [資料與工具](DATA_AND_TOOLS.md) · [設計決策](DESIGN_DECISIONS.md)


## 活動客服的預設入口

`app.py` 與批次評測皆呼叫 `agent.answer`。一個語意決策即可直接提出業務查詢。舊的五類分類及 Audit 只有在明確呼叫 `intent_answer` 時才會執行。

| 使用者需求 | 資料與執行方式 |
| --- | --- |
| 本人餘額 | 讀取登入者、指定檔期的 PostgreSQL 紀錄，保留日期 |
| 商品售價或完整商城清單 | 依檔期核對商城，直接呈現售價或指定品名查無 |
| 本人距離指定商品的差額 | 先確認品項，才允許讀本人餘額。Python 計算價格差，附商品與本人兩份引用 |
| 規則、資格或診斷 | Agent 檢索本輪公告及必要證據，提交可逐項核對的主張 |
| 要求加幣、代換或查其他帳號 | 由主機呈現服務或帳號限制，其他獨立的查詢仍可完成 |

一批最多六個原生工具提案。業務工具將每個固定需求展開成至多兩個唯讀查詢，沿用既有 ToolNode、參數驗證、去重與每輪對話累計十二次底層工具嘗試的上限。固定業務結果涵蓋原問即可結束。混合題保存已核對部分，再由同一 Agent 處理其他需求。

多輪商品指涉可以使用上一輪在同檔期唯一核對的指定商品。公告的適用條件可以用上一輪實際採用的同檔期段落延續，仍須重新取得本輪來源。每次本人查詢都從本輪讀取結果取值。

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

## 主機端資料限制呈現（第二階段）

Single Agent 在 finalize 依本輪 sources、errors 產生限制資訊，僅在經驗證的 no_evidence 存在時附上本輪查詢摘要；同輪純未知需求合併一段拒答但保留各需求標題。狀態分類與固定的工具能力說明由 Python 管理，不輸出工具參數、紀錄值、原文、例外或模型自由理由。沒有可信執行資訊時保留通用拒答；無效參數提案不是已執行查詢。搜尋無段落與背景資料不證明政策不存在，精確商城查無只限該次品名及檔期。摘要只描述全輪，不能將任一次查詢或失敗硬配到個別子問題。不更改 Prompt、Schema、工具派發、RAG、模型預算、NVIDIA 節流或評測門檻；原有 supported、account_limit、其他拒答與歷史 renderer 路徑保留。no_evidence 的正文與來源仍須留空，限制資訊不算作採用證據或語意證明。

另將沒有玩家進度紀錄的證據段落標記為 unavailable，與原有商城未確認狀態的主機驗證一致。單獨引用僅能呈現可信查詢狀態原文，不允許模型將其改寫為餘額；與一般規則混合仍走原有拒絕規則。不將 unavailable 自動判為 not_found 或餘額為零。

## 查詢效率與停止條件（保守最佳化）

保留相同 RAG 候選、BM25/向量排名、RRF、適用條件、原問與改寫查詢、來源引用及安全契約。既有 request-scoped 排名 single-flight 按候選範圍重用；新增同輪 query vector single-flight，完全相同的 provider/model/endpoint/input_type/query 可跨公開檔期重用向量，但 SQL 仍逐檔期核對索引版本、候選與維度並獨立排名。不將語意相似的查詢合併，不跨使用者或回合快取，不新增 SDK、批次服務請求、重試預算或語意審查模型。失敗的向量計算不留在 memo。原工具執行錯誤快取及既有完全重複批次停止規則不變。

Single Agent 的 find_rules 描述明示成功 search 已含 overview；不在主機預先跳過不同查詢（例如 search 失敗仍可能需要 overview）。每批成功公告工具回傳後由 Python 計算可採用來源的唯一、已知、新增數；沒有新段落只給下一次模型請求簡短的主機觀測提示，不當作證據充分或強制拒答。新段落存在也不證明足以回答。MAX_TOOL_ROUNDS、MAX_TOOL_CALLS、MAX_MODEL_CALLS、終止 Schema、原有 Guardrails、20 RPM 不變。

新 Trace：Python 查詢進展、Python 檢索快取統計、Python 單一 Agent 輪次的停止資料工具原因；終止契約修復保留既有錯誤代碼並於終端機顯示。Evaluation V2 另列 retrieval_diagnostics，舊報告無新欄位標記未知；聚合只計觀測到的輪次，不變更評分門檻。快取重用包含等待相同 in-flight 計算，不能直接等同節省 HTTP 次數；HTTP 實際觀測仍看 transport。未新增證據批次也不代表無效查詢或模型語意正確。

## 終止契約、工具調度及證據評測整合（第四階段）

本節取代前述歷史 Single Agent 終止封套及商品依賴指引；legacy_answer、intent_answer、RAG 排名、快取、權限、既有停止條件與預算保留。現行 submit_answer 與無工具 Schema fallback 共用同一閉合物件，兩個必填陣列：supported 為 question、statement、condition、source_ids，unresolved 只含 question 與 status（clarify/no_evidence/account_limit/unsupported/service_help）。沒有項目填空陣列，不使用 anyOf/oneOf/discriminator。主機轉為既有驗證/呈現格式，再逐主張核對本輪短 ID、運算依賴、適用條件和可信查無狀態；沒有有效主張或需求、超過原 16 項上限、缺來源或非法欄位仍不得直接放行。舊 results 回傳保留嚴格相容 decoder，記錄契約版本，不接收舊非 supported 的任意正文；舊已確認查無的恢復路徑仍然保留。新版已確認查無必須作 supported 並引用查詢結果，未知需求沒有來源欄。新欄位 condition 是模型提出的成立條件，不是主機已驗證語意充分性。只有實際呈現的 supported 文字及其來源記入主張級 Trace，方便人工審查；同需求的多主張應使用相同 question。主張與未知需求分組呈現，可能先呈現已能回答的部分，不承諾逐字原問順序。

本人進度工具新增必填 required_item：相依品項使用原問或可信參照的名稱；獨立本人需求使用空字串。缺欄位、型別錯誤、未核對商品指涉、非 found（含 ambiguous、not_found、unavailable）或非 self 一律不得執行相依私人查詢。主機先在本機唯讀商城查核 declared dependency，才派發本人 DB 查詢；與其他公開工具可同批。這個預檢不是模型證據，回答仍需採用正式本輪工具來源；沒有商品欄位的獨立查詢仍可執行。模型填空字串是語意提案，主機無法確定其是否正確辨認所有依賴或本人指涉，不把它當作授權或完整的語意安全證明。計算仍依賴先前已驗證的本輪 ID，不能引用同批尚未取得的結果。沒有本輪證據時暫不提供 calculate，後續恢復；其餘工具不依語意猜測而隱藏。精簡 Single Agent system prompt，保留原問、已知條件、歷史指涉、證據全文與既有預算；不新增 LLM 呼叫或外部 grounding API。

評測器保留原 rubric 2.0.0 與 legacy gate；新增可顯式選用的 rubric_v2_1.json（2.1.0）。價格等價來源只證明指定品名及價格，不證明完整限兌或交易狀態。新版另要求可回答本人餘額的案例採用本輪本人紀錄，部分資格案例需保留相關規則來源。這些是預先定義的測試資料，不進入正式 Agent 路由。新增 generalization 六題（同一示範語料另編，未實機驗收，不宣稱第三方獨立盲測）。新舊評測結果應分別保留，對照相同 rubric；歷史欄位缺失保持未知。claim_evidence 僅供人工對照，不自動評定 correctness、completeness、groundedness、uncertainty 或 relevance。
