# 評測與驗收


## 活動業務結果驗收

`--suite business --gate` 包含 11 個情境，其中三個會先建立前一輪歷史。每個案例可以指定多項預期成果。每一項都要在實際呈現的一則主張裡同時找到指定來源和預期值。帳號界線及不支援的操作則檢查狀態與工具派發。測試使用固定活動資料和資料庫種子。

例如，混合題中只有 80 枚餘額仍缺少「指定品項查無」。差額題僅引用價格與餘額也不夠，實際回答還須交代差多少。文字標記只涵蓋題目列出的業務事實，規則解釋和語意完整度仍須對照 reference。既有 baseline、holdout 及 generalization 題庫保留為較廣的回歸檢查。

[批次命令與結果欄位](TESTING.md)

[返回 README](../README.md) · [設計決策](DESIGN_DECISIONS.md)

## Intent-first 實驗分支驗收

新流程的腳本測試位於 tests/test_intent_workflow.py；原有針對舊回答契約的測試明確使用 legacy_answer，保留基準，不代表新流程通過所有舊案例。真實模型測試及 evaluations/eval_rag.py 使用新預設入口。evaluations/task_cases.json 增加操作拒絕、混合需求、不存在的商品與差額案例；只加入實機重播，原 27 題離線檢索基準不變。

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_intent_workflow -v
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live ollama --case unsupported-operation --gate --report reports/intent-operation.json
```

檢查需求覆蓋、工具範圍、逐需求終止狀態、實際來源與最終正文。線上模型 verifier 通過不等於人工語意驗收；尚未完成此分支完整回歸及實機驗證。CI 結果以對應 commit 的 Actions 為準。

## 1. 已確認的基準

下列回歸及檢索數據為遷移前 Pydantic Graph 的歷史基準。LangGraph 新分支新增 SDK、非作用欄位、具體修復回饋及證據去重測試；第一版 LangGraph CI（c0082a4）已通過；本輪變更、本機 PostgreSQL 19 及實機模型仍待驗證，不把歷史成績當成新版本成績。

既有純檢索基準為 Ubuntu / Python 3.11；公告及評測語料未變更。完整回歸加入 PostgreSQL 服務及資料庫整合測試。下表保留既有歷史結果，不代表已在 PostgreSQL 19 重跑；目前 CI workflow 仍指定 `postgres:16`，本機安裝與測試說明改採 PostgreSQL 19（此文件更新未修改 CI）。

| 項目 | 結果 |
| --- | --- |
| 回歸測試 | 93 個測試方法，90 通過、3 個 opt-in 真實模型測試跳過；0 失敗（含 7 個 PostgreSQL 整合測試全部通過） |
| 舊 bigram + 舊取段 | 17/22 案例找齊必要段落，平均召回 81.8% |
| BM25 + 新取段 | 21/22 案例找齊必要段落，平均召回 95.5% |
| 相同 top 2 的聚焦問題：舊 bigram / BM25 | 均為 16/18，88.9% |
| Hybrid、全量實機、語意品質 | 未執行完整評測 |

27 個情境中，22 個有必要公告段落標籤，其餘須人工或工具契約驗收。離線 BM25 仍遺漏 ranked-slang 的 2026-11/launch/玩法。新取段的候選策略也有改變，因此不可把 81.8% 到 95.5% 全部歸因於 BM25。Agent 實際會附活動背景，純檢索評測與端到端不同。

## 2. 分層測試

| 測試模組 | 主要保護 |
| --- | --- |
| tests/test_player_progress.py | PostgreSQL 初始化、持久化、帳號／檔期隔離、查詢錯誤及日期 |
| tests/test_guide.py | 檔期、資料、公告檢索、商城及工具基礎 |
| tests/test_bounded_agent.py | 工具選擇、補查、權限、多輪指涉與 fixture 重播 |
| tests/test_harness.py | Graph 節點、停止檢查、預算、請求隔離 |
| tests/test_verified_lookup.py | 查無商品的安全正規化、錯誤引用與保護條件 |
| tests/test_answer_quality.py | 品質審查、修訂保護與覆蓋指標 |
| tests/test_answer_presentation.py | 參考資料去重、呈現、原文及拒答保護 |
| tests/test_markdown_contract.py | 初答與審查共用呈現契約及 Graph 流程 |
| tests/test_model_timeout.py | 模型傳輸 timeout 設定 |
| tests/test_starter_questions.py | 快速提問顯示與送出一致 |
| tests/test_live_agent.py | opt-in 真實 Ollama 工具與證據覆蓋驗收 |

不因測試檔多而刪除保護；以 tests/ 集中管理。離線腳本模型驗證後端控制與流程，不代表模型自身會正確選擇工具。

## 3. 執行指令

下列用 Python 表示已安裝 requirements 的虛擬環境 Python。Windows 可將 python 換為 .\.venv\Scripts\python.exe，macOS/Linux 換為 ./.venv/bin/python。

先依 [QUICKSTART 第 4 節](QUICKSTART.md#4-共用-postgresql-19-初始化ab-均必須) 啟動 `pgvector/pgvector:pg19-trixie`，確認 PostgreSQL 19 就緒且主機 DSN 驗證成功，再依 [第 8 節](QUICKSTART.md#8-測試) 建立獨立空白測試資料庫並設定 `EVENT_SUPPORT_AGENT_PG_DSN` 為 `postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent_test`。整合測試使用獨立 schema；既有腳本回歸依賴未修改的初始示範資料。禁止對正式或已修改的 demo 資料庫執行。測試完成後切回 demo DSN（資料庫 `event_support_agent`）。

```bash
python player_progress.py
python -m unittest discover -s tests -t . -v
python -m pip check
python -m evaluations.eval_rag
python -m evaluations.eval_rag --case ranked-slang
```

先依 [QUICKSTART](QUICKSTART.md) 建好 hybrid 索引，再執行：

```bash
python -m evaluations.eval_rag --hybrid
python -m evaluations.eval_rag --hybrid --case ranked-slang
```

Hybrid 離線評測會使用嵌入模型與 pgvector，不使用聊天模型。本機完整回歸使用真實 PostgreSQL 19，但不需 Ollama 或 pgvector extension；純 BM25 檢索評測仍不需資料庫連線。

## 4. 真實模型評測

先啟動模型服務及 PostgreSQL 19，依 QUICKSTART 設定環境變數、驗證 DSN、初始化玩家進度並查詢玩家 A 餘額。聊天檢索模式由 EVENT_SUPPORT_AGENT_RETRIEVAL 指定；--hybrid 與 --live 不可同時使用。

```bash
python -m evaluations.eval_rag --case first-night --live ollama --strict --report reports/first-night.json
python -m evaluations.eval_rag --live ollama --report reports/ollama.json
```

OpenAI 使用 --live openai 並設定 API key。完整題集耗時依模型及設備而異，建議先跑代表案例。

Windows PowerShell 啟用 opt-in：

```powershell
$env:EVENT_SUPPORT_AGENT_LIVE_OLLAMA = "1"
.\.venv\Scripts\python.exe -m unittest -v tests.test_live_agent.LiveOllamaTests.test_representative_dialogues
.\.venv\Scripts\python.exe -m unittest -v tests.test_live_agent.LiveOllamaTests.test_explicit_negative_lookup_is_answered_with_verified_evidence
.\.venv\Scripts\python.exe -m unittest -v tests.test_live_agent.LiveOllamaTests.test_fixture_retrieval_and_account_scope
Remove-Item Env:EVENT_SUPPORT_AGENT_LIVE_OLLAMA
```

macOS/Linux 可用 EVENT_SUPPORT_AGENT_LIVE_OLLAMA=1 ./.venv/bin/python -m unittest -v tests.test_live_agent 直接執行三項。--strict 只檢查標記必要來源的檢索及採用是否有缺漏，不是語意驗收。

## 5. 指標與人工驗收

| 指標 | 現況 |
| --- | --- |
| 檢索召回及必要來源找齊 | evaluations/eval_rag.py 自動計算 |
| 實機可用證據、最終引用採用 | 逐案例分開計算，排除不適用段落 |
| 模型呼叫、Token、耗時 | JSON report；供應商缺計數保留 null，非 0 |
| 工具選擇與參數正確率 | 部分測試有斷言；完整比例未建立 |
| 回答正確性、完整性、相關性、排版 | 對照 reference 人工驗收，尚未有全量語意分數 |
| 能力邊界、帳號隔離、錯誤前提 | 腳本及部分實機驗收；不可把安全降級算成完全正確 |

每次比較固定程式版本、資料、題集、模型及執行環境。報告提供 UTC 日期、Python/OS、provider、configured_model、檢索模式、工具選擇、審查及 Stop Gate；另人工記錄硬體、模型實際版本與量化及嵌入模型。勿分享含私人資料或 API key 的紀錄。

比較 Review Auto/Off、Stop Gate Auto/Off 時，每次只變更一個設定，兩組都保留完整原始結果與人工判定。不要因單題失敗改整體觸發策略。

## 6. 已知限制

目前支援唯讀客服查詢，不具備實際發放、兌換或退款能力。能力範圍辨識、審查的相關性與延遲，以及繁體中文呈現仍待改善。完整混合檢索與真實模型語意評測尚未完成；安全降級不代表回答完全正確。

原生工具選擇的腳本測試涵蓋空回應、補查、Stop Gate 與帳號限制。模型是否選對工具仍須以實際 Ollama 模型驗收。

## 7. 分階段實機品質門檻

使用本機現存的完整工程 Log（勿提交私人資料）。先用固定證據比較 legacy（舊 quote 契約）、ids（新 ID 契約、保留完整證據欄位）、compact（精簡證據）。這三組為逐步設計比較，不是只變更單一欄位的嚴格因果實驗。legacy 只適用包含舊 quote schema 的 Log。

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_answer --log path/to/turn.json --provider ollama --variant legacy --repeat 3 --report reports/legacy.json
.\.venv\Scripts\python.exe -m evaluations.eval_answer --log path/to/turn.json --provider ollama --variant ids --repeat 3 --report reports/ids.json
.\.venv\Scripts\python.exe -m evaluations.eval_answer --log path/to/turn.json --provider ollama --variant compact --repeat 3 --report reports/compact.json
```

