# 現行設計原則


## Single Agent Loop 分支（目前預設）

本分支 `refactor/single-agent-loop` 從 `refactor/intent-task-simple` 的 8b6fdd0 建立。預設 `agent.answer` 改為 `single_agent.py`：同一模型角色以 native tool_calls 取得工具結果，或直接輸出最終回答。保留 LangGraph ToolNode、ToolRegistry、資料庫、RAG、參數驗證、快取、適用條件及引用驗證；不呼叫原分類、Audit、獨立證據評估、停止審查或回答品質審查。舊 `intent_answer`、`legacy_answer` 與歷史題庫保留，以下舊流程章節只供歷史參考。

工具輪數 3、單批 6、累計嘗試 12、模型請求 5（含最多一次格式修正）、Ollama 每次 num_predict 2048。這是本專案實驗上限，不是官方推薦通用數值。原生工具結果以短 ID 回傳，calculate 的來源 ID 經主機對照。正常路徑以同一 Agent 的原生終止工具 submit_answer（JSON Schema）提交 results，移除 action/missing，將 unavailable 合併至 no_evidence；不計入資料工具預算。終止工具必須單獨提交才可能被接受；混合批次保留全部呼叫 ID，資料工具各自依原有權限、參數及預算核准，終止提案回傳拒絕結果而不執行、不計入資料工具嘗試。整批封套不合法、截斷或超額仍不執行。只有回傳契約失敗、重複查詢停止或達到預算時，使用無工具的供應商 JSON Schema 結構化回答，仍受原有模型呼叫上限及一次格式修正限制。主機驗證引用、運算依據及格式，不代表語意完整性。帳號仍由主機選定，不接受任意帳號 ID；明示 other/unclear 拒絕，但模型誤標 self、漏查商品或漏答的語意問題尚未被確定性驗證。商品依賴順序由 Agent 指引，不再有舊的逐需求槽位強制控制。正式認證與語意驗收仍需補足。

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

[返回 README](../README.md) · [技術參考來源](REFERENCES.md)

## 職責分離

LLM 理解需求、選擇資料工具及生成回答；Python 管理工具契約、資料範圍、權限、證據、停止條件與確定性運算。LangGraph 負責執行節點與狀態，不負責推論業務事實。

## 資料與工具

公開公告、商城及個人活動進度分開讀取。公告採檔期與發布版本過濾，使用 BM25 或 BM25 + pgvector 混合檢索；個人紀錄由 PostgreSQL 獨立資料表持久化保存，初始化種子與公開公告分離，不加入向量索引。資料工具均為唯讀或純運算，模型不得指定其他帳號。

## 有界執行與可靠性

每輪有工具批次、輪數、工具嘗試、格式修復及審查上限。工具結果先形成可核對的證據，再由模型整理。引用 ID 需存在於本輪，原文由主機產生；查無、資料缺失、權限限制及例外分別處理。無足夠資料時保守回答，獨立需求可部分完成。引用驗證不等於完整語意證明。

## 呈現與維護

客服正文使用共用 Markdown 契約，由 Streamlit 顯示；引用、工具原始資料與工程紀錄分開呈現。測試集中在 tests/，以相同題集比較檢索與模型行為。新增功能須維持通用性與模組邊界，不針對單一問題硬編碼。

實際測試方法與已知限制見 [EVALUATION](EVALUATION.md)。

兩個供應商在工具選擇與重試時均使用 LangChain bind_tools 與原生 tool_calls。主機以同一個 ToolRegistry 驗證及正規化參數，LangGraph ToolNode 並行執行唯一核准的查詢；依賴本輪新資料的運算需下一輪。回答與審查另以 with_structured_output 使用 JSON Schema。Pydantic BaseModel 驗證輸出資料契約，無作用欄位不觸發重試。

## LangGraph 遷移決策

從 ollama-native-tool-calls 的 7c9385c 建立獨立重構分支。採用 StateGraph、MessagesState、ToolNode、Conditional Edges 與有限 RetryPolicy，不重建 RAG、SQL、商城或業務驗證。先保留工具與回答分離，不同模型相容性待實機驗證。保留原始回歸測試意圖，傳輸測試改驗證 SDK 行為；外部 tracing 預設停用。本次未導入 MCP、Checkpointer 或 Multi-Agent。舊分支保留作比較基準。

## 回答契約精簡

