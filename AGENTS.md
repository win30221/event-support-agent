# Event Support Agent — 開發規範

本檔供維護者與 AI coding agent 使用。

- 維持既有 Python / Streamlit / LangGraph / LangChain 及本地工具架構，除非有實測證據與獨立設計決策。
- 先定位根因、閱讀目前實作及成熟產品官方文件。記錄來源、適用性及取捨至 docs/REFERENCES.md、docs/DESIGN_DECISIONS.md。
- 禁止針對單一提問、測試商品、固定回答寫特殊路由或硬編碼。示範業務資料與公告條件應來自資料設定。
- LLM 理解語意；Python 控制工具、權限、參數、證據、停止及業務不變量。不得新增模型呼叫或放寬權限只為改善文案。
- 每次變更維持模組邊界；呈現、檢索、模型介接、業務與流程問題分開修正。
- 工具及引用必須使用本輪可核對資料；不將歷史回答當成新事實。保護原始公告、品名、代碼及私人資料。
- 不刪測試以取得綠燈，不把離線 fixture 成功當成實機準確率。新增跨案例測試並執行完整回歸。
- 先準備獨立測試 PostgreSQL 並執行 python player_progress.py 初始化，再執行 python -m unittest discover -s tests -t . -v、python -m pip check、python -m evaluations.eval_rag；涉及模型或 hybrid 時另做實機驗收，無法執行則明示。
- 更新 README、QUICKSTART、相關架構、工具與評測文件；指令須符合實際模組路徑和環境變數。
- 註解以繁體中文說明重要設計原因、信任邊界及取捨，不逐行翻譯。程式、Prompt、測試及文件應一致。
- 保留既有測試基準及可重現評測；勿在專案檔案中保存 API key 或私人紀錄。

驗收門檻及已知限制見 docs/EVALUATION.md。

- 模型只產生短來源 ID，主機回填真實原文；不得將引用存在性當成語意證明。真實模型結構驗收與人工語意驗收皆須記錄；無實機環境時不可宣稱完成。