macOS/Linux 將 Python 路徑改為 `./.venv/bin/python`。先固定模型、資料、硬體與設定，再逐次比較 EVENT_SUPPORT_AGENT_OLLAMA_REASONING=false、其他模型或 OpenAI；使用 `ollama ps` 記錄 CPU/GPU、模型與 Context。報告包含實際 Token、供應商時間與回答，無須再次選工具或做 RAG。

端到端結構門檻：每個選定案例、每次重跑均不能拒絕應回答的問題、遺漏必要標記來源、超出檔期或本人查詢範圍；應拒答案例需正確標示限制。以下命令失敗回傳非零，不等同完整語意證明。

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live ollama --case first-night --gate --repeat 3 --report reports/first-night.json
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live ollama --gate --repeat 3 --report reports/full.json
```

人工語意門檻：對照每題 reference，確認事實正確、必要限制完整、沒有捏造資料、拒答及部分回答合理；三次重跑均通過才能認定該案例驗收。比較模型與 Reasoning 時記錄延遲、Token、任務成功率與失敗原因，不能把安全降級當作成功。未執行真實模型或人工審查時，不可宣稱新版已達語意品質門檻。


## 第一層意圖獨立驗收

依玩家最終目的分類（knowledge / query / analysis / operation / other），每類獨立寫明定義、適用、不適用、判斷原則及對照範例。不因查詢手段、資料不足或執行權限改變意圖。第一層不再接收能力清單；第二層仍保留原有能力資料。既有 Schema、Audit 判斷邏輯、工具、證據、Renderer 與 Verifier 不變。

啟動 Ollama 並使用原有模型設定，在專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_intent
```

