# 評測與驗收

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
