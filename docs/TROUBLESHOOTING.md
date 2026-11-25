# 常見問題與排查

[返回 README](../README.md) · [完整安裝](QUICKSTART.md)

下列 python 皆指專案 .venv 的直譯器。

| 症狀 | 排查與處理 |
| --- | --- |
| ModuleNotFoundError: pydantic_graph | 安裝或更新專案後執行 python -m pip install -r requirements.txt；python -c "import pydantic_graph"；用同一 Python 啟動 Streamlit |
| Python 版本不足 | 使用 3.11+ 重建 venv，確認 python --version |
| Ollama connection refused | 啟動 Ollama，ollama list，核對 OLLAMA_BASE_URL 預設 http://localhost:11434/v1 |
| Ollama model not found | ollama pull qwen3:8b 或指定實際已裝且相容的 OLLAMA_MODEL |
| 本機 Vulkan/Flash Attention pipeline 失敗 | 先核對 Ollama 版本、GPU driver、記憶體及日誌；已觀察到部分 Windows 環境可透過 OLLAMA_FLASH_ATTENTION=0 排查，需重啟 Ollama 服務才生效，不是本專案設定或通用解法 |
| 模型回應逾時 | 核對模型日誌及硬體；必要時提高 EVENT_SUPPORT_AGENT_OLLAMA_TIMEOUT_SECONDS（如 600），預設 120；0 代表 Python socket 無 timeout，不建議預設使用 |
| OpenAI API key 缺失或 401 | 在啟動程序同一終端設定正確 OPENAI_API_KEY；確認模型存取權、額度；勿將 key 寫入 repo |
| 原生工具呼叫空內容 | 兩個供應商共用一次原生工具選擇重試機會。查看 provider meta，勿自動增加無限重試 |
| JSON 回答不符合契約 | 查看格式驗證事件；既有流程只有有限修復機會，非 supported 正文不直接採用 |
| ImportError psycopg | 執行 python -m pip install -r requirements.txt（兩種檢索模式皆需 psycopg） |
| PostgreSQL 連線失敗 | docker ps -a、docker start event-support-agent-pg、docker exec event-support-agent-pg pg_isready -U postgres -d event_support_agent；核對 DSN 及主機 port |
| 玩家進度資料表不存在 | 確認 DSN 後執行 python player_progress.py；資料庫錯誤不是查無玩家紀錄 |
| 初始玩家餘額未恢復 | 初始化不覆寫既有資料；使用獨立空白測試資料庫重現預設紀錄，切勿直接重設正式資料 |
| Docker 名稱或 port 衝突 | 既有容器用 docker start；其他服務占 port 時選另一 loopback port 並更新 DSN |
| 無法取得 bge-m3 向量 | ollama list、ollama pull bge-m3；確認 EVENT_SUPPORT_AGENT_EMBED_URL 為 http://localhost:11434，不加 /v1 |
| 索引缺失或公告版本不符 | 停止舊程序，確認 DSN、嵌入模型及資料，執行 python vector_search.py、python eval_rag.py --hybrid；成功才啟動 hybrid |
| 更新資料後仍是舊內容 | 確認已使用最新專案檔案、停止舊 Streamlit 並重新啟動；公開公告變更須重建 hybrid 索引 |
| unittest 找不到舊 test 模組 | 現在位於 tests/；python -m unittest discover -s tests -t . -v，指定類別使用 tests.test_live_agent 等完整路徑 |
| 快速提問文字不同 | 新版應提交按鈕原文；若仍使用舊版，確認專案檔案與程序是否已更新 |
| 清單沒有換行或混入簡體 | 先核對原始模型回傳；共用 Markdown 指引及繁體中文 Prompt 不保證每次遵循，目前沒有 OpenCC 轉換 |
| 回答過長或補充不必要資訊 | 工程檢視確認審查有無執行；保留 auto/off 對照，先評估必要資訊與延遲，不直接停用保護 |

## 問題定位順序

1. 核對程式與資料版本、Python、模型、provider 與環境變數。
2. 跑離線測試及純檢索評測，先隔離是否為程式或資料問題。
3. 用相同玩家與對話重現，開啟工程檢視。
4. 確認工具是否正確、參數是否受允許、原始工具結果及適用條件。
5. 確認結構化回答的 action/status/citations、Stop Gate、審查結果及最終驗證。
6. 比對模型原始輸出與 UI 顯示，確認是否單純呈現問題。

查詢失敗、沒有新證據、商品不存在、權限不足、服務不具操作能力是不同原因。不要以更多檢索或無限補查當成通用解法。若向量索引或嵌入服務暫時不可用，可切回 BM25；玩家查詢仍依賴 PostgreSQL。工程紀錄只能使用模擬或已遮罩資料分享。