macOS/Linux：`./.venv/bin/python -m evaluations.eval_intent`。逐題輸入，空白行離開；各題獨立，不需要 PostgreSQL 或 Streamlit。工具與後續節點不執行。輸出 type、原問片段、契約是否有效、模型呼叫及耗時。格式錯誤維持既有最多一次修正；契約有效不表示分類正確。

單題可加 `--question "你的問題"`；`--cases` 逐一執行 evaluations/intent_cases.json（保留原 23 題及預期答案，補充分類理由和 13 個邊界案例，共 36 題），`--case ID` 單獨執行指定題目。標記題集獨立於 Prompt；expected/rationale 不會傳送模型。

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_intent_analysis -v
.\.venv\Scripts\python.exe -m evaluations.eval_intent --case diagnosis --repeat 3 --report reports/intent-diagnosis.json
.\.venv\Scripts\python.exe -m evaluations.eval_intent --cases --report reports/intent-all.json
```

離線單元測試不需要模型、PostgreSQL 或 Streamlit，只驗證共用入口、契約、標記資料、第一層與 Audit 輸入邊界和評測統計。真實模型驗收另外執行。報告分開統計 contract_valid、count_match、type_sequence_match、span_match、exact_match，並記錄模型設定、Prompt 雜湊、原始輸出、耗時及供應商資訊；連線錯誤計入失敗，未知呼叫數保留 null。任何嚴格比對不符回傳非零。多輪題的歷史僅在題集中明確提供；標點或等價切分須人工複核，不能只看總分。固定同一模型、設定及題集比較版本，再以未見問句驗證泛化。

**階段門檻**：第一層離線測試通過後，逐題核對分類與拆分；代表性案例可用 --repeat 3 確認一致性，並加入未見問句人工檢查。未確認結果前不進入第二層修改。第一層通過不代表整體回歸或語意品質通過。

### 新題泛化驗收（20 題）

原 36 題已於 Qwen3:8b 的一次實機驗收達到 36/36（Prompt SHA-256：162ce182352501066f1e84c52d70a2a0ee3c4b7c9eb769f11ce637ac85e2469b）。保留原題庫作為回歸基準；目前同一 Prompt 不需要再次執行這 36 題。獨立 `evaluations/intent_holdout_cases.json` 有 20 個新問句及預期答案，涵蓋五類意圖、服務主題邊界、知識與結論、同句複合需求及歷史指涉。新題沒有加入 Prompt，預期答案與理由仍只供主機比對。

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_intent --cases --suite holdout --report reports/intent-holdout.json
```