2026-11-25 LangGraph 實機紀錄顯示工具與 JSON 成功，但五筆來源 ID 正確、摘錄均為改寫，導致拒答。移除模型逐字複製責任，保留主機來源、條件、查無、計算依據及權限核對。Pydantic 單一契約、短 ID、精簡證據投影及分項驗證。合併模型可見 eligibility/search（保留舊離線模式），同輪共享重複子查詢。品質審查預設 off，保留 auto 比較；不將結構成功當成語意品質。延遲改善、模式簡化的實機成效仍需驗收。


## Intent-first 實驗分支

基準 3ea6e67 實機 Log 顯示完整商城價格回答仍以 retrieve/missing=無補查；要求贈送星星幣則意圖漂移、引用歷史資料且將不支援操作當成資料不足。採用獨立意圖規劃、逐需求主機狀態、業務必要欄位、證據充足性評估、可信資料呈現與獨立語意驗證。保留原生工具、既有 registry 與 LangGraph；不加入寫入功能，不以關鍵字或固定商品實作路由。能力分類為業務契約，不是使用者措辭的特例。模型仍可能誤分類、遺漏必要資料或錯判語意；修正預算有限，未完成任務安全降級。優先正確性，實機延遲與模型品質待評估。

## 精簡意圖與完成音效

初始契約僅五類意圖與逐字原問片段；主機拒絕重疊或無法錨定的片段，後續審查 precision/recall，核定每個既有 ID 的業務能力與必要資料，不允許增加任務。業務型別不適用商品時捨棄該欄位。語意驗證針對所有需求的實際答覆，包含限制回覆，避免空清單衝突。LangGraph 節點明示 IntentState，避免舊 SupportState 隱藏新增欄位。仍可能誤分類及過度拆分，必須實機比較。

音效屬於 UI，不增加 LLM 呼叫；Streamlit v2 component 使用 Web Audio AudioContext，預設開啟，正常使用者手勢解鎖音效並保留手動關閉功能，按新回覆事件 ID 去重，不播放歷史。參考 MDN autoplay policy 與 Streamlit components.v2 API。背景分頁及瀏覽器政策可能影響時機，無法保證背景通知。


## 第一層分類邊界（獨立實驗）

2026-11-25 實機案例顯示指定商品異常被分類為 knowledge、他人餘額被分類為 other。原五類定義過短，未區分最終目的、中間手段及權限。參考 Google Dialogflow CX 的清楚分類與對照範例，以及 Anthropic 對 Routing 的適用條件，保留原五類及輸出契約，補充適用/排除情境與通用對照範例，不使用特定商品或問句路由。已知但無法執行的需求仍保留語意類型。只調整第一層 Prompt 及提取共用測試入口，後續階段不變；分類效果及延遲需以相同模型、獨立標記題集及未見問句驗證。不能以契約通過代替語意正確。


## 第一層完整定義與分層驗收

修正上一版未完整落實定義格式的問題：每一類均明列定義、適用、不適用、判斷原則及對照範例，不自行縮減為單段。一般政策說明（含公告的理由）為 knowledge；具體狀況、依資料推導未知原因、比較或資格判定為 analysis。原 public-why 案例保留並記錄分類理由，另加明確對照案例；保留所有舊標記及新增邊界案例。第一層的初始輸入移除工具能力及權限清單，Audit 保留原封包欄位；已有語意修正回饋仍按既有流程傳入，不改重試或權限檢查。評測共用正式第一層，分開統計契約、需求數量、類型序列、逐字片段、完全一致，支援選案、重跑及本機報告；離線 fixture 與真實模型驗收不可混為一談。不得藉此宣稱全流程已正確。

**待第二層處理（本次不變更）**：Audit 允許跨期公開比較使用 knowledge 或 calculation，與第一層明確以 analysis 分類的原則存在潛在歧義。整體回歸在上一個 commit 的 CI 有 4 個既存失敗（與更早基準相同）；不刪除或弱化測試，待相關階段逐一定位。本次優先驗證第一層。

## 第一層分類優先邊界：服務主題與需求目的

2026-11-26：使用者提供的 Qwen3:8b 實機基準（commit e2dfbb381e501d2b4800b48ffb6a85b8388afd0d，Prompt SHA-256 75ceb2d0243892daea39e0b3697e59bbc2c16510727fd1180d70bec91cc55f90，單次 36 題）：契約、需求數量、逐字片段皆 36/36，意圖序列及完全一致 32/36。四題不符分為兩類：eligibility、compare 預期 analysis 卻回傳 knowledge；unrelated、domain-outside-operation 預期 other 卻回傳 knowledge、operation。這些是觀察到的誤分類，不代表已證實模型內部成因。

