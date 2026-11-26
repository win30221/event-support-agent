# 測試使用指南

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