macOS/Linux：`./.venv/bin/python -m evaluations.eval_intent --cases --suite holdout --report reports/intent-holdout.json`。此指令只執行新題庫一次；執行時會顯示選用題庫與題數，結果 JSON 記錄 suite、case_file。若某題失敗，可用 `--case holdout-boundary-match --suite holdout`（將 ID 換成報告中的 case）單獨重測，不用重跑已通過案例。若沒有任何 --suite，既有 --cases 及 --case 繼續使用 baseline；不修改原分類器或輸出契約。若 Prompt 或模型設定後續改變，仍需要重新考慮舊案例回歸風險。

2026-11-26 使用者提供 Qwen3:8b、相同 Prompt 雜湊、holdout 單次實機報告：20/20 完全一致，24/24 拆分後意圖正確；每題一次模型呼叫，無契約修正。原 36 題與新 20 題各自單次共 56/56，足以凍結本輪第一層調整，但不能證明任意問句或多次試驗均穩定。不因目錄整理而重跑已通過的真實模型案例。

## 舊第二層意圖審查與業務綁定測試（歷史對照）

第一層已凍結（原 36 題與新 20 題各單次通過）；第二層使用預先標記的第一層需求，不重新呼叫第一層模型。評測共用正式 Audit Prompt、Schema、輸入封包、契約修正及 Python 業務核定，不執行工具、證據評估或回答生成，也不需要 PostgreSQL 或 Streamlit。

先啟動 Ollama（原本的 qwen3:8b 及設定），從專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_intent_audit -v
.\.venv\Scripts\python.exe -m evaluations.eval_audit --cases --report reports/audit-all.json
```

macOS/Linux 將 Python 路徑換成 `./.venv/bin/python`。指定案例：`python -m evaluations.eval_audit --case other-account --report reports/audit-single.json`；可加 --repeat 3 重複驗證。OpenAI 使用 --provider openai 並配置 API key。

題庫 `evaluations/audit_cases.json`：24 題，19 題為正確意圖（預期 accept），5 題故意提供錯誤分類、漏拆或多拆（預期 revise）；涵蓋所有 18 種現行 kind。預期答案及分類理由僅供主機比對，不傳入 LLM。revise 不會觸發第一層重跑。

報告分開記錄 contract_valid、decision_match、accept_decision_match、revise_decision_match；正確意圖另比較 identity、kind、edition、product、account、status，分母為 accept_cases（19）。exact_match 是各案例所有適用欄位均符合。檔期以主機解析後 ID 集合比對，商品接受同一商品的正式名稱、ID 或已登錄精確別名；終止任務的檔期為主機清空後的結果，不評估不必要的原始檔期欄位。商品仍核對原始 binding，避免不適用欄位被主機丟棄後算正確。revise 的 issues 是否真正指出問題需人工核對，不能僅依 decision 或非空 issues 宣稱語意通過。模型呼叫次數計算實際送出請求（包含契約修正），原始輸出與錯誤一併保留。

先完成第二層離線及真實模型驗收，才考慮第三層；不因格式正確或主機安全拒絕就宣稱模型判斷正確。若同一案例的合理等價解讀與標記不一致，先討論業務定義再修改，禁止為單一案例硬編碼。

## 新第二層：任務感知工具規劃（獨立實驗）

保留第一層五種意圖及已驗收的 56 題。新候選模組 `task_tool_planning.py` 接收完整一輪的固定第一層輸出，Python 建立任務 ID、分派允許的工具，對 operation/other 作主機分流；待處理任務一次批次原生 Tool Calling，逐任務核定工具名稱、參數及欄位。控制工具 report_task_status 只回報缺參數、帳號限制、不可用檔期或不支援資料，不查資料。首輪無本輪證據，calculate 不提供。指定商品存在性尚未核對時，同一分析任務內的依賴性本人查詢不得與商品查詢同批核准。

從專案根目錄執行（Windows PowerShell）：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_task_tool_planning -v
.\.venv\Scripts\python.exe -m evaluations.eval_task_tools --cases --report reports/task-tools-all.json
```