經討論維持最終交付結果為分類基準：將規則套用至具體情境（含假設）或比較歸納差異為 analysis；取得規則是中間手段，除非另有明確要求，不拆成 knowledge。參考 AWS Assisted NLU 清楚且互斥的意圖描述，以及 Google Dialogflow CX 的範圍外負例與避免相似意圖，僅修改 INTENT_PROMPT 的共用判斷原則（拆分後逐項先辨識服務主題、再辨識需求目的）及 knowledge/analysis 的判斷原則。兩階段是同一模型呼叫內的提示指令，不是新增分類器，也不是大廠公開固定演算法。明確範圍外先判 other，不將執行能力或權限混入主題辨識；不強制四種範圍內意圖的固定優先序。

保留五類完整定義、適用/不適用情境、原有範例、需求拆分及輸出契約；不新增失敗問句到 Prompt，不改任何測試及預期答案、Python 邏輯、呼叫次數、Audit 或後續層。先以既有 --case 重測四個案例，再用相同模型及設定重跑完整 36 題（另存 reports/intent-all-v2.json），確認是否改善及有無退步，另以未見問句和重複試驗確認泛化與穩定性。未完成實機評測前，不宣稱改善或進入第二層。

## 第一層實機回歸通過，獨立新題驗收

2026-11-26：使用者提供 commit e5a7724dc84416f084b92b6a71448f9c5122890a 的 Qwen3:8b 一次實機結果，Prompt SHA-256 162ce182352501066f1e84c52d70a2a0ee3c4b7c9eb769f11ce637ac85e2469b，36 題的 contract_valid、count_match、type_sequence_match、span_match、exact_match 均為 36/36，每題僅一次模型呼叫。已知四題改善，原先通過的題目均未退步。此為一次評測，尚非穩定性證明。

凍結 Prompt、原 36 題、正式工作流及後續層，新增獨立 20 題 evaluations/intent_holdout_cases.json；依既有分類共識在執行前標記，包含新措辭、新情境及複合需求。只為第一層評測器加入題庫選擇，不修改正式分類行為、不導入新相依。依使用者要求，此輪實機只執行新題，失敗題可選案重測；不刪除原基準，不在 Prompt 放入新題或答案。新題的語意及穩定性仍需人工與實機驗收。

## 測試及評測目錄整理

2026-11-26：相同 Qwen3:8b Prompt 下，原題庫 36/36、新題庫 20/20，均單次實機通過。凍結第一層 Prompt 及業務流程。依使用者要求將三個評測器及四份題庫移至 evaluations/，既有 unittest 保留 tests/。以 Python package 的 `-m` 從根目錄執行，避免 sys.path 修改；題庫以模組路徑定位。同步調整測試匯入、mock 目標、CI、UI 練習題資料路徑及文件，保留原資料內容、測試斷言、CLI 參數、報告位置及行為。此次不重跑已通過的真實模型 56 題；目錄移動須獨立確認 CLI、離線測試與 CI，既有全流程四個回歸失敗仍留待後續階段處理。

## 第二層 Audit 的明確邊界與獨立評測

2026-11-26：第一層兩批真實模型測試共 56/56，凍結 INTENT_PROMPT、原有題庫與第一層契約。原 AUDIT_PROMPT 允許跨期比較為 knowledge 或 calculation，與 CAPABILITIES 及第一層 analysis 定義矛盾；「他人紀錄填 other」未明確區分 kind 與 account。此次在原第二層 Prompt 內按意圖審查、業務類型及參數擷取三部分澄清，不增加模型呼叫，不修改 Schema、CAPABILITIES 或主機權限規則。比較結論為 analysis/calculation；他人紀錄保留語意 kind 並 account=other；缺參數及不可執行仍是有效意圖。LLM 的帳號範圍判斷可能出錯，主機現有的 account=other 拒絕並不能單獨證明任意錯誤 self 提案均會被阻擋；獨立評測特別檢查此類錯綁。後續工具與帳號授權不在本次修改範圍。

