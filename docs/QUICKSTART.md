# Event Support Agent｜從零安裝與啟動

[返回 README](../README.md) · [疑難排解](TROUBLESHOOTING.md)

首次完整體驗採 A：BM25 + pgvector 混合 RAG；B 為不需向量索引的 BM25 路徑。兩者皆使用 PostgreSQL 19 查詢玩家進度，已有相容 PostgreSQL 19 服務時可不使用 Docker。現有程式預設 bm25，文件推薦 hybrid 並不表示已修改預設值。

## 1. 環境需求

- Python 3.11 以上（CI 為 3.11）、可用虛擬環境。
- [Ollama](https://ollama.com/download)：聊天 qwen3:8b，混合檢索另需 bge-m3。
- [Docker](https://docs.docker.com/get-docker/)：本機 PostgreSQL 19 + pgvector（`pgvector/pgvector:pg19-trixie`）；兩條路徑均需 PostgreSQL。
- 選用 OpenAI API key。模型速度視硬體、模型量化、上下文而定，不保證固定延遲。

## 2. 安裝專案

將取得的專案檔案解壓縮或放置於本機資料夾，開啟終端並切換至包含 `app.py`、`requirements.txt` 的專案根目錄。以下指令皆從該目錄執行。

Windows PowerShell：

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import streamlit, langgraph, langchain_openai, langchain_ollama, pydantic, psycopg; print('OK')"
```

macOS/Linux：

```bash
python3 --version
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pip check
./.venv/bin/python -c "import streamlit, langgraph, langchain_openai, langchain_ollama, pydantic, psycopg; print('OK')"
```

若預設 Python 低於 3.11，改用已安裝的 3.11+ 建立 venv。以下全程使用相同 .venv Python，不需要執行環境啟用腳本。

## 3. Ollama 模型

先啟動 Ollama 桌面程式；若尚未運作，可在另一終端執行 `ollama serve`。不要重複啟動已占用 port 的服務。

```bash
ollama list
ollama pull qwen3:8b
ollama pull bge-m3
ollama list
```

B 路徑可不下載 bge-m3。即使用 OpenAI 聊天，A 路徑仍需 Ollama 嵌入模型。

## 4. 共用 PostgreSQL 19 初始化（A、B 均必須）

先啟動 Docker Desktop 或 Docker Engine，執行 `docker ps -a` 檢查容器。首次建立使用新容器及新資料卷；不要將舊 `event-support-agent-pgdata` 掛到 PostgreSQL 19，也不要刪除舊資料卷。PostgreSQL 18 起官方映像的 VOLUME 改為 `/var/lib/postgresql`，19 的預設 PGDATA 為 `/var/lib/postgresql/19/docker`；因此不再掛載 `/var/lib/postgresql/data`。需要舊資料時請先備份，再依相容的邏輯備份／還原或正式升級程序遷移，不是直接重用舊實體資料目錄。

若舊容器占用 55432，確認可中斷後只停止舊容器（`docker stop event-support-agent-pg`），保留其容器和資料卷。若其他服務占用，參考[埠衝突及密碼驗證](TROUBLESHOOTING.md#postgresql-19-連線與驗證)。帳密只供本機 demo，主機 port 僅綁 loopback。

### Windows PowerShell

1. 首次建立容器；若已建立 `event-support-agent-pg19`，改用 `docker start event-support-agent-pg19`，不要再執行 docker run。

```powershell
docker run --name event-support-agent-pg19 -e POSTGRES_DB=event_support_agent -e POSTGRES_PASSWORD=event-support-agent-demo -p 127.0.0.1:55432:5432 -v event-support-agent-pg19data:/var/lib/postgresql -d pgvector/pgvector:pg19-trixie
```

2. 等待 PostgreSQL 就緒（最多約兩分鐘），出現 accepting connections 才繼續。

```powershell
for ($i = 0; $i -lt 60; $i++) {
    docker exec event-support-agent-pg19 pg_isready -h 127.0.0.1 -p 5432 -U postgres -d event_support_agent
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
}
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL is not ready; check docker logs." }
```

3. 在同一個終端設定 DSN，先驗證主機連線、密碼及版本，成功後初始化並查詢。

```powershell
$env:OLLAMA_MODEL = "qwen3:8b"
$env:EVENT_SUPPORT_AGENT_PG_DSN = "postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent"
.\.venv\Scripts\python.exe -c "from database import connect; db = connect(); print(db.execute('SHOW server_version').fetchone()[0]); assert db.info.server_version // 10000 == 19; db.close()"
if ($LASTEXITCODE -ne 0) { throw "Check PostgreSQL version and DSN before initialization." }
.\.venv\Scripts\python.exe player_progress.py
if ($LASTEXITCODE -ne 0) { throw "Player progress initialization failed." }
.\.venv\Scripts\python.exe -c "from player_progress import read_progress; row = read_progress('A', '2026-11'); print('2026-11 A coins:', row[1] if row else 'no record')"
```

### macOS/Linux

1. 首次建立容器；若已建立 `event-support-agent-pg19`，改用 `docker start event-support-agent-pg19`。

```bash
docker run --name event-support-agent-pg19 -e POSTGRES_DB=event_support_agent -e POSTGRES_PASSWORD=event-support-agent-demo -p 127.0.0.1:55432:5432 -v event-support-agent-pg19data:/var/lib/postgresql -d pgvector/pgvector:pg19-trixie
```

2. 等待 PostgreSQL 就緒（最多約兩分鐘），最後一次檢查須出現 accepting connections，否則查看容器日誌，不要繼續。

```bash
for i in {1..60}; do
    docker exec event-support-agent-pg19 pg_isready -h 127.0.0.1 -p 5432 -U postgres -d event_support_agent && break
    sleep 2
done
docker exec event-support-agent-pg19 pg_isready -h 127.0.0.1 -p 5432 -U postgres -d event_support_agent
```

3. 在同一個終端設定 DSN；逐行確認成功後才執行下一行。

```bash
export OLLAMA_MODEL=qwen3:8b
export EVENT_SUPPORT_AGENT_PG_DSN='postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent'
./.venv/bin/python -c "from database import connect; db = connect(); print(db.execute('SHOW server_version').fetchone()[0]); assert db.info.server_version // 10000 == 19; db.close()"
./.venv/bin/python player_progress.py
./.venv/bin/python -c "from player_progress import read_progress; row = read_progress('A', '2026-11'); print('2026-11 A coins:', row[1] if row else 'no record')"
```

兩種平台的 DSN 均為 `postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent`（主機 55432 對應容器內 5432）。`pg_isready` 只確認服務接受連線，不能證明密碼正確；上面的 Python 查詢使用與客服相同的連線工廠，會實際驗證 DSN 與 PostgreSQL 19。全新空白 demo 初始化預期新增 3 筆，餘額查詢輸出 `2026-11 A coins: 80`。查不到或報錯請先排查，不要直接啟動 UI。

重跑初始化只新增不存在的帳號／檔期，不會覆寫已儲存的進度；既有資料的餘額不一定是 80。資料庫 volume 保留資料，客服查詢不會重新載入種子資料。請勿以正式資料庫執行示範初始化。

確認上述步驟成功後，保留此終端的 DSN，依需求擇一執行第 4A（hybrid）或第 4B（BM25），最後從同一終端啟動 Streamlit。

## 4A. 首次完整 RAG（推薦）

完成第 4 節後，在同一個終端執行：

Windows PowerShell：

```powershell
$env:EVENT_SUPPORT_AGENT_EMBED_MODEL = "bge-m3"
$env:EVENT_SUPPORT_AGENT_EMBED_URL = "http://localhost:11434"
.\.venv\Scripts\python.exe vector_search.py
.\.venv\Scripts\python.exe -m evaluations.eval_rag --hybrid --case ranked-slang
.\.venv\Scripts\python.exe -m evaluations.eval_rag --hybrid
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "hybrid"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

macOS/Linux：

```bash
export EVENT_SUPPORT_AGENT_EMBED_MODEL=bge-m3
export EVENT_SUPPORT_AGENT_EMBED_URL=http://localhost:11434
./.venv/bin/python vector_search.py
./.venv/bin/python -m evaluations.eval_rag --hybrid --case ranked-slang
./.venv/bin/python -m evaluations.eval_rag --hybrid
export EVENT_SUPPORT_AGENT_RETRIEVAL=hybrid
./.venv/bin/python -m streamlit run app.py
```

vector_search.py 自動建立 extension、資料表及公開公告向量（本版預期 41 段）。確認索引成功後才啟用 hybrid。離線 hybrid 評測會呼叫嵌入模型與資料庫，不呼叫聊天模型；不保證找齊所有段落。overview/compare 直接讀公告，商城和本人進度由專用工具處理，因此不是每題都會呼叫向量模型。

## 4B. BM25 輕量 RAG

完成第 2、3、4 節及玩家 A 餘額查詢後，在同一終端保留 PostgreSQL 19 DSN。可略過 bge-m3 及向量索引建置，直接執行下列對應平台指令。

```powershell
$env:OLLAMA_MODEL = "qwen3:8b"
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "bm25"
.\.venv\Scripts\python.exe -m evaluations.eval_rag
.\.venv\Scripts\python.exe -m streamlit run app.py
```

```bash
export OLLAMA_MODEL=qwen3:8b
export EVENT_SUPPORT_AGENT_RETRIEVAL=bm25
./.venv/bin/python -m evaluations.eval_rag
./.venv/bin/python -m streamlit run app.py
```

## 5. 開啟介面與驗收

開啟終端顯示的 Local URL（通常 http://localhost:8501），選本機 Ollama 與玩家 A，開啟工程檢視。

| 問題 | 預期驗收 |
| --- | --- |
| 新手怎麼玩這個活動？ | 報名、正式冒險、前三場各 20 枚、100 枚披風；有引用 |
| 排位賽有算嗎？ | 本期排位不計，查詢適用公告 |
| 這期商城有什麼？ | 五款商品與價格 |
| 我目前有多少星星幣？ | A 為 80 枚；B 80；C 0 |
| 這週末有雙倍獎勵嗎？ | 週末不加倍，核對公告 |
| 我今天再打一場冒險能換披風嗎？ | A 尚有一場額度，B 已達上限 |

驗收是目標，不代表每次實機輸出都相同。檢查查詢紀錄、引用及互動流程。玩家資料由 PostgreSQL 持久化保存；表內資料為查詢時的權威來源，上述餘額是首次初始化的預期值。

### 選用 OpenAI 聊天

關閉舊 Streamlit；在同一個環境設定自己的 key 與可用模型，重新啟動並於側欄選 OpenAI。勿將真實 key 寫入專案檔案或分享含私人資料的工程紀錄。雲端模式會傳送對話及工具資料。

```powershell
$env:OPENAI_API_KEY = "YOUR_KEY"
$env:OPENAI_MODEL = "gpt-4o-mini"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

```bash
export OPENAI_API_KEY='YOUR_KEY'
export OPENAI_MODEL=gpt-4o-mini
./.venv/bin/python -m streamlit run app.py
```

保留第 4 節 PostgreSQL DSN；若採 hybrid，亦保留第 4A 的設定。

## 新分支測試

既有基準環境可停止 Streamlit 後執行 git fetch origin，再以 git switch --track origin/refactor/intent-task-simple 首次切換；已建立本地分支時改用 git switch refactor/intent-task-simple 與 git pull --ff-only。先檢查 git status，不覆蓋未提交修改。從原終端以既有 DSN、模型及檢索設定啟動 Streamlit。不需重建資料庫。工程檢視應出現 Python Intent Workflow、意圖分析、需求清單、證據充足性評估及獨立語意驗證（終止需求不需查詢）。

### 回覆音效

回覆音效與工程檢視預設開啟，不需要先按音效開關。送出問題或正常滑鼠／鍵盤操作時會嘗試解除 Web Audio 播放限制。可在左側手動關閉；同頁 rerun 保留設定，整頁重載恢復預設。成功／失敗提示音由瀏覽器生成，無額外依賴。若無聲音，核對分頁靜音、網站播放權限及裝置音量，並點一下頁面。背景分頁可能延遲，關閉頁面不會收到提醒。

## 6. 環境變數

ChatOllama 直接透過 Ollama SDK 的原生 /api/chat。舊 OLLAMA_BASE_URL 末尾 /v1 會自動移除；新設定請使用下表主機 URL。

| 變數 | 預設與用途 |
| --- | --- |
| OLLAMA_BASE_URL | http://localhost:11434 |
| OLLAMA_MODEL | qwen3:8b |
| OPENAI_API_KEY | 無；OpenAI 必填 |
| OPENAI_MODEL | gpt-4o-mini |
| EVENT_SUPPORT_AGENT_RETRIEVAL | bm25；完整 RAG 可設 hybrid |
| EVENT_SUPPORT_AGENT_PG_DSN | 無；玩家進度查詢及 hybrid 必填 |
| EVENT_SUPPORT_AGENT_EMBED_MODEL | bge-m3 |
| EVENT_SUPPORT_AGENT_EMBED_URL | http://localhost:11434（不加 /v1） |
| EVENT_SUPPORT_AGENT_STOP_GATE | 僅基準 legacy_answer；auto 或 off |
| EVENT_SUPPORT_AGENT_ANSWER_REVIEW | 僅基準 legacy_answer；新流程獨立語意驗證預設啟用 |
| EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS | 120；非負整數秒，0 為無 SDK HTTP timeout |
| EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX | 未設定；選用正整數 context size |
| EVENT_SUPPORT_AGENT_OLLAMA_REASONING | 未設定；選用 true/false 或模型支援的 low/medium/high |
| EVENT_SUPPORT_AGENT_LIVE_OLLAMA | 未設定；1 啟用 opt-in 實機測試 |

程序直接讀環境變數，不自動載入 .env。新終端須重新設定；timeout 是 socket timeout，不是整輪 Agent deadline。慢速模型可先提高有限 timeout（例如 600），不建議一律無限等待。

## 7. 下次啟動、更新與重建

若從專案改名前的版本更新，請重新設定本文件的環境變數。Docker 容器、volume、示範資料庫及公告向量資料表名稱均已統一；PostgreSQL 19 使用新容器 `event-support-agent-pg19` 及新資料卷 `event-support-agent-pg19data`，首次使用請依第 4 節初始化。舊 `event-support-agent-pgdata` 保留原狀；若需保留既有玩家進度，先備份舊資料庫，再將資料還原或遷移至新資料庫，不直接跨大版本掛載，也不要刪除原有 volume（包括避免 `docker volume prune`）。採 hybrid 時需重新執行第 4A 節的向量索引建置。

取得新版專案檔案後重新執行第 2 節的套件安裝與 `pip check`。停止舊 Streamlit，確認 Ollama 服務、模型及環境變數；若 PostgreSQL 容器未啟動，先執行 `docker start event-support-agent-pg19`，再依第 4 節等待就緒、驗證 DSN 及版本、執行 `player_progress.py` 並查詢玩家 A。從同一終端啟動 Streamlit。

更新 events.json 後重新啟動。公開公告內容、標題、發布日、段落或嵌入模型改變時，hybrid 必須重跑 vector_search.py 及檢索評測；商城或玩家資料不納入公告向量；玩家進度更新以資料庫為準，修改 seed 不會覆寫既有列。BM25 查詢時直接使用公開公告，不需額外索引建置。索引不可用時依疑難排解處理，不把錯誤當成查無資料。

## 8. 測試

測試目錄與指令總覽見 [TESTING](TESTING.md)。評測模組一律從專案根目錄使用 `python -m evaluations.eval_*`（以實際模組名稱取代星號），不要切換至 evaluations/ 執行。

本機完整回歸使用 PostgreSQL 19 的獨立空白測試資料庫，勿使用正式或已修改的 demo 資料。先完成第 4 節服務就緒及 DSN 驗證，再執行以下建立專用測試資料庫的指令（兩種平台皆可，僅首次建立時執行；若同名資料庫已存在，先確認其為未修改的測試資料庫，否則另取新測試庫名稱及 DSN，不要刪除 demo 資料）。

```bash
docker exec -e PGPASSWORD=event-support-agent-demo event-support-agent-pg19 psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE event_support_agent_test;"
```

Windows PowerShell：

```powershell
$env:EVENT_SUPPORT_AGENT_PG_DSN = "postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent_test"
.\.venv\Scripts\python.exe player_progress.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m evaluations.eval_rag
$env:EVENT_SUPPORT_AGENT_PG_DSN = "postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent"
```

macOS/Linux：

```bash
export EVENT_SUPPORT_AGENT_PG_DSN='postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent_test'
./.venv/bin/python player_progress.py
./.venv/bin/python -m unittest discover -s tests -t . -v
./.venv/bin/python -m pip check
./.venv/bin/python -m evaluations.eval_rag
export EVENT_SUPPORT_AGENT_PG_DSN='postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent'
```

逐行確認成功；測試後切回 demo DSN 並重做第 4 節的餘額查詢，再依第 4A 或 4B 啟動客服。此處是本機 PostgreSQL 19 測試流程，既有 CI 服務設定仍使用 PostgreSQL 16；完整實機重播與歷史基準見 [EVALUATION](EVALUATION.md)。

Ollama 請選用支援 tool calling 的模型。兩個供應商的工具選擇都使用原生呼叫。


## 單獨測試第一層意圖

完成 Python 套件安裝並啟動 Ollama 後，即可執行 `.\.venv\Scripts\python.exe -m evaluations.eval_intent`（macOS/Linux：`./.venv/bin/python -m evaluations.eval_intent`）。不需先啟動 PostgreSQL 或 Streamlit。逐題輸入，空白行結束。完整題集加 `--cases`，詳見 [EVALUATION](EVALUATION.md#第一層意圖獨立驗收)。

離線契約測試：`python -m unittest tests.test_intent_analysis -v`（不需 Ollama 或 PostgreSQL）。真實模型指定單題 ID：`python -m evaluations.eval_intent --case diagnosis --repeat 3 --report reports/intent-diagnosis.json`。完整題集：`python -m evaluations.eval_intent --cases --report reports/intent-all.json`。Windows 使用同一虛擬環境的 `.\.venv\Scripts\python.exe`；macOS/Linux 使用 `./.venv/bin/python`。報告放 reports/，預設不納入 Git。

原 36 題已通過且 Prompt 不變時，只執行新增 20 題：`.\.venv\Scripts\python.exe -m evaluations.eval_intent --cases --suite holdout --report reports/intent-holdout.json`（macOS/Linux：`./.venv/bin/python` 取代 Python 路徑）。失敗題可用 `--case ID --suite holdout` 單獨重測。

第二層獨立評測（只需已安裝依賴、啟動模型）：`.\.venv\Scripts\python.exe -m evaluations.eval_audit --cases --report reports/audit-all.json`；macOS/Linux 換成 `./.venv/bin/python`。詳見 [TESTING](TESTING.md)。

新第二層候選實驗（只驗首輪工具規劃）：`.\.venv\Scripts\python.exe -m evaluations.eval_task_tools --cases --report reports/task-tools-all.json`，macOS/Linux 換成 `./.venv/bin/python`。舊 eval_audit 為歷史對照，正式 UI 暫未切換。詳見 [TESTING](TESTING.md)。

第二層獨立新題庫：`python -m evaluations.eval_task_tools --cases --suite holdout --report reports/task-tools-holdout.json`（20 輪、33 任務），預設 baseline 不變。詳見 TESTING 文件。