macOS/Linux 改用 `./.venv/bin/python`。單題使用 `--case single-balance` 取代 `--cases`，可加 `--repeat 3`。真實模型只需 Ollama 或 OpenAI（--provider openai），不需 PostgreSQL、Streamlit 或向量模型。題庫 `evaluations/task_tool_cases.json`：10 個單一需求、5 個同意圖複合需求、5 個混合意圖複合需求，共 20 輪、31 個任務；固定第一層結果，不重跑第一層模型，預期答案與理由不傳給模型。

報告記錄整輪 exact_match、逐任務 task_exact_match、status_match、tool_match、argument_match、contract_valid、原生 tool_calls、核定參數、錯誤、模型呼叫數與耗時。分別核對任務 ID、工具及欄位、檔期、商品、帳號範圍、限制狀態；不要求呼叫順序一致，檔期以解析後 ID、已登錄商品以等價名稱比較。檢索詞使用題庫的粗略概念匹配，仍需人工檢查語意；限制原因也須人工核對。錯誤或缺漏呼叫不得被合規子集掩蓋。純終止任務不呼叫 LLM。這是首輪規劃驗收，不測資料取得、跨輪依賴完成、證據隔離或最終回答。

**目前僅獨立候選，正式聊天流程仍使用原 Audit 與既有工具執行契約。** 舊 eval_audit 與 24 題保留為歷史對照，勿與新第二層分數混用。新規劃中的 task_id、fields、account_scope 是模型提案的 metadata，未直接擴充既有 ToolRegistry 執行介面。模型聲稱 self 不構成授權：主機拒絕明示 other/unclear、任意帳號參數，但無法單靠型別驗證辨識模型錯誤自稱 self；測試專門揭露此缺口。候選模組不執行任何私人或公開資料工具，整合前須解決語意錯綁與任務級資料隔離，不能以此驗收宣稱安全上線。

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


## Single Agent：證據依賴展開

最終回答可只引用已取得的計算結果短 ID。主機依本輪可信證據內的 `運算依據` 展開多層來源，去重並保留穩定順序，再執行原有引用及適用條件驗證。未知 ID、缺失原文、循環或不合法依賴會使受影響的子問題降級；不新增模型呼叫，不自動引入未被引用的計算。舊 `legacy_answer`、`intent_answer` 的引用契約不變。此機制僅保證來源追溯，不證明運算式選擇、數字來源或回答語意正確。測試：`python -m unittest tests.test_evidence_dependencies tests.test_single_agent -v`。


## NVIDIA 端到端批次評測

