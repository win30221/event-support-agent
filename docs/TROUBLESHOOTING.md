# 常見問題與排查

[返回 README](../README.md) · [完整安裝](QUICKSTART.md)

下列 python 皆指專案 .venv 的直譯器。

| 症狀 | 排查與處理 |
| --- | --- |
| ModuleNotFoundError: langgraph | 安裝或更新專案後執行 python -m pip install -r requirements.txt；python -c "import langgraph"；用同一 Python 啟動 Streamlit |
| Python 版本不足 | 使用 3.11+ 重建 venv，確認 python --version |
| Ollama connection refused | 啟動 Ollama，ollama list，核對 OLLAMA_BASE_URL 預設 http://localhost:11434 |
| Ollama model not found | ollama pull qwen3:8b 或指定實際已裝且相容的 OLLAMA_MODEL |
| 本機 Vulkan/Flash Attention pipeline 失敗 | 先核對 Ollama 版本、GPU driver、記憶體及日誌；已觀察到部分 Windows 環境可透過 OLLAMA_FLASH_ATTENTION=0 排查，需重啟 Ollama 服務才生效，不是本專案設定或通用解法 |
| 模型回應逾時 | 核對模型日誌及硬體；必要時提高 EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS（如 600），預設 120；0 代表 SDK HTTP 無 timeout，不建議預設使用 |
| OpenAI API key 缺失或 401 | 在啟動程序同一終端設定正確 OPENAI_API_KEY；確認模型存取權、額度；勿將 key 寫入 repo |
| 原生工具呼叫空內容 | 兩個供應商共用一次原生工具選擇重試機會。查看 provider meta，勿自動增加無限重試 |
| JSON 回答不符合契約 | 查看格式驗證事件；既有流程只有有限修復機會，非 supported 正文不直接採用 |
| ImportError psycopg | 執行 python -m pip install -r requirements.txt（兩種檢索模式皆需 psycopg） |
| PostgreSQL 19 連線失敗 | `docker ps -a`、`docker start event-support-agent-pg19`、`docker exec event-support-agent-pg19 pg_isready -h 127.0.0.1 -p 5432 -U postgres -d event_support_agent`；依下方步驟驗證 DSN、版本及密碼 |
| PostgreSQL 密碼驗證失敗 | `pg_isready` 成功不代表帳密正確；核對 DSN 及既有資料卷初始化時的密碼，詳見下方步驟 |
| 玩家進度資料表不存在 | 確認 DSN 後執行 python player_progress.py；資料庫錯誤不是查無玩家紀錄 |
| 初始玩家餘額未恢復 | 初始化不覆寫既有資料；使用獨立空白測試資料庫重現預設紀錄，切勿直接重設正式資料 |
| Docker 名稱或 port 衝突 | 已建立的 PG19 容器用 `docker start event-support-agent-pg19`；先找出占用 55432 的服務並安全處理，詳見下方步驟 |
| 無法取得 bge-m3 向量 | ollama list、ollama pull bge-m3；確認 EVENT_SUPPORT_AGENT_EMBED_URL 為 http://localhost:11434，不加 /v1 |
| 索引缺失或公告版本不符 | 停止舊程序，確認 DSN、嵌入模型及資料，執行 python vector_search.py、python -m evaluations.eval_rag --hybrid；成功才啟動 hybrid |
| 更新資料後仍是舊內容 | 確認已使用最新專案檔案、停止舊 Streamlit 並重新啟動；公開公告變更須重建 hybrid 索引 |
| unittest 找不到舊 test 模組 | 現在位於 tests/；python -m unittest discover -s tests -t . -v，指定類別使用 tests.test_live_agent 等完整路徑 |
| 快速提問文字不同 | 新版應提交按鈕原文；若仍使用舊版，確認專案檔案與程序是否已更新 |
| 清單沒有換行或混入簡體 | 先核對原始模型回傳；共用 Markdown 指引及繁體中文 Prompt 不保證每次遵循，目前沒有 OpenCC 轉換 |
| 回答過長或補充不必要資訊 | 工程檢視確認審查有無執行；保留 auto/off 對照，先評估必要資訊與延遲，不直接停用保護 |

## PostgreSQL 19 連線與驗證

### 1. 容器名稱、資料卷與埠衝突

```bash
docker ps -a
docker ps --filter publish=55432
docker volume ls
docker logs --tail 100 event-support-agent-pg19
docker inspect --format '{{.Config.Image}} {{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}' event-support-agent-pg19
docker port event-support-agent-pg19 5432
```

預期映像為 `pgvector/pgvector:pg19-trixie`、資料卷 `event-support-agent-pg19data:/var/lib/postgresql`、主機 port `127.0.0.1:55432`。PostgreSQL 19 的 PGDATA 預設為 `/var/lib/postgresql/19/docker`。舊 `event-support-agent-pgdata` 必須保留，不直接掛到 PG19、不執行 volume 刪除或 prune。

若 Docker 沒有列出占用 55432 的容器，檢查主機的監聽程序。

Windows PowerShell：

```powershell
Get-NetTCPConnection -LocalPort 55432 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess
```

macOS/Linux：

```bash
lsof -nP -iTCP:55432 -sTCP:LISTEN
```