正式流程抽出共用 audit_packet/audit_intents，保留既有封包、審查回饋及重試行為；新增 24 題獨立 Audit 評測（19 accept、5 revise）與離線測試，涵蓋 18 種 kind。對故意不正確的第一層輸入只驗證 Audit 決策，不啟動第一層重提案。核對主機狀態與原始 binding，避免安全降級或欄位被丟棄掩蓋語意錯誤。標記為依現有業務契約預先制定，合理等價答案與 revise issues 必須人工複核。新 Prompt 真實模型尚未驗收；既有全流程四項回歸失敗不藉此刪除或弱化。

## 新第二層任務感知工具規劃實驗

採用 Routing + Native Tool Calling + 主機核定，保留第一層 Prompt、標記題庫及現有正式流程。批次任務工具聯集可能等於全部工具；主要收益假設是任務 ID、覆蓋追蹤及逐任務工具範圍，不宣稱必然改善準確率。第二層不重複意圖審查或業務 kind 分類，欄位需求以 tool metadata 描述。新增 task_tool_planning.py、獨立實機評測與離線契約測試，10/5/5 題組、31 個任務。原生工具由既有 model_step / LangChain 介接，不以文字 JSON 模擬。控制工具不是實際資料工具。批次失敗不提交，合法子任務僅供診斷；不執行任何工具。待首輪模型驗收後再設計跨輪取證及正式 Graph 接入，避免改動後續層掩蓋問題。模型錯誤聲稱 self 仍可能通過結構檢查，需以語意驗收及後續授權方案處理，不能把 model scope 當授權。沒有已取得資料時不允許運算。此版未建立實際欄位投影及任務級證據傳遞，不能直接取代原流程上線。首輪檢索詞以概念粗略比對，非嚴格語意判定。舊 Audit 評測保留。

### 第二層契約精簡實驗

初次 Qwen3:8b 實機基準：15/20 輪、25/31 任務，17/20 契約有效。保留原題庫、預期結果及 exact_match。唯一待辦任務由主機綁定 ID；多任務仍要求 task_id。公告 overview/compare 不使用 query，缺欄位時主機補空字串；search 仍需非空 query。為維持 OpenAI strict schema，模型可見 query 仍宣告必填，但描述指出非 search 填空字串。僅傳待辦任務與服務、歷史上下文，不重複傳完整原問；原問仍用於主機錨定。工具描述集中在候選契約，不增加工具或模型呼叫。若終止任務含待辦所需指涉，可能須補問，未驗證跨任務指涉。Ollama 候選獨立預設 num_predict=2048，CLI --num-predict -1 可做不限制的對照；OpenAI 不套用此參數。長度截斷批次不核准，無自動重試。未實機驗證新版。

## 第二層獨立題庫（Holdout）

V2 原題庫實機基準：17/20 輪、28/31 任務、20/20 契約有效。保留第二層 Prompt、工具契約、模型設定及原題庫 exact_match 不變，新增 `evaluations/task_tool_holdout_cases.json`：20 輪、33 任務，10 單一、5 同意圖複合、5 混合。固定第一層輸出，重點為欄位過取、未知/已知商品、分析依賴、跨期公告、帳號與能力邊界。這是針對既有錯誤類型設計的獨立新問句測試，非隨機抽樣或完全未見的業務類型。預期結果不傳模型；不執行工具。