在 `research/nvidia-provider` 分支，保留原本 27 個 RAG 情境與 4 個業務案例及正式 Single Agent 流程。評測會依序呼叫同一個 `agent.answer`，每個案例重設對話，保留案例內的多輪歷史。先使用原 Demo PostgreSQL DSN，確保模型服務及資料查詢皆正常，再於同一終端執行（不需 Streamlit）。

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --case redemption-gap --case compare-months --case mixed-operation --case unknown-item-price --case another-player --case midnight --report reports/nvidia-smoke.json
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --report reports/nvidia-all.json
```

macOS/Linux 使用 `./.venv/bin/python`。需先設定 NVIDIA_API_KEY、NVIDIA_MODEL（選用）及 EVENT_SUPPORT_AGENT_PG_DSN。使用原 Demo 資料，勿提交含敏感資料的工程報告。每題完成即原子更新報告，可保留中斷前的結果；每題記錄模型回報 token、模型與工具耗時、工具呼叫、完整工程事件、必要來源及結構驗收。失敗案例保留錯誤類型，不將例外原文寫進報告。結構驗收通過不等於語意正確，必須逐題核對 reference；部分來源覆蓋要求可能比基本回答更嚴格。模型、供應商、取樣設定及運行環境不同，並非單變因效能實驗。先跑代表案例，再視結果跑全量 31 題。完整批次尚未實機驗證。

## Embedding provider comparison

For a controlled chat-model comparison fix EVENT_SUPPORT_AGENT_EMBED_PROVIDER explicitly; for a retrieval comparison fix the chat model. The live report records retrieval and effective embedding provider/model. Offline --hybrid uses the explicit environment selection (auto resolves to Ollama without a chat provider). See QUICKSTART for commands. Existing coverage gates and business tools are unchanged. Cloud embedding requests are real API requests even in offline retrieval evaluation.

### 雲端 Embedding 服務失敗與召回率

離線 Hybrid 評測將耗盡暫時性錯誤重試的案例獨立列為執行失敗，保留其他案例的結果及完整 BM25 基準。Hybrid 召回率明確限定於成功完成且具有標記來源的案例；有任何此類錯誤時列出失敗案例 ID，回傳 exit code 1，不將服務失敗視為檢索漏召回，也不將成功子集當作全量成績。固定 Embedding provider/model 後可用 --case 重測失敗案例。


## NVIDIA 批次評測節流

NVIDIA API Catalog 試用速率依模型、帳號及當前負載而異；請於 build.nvidia.com 帳號查看限額。完整評測預設每分鐘最多 20 次 NVIDIA API 請求（本專案保守初始值，不是官方保證配額）。聊天與 Embedding 共用程序內節流器，重試也計入；每次請求開始至少間隔 60 / RPM 秒，模型已耗時超過間隔就不額外等待。429 時延後下一次請求，遵守 Retry-After；未提供則等待 60 秒。既有 Embedding 重試預算與 LLM 呼叫預算不增加；LLM 429 仍記錄該案例失敗，繼續其餘案例。HTTP 500 不等同於限流。多個程序或其他裝置不共用本節流器；不要同時跑多套評測。

Windows PowerShell：

```powershell
$env:EVENT_SUPPORT_AGENT_EMBED_PROVIDER = "nvidia"
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "hybrid"
.\.venv\Scripts\python.exe -m evaluations.eval_rag --hybrid --nvidia-rpm 20
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --nvidia-rpm 20 --report reports/nvidia-guardrails-hybrid.json
```

如仍頻繁出錯，將 --nvidia-rpm 20 改為 10。0 停用。未傳參數時使用 EVENT_SUPPORT_AGENT_NVIDIA_RPM，若未設定，批次預設 20；一般聊天與向量建置未設定時不節流。永久使用者設定（重新開啟 PowerShell 生效）：

```powershell
[Environment]::SetEnvironmentVariable("EVENT_SUPPORT_AGENT_NVIDIA_RPM", "20", "User")
```

評測報告記錄 nvidia_request_pacing_rpm；耗時包含等待與重試，不與未節流的效能直接比較。報告逐題保存，但沒有自動續跑；可用 --case 重測失敗案例，另存報告。

### Single Agent 終止契約回歸

Native submit_answer 與 Schema-only 均先通過原有證據驗證再結束。非 supported 附正文或來源視為契約錯誤（保留既有明確查無的可信原文恢復）。混合查無與正常證據不放行，回饋來源範圍及拆分方式。沿用原有一次格式修復及模型/工具總上限；若已耗盡，仍安全呈現通過的獨立部分。不變更評測門檻、RAG 排名、適用條件原文呈現或帳號權限。先跑單元測試，再以 --case wallet-b --case nov-title --case nov-sticker --case incident 等案例實測。後兩題的語意充分性仍須人工驗收，不宣稱本次已解決。


## Evaluation V2：使用既有報告重新評分

舊 `eval_rag --gate/--strict`、原始題庫和報告不變；V2 是獨立、唯讀的離線評分器，無模型、資料庫、LangChain 或 API key 依賴。分別呈現契約、實際安全邊界與模型禁止提案、任務狀態／必要工具、證據群組、工具效率、服務可靠性及人工語意評分。等價證據由凍結的 `evaluations/rubric_v2.json` 明確標記，群組內為 OR、每個選項為 AND；不同群組都必須滿足。不是文字相似度或全商城來源互換。原公告 RAG 召回率與舊 gate 保留。公用公告額外檔期派發是效率診斷；錯用檔期證據、私有資料錯範圍與禁止工具仍單獨判定失敗；模型提案不等於主機執行。所有已記錄輪次依各輪預期分別檢查。

Windows PowerShell（**不需要再跑 NVIDIA**）：

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_report reports/nvidia-guardrails-hybrid.json --out reports/nvidia-guardrails-hybrid-v2.json --review-template reports/nvidia-guardrails-hybrid-review.json
.\.venv\Scripts\python.exe -m evaluations.eval_report reports/nvidia-hybrid-nvidia-embed.json --out reports/nvidia-hybrid-v2.json
```

macOS/Linux 改用 `./.venv/bin/python`。BM25 歷史報告以實際檔名代入。輸出路徑須不存在，避免覆蓋原始報告。V2 同時記錄來源報告及 rubric 的 SHA256（規範化 JSON）。只有吻合題目、參考、玩家、檔期及既有必要來源標記的報告可重評；部分完成/執行錯誤會列明並回傳非零，V2 不以機械分數判定正式驗收。舊報告無 transport 欄位就標記未知，不能填 0。未執行輪次也不算通過。