Linux 若未安裝 lsof，可使用 `ss -ltnp` 查找 55432。確認服務可中斷後才停止；若為舊 demo 容器，可執行 `docker stop event-support-agent-pg`，不刪容器或資料卷。釋放埠後，已存在（包括 Created 狀態）的 PG19 容器可用 `docker start event-support-agent-pg19`，否則依 QUICKSTART 第 4 節首次建立。若既有容器映像或掛載錯誤，先核對／備份資料，再重建容器，不刪資料卷。

預設維持 55432。只有無法釋放該埠時才另選 loopback port（如 55433），建立容器時改為 `-p 127.0.0.1:55433:5432`；已建立的容器不能用 docker start 改 port，需在確認資料卷保留後重建容器。兩種平台的 DSN（包括測試 DSN）也必須同步改成 55433，容器內 PostgreSQL 仍是 5432。

### 2. 就緒、版本與密碼驗證

```bash
docker exec event-support-agent-pg19 pg_isready -h 127.0.0.1 -p 5432 -U postgres -d event_support_agent
```

出現 accepting connections 只代表服務接受連線，不會保證指定帳密能登入。若是 connection refused，先確認容器狀態、日誌、port mapping 與 DSN。用專案虛擬環境從主機驗證真正連線（預設 demo 帳密）：

Windows PowerShell：

```powershell
$env:EVENT_SUPPORT_AGENT_PG_DSN = "postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent"
.\.venv\Scripts\python.exe -c "from database import connect; db = connect(); print(db.execute('SHOW server_version').fetchone()[0]); assert db.info.server_version // 10000 == 19; db.close()"
```

macOS/Linux：

```bash
export EVENT_SUPPORT_AGENT_PG_DSN='postgresql://postgres:event-support-agent-demo@127.0.0.1:55432/event_support_agent'
./.venv/bin/python -c "from database import connect; db = connect(); print(db.execute('SHOW server_version').fetchone()[0]); assert db.info.server_version // 10000 == 19; db.close()"
```

若出現 password authentication failed，先確認連到正確 PG19 容器與資料庫、角色及密碼（DSN URI 中的特殊字元須 percent-encode）。`POSTGRES_PASSWORD`、`POSTGRES_DB` 僅在空白資料目錄首次初始化時生效；重建容器但沿用已初始化的 volume 不會重設既有密碼。

若知道原密碼，使用正確 DSN；若需要重設自己的本機 demo 角色密碼且具備合法管理存取，可從該容器進入 psql：

```bash
docker exec -it event-support-agent-pg19 psql -U postgres -d postgres
```

在 psql 內執行 `\password postgres`，依提示輸入新密碼兩次，再用 `\q` 離開。若無法登入管理角色，停止操作並使用正規管理員復原流程；不要開啟 trust 驗證或刪除資料卷來繞過問題。修改密碼後更新同一終端的 DSN，重做上述主機連線驗證，依 QUICKSTART 第 4 節執行 player_progress.py、查詢玩家 A 的 2026-11 餘額（全新 demo 預期 80 枚），再由同一終端啟動 Streamlit。舊資料卷及已持久化的餘額不會被重置。

## LangGraph / LangChain 排錯

- Ollama 新版 base URL 使用 http://localhost:11434；舊 /v1 設定會自動去除。確認 Ollama 服務及模型支援 tools 與 json_schema。
- 核對互動流程內模型 metadata、實際 context、prompt_eval_duration、eval_duration；若需調整可設定 EVENT_SUPPORT_AGENT_OLLAMA_NUM_CTX 或 EVENT_SUPPORT_AGENT_OLLAMA_REASONING，需實測品質及延遲。
- ToolNode 只執行主機核准的唯讀工具。工具驗證錯誤、查無、服務錯誤及證據不足不同；不可直接放寬 registry 或帳號驗證。
- 回答格式錯誤會記錄具體欄位及原因；一次修復仍失敗則安全降級。歷史回答不視為本輪事實。

## 問題定位順序

1. 核對程式與資料版本、Python、模型、provider 與環境變數。
2. 跑離線測試及純檢索評測，先隔離是否為程式或資料問題。
3. 用相同玩家與對話重現，開啟工程檢視。
4. 確認工具是否正確、參數是否受允許、原始工具結果及適用條件。
5. 確認結構化回答的 action/status/source_ids、Stop Gate、審查結果及最終驗證。
6. 比對模型原始輸出與 UI 顯示，確認是否單純呈現問題。

查詢失敗、沒有新證據、商品不存在、權限不足、服務不具操作能力是不同原因。不要以更多檢索或無限補查當成通用解法。若向量索引或嵌入服務暫時不可用，可切回 BM25；玩家查詢仍依賴 PostgreSQL。工程紀錄只能使用模擬或已遮罩資料分享。

新版引用不再要求模型逐字複製：若短 ID 不存在，僅拒絕受影響的需求。優先檢查 `Python 回答上下文` 來源對照及 `Python 子問題驗證失敗`；可將工程 Log 另存於本機，使用 evaluations/eval_answer.py 固定證據重播，勿重跑整個 RAG 來診斷回答契約。詳見 EVALUATION。

評測腳本已移至 `evaluations/`。若找不到根目錄的舊腳本，從專案根目錄改用 `python -m evaluations.eval_intent`、`python -m evaluations.eval_rag` 或 `python -m evaluations.eval_answer`；參見 [TESTING](TESTING.md)。
