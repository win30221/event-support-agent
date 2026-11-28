# 測試使用指南


## 活動客服批次測試

以下命令從專案根目錄執行。使用既有虛擬環境及獨立測試庫。若沿用本專案的示範 PostgreSQL 容器且還沒有測試庫，可先執行一次 `docker exec event-support-agent-pg19 createdb -U postgres event_support_agent_test`。其他安裝方式請將 DSN 改成自己的測試庫。

```powershell
$py = ".\.venv\Scripts\python.exe"
$env:EVENT_SUPPORT_AGENT_PG_DSN = "postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent_test"
$env:OLLAMA_MODEL = "qwen3:8b"
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "bm25"
$env:EVENT_SUPPORT_AGENT_OLLAMA_REASONING = "false"
$env:EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX = "8192"
& $py player_progress.py
& $py -m unittest discover -s tests -t . -v
```

Ollama 需已啟動並具備此模型，首次可執行 `ollama pull qwen3:8b`。只跑有活動業務交付條件的 11 個實機情境：

```powershell
& $py -m evaluations.eval_rag --live ollama --suite business --gate --report reports/business.json
```

針對漏答與差額反覆驗證，兩個流程使用相同模型、檢索設定與種子。每項指令各跑兩題、每題三次：

```powershell
& $py -m evaluations.eval_rag --live ollama --suite business --case unknown-item-price --case redemption-gap --repeat 3 --gate --report reports/business-repeat.json
& $py -m evaluations.eval_rag --live ollama --workflow intent --suite business --case unknown-item-price --case redemption-gap --repeat 3 --gate --report reports/intent-repeat.json
```

查看每題失敗原因與最後一輪實際回答：

```powershell
$r = Get-Content reports/business.json -Raw -Encoding UTF8 | ConvertFrom-Json
$r.cases | Where-Object { $_.gate_issues.Count -gt 0 } | ForEach-Object {
    [PSCustomObject]@{ Case = $_.case; Trial = $_.trial; Issues = $_.gate_issues -join ", "; Reply = $_.turns[-1].reply }
} | Format-List
```

`--gate` 遇到漏答、錯誤的工具範圍或執行錯誤會回傳非零結束碼。每題結束都寫入 JSON，`complete` 表示指定試驗均已結束，通過與否見各題的 `gate_issues`。示範金額以新測試庫的種子為準，`player_progress.py` 重跑只補缺漏。macOS、Linux 或 WSL 可在啟用虛擬環境後使用相同的 `python -m evaluations.eval_rag` 參數。

[返回 README](../README.md) · [安裝與資料庫](QUICKSTART.md) · [評測設計及歷史結果](EVALUATION.md)

## 1. 目錄結構

```text
tests/                           # unittest：單元、整合及 opt-in 實機測試
  test_intent_analysis.py        # 第一層契約與評測器測試
  test_task_tool_planning.py     # 新第二層主機契約測試
  test_intent_audit.py           # 舊 Audit 契約測試
  test_intent_workflow.py        # 意圖工作流及後續節點
  test_player_progress.py        # PostgreSQL 整合
  test_live_agent.py             # opt-in 真實模型
  test_*.py                      # 其他回歸測試
evaluations/
  __init__.py
  eval_intent.py                 # 第一層 LLM 分類
  eval_task_tools.py             # 新第二層工具規劃
  task_tool_cases.json           # 新第二層 20 輪 / 31 任務
  eval_audit.py                  # 舊 Audit（歷史對照）
  audit_cases.json               # 第二層 24 題
  eval_rag.py                    # BM25 / hybrid / 端到端重播
  eval_answer.py                 # 固定證據的回答重播
  intent_cases.json              # 原 36 題
  intent_holdout_cases.json      # 新 20 題
  rag_cases.json                 # RAG 與 UI 練習題
  task_cases.json                # 實機業務案例
reports/                         # 本機產生，Git 忽略
```

正式 Agent、公開公告 events.json、玩家種子資料及初始化腳本留在原位置。四份題庫內容不變。既有 unittest 模組名稱不變。評測器改為 Python 模組入口，不使用根目錄舊腳本或自行插入 sys.path。

## 2. 執行環境

先依 QUICKSTART 安裝 requirements.txt。所有指令均從含 app.py 的專案根目錄執行。以下以 Windows PowerShell 示範；macOS/Linux 將 `.\.venv\Scripts\python.exe` 換成 `./.venv/bin/python`，其他參數相同。