人工語意評分：開啟 review JSON，填 reviewer，對每一輪的 correctness、completeness、groundedness、uncertainty、relevance 填 pass、fail、not_run；核對原始報告的問題、回答、工具原文、引用及 rubric，已評分項目須填 notes（理由）。未確認的不評分。審查必須匹配原報告及 rubric 雜湊，避免拿別次的分數當作本次結果。

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_report reports/nvidia-guardrails-hybrid.json --review reports/nvidia-guardrails-hybrid-review.json --out reports/nvidia-guardrails-hybrid-v2-reviewed.json
```

九個新問句另存 holdout，先凍結不調 Prompt；它與原題庫用相同示範語料，但並非獨立第三方資料集。確認原報告重評與離線回歸後再執行：

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --suite holdout --nvidia-rpm 20 --report reports/nvidia-holdout.json
.\.venv\Scripts\python.exe -m evaluations.eval_report reports/nvidia-holdout.json --out reports/nvidia-holdout-v2.json --review-template reports/nvidia-holdout-review.json
```

重複試驗用 --repeat 3；不要混合不同套件、模型、語料與設定的成績。每輪新報告 transport 記錄 chat/embedding adapter attempts、成功/失敗、HTTP 狀態、重試與節流等待；只使用安全欄位，無憑證或正文。SDK 內部未觀測請求不冒充已知；並行等待加總不是牆鐘時間。20 RPM 與原重試預算未改。

## 不確定性指引第一階段：驗收

保留原始 31 題、九題 Holdout、V2 rubric 與舊 gate。新版只調整模型指引與工具描述，沒有新增自動語意 grader；不可將證據覆蓋、契約通過當成語意通過。先執行 `python -m unittest tests.test_single_agent tests.test_eval_report -v` 及 AGENTS.md 的完整離線回歸，再使用相同 NVIDIA 模型、Hybrid、Embedding、20 RPM，另存報告。由於先前已閱讀 Holdout 結果，本輪 Holdout 用於回歸，不代表真正未見泛化。先小批針對不確定性、部分回答及明確否定確認，再視需要重複試驗與新獨立問句。

Windows PowerShell：

```powershell
$env:EVENT_SUPPORT_AGENT_EMBED_PROVIDER = "nvidia"
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "hybrid"
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --case incident --case compare-months --case wallet-a --case missing-policy --case unrecorded-days --case wallet-b --nvidia-rpm 20 --report reports/nvidia-uncertainty-smoke.json
.\.venv\Scripts\python.exe -m evaluations.eval_report reports/nvidia-uncertainty-smoke.json --out reports/nvidia-uncertainty-smoke-v2.json --review-template reports/nvidia-uncertainty-smoke-review.json
```

語意複核逐項核對：缺乏支持的主張、適當拒答、過度拒答、必要條件是否保留、是否出現無必要補查。使用現有五項人工評分記錄具體原因；未執行保持 not_run。本次尚未引入主張切分/自動比率計算，不能宣稱 Unsupported Claim Rate 或 Appropriate Abstention 等指標已自動計算。相同案例的單次結果只供診斷；跨版本比較應使用相同測試設定、題集、重複次數並核對完整 Trace。

## 第二階段離線回歸與實機檢查

先跑新增主機限制模組與 Single Agent 回歸（不需模型 API）：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_answer_limitations tests.test_single_agent tests.test_eval_report -v
```

macOS/Linux 使用 `./.venv/bin/python`。依 AGENTS.md 初始化獨立測試 PostgreSQL，再跑完整 unittest、pip check 及離線 RAG。確認通過後，若需實機驗收，先以既有相同 NVIDIA Hybrid 配置測試三個案例（保留舊報告）：

```powershell
$env:EVENT_SUPPORT_AGENT_EMBED_PROVIDER = "nvidia"
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "hybrid"
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --case unrecorded-days --case missing-policy --case wallet-b --nvidia-rpm 20 --report reports/nvidia-limitations-smoke.json
.\.venv\Scripts\python.exe -m evaluations.eval_report reports/nvidia-limitations-smoke.json --out reports/nvidia-limitations-smoke-v2.json
```

人工確認未知需求的限制說明只反映本輪工具狀態、不洩漏值、不宣稱全庫查無；多個未知需求不重複拒答。保留 supported 的正確答案與引用。舊評測規則不變，勿把限制摘要當成語意驗收通過。此次不需為了呈現改動重新建向量索引。

### 效率改善回歸

先執行無模型 API 的測試：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_retrieval_efficiency tests.test_single_agent tests.test_eval_report tests.test_guide -v
```

