# Event Support Agent｜從零安裝與啟動

[返回 README](../README.md) · [疑難排解](TROUBLESHOOTING.md)

首次完整體驗採 A：BM25 + pgvector 混合 RAG；B 為不需向量索引的 BM25 路徑。兩者皆使用 PostgreSQL 查詢玩家進度，已有相容資料庫服務時可不使用 Docker。現有程式預設 bm25，文件推薦 hybrid 並不表示已修改預設值。

## 1. 環境需求

- Python 3.11 以上（CI 為 3.11）、可用虛擬環境。
- [Ollama](https://ollama.com/download)：聊天 qwen3:8b，混合檢索另需 bge-m3。
- [Docker](https://docs.docker.com/get-docker/)：本機 PostgreSQL + pgvector；兩條路徑均需 PostgreSQL。
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
.\.venv\Scripts\python.exe -c "import streamlit, pydantic_graph, psycopg; print('OK')"
```

macOS/Linux：

```bash
python3 --version
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pip check
./.venv/bin/python -c "import streamlit, pydantic_graph, psycopg; print('OK')"
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

## 4. 共用 PostgreSQL 初始化（A、B 均必須）

啟動 Docker 後，兩種平台均可執行：

```bash
docker run --name event-support-agent-pg -e POSTGRES_DB=event_support_agent -e POSTGRES_PASSWORD=event-support-agent-demo -p 127.0.0.1:55432:5432 -v event-support-agent-pgdata:/var/lib/postgresql/data -d pgvector/pgvector:pg16
docker exec event-support-agent-pg pg_isready -U postgres -d event_support_agent
```

等 accepting connections 再繼續。已建立容器改用 `docker start event-support-agent-pg`。帳密只供本機 demo；port 僅綁 loopback。若 port 衝突，調整主機 port 及 DSN。已有 PostgreSQL 可直接使用；完整 RAG 路徑還需 pgvector extension。

Windows PowerShell：

```powershell
$env:OLLAMA_MODEL = "qwen3:8b"
$env:EVENT_SUPPORT_AGENT_PG_DSN = "postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent"
.\.venv\Scripts\python.exe player_progress.py
```

macOS/Linux：

```bash
export OLLAMA_MODEL=qwen3:8b
export EVENT_SUPPORT_AGENT_PG_DSN='postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent'
./.venv/bin/python player_progress.py
```

首次初始化會將 player_progress_seed.json 的三筆示範資料寫入 player_event_progress。重跑只新增不存在的帳號／檔期，不會覆寫已儲存的進度。資料庫 volume 保留資料，客服查詢不會重新載入種子資料。請勿以正式資料庫執行示範初始化。

## 4A. 首次完整 RAG（推薦）

完成第 4 節後，在同一個終端執行：

Windows PowerShell：

```powershell
$env:EVENT_SUPPORT_AGENT_EMBED_MODEL = "bge-m3"
$env:EVENT_SUPPORT_AGENT_EMBED_URL = "http://localhost:11434"
.\.venv\Scripts\python.exe vector_search.py
.\.venv\Scripts\python.exe eval_rag.py --hybrid --case ranked-slang
.\.venv\Scripts\python.exe eval_rag.py --hybrid
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "hybrid"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

macOS/Linux：

```bash
export EVENT_SUPPORT_AGENT_EMBED_MODEL=bge-m3
export EVENT_SUPPORT_AGENT_EMBED_URL=http://localhost:11434
./.venv/bin/python vector_search.py
./.venv/bin/python eval_rag.py --hybrid --case ranked-slang
./.venv/bin/python eval_rag.py --hybrid
export EVENT_SUPPORT_AGENT_RETRIEVAL=hybrid
./.venv/bin/python -m streamlit run app.py
```

vector_search.py 自動建立 extension、資料表及公開公告向量（本版預期 41 段）。確認索引成功後才啟用 hybrid。離線 hybrid 評測會呼叫嵌入模型與資料庫，不呼叫聊天模型；不保證找齊所有段落。overview/compare 直接讀公告，商城和本人進度由專用工具處理，因此不是每題都會呼叫向量模型。

## 4B. BM25 輕量 RAG

完成第 2、3、4 步，保留 PostgreSQL DSN 及玩家進度初始化。可略過 bge-m3 及向量索引建置。

```powershell
$env:OLLAMA_MODEL = "qwen3:8b"
$env:EVENT_SUPPORT_AGENT_RETRIEVAL = "bm25"
.\.venv\Scripts\python.exe eval_rag.py
.\.venv\Scripts\python.exe -m streamlit run app.py
```

```bash
export OLLAMA_MODEL=qwen3:8b
export EVENT_SUPPORT_AGENT_RETRIEVAL=bm25
./.venv/bin/python eval_rag.py
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

## 6. 環境變數

| 變數 | 預設與用途 |
| --- | --- |
| OLLAMA_BASE_URL | http://localhost:11434/v1 |
| OLLAMA_MODEL | qwen3:8b |
| OPENAI_API_KEY | 無；OpenAI 必填 |
| OPENAI_MODEL | gpt-4o-mini |
| EVENT_SUPPORT_AGENT_RETRIEVAL | bm25；完整 RAG 可設 hybrid |
| EVENT_SUPPORT_AGENT_PG_DSN | 無；玩家進度查詢及 hybrid 必填 |
| EVENT_SUPPORT_AGENT_EMBED_MODEL | bge-m3 |
| EVENT_SUPPORT_AGENT_EMBED_URL | http://localhost:11434（不加 /v1） |
| EVENT_SUPPORT_AGENT_TOOL_SELECTION | Ollama structured / OpenAI native；可覆寫 |
| EVENT_SUPPORT_AGENT_STOP_GATE | auto；off 關額外矛盾停止檢查，不取消原有預算 |
| EVENT_SUPPORT_AGENT_ANSWER_REVIEW | auto；off 用於對照 |
| EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS | 120；非負整數秒，0 為無 Python socket timeout |
| EVENT_SUPPORT_AGENT_LIVE_OLLAMA | 未設定；1 啟用 opt-in 實機測試 |

程序直接讀環境變數，不自動載入 .env。新終端須重新設定；timeout 是 socket timeout，不是整輪 Agent deadline。慢速模型可先提高有限 timeout（例如 600），不建議一律無限等待。

## 7. 下次啟動、更新與重建

若從專案改名前的版本更新，請重新設定本文件的環境變數。Docker 容器、volume、示範資料庫及公告向量資料表名稱均已統一；首次使用新容器請依第 4 節初始化。若需保留既有玩家進度，先備份舊資料庫，再將資料還原或遷移至新資料庫；不要刪除原有 volume。採 hybrid 時需重新執行第 4A 節的向量索引建置。

取得新版專案檔案後重新執行第 2 節的套件安裝與 `pip check`。停止舊 Streamlit，確認 Ollama 服務、模型及環境變數；若 PostgreSQL 容器未啟動，先執行 `docker start event-support-agent-pg`，再確認 DSN 與 `player_progress.py` 初始化成功。

更新 events.json 後重新啟動。公開公告內容、標題、發布日、段落或嵌入模型改變時，hybrid 必須重跑 vector_search.py 及檢索評測；商城或玩家資料不納入公告向量；玩家進度更新以資料庫為準，修改 seed 不會覆寫既有列。BM25 查詢時直接使用公開公告，不需額外索引建置。索引不可用時依疑難排解處理，不把錯誤當成查無資料。

## 8. 測試

完整回歸需要專用測試 PostgreSQL，勿使用正式或已修改的 demo 資料。建立獨立空白資料庫，將 EVENT_SUPPORT_AGENT_PG_DSN 指向該資料庫，先執行 `python player_progress.py` 初始化預設資料，再執行下列測試。測試結束後切回 demo DSN 才啟動客服。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.\.venv\Scripts\python.exe eval_rag.py
```

macOS/Linux 改用 `./.venv/bin/python`。完整實機重播與 JSON 報告見 [EVALUATION](EVALUATION.md)。
