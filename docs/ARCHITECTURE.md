# 系統架構與執行流程

[返回 README](../README.md) · [資料與工具](DATA_AND_TOOLS.md) · [設計決策](DESIGN_DECISIONS.md)

## 1. 設計目標

LLM 負責理解意圖、選擇工具與生成回答；Python 負責工具權限、參數、業務條件、證據與停止預算。實際採用 Pydantic Graph 2.66.0 編排既有 Agent，不是完整 Pydantic AI Agent、Codex SDK 或 Claude Agent SDK。參考來源及採用範圍見 [REFERENCES](REFERENCES.md)。

## 2. 模組責任

| 模組 | 責任 |
| --- | --- |
| app.py / starter_questions.py | UI、依玩家與 provider 分隔對話、快速提問、背景執行及工程檢視 |
| agent.py | Graph 節點、狀態轉移、answer 入口 |
| agent_runtime.py | 每輪 TurnState、預算、工具、證據快取及降級 |
| tool_selection.py | 原生/結構化工具選擇共用 Schema |
| model_client.py | OpenAI-compatible API、JSON Schema、相容備援、紀錄 |
| request_context.py | 最近 8 則訊息；上一輪可信工具檔期及商品參照 |
| backend.py | Tool Registry、參數/帳號驗證、證據轉換及適用條件 |
| guide.py / vector_search.py | 公開公告、版本、BM25、hybrid、商城及進度契約 |
| database.py / player_progress.py | PostgreSQL 連線、示範初始化、參數化唯讀查詢 |
| answering.py | 結構化回答、引用、部分回答、Markdown 及參考資料 |
| answer_quality.py | 審查候選、修訂及保護既有回答 |
| eval_rag.py / tests | 檢索評測、腳本回歸、實機重播 |

## 3. Agent 生命週期

1. answer 建立每輪獨立狀態及綁定目前 player 的 registry；歷史文字供指涉，不直接當成本輪事實。
2. SelectTools 選擇唯讀工具。Ollama 預設 structured；OpenAI 預設 native；兩者使用同一份工具定義。
3. ExecuteTools 驗證名稱、參數、檔期與可信指涉後呼叫本地函式；同一輪相同正規化參數重用結果。
4. evidence_for 產生本輪引用資料；以原問核對公開條款的適用條件，排除不適用段落。
5. AssessEvidence 使用本輪證據產生 action/missing/results，格式不符有有限修復機會。
6. StopGate 檢查補查預算及進展。無資料時有限備援；單一 no_evidence 需求若同時有正文及有效引用，可額外補查一次；不直接認定正文正確。
7. Finalize 在審查條件成立時最多進行一次品質審查，修訂不合契約時保留草稿；最後逐需求驗證引用、狀態及原文保護。

| 上限 | 數值 |
| --- | ---: |
| 工具輪數 | 3 |
| 單批工具 | 6 |
| 每次玩家請求工具嘗試 | 12（含無效參數、快取命中） |
| 回答格式修復 | 全輪 1 次 |
| 額外矛盾停止檢查 | 1 次 |
| 最終審查 | 1 次 |

原生選擇無有效呼叫及文字時可有一次結構化備援；無來源而報 no_evidence 也使用有限備援狀態。常見成功路徑為選擇、回答各一次模型步驟；不保證每輪僅兩次。底層 HTTP 相容備援可能增加傳輸次數，工程事件計數不等同所有 HTTP 請求。

## 4. 信任邊界與限制

工具均唯讀或純運算；進度工具不接受任意帳號 ID。demo 帳號由 UI 選擇，不是正式登入驗證。RAG 不包含玩家資料，公開與本人紀錄各有來源。PostgreSQL 的 player_event_progress 與 event_support_agent_embeddings 分表；demo 共用 DSN，尚未配置獨立最小權限角色。商品查無是查詢事實，不等於查明交易失敗原因。計算來源 ID 有驗證，但並未保證每個運算元都語意受支持。引用驗證是本輪 ID、逐字摘錄與狀態核對，非完整語意證明。

品質審查觸發條件為重複需求、未引用同文件章節或未採用的工具組；候選不等於必要證據，可能有過度審查問題。保留 auto/off 作實測比較，不以單一案例修改流程。

工程檢視顯示 Graph 節點、模型請求/回傳、用量、工具、證據與停止原因。正式系統需補認證、授權、日誌遮罩及保留政策。正文透過共用 Markdown 指引與 Streamlit 渲染；沒有新增排版模型呼叫或正規表示式重排。