部分 guide/Agent 測試需要已初始化的獨立 PostgreSQL；依 AGENTS.md 再執行完整回歸。確認通過後使用相同模型及 Hybrid 設定測試（舊報告保留；不需重建索引）：

```powershell
$env:EVENT_SUPPORT_AGENT_EMBED_PROVIDER = "nvidia"
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "hybrid"
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --case compare-months --case incident --case wallet-a --case wallet-b --case missing-policy --case unrecorded-days --nvidia-rpm 20 --report reports/nvidia-efficiency-smoke.json
.\.venv\Scripts\python.exe -m evaluations.eval_report reports/nvidia-efficiency-smoke.json --out reports/nvidia-efficiency-smoke-v2.json
```

逐題比對必要來源、狀態及語意；檢查 retrieval_diagnostics 的 query_vector reuses、ranking reuses、新段落數、schema reasons、repair codes，以及 transport 的實際請求數和延遲。一次試驗不能證明 Token 或模型呼叫降低；以相同案例、模型、配置及重複試驗比較，禁止因少查而過度拒答。舊資料不存在的觀測欄位保持未知。

## 第四階段整合驗收（現行 research/nvidia-provider）

先使用 QUICKSTART 第 8 節的獨立測試 PostgreSQL，勿用正式或已修改的 demo DB。以下為 Windows PowerShell；macOS/Linux 使用 ./.venv/bin/python。

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe player_progress.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m evaluations.eval_rag
```

上述預設不呼叫模型（勿開啟 opt-in LIVE_OLLAMA）；test_final_contract_v2 驗證 schema、主張、來源與模擬三個 provider adapter；test_single_agent 驗證相依工具同批、未確認品項、缺相依欄位及同批未取得來源的拒絕。先確認全部通過，再切回正常 demo DSN 及已建立的 hybrid 索引。此次沒有修改公告或 embedding 模型，不需重建索引。先針對 NVIDIA 及另外兩個可用的實機模型做少量相容性驗收；不要在未確認回傳合法前耗用完整題庫配額。OpenAI/Ollama 實測必須先完成各自的 API key/本機服務設定；不具備環境時保持未驗證。為各供應商分別使用對應 embedding 設定與索引，或在 schema smoke 使用 BM25。

```powershell
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "bm25"
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --case missing-policy --case wallet-a --nvidia-rpm 20 --report reports/nvidia-contract-v2-smoke.json
# 選擇已配置的其他 provider，使用獨立檔名：
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live ollama --case missing-policy --case wallet-a --report reports/ollama-contract-v2-smoke.json
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live openai --case missing-policy --case wallet-a --report reports/openai-contract-v2-smoke.json
```

確認 native 及 Schema fallback 實際回傳、契約版本、私人工具依賴和主張引用；若 smoke 沒有觸發其中一路徑，該路徑仍未實機驗證，不宣稱完整相容。使用已建 NVIDIA hybrid 索引及相同 20 RPM，依序跑原 baseline 31、既有 holdout 9、另編 generalization 6；不要同時執行多個評測。

```powershell
$env:EVENT_SUPPORT_AGENT_EMBED_PROVIDER = "nvidia"
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "hybrid"
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --suite baseline --nvidia-rpm 20 --report reports/nvidia-integrated-baseline.json
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --suite holdout --nvidia-rpm 20 --report reports/nvidia-integrated-holdout.json
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live nvidia --suite generalization --nvidia-rpm 20 --report reports/nvidia-integrated-generalization.json
```

新評分標準需顯式指定 --rubric（不傳仍是原 2.0.0）。舊報告可重評新標準，但不會得到新版模型回答；留存原評分與 rubric hash，不覆蓋任何檔案。

```powershell
foreach ($suite in @("baseline","holdout","generalization")) {
    .\.venv\Scripts\python.exe -m evaluations.eval_report "reports/nvidia-integrated-$suite.json" --rubric evaluations/rubric_v2_1.json --out "reports/nvidia-integrated-$suite-v2_1.json" --review-template "reports/nvidia-integrated-$suite-review.json"
}
```

人工在 review 檔案填 reviewer、criteria（pass/fail/not_run）與 notes；逐主張核對 claim_evidence、原文、必要條件、未知需求、安全及相關性。以同一 rubric 再執行 --review，使用不同 --out 檔名。先驗收正確性及安全，再比較 model_requests、tokens、HTTP、cache、schema repair 及耗時。原 legacy gate 不因新版等價證據而改寫，單次試驗不代表穩定效益；新題集尚未經實機測試。
