# Event Support Agent｜AI 活動客服系統

以遊戲活動為示範情境的 AI Agent PoC。整合 RAG 公告檢索、Function Tools、個人活動進度及多輪對話，透過有界 Agent Harness 與本輪引用驗證回答玩家問題。

本作品著重後端業務與 LLM 的整合、可靠性、可觀測性及可重現評測。星夜祭是示範活動名稱；所有公告、商品、角色和玩家資料皆為原創模擬資料。系統僅提供唯讀查詢及運算，不是正式營運客服，不具備發幣、兌換或退款功能。

## 實驗分支：Intent-first Task Workflow

本分支自 `refactor/langgraph-agent` 的 3ea6e67 建立，預設入口 `agent.answer` 使用新流程。舊工作流仍保留在 `agent.legacy_answer` 供離線比較，原分支不變。優先驗證語意及業務結果，暫不以延遲為優化目標。

意圖拆分 → 主機核定需求及必要資料 → 原生工具 → 獨立證據評估 → 主機呈現結構化事實／LLM 生成知識回答 → 語意驗證 → 分項完成或降級。無全域 action/missing；不支援的操作、權限限制與資料不足分開處理。驗證模型可能判斷錯誤，尚未完成真實模型驗收。現有畫面為基準版本展示，不代表本分支實測成果。

既有虛擬環境及 PostgreSQL 19 不變；已安裝基準版本依賴時不必重裝。停止 Streamlit 後切換本分支，再從同一設定好的終端啟動。

## 回覆完成音效

回覆音效及左側「顯示工程檢視」預設開啟，可自行關閉。正常送出問題或操作頁面時，會嘗試解除瀏覽器 Web Audio 播放限制，不必先按音效開關。新回覆進入聊天室時播放三聲上行提示音，查詢失敗則為下行音；同一回覆不因 rerun 重播。整頁重載後恢復預設開啟。瀏覽器背景分頁節流、靜音及自動播放政策仍可能影響播放或提醒時間，不保證關閉頁面後也能收到通知。不需額外音檔或套件。

本實驗分支採用精簡意圖：先回傳五類 type 與原問 request，再做意圖覆蓋與業務綁定審查，工具前確認必要欄位。仍需實機驗收。

## 第一層意圖測試

測試及評測入口總覽：[測試使用指南](docs/TESTING.md)。所有指令從專案根目錄執行。