PowerShell：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_task_tool_planning -v
.\.venv\Scripts\python.exe -m evaluations.eval_task_tools --cases --suite holdout --report reports/task-tools-holdout.json
```

macOS/Linux 改用 `./.venv/bin/python`。不指定 `--suite` 仍測原 baseline；不必重跑原題庫。報告記錄 suite、case_file 及 Prompt hash。新版 holdout 尚未真實模型驗收。


## Single Agent 終止契約（V2）

實機紀錄顯示自由文字 JSON 多出結尾大括號、查無狀態誤用，以及單一商品被完整清單取代。依 [LangChain Structured Output](https://docs.langchain.com/oss/python/langchain/structured-output) 的 ToolStrategy 概念，將輸出契約表達為原生終止工具；並參考 [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs) 與 [Tool Calling](https://docs.ollama.com/capabilities/tool-calling)、[OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)。本專案保留既有 LangGraph，不新增 SDK；不假設所有 Ollama 版本支援同時啟用工具及 format 約束。OpenAI 原生工具沿用 strict；Ollama 原生工具的 Schema 為模型介面，不保證嚴格遵守，仍由 Pydantic 驗證並使用既有 provider schema-only 介接備援。精簡重複狀態與終止欄位；商品工具描述依資料用途區分，不依特定題目硬編碼。已驗證查無的既有保守恢復只採本輪引用的可信查無原文，不能保證模型必然引用正確來源。沒有新增語意審查或固定模型呼叫；工具依賴順序仍是語意指引，而非主機強制。18 項單一 Agent 腳本回歸含新增終止混批、無效契約、結構化備援及查無證據恢復；須另行執行完整回歸與實機驗收。


## Single Agent：證據依賴展開

最終回答可只引用已取得的計算結果短 ID。主機依本輪可信證據內的 `運算依據` 展開多層來源，去重並保留穩定順序，再執行原有引用及適用條件驗證。未知 ID、缺失原文、循環或不合法依賴會使受影響的子問題降級；不新增模型呼叫，不自動引入未被引用的計算。舊 `legacy_answer`、`intent_answer` 的引用契約不變。此機制僅保證來源追溯，不證明運算式選擇、數字來源或回答語意正確。測試：`python -m unittest tests.test_evidence_dependencies tests.test_single_agent -v`。

依 [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling) 減少模型重複填寫主機已知資料的原則，以及 [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) 的衍生來源概念。這是本專案實作取捨，不是供應商強制規定。舊分類、審查、工具規劃仍被歷史入口及獨立評測引用，不視為無用程式；共用 TurnRuntime 仍為現行工具派發的基底。避免刪除歷史測試或改動其基準。此次移除 single_output.py 未使用的 json import。完整回歸與實機仍需驗證。

## Single Agent：工具批次回饋與可觀測性

2026-11-26，基準 9fdf6258：合法混批不再整批重試。每個合法正規化 ID 對應一個 ToolMessage；終止提案回傳 TERMINAL_MUST_BE_ALONE，其他資料工具照原契約核准，尚未取得的證據仍不能用於同批相依工具。多個終止提案均拒絕，消耗工具輪次而不消耗資料工具嘗試；達預算使用既有 Schema-only 路徑。無效單獨終止的封套以 INVALID_FINAL_SCHEMA 回傳，缺合法封套的修正及停止狀態由本次 system 請求提供，不新增 HumanMessage。既有格式修復、資料與模型呼叫上限不變。

工程觀測新增每次模型的配置/回報名稱、配置 Context（非實際伺服器容量）、訊息與工具結果字元數、供應商 Token、停止原因及耗時；新增實際資料工具耗時及公開 RAG 候選排序、分數、採用狀態。缺少供應商資訊時不猜測；不將字元數換算為 Token，不新增模型請求或調整檢索策略。主機格式修復與停止指令只附加於該次 system 請求，合法終止封套驗證失敗則回傳對應 ToolMessage。現有完整工程紀錄仍屬本機 Demo，正式環境須限制存取、遮罩及保留期限。

參考 OpenAI Function Calling 的多呼叫與結果配對、Anthropic Parallel Tool Use 的未執行呼叫回傳錯誤、Ollama Tool Calling 與 Chat API，以及 Anthropic Effective Context Engineering；具體終止策略與錯誤代碼是本專案契約，不是供應商強制規則。保留歷史流程及題庫；其共用模組仍被既有回歸和獨立評測使用，不刪除。回歸涵蓋混批、未授權工具、重複終止、格式修復角色、RAG 及模型指標；CI 與本地真實模型驗收待確認。

## Multi-provider embedding spaces

Resolve the effective embedding provider/model once per request rather than mutate process-wide environment variables. Keep a manual override so evaluations isolate chat and retrieval changes. Migrate the original Ollama-only table transactionally, preserving existing rows under provider=ollama. Scope exact similarity by provider/model/edition/revision/passage IDs; reject missing data and dimensional mismatch. No automatic fallback that would conceal embedding failures. NVIDIA native 2048 dimensions are retained; approximate indexing is outside this change. No prompt, answer contract, or business authorization changes. Live cloud and PostgreSQL integration requires user-side verification.

### Embedding transport resilience

單題 Hybrid 成功後全量同題曾收到 NVIDIA HTTP 500。此證據只定位至服務請求失敗，無法證明具體供應商根因。唯讀嵌入請求採最多三次嘗試、指數退避加 jitter、有限 Retry-After；不重試非暫時性 HTTP 錯誤、不改模型或檢索排名、不自動降級。離線評測分開供應商執行失敗與證據漏召回，成功子集明確標記，仍以非零退出表示不完整。參考 OpenAI Python SDK 的暫時性錯誤重試做法，具體重試碼及上限為本專案取捨，尚待實機驗證。
