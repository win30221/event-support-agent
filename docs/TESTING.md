# 測試使用指南

[返回 README](../README.md) · [安裝與資料庫](QUICKSTART.md) · [評測設計及歷史結果](EVALUATION.md)

## 1. 目錄結構

```text
tests/                           # unittest：單元、整合及 opt-in 實機測試
  test_intent_analysis.py        # 第一層契約與評測器測試
  test_intent_workflow.py        # 意圖工作流及後續節點
  test_player_progress.py        # PostgreSQL 整合
  test_live_agent.py             # opt-in 真實模型
  test_*.py                      # 其他回歸測試
evaluations/
  __init__.py
  eval_intent.py                 # 第一層 LLM 分類
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