第一層五類意圖各自具有定義、適用、不適用、判斷原則及對照範例。只理解語意與拆分需求，不接收工具能力及權限清單。先執行 `python -m unittest tests.test_intent_analysis -v`（離線契約測試），再執行 `python -m evaluations.eval_intent`（自由提問），或 `python -m evaluations.eval_intent --cases`（真實模型標記題集）。可用 `--case diagnosis --repeat 3 --report reports/intent.json` 單獨重跑；不執行 Audit 或任何資料工具，不需資料庫。已通過原題庫時，可用 `python -m evaluations.eval_intent --cases --suite holdout --report reports/intent-holdout.json` 只跑新增的 20 題。詳見 [EVALUATION](docs/EVALUATION.md#第一層意圖獨立驗收)。

## 舊第二層意圖審查測試（歷史對照）

第一層固定輸出，獨立評測第二層 Audit 與 Python 業務核定。執行 `python -m unittest tests.test_intent_audit -v` 或 `python -m evaluations.eval_audit --cases --report reports/audit-all.json`（真實模型，24 題）。不需資料庫，不重跑第一層 56 題。詳見 [TESTING](docs/TESTING.md)。

## 新第二層任務感知工具規劃

獨立候選實驗：第一層固定輸出，Python 分派工具，LLM 一次原生 Tool Calling，逐任務核定參數。執行 `python -m evaluations.eval_task_tools --cases --report reports/task-tools-all.json`（20 輪、31 個任務）。不執行資料工具、不重跑第一層、不改正式聊天流程。詳見 [TESTING](docs/TESTING.md)。

## 操作展示

本機 qwen3:8b 對話：活動玩法 → 商城價格 → 本人餘額 → 要求客服贈送星星幣。

![Event Support Agent 聊天展示](docs/assets/demo.png)

展示公告檢索、商城查詢、本人進度與唯讀服務的安全降級。系統目前不提供發放或兌換獎勵等寫入操作。

## 核心功能與技術

| 功能 | 技術及設計 |
| --- | --- |
| Agent Harness | LangGraph StateGraph / MessagesState / ToolNode、逐需求狀態與有界工具循環 |
| RAG | BM25；選用 Ollama bge-m3 + PostgreSQL/pgvector，RRF 排名融合 |
| 工具 | 公開公告、商城、指定商品、本人 PostgreSQL 進度及四則運算 |
| LLM | Ollama / OpenAI；ChatOllama / ChatOpenAI、共用工具登錄與結構化回答契約 |
| 可靠性 | 工具參數驗證、帳號綁定、適用條件、引用驗證、部分回答 |
| UI 與觀測 | Streamlit、參考資料、查詢紀錄、互動流程及模型用量 |
| 品質與評測 | 自動化回歸測試、RAG 檢索評測及真實模型重播；詳見 [EVALUATION](docs/EVALUATION.md) |

Ollama 與 OpenAI 由 LangChain 共用工具契約，ChatOllama 使用 Ollama SDK /api/chat；不是 MCP。引用存在性不等同於回答語意完全正確。

## 從零啟動（含 RAG）

首次完整體驗請依 [QUICKSTART](docs/QUICKSTART.md) 安裝 Python、Ollama 與 Docker，下載聊天及嵌入模型。使用 PostgreSQL 19（`pgvector/pgvector:pg19-trixie`），等待資料庫就緒、核對 DSN、初始化並查詢玩家 A 的 2026-11 餘額（全新 demo 預期 80 枚），再建立 pgvector 公告索引、驗證檢索並從同一終端啟動客服。文件提供 Windows PowerShell 與 macOS/Linux 全部指令。

若不使用向量檢索，可採同文件的 BM25 路徑，略過嵌入模型及向量索引建置；玩家進度仍需 PostgreSQL 19。已有相容 PostgreSQL 19 服務時可不使用 Docker。新版容器 `event-support-agent-pg19` 使用新資料卷 `event-support-agent-pg19data`（掛載 `/var/lib/postgresql`），沿用 `127.0.0.1:55432`；保留舊資料卷，不直接跨大版本掛載。

## 專案結構

```text
app.py                 # Streamlit 入口
agent.py               # Graph 節點
agent_runtime.py       # 共用工具、證據、基準工作流
intent_analysis.py    # 五類意圖與原問錨定、業務綁定
task_contracts.py      # 意圖、證據評估、生成與驗證契約
task_business.py       # 主機能力、必要欄位及結構化呈現
task_runtime.py        # 新的逐需求 LangGraph 工作流
tool_selection.py      # 工具選擇介接
model_client.py        # LangChain 原生模型介接、Structured Output 與紀錄
request_context.py     # 可信多輪指涉
backend.py             # 工具契約、權限與證據
guide.py               # 公告 BM25、商城、進度契約
database.py            # 共用 PostgreSQL 連線
player_progress.py     # 初始化與唯讀玩家進度
vector_search.py       # pgvector 索引與混合檢索
answering.py           # Pydantic 回答契約與來源驗證
evidence.py            # 可信證據的模型投影與短 ID
answer_quality.py      # 有界品質審查
starter_questions.py   # 快速提問 UI
events.json            # 公開活動公告及商城
player_progress_seed.json # 玩家初始示範資料
requirements.txt
tests/                 # unittest 單元、整合及 opt-in 實機測試
evaluations/           # 評測腳本與標記題庫
  eval_intent.py       # 第一層真實模型評測
  eval_audit.py        # 第二層審查與業務綁定評測
  audit_cases.json     # 第二層標記題庫
  eval_rag.py          # RAG、端到端實機重播
  eval_answer.py       # 固定證據回答評測
  intent_cases.json / intent_holdout_cases.json
  rag_cases.json / task_cases.json
reports/               # 本機評測報告（Git 忽略）
docs/                  # 技術文件及展示資產
AGENTS.md              # 開發規範
```

## 技術文件

| 文件 | 說明 |
| --- | --- |
| [QUICKSTART](docs/QUICKSTART.md) | 從零安裝、RAG 索引、啟動及更新 |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | 架構、節點、生命週期及信任邊界 |
| [DATA_AND_TOOLS](docs/DATA_AND_TOOLS.md) | 資料、檢索、工具及輸出契約 |
| [TESTING](docs/TESTING.md) | 測試目錄、各類型指令及環境需求 |
| [EVALUATION](docs/EVALUATION.md) | 測試分層、實際數據及驗收方式 |
| [DESIGN_DECISIONS](docs/DESIGN_DECISIONS.md) | 現行技術設計原則 |
| [REFERENCES](docs/REFERENCES.md) | 官方參考來源、採用與研究範圍 |
| [TROUBLESHOOTING](docs/TROUBLESHOOTING.md) | 常見錯誤及排查方式 |
| [AGENTS](AGENTS.md) | 後續開發規範 |

## 回答品質與實機驗收

模型只回傳本輪短來源 ID，由程式取得原文；未知 ID 或壞格式只拒絕受影響的獨立需求。共用工具的查詢模式精簡為 overview/search/compare；同輪重複子查詢共享檢索。預設關閉額外品質審查，以離線及實機評測比較。CI 不代表語意驗收，詳見 [EVALUATION](docs/EVALUATION.md)。
