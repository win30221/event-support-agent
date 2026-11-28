# 活動客服重構交接

## 版本與範圍

分支 `refactor/event-business-tools` 以 `research/nvidia-provider` 的 `bb863cf` 為起點。第一個可取回的提交為 `57c4f67`。Streamlit 與端到端測試共用 `agent.answer`；舊五類意圖流程可經 `agent.intent_answer` 單獨比較。

| 需求 | 實作位置 | 核對事項 |
| --- | --- | --- |
| 餘額、單品售價、完整清單、價格差額 | `business_replies.py`、`task_business.py` | 依本輪指定檔期、商品、登入者紀錄產生答案。查無品項時停止相依的個人查詢 |
| 一句多問、查詢與加幣混合 | `single_agent.py` | 同批處理各獨立需求，保留已核對答案與來源。資料不足的部分另外呈現 |
| 指涉前一件商品或同一筆錯誤 | `request_context.py`、`backend.py` | 使用上輪核對過的來源，重新取得本輪證據。換檔期、不同錯誤碼及多個候選均有測試 |
| 批次驗收 | `evaluations/eval_rag.py`、`evaluations/business_cases.json`、`evaluations/task_cases.json` | 比對實際呈現主張、實際採用來源及本人資料存取範圍，另檢查有標記的前置回合 |

## Qwen3 工具回傳差異

在獨立 PostgreSQL 16.2、Ollama 0.40.19、CPU、`qwen3:8b`、`num_ctx=8192`、`think=false` 與 BM25 測試。「我現在有多少星星幣？」在第一個提交的聊天入口收到空正文與空工具清單。後續即使模型填入數字，也會因缺少本輪來源而遭主機拒絕。

對同一個玩家問題與工具定義做四組傳輸對照：

| 請求 | 非串流 | 串流 |
| --- | --- | --- |
| 直接送完整定義至 Ollama `/api/chat` | 回傳 `answer_event_fact(balance)` | 回傳相同業務工具 |
| `ChatOllama.bind_tools` | 正文及工具均為空 | 正文及工具均為空 |

另在非串流 SDK 請求截取 HTTP 回應，`message` 僅含 `role` 與空 `content`。已觀察到 SDK 序列化會移除部分 schema 欄位，兩條請求的輸入 token 數也不同。尚未隔離出某一個被移除的欄位是成因。測試中的修正讓預設 Agent 的 Ollama 工具回合直接送出完整定義，結構化終止回答、LangGraph 編排、工具授權及引用核對仍走原流程。

在修正後的正式聊天入口重播相同問題，Qwen 以一次模型請求選中業務工具，後端查回 A 帳號 80 枚並交付附日期的答案。

## 重跑方法

依 [PowerShell 測試指南](TESTING.md) 啟用虛擬環境、啟動本地模型並初始化獨立測試庫。以下指令從專案根目錄執行：

```powershell
python -m unittest discover -s tests -t . -v
python -m evaluations.eval_rag --live ollama --suite business --gate --report reports/business.json
python -m evaluations.eval_rag --live ollama --suite business --case unknown-item-price --case redemption-gap --repeat 3 --gate --report reports/business-repeat.json
```

加入 `--workflow intent` 可重播舊工作流程，請另取報告名稱。這是兩個完整流程的比較，包含其模型與工具介接差異。自動判準針對示範種子與具體業務結果。公告解釋及新問法仍應人工對照原文。
