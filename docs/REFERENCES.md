# 技術參考來源

[返回 README](../README.md) · [設計決策](DESIGN_DECISIONS.md)

以下保留開發及架構研究中使用的官方來源。採用表示專案實際有對應設計或函式庫；參考表示借鑑設計概念，不代表使用該公司的 SDK 或服務。參考來源用於說明技術選型及設計原則。

| 官方來源 | 狀態及對應設計 |
| --- | --- |
| [Pydantic Graph](https://ai.pydantic.dev/graph/) | 採用；agent.py GraphBuilder、節點及 TurnState，依 requirements 的 2.66.0 |
| [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling) | 採用介接原則；model_client.py、backend.py、tool_selection.py |
| [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs) | 採用結構化回答與工具請求契約；answering.py、answer_quality.py |
| [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling) | 採用相容介接及結構化備援；本機預設非原生工具呼叫 |
| [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs) | 採用 JSON Schema 生成；host 仍需驗證 |
| [Ollama Embed API](https://docs.ollama.com/api/embed) | 採用 /api/embed；vector_search.py |
| [Psycopg 3 基本用法](https://www.psycopg.org/psycopg3/docs/basic/usage.html) | 採用連線、交易 context manager 及參數化 SQL |
| [PostgreSQL INSERT](https://www.postgresql.org/docs/current/sql-insert.html) | 採用 ON CONFLICT DO NOTHING 的非覆寫初始化 |
| [pgvector](https://github.com/pgvector/pgvector) | 採用 PostgreSQL vector、精確 cosine distance，非近似索引 |
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
| [Pydantic AI](https://ai.pydantic.dev/) | 評估完整 agent 框架；目前只採用 Pydantic Graph，保留既有模型介接 |
| [Streamlit st.write](https://docs.streamlit.io/develop/api-reference/write-magic/st.write) | 採用既有 Markdown 渲染 |
| [CommonMark 0.31.2](https://spec.commonmark.org/0.31.2/) | 參考段落與清單格式；不代表 UI 完全等同 CommonMark |
| [Python unittest](https://docs.python.org/3/library/unittest.html) | 採用標準庫測試及 discovery |
| [Docker 安裝文件](https://docs.docker.com/get-docker/) | 本機 hybrid 環境建置 |

參考來源的概念不能代替自己的實驗證據。日後新增決策時應記錄來源、查閱日期、適用版本、採用範圍、實際程式模組及驗收結果。
