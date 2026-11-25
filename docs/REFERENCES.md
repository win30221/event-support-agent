# 技術參考來源

[返回 README](../README.md) · [設計決策](DESIGN_DECISIONS.md)

以下保留開發及架構研究中使用的官方來源。採用表示專案實際有對應設計或函式庫；參考表示借鑑設計概念，不代表使用該公司的 SDK 或服務。參考來源用於說明技術選型及設計原則。

| 官方來源 | 狀態及對應設計 |
| --- | --- |
| [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) | 採用；StateGraph、MessagesState、條件路由，依 requirements 的 1.2.24 |
| [LangGraph Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents) | 採用；ToolNode 與自訂受控流程 |
| [LangChain ChatOllama](https://docs.langchain.com/oss/python/integrations/chat/ollama) | 採用；原生 Ollama SDK、工具及結構化輸出 |
| [ChatOllama API](https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama) | 採用；client_kwargs、num_ctx、reasoning、with_structured_output |
| [LangGraph fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance) | 採用；暫時性模型請求錯誤有限 RetryPolicy |
| [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/) | 採用；回答資料契約驗證 |
| [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling) | 採用介接原則；model_client.py、backend.py、tool_selection.py |
| [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs) | 採用結構化回答與審查契約；answering.py、answer_quality.py |
| [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling) | 透過 ChatOllama 原生 /api/chat 與 ChatOpenAI 傳送 tools，以 tool_calls 及工具結果進行多輪查詢 |
| [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs) | 採用 JSON Schema 生成；host 仍需驗證 |
| [Ollama Embed API](https://docs.ollama.com/api/embed) | 採用 /api/embed；vector_search.py |
| [Psycopg 3 基本用法](https://www.psycopg.org/psycopg3/docs/basic/usage.html) | 採用連線、交易 context manager 及參數化 SQL |
| [PostgreSQL INSERT](https://www.postgresql.org/docs/current/sql-insert.html) | 採用 ON CONFLICT DO NOTHING 的非覆寫初始化 |
| [pgvector](https://github.com/pgvector/pgvector) | 採用 PostgreSQL vector、精確 cosine distance，非近似索引；本機安裝使用 `pg19-trixie` 映像 |
| [PostgreSQL Docker 官方映像](https://hub.docker.com/_/postgres) | PostgreSQL 18 起使用版本化 PGDATA 及 `/var/lib/postgresql` 掛載；初始化環境變數只對空白資料目錄生效，舊大版本資料卷須保留並另行遷移 |
| [pg_isready](https://www.postgresql.org/docs/19/app-pg-isready.html) | 檢查服務是否接受連線；不取代以應用程式 DSN 實際驗證帳密 |
| [Anthropic Contextual Retrieval](https://www.anthropic.com/research/contextual-retrieval) | 參考詞彙/向量互補及段落背景；保留 BM25 基準，未宣稱實作其完整 contextual embedding 方法 |
| [Microsoft Azure RAG preparation](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-preparation-phase) | 參考標記問題、來源及參考答案；rag_cases.json |
| [Microsoft RAG evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/rag-evaluators) | 參考檢索與回答品質分開評估；未使用 Azure 評測服務 |
| [Anthropic Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) | 參考 agent/workflow、工具及有界迭代的取捨 |
| [OpenAI Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/) | 參考模型/工具/上下文迴圈；未整合 Codex |
| [Codex 原始碼](https://github.com/openai/codex) | 架構研究來源，非依賴 |
| [Claude Code 運作方式](https://code.claude.com/docs/en/how-claude-code-works) | 參考上下文與工具生命週期 |
| [Claude Code Hooks](https://code.claude.com/docs/en/hooks) | 參考執行前後及停止檢查；本地 Stop Gate 非 Claude hook |
| [OpenAI Agents SDK Guardrails](https://openai.github.io/openai-agents-python/guardrails/) | 參考輸入/輸出與工具驗證；未採用 Agents SDK |
| [Salesforce Action Chaining](https://developer.salesforce.com/docs/ai/agentforce/guide/ascript-patterns-action-chaining.html) | 參考業務前置依賴與工具順序，不使用 Salesforce 服務 |
| [AWS Bedrock Agents actions](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-action-add.html) | 參考應用端掌握工具執行，未採用 Bedrock |
| [Intercom Fin AI Engine](https://www.intercom.com/help/en/articles/9929230-the-fin-ai-engine) | 參考客服依需求取得知識及個人資料；未使用 Fin |
| [Intercom Fin Procedures](https://www.intercom.com/help/en/articles/12495167-fin-procedures-explained) | 參考條件流程及失敗處理 |
| [Pydantic AI](https://ai.pydantic.dev/) | 歷史比較來源；現行不採用 Pydantic AI 或 Pydantic Graph |
| [Streamlit st.write](https://docs.streamlit.io/develop/api-reference/write-magic/st.write) | 採用既有 Markdown 渲染 |
| [CommonMark 0.31.2](https://spec.commonmark.org/0.31.2/) | 參考段落與清單格式；不代表 UI 完全等同 CommonMark |
| [Python unittest](https://docs.python.org/3/library/unittest.html) | 採用標準庫測試及 discovery |
| [Docker 安裝文件](https://docs.docker.com/get-docker/) | 本機 hybrid 環境建置 |

參考來源的概念不能代替自己的實驗證據。日後新增決策時應記錄來源、查閱日期、適用版本、採用範圍、實際程式模組及驗收結果。

| 研究來源 | 設計採用範圍 |
| --- | --- |
| [Anthropic Writing effective tools](https://www.anthropic.com/research/writing-tools-for-agents) | 參考工具邊界、相關回傳資料、簡潔識別碼；實機改善待驗證 |
| [Anthropic Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | 分階段重播、重複試驗與人工語意驗收 |
| [ALCE](https://arxiv.org/abs/2305.14627) | 分開答案正確性及來源品質；不以 ID 存在性聲稱語意證明 |

- [Streamlit components v2](https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v2.component)：採用既有 UI component 介面及固定事件 ID。
- [MDN Web Audio best practices](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Best_practices)：AudioContext 使用者手勢啟用、音效開關；不保證背景自動播放。


- [Google Dialogflow CX — Intents](https://docs.cloud.google.com/dialogflow/cx/docs/concept/intent) 與 [General agent design best practices](https://docs.cloud.google.com/dialogflow/cx/docs/concept/agent-design)：2026-11-25 查閱；參考分類邊界、正反對照與避免相似意圖，將概念用於現有 LLM Prompt；未導入 Dialogflow NLU 或訓練模型。
- [Anthropic — Building effective agents / Routing](https://www.anthropic.com/research/building-effective-agents)：2026-11-25 查閱；路由需具備可區隔、可可靠分類的類別，先獨立驗證第一層再評估是否改架構。

- [Claude Platform — Prompting best practices](https://platform.claude.com/docs/en/prompt-library/library)：2026-11-25 查閱；參考範例相關性、多樣性與清楚結構，將對照範例與分類定義分開，獨立測試題不提供模型。不代表已證實本地小模型準確率改善。

- [AWS Amazon Lex V2 — Primary and Fallback modes for assisted NLU / Writing effective intent and slot descriptions](https://docs.aws.amazon.com/lexv2/latest/dg/assisted-nlu-modes.html)：2026-11-26 核對；參考 LLM 意圖描述明確目的、對象、邊界、避免重疊及測試不同措辭。採用於 INTENT_PROMPT 的 knowledge/analysis 邊界；不導入 Lex、額外分類器或信心分數。
- [Google Dialogflow CX — Intents / Default negative intent](https://docs.cloud.google.com/dialogflow/cx/docs/concept/intent) 與 [Avoid similar intents](https://docs.cloud.google.com/dialogflow/cx/docs/concept/agent-design)：2026-11-26 核對；參考服務範圍外辨識和避免類別重疊。專案採用同一次 LLM 呼叫內先主題辨識、後目的分類的提示設計，並非 Dialogflow 的訓練式 NLU 或官方固定演算法。成效須以原 36 題及未見問句實測。
