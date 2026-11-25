# 資料、RAG 與工具契約

[返回 README](../README.md) · [索引建置](QUICKSTART.md)

## 1. 模擬資料

events.json 固定服務日期 as_of=2026-11-21；十月與十一月兩期，14 份有效公開文件、41 段公開公告，另保留一份修訂舊版本。

| 欄位 | 用途 |
| --- | --- |
| name / as_of | 活動名稱及可重現日期 |
| editions[].id/title/start/end | 唯一、不重疊的檔期 |
| sections | 開幕公告章節 |
| notices[].id/title/published/sections | 補充及修訂公告；同 ID 取已發布最新版本 |
| shop_items[] | id、name、aliases、price（星星幣）、description |
| player_progress_seed.json（獨立檔案） | 初始化用 edition、player、recorded_on、coins、rounds、joined |

公告章節可為純文字，或含 text 與 requires 物件。每一 requires 群組含 label、mentions_any；所有群組均須在玩家當輪原問命中至少一個識別值，不以模型改寫或舊回答啟用條款。

本期五項商品：霧港領航員 100、霧潮貼圖 20、海圖航線框 35、霧港守燈者 60、月港夜色 75 枚。A、B 均有 80 枚，已計場次分別為 2、3；C 為 0 枚、0 場。A 再完成一場有效冒險可達 100 枚門檻；B 當日無額度。價格門檻不等於確認所有兌換資格。

玩家資料持久化於 PostgreSQL `player_event_progress`，以 `(edition, player)` 複合主鍵識別；recorded_on 為 date，coins 與 rounds 非負，joined 為 boolean。`player_progress.py` 交易式建表及參數化初始化，衝突時 DO NOTHING，避免覆寫現有資料。查詢僅 SELECT 指定檔期與綁定玩家，資料庫錯誤回傳工具失敗而非查無資料；無紀錄仍依既有契約回傳狀態。無訂單明細、累計遊玩天數。玩家資料與商城清單不嵌入公告索引。

## 2. RAG 檢索

published_passages 依 as_of 選已發布有效公告；段落 ID 為檔期/文件ID/章節，保留文件、發布日、來源及原文。

| 模式 | 行為 |
| --- | --- |
| overview | 該期開幕公告完整章節 |
| compare | 該期全部有效公告；跨期各自查詢 |
| search | BM25/hybrid 聚焦檢索；Agent 額外附開幕背景 |
| eligibility | 開幕公告及相關細節，配合獨立本人進度工具 |

BM25：NFKC/casefold，中文相鄰雙字、ASCII 代碼完整保留；同檔期候選內 k1=1.2、b=0.75，預設聚焦取前三個正分段落。原問與改寫若不同，各自檢索再以 RRF（1/(60+rank)）融合，最多六個細節候選，並附必要背景。只有背景不代表找到特定政策。

Hybrid：以 Ollama /api/embed（bge-m3）向量化檔期、文件、發布日、章節、原文；pgvector 做精確餘弦距離排序，非 HNSW/IVFFlat 近似索引。event_support_agent_embeddings 主鍵為 model、edition、revision、passage_id，revision 是公告內容雜湊。詞彙與向量各最多六個名次，RRF 取前三段；多查詢另融合。建表/upsert 以交易執行。模型或公告版本不符報錯，不靜默冒用舊索引；舊版索引清理尚未實作。

## 3. Tool Registry

所有模型請求皆由 registry 驗證參數、型別與列舉，不接受額外欄位。edition 支援 current、previous、context、unavailable 或已知 ID；無法解析不替換檔期。

兩個模型供應商透過 LangChain bind_tools 取得原生 tool_calls，主機核准後由 LangGraph ToolNode 執行。只有通過 ToolRegistry 驗證的呼叫才會執行，文字中的 JSON 不會觸發工具。

| 工具 | 參數 | 回傳 |
| --- | --- | --- |
| find_rules | edition, query, mode | 公開有效公告段落；overview/compare 忽略 query |
| list_shop_items | edition | 完整商城，不依餘額過濾 |
| find_shop_item | edition, name | 指定品名、別名或 SKU 的查詢 |
| get_my_event_progress | edition | 目前綁定帳號的餘額、場次及參加狀態 |
| calculate | expression, source_ids | 受限四則運算及本輪有效來源 ID |

商品參數須由原問、上一輪玩家原句或可信商品參照核對。calculate 使用受限 AST 運算，不執行任意 Python eval；有來源 ID 驗證但尚無完整運算元語意證明。

| 工具證據狀態 | 語意 |
| --- | --- |
| found / listed | 找到資料或完整清單（可為零件） |
| context_only / no_match | 僅有活動背景 / 無可用公告 |
| not_found | 成功查詢清單，指定商品不存在 |
| ambiguous / need_name | 對象未確認或缺少名稱 |
| unavailable | 資料來源或紀錄無法確認 |
| computed | 運算結果，須同時引用原始依據 |
| invalid_arguments / tool_error / budget_exhausted | 參數、執行或預算問題，不是查無事實 |

## 4. 回答契約

```json
{
  "action": "answer",
  "missing": "",
  "results": [
    {
      "question": "獨立玩家需求",
      "status": "supported",
      "answer": "回答正文",
      "citations": [{"id": "本輪段落ID", "quote": "連續原文摘錄"}]
    }
  ]
}
```

action 可為 retrieve（missing 說明必要的新證據）；否則 answer。answer 時 missing 不具執行作用，由主機正規化為空，不為無作用欄位重跑模型；各需求分開驗證。Pydantic BaseModel 檢查回答欄位型別，引用及業務條件仍由本輪證據驗證。

| status | 語意 |
| --- | --- |
| supported | 有可核對來源，包括已確認查無 |
| no_evidence | 證據不足 |
| clarify | 需要玩家補充 |
| unavailable | 目前無法取得或確認資料 |
| account_limit | 超出本人查詢權限 |
| service_help | 服務範圍引導 |

非 supported 的正文和引用留空，由 Python 產生對應訊息。已確認 not_found 被錯標 no_evidence 時，僅在本輪完整有效查無引用、原問和子問題均明示對象等條件下恢復為 supported；採用工具原文，不採用模型推測。條件條款保留原文。沒有獨立 capability_limit，唯讀能力辨識待評估。

## 5. 資料更新

新增檔期須 ID 唯一、起訖不重疊；修訂公告沿用文件 ID、新發布日及完整章節。歷史規則可在有效修訂公告中明示前後適用期間，例如 11/13 以前等候 15 分鐘、11/14 起 5 分鐘。商品修改同步品名、別名、價格及用途；公開公告或嵌入模型變動需重建 hybrid 索引，公開資料改動後重啟程式並重新評測；玩家進度以資料庫為準。種子檔僅用於初始化缺少的紀錄，不是執行期資料來源。
