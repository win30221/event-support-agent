# 評測與驗收

[返回 README](../README.md) · [設計決策](DESIGN_DECISIONS.md)

## Intent-first 實驗分支驗收

新流程的腳本測試位於 tests/test_intent_workflow.py；原有針對舊回答契約的測試明確使用 legacy_answer，保留基準，不代表新流程通過所有舊案例。真實模型測試及 eval_rag.py 使用新預設入口。task_cases.json 增加操作拒絕、混合需求、不存在的商品與差額案例；只加入實機重播，原 27 題離線檢索基準不變。

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_intent_workflow -v
.\.venv\Scripts\python.exe eval_rag.py --live ollama --case unsupported-operation --gate --report reports/intent-operation.json
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
python eval_rag.py
python eval_rag.py --case ranked-slang
```

先依 [QUICKSTART](QUICKSTART.md) 建好 hybrid 索引，再執行：

```bash
python eval_rag.py --hybrid
python eval_rag.py --hybrid --case ranked-slang
```

Hybrid 離線評測會使用嵌入模型與 pgvector，不使用聊天模型。本機完整回歸使用真實 PostgreSQL 19，但不需 Ollama 或 pgvector extension；純 BM25 檢索評測仍不需資料庫連線。

## 4. 真實模型評測

先啟動模型服務及 PostgreSQL 19，依 QUICKSTART 設定環境變數、驗證 DSN、初始化玩家進度並查詢玩家 A 餘額。聊天檢索模式由 EVENT_SUPPORT_AGENT_RETRIEVAL 指定；--hybrid 與 --live 不可同時使用。

```bash
python eval_rag.py --case first-night --live ollama --strict --report reports/first-night.json
python eval_rag.py --live ollama --report reports/ollama.json
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
| 檢索召回及必要來源找齊 | eval_rag.py 自動計算 |
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
.\.venv\Scripts\python.exe eval_answer.py --log path/to/turn.json --provider ollama --variant legacy --repeat 3 --report reports/legacy.json
.\.venv\Scripts\python.exe eval_answer.py --log path/to/turn.json --provider ollama --variant ids --repeat 3 --report reports/ids.json
.\.venv\Scripts\python.exe eval_answer.py --log path/to/turn.json --provider ollama --variant compact --repeat 3 --report reports/compact.json
```

macOS/Linux 將 Python 路徑改為 `./.venv/bin/python`。先固定模型、資料、硬體與設定，再逐次比較 EVENT_SUPPORT_AGENT_OLLAMA_REASONING=false、其他模型或 OpenAI；使用 `ollama ps` 記錄 CPU/GPU、模型與 Context。報告包含實際 Token、供應商時間與回答，無須再次選工具或做 RAG。

端到端結構門檻：每個選定案例、每次重跑均不能拒絕應回答的問題、遺漏必要標記來源、超出檔期或本人查詢範圍；應拒答案例需正確標示限制。以下命令失敗回傳非零，不等同完整語意證明。

```powershell
.\.venv\Scripts\python.exe eval_rag.py --live ollama --case first-night --gate --repeat 3 --report reports/first-night.json
.\.venv\Scripts\python.exe eval_rag.py --live ollama --gate --repeat 3 --report reports/full.json
```

人工語意門檻：對照每題 reference，確認事實正確、必要限制完整、沒有捏造資料、拒答及部分回答合理；三次重跑均通過才能認定該案例驗收。比較模型與 Reasoning 時記錄延遲、Token、任務成功率與失敗原因，不能把安全降級當作成功。未執行真實模型或人工審查時，不可宣稱新版已達語意品質門檻。