| 測試 | Ollama 聊天模型 | PostgreSQL | 嵌入模型 |
| --- | --- | --- | --- |
| 第一層離線 unittest | 不需 | 不需 | 不需 |
| 第一層實機評測 | 需（或 OpenAI） | 不需 | 不需 |
| 第二層離線 unittest | 不需 | 不需 | 不需 |
| 第二層實機評測 | 需（或 OpenAI） | 不需 | 不需 |
| 完整 unittest（預設） | 不需 | 需獨立測試庫 | 不需 |
| 純 BM25 評測 | 不需 | 不需 | 不需 |
| Hybrid 評測 | 不需 | 需 pgvector 索引 | 需 |
| 端到端實機 | 需（或 OpenAI） | 需測試資料 | hybrid 時需 |
| 固定 Log 回答評測 | 需（或 OpenAI） | 不需重查 | 不需重查 |

## 3. 第一層意圖

離線契約及評測入口測試（無需模型、資料庫、Streamlit）：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_intent_analysis -v
```

真實模型自由提問（空白行退出）：

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_intent
```

指定單題問句：

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_intent --question "活動怎麼參加？"
```

原始題庫、獨立新題庫、指定單一案例：

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_intent --cases --report reports/intent-baseline.json
.\.venv\Scripts\python.exe -m evaluations.eval_intent --cases --suite holdout --report reports/intent-holdout.json
.\.venv\Scripts\python.exe -m evaluations.eval_intent --case holdout-boundary-match --suite holdout --repeat 3 --report reports/intent-single.json
```

原 36 題及新 20 題已於相同 Qwen3:8b Prompt 各單次全部通過。本次搬移目錄不需要重跑真實模型題庫。上述指令供日後變更或指定案例重測；兩個題庫相互獨立。自由提問只檢查契約，不提供自動語意正確率。標記題庫以 exact_match 為嚴格比對，分類序列、需求數量、逐字片段、模型呼叫與耗時另外記錄。

## 4. 完整回歸

先依 [QUICKSTART 第 8 節](QUICKSTART.md#8-測試) 建立獨立空白測試資料庫並設定 DSN，禁止使用正式或已修改的 demo 資料庫。完成後切回 demo DSN。

```powershell
.\.venv\Scripts\python.exe player_progress.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.\.venv\Scripts\python.exe -m pip check
```

單獨執行某個模組，例如 `-m unittest tests.test_intent_workflow -v`。完整回歸的 opt-in 真實模型測試預設跳過；第一層離線測試不依賴整合環境。此前完整回歸有四項既存失敗，不以目錄搬移或 mock 通過宣稱已修復。

## 5. RAG、端到端與回答評測

```powershell
.\.venv\Scripts\python.exe -m evaluations.eval_rag
.\.venv\Scripts\python.exe -m evaluations.eval_rag --case ranked-slang
.\.venv\Scripts\python.exe -m evaluations.eval_rag --hybrid
.\.venv\Scripts\python.exe -m evaluations.eval_rag --live ollama --case first-night --gate --report reports/first-night.json
.\.venv\Scripts\python.exe -m evaluations.eval_answer --log path/to/turn.json --provider ollama --variant compact --repeat 3 --report reports/answer.json
```

Hybrid 須先建好向量索引；端到端須啟動模型及測試資料庫；回答重播須提供現有工程 Log。詳見 [EVALUATION](EVALUATION.md)。可用各模組的 `--help` 查看完整參數。使用 OpenAI 時配置 API key，intent/answer 用 --provider openai，RAG 實機用 --live openai。

## 6. 報告與 CI

JSON 報告預設由使用者指定 --report 寫到本機 reports/，不納入 Git。不要提交含私人資料、工程 Log 或 API key 的紀錄。區分單元測試、資料庫整合、真實模型語意驗收；CI 不會自動執行第一層的 56 題實機評測。題庫或 Prompt 變更時，保留原基準並重新考慮回歸風險。

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

先重測原本五個失敗案例，使用 `--case` 逐一指定：`single-participation`、`same-two-items`、`mixed-rule-balance-operation`、`mixed-gap-and-balance`、`mixed-compare-and-other`。再執行完整 `--cases`，並比較 `--num-predict -1` 與預設 2048 的報告。第一層及正式聊天流程不受影響。

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
