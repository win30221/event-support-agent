# 現行設計原則

[返回 README](../README.md) · [技術參考來源](REFERENCES.md)

## 職責分離

LLM 理解需求、選擇資料工具及生成回答；Python 管理工具契約、資料範圍、權限、證據、停止條件與確定性運算。LangGraph 負責執行節點與狀態，不負責推論業務事實。

## 資料與工具

公開公告、商城及個人活動進度分開讀取。公告採檔期與發布版本過濾，使用 BM25 或 BM25 + pgvector 混合檢索；個人紀錄由 PostgreSQL 獨立資料表持久化保存，初始化種子與公開公告分離，不加入向量索引。資料工具均為唯讀或純運算，模型不得指定其他帳號。

## 有界執行與可靠性

每輪有工具批次、輪數、工具嘗試、格式修復及審查上限。工具結果先形成可核對的證據，再由模型整理。引用需存在於本輪並核對原文；查無、資料缺失、權限限制及例外分別處理。無足夠資料時保守回答，獨立需求可部分完成。引用驗證不等於完整語意證明。

## 呈現與維護

客服正文使用共用 Markdown 契約，由 Streamlit 顯示；引用、工具原始資料與工程紀錄分開呈現。測試集中在 tests/，以相同題集比較檢索與模型行為。新增功能須維持通用性與模組邊界，不針對單一問題硬編碼。

實際測試方法與已知限制見 [EVALUATION](EVALUATION.md)。

兩個供應商在工具選擇與重試時均使用 LangChain bind_tools 與原生 tool_calls。主機以同一個 ToolRegistry 驗證及正規化參數，LangGraph ToolNode 並行執行唯一核准的查詢；依賴本輪新資料的運算需下一輪。回答與審查另以 with_structured_output 使用 JSON Schema。Pydantic BaseModel 驗證輸出資料契約，無作用欄位不觸發重試。

## LangGraph 遷移決策

從 ollama-native-tool-calls 的 7c9385c 建立獨立重構分支。採用 StateGraph、MessagesState、ToolNode、Conditional Edges 與有限 RetryPolicy，不重建 RAG、SQL、商城或業務驗證。先保留工具與回答分離，不同模型相容性待實機驗證。保留原始回歸測試意圖，傳輸測試改驗證 SDK 行為；外部 tracing 預設停用。本次未導入 MCP、Checkpointer 或 Multi-Agent。舊分支保留作比較基準。
