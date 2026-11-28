# 技術參考來源


## 單一活動的工具與工作流程

- LangChain 說明可以在一個工作流程結合程式函式與模型節點。本專案保留 LangGraph，在固定業務結果已有完整來源時直接完成回答。[LangChain Custom workflow](https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow)
- OpenAI 建議把應用程式已知的資訊留在程式，並合併固定要連續執行的操作。本專案在主機綁定登入者，將查價與讀取本人餘額組成差額查詢。[Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- Anthropic 的客服範例讓客服直接選擇業務工具。本專案依活動服務範圍決定工具及唯讀權限。[Customer service agent](https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/customer_service_agent.ipynb)

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
| [Microsoft Azure RAG preparation](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-preparation-phase) | 參考標記問題、來源及參考答案；evaluations/rag_cases.json |
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

- [Anthropic Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)：參考 routing、可組合工具工作流；採用既有意圖分派，不聲稱供應商要求固定兩層。
- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling) 與 [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling)：原生工具定義與多呼叫，主機驗證後執行。候選 task_tool_planning.py 共用 model_step，動態定義 task_id 與允許工具；只驗收提案，不執行。任務 ID、欄位 metadata 是本專案設計，不是供應商固定契約。
- [Anthropic Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)：參考多任務評測及避免固定軌跡；首輪工具集合無序比對，限制理由與檢索語意人工複核。尚無本專案真實模型結果。

- [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)：參考程式持有的已知參數不交給模型；唯一待辦 ID 由主機綁定。strict schema 相容性下保留 query 宣告，僅非檢索模式允許主機補空字串。
- [Anthropic Writing Effective Tools](https://www.anthropic.com/research/writing-tools-for-agents) 與 [Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：參考減少工具歧義與重複上下文，不增加問句特例。
- [ChatOllama API](https://reference.langchain.com/python/langchain-ollama/chat_models/ChatOllama)：num_predict 是生成限制；2048 為本專案實驗值，不是官方建議閾值。


## Single Agent 終止契約（V2）

實機紀錄顯示自由文字 JSON 多出結尾大括號、查無狀態誤用，以及單一商品被完整清單取代。依 [LangChain Structured Output](https://docs.langchain.com/oss/python/langchain/structured-output) 的 ToolStrategy 概念，將輸出契約表達為原生終止工具；並參考 [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs) 與 [Tool Calling](https://docs.ollama.com/capabilities/tool-calling)、[OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)。本專案保留既有 LangGraph，不新增 SDK；不假設所有 Ollama 版本支援同時啟用工具及 format 約束。OpenAI 原生工具沿用 strict；Ollama 原生工具的 Schema 為模型介面，不保證嚴格遵守，仍由 Pydantic 驗證並使用既有 provider schema-only 介接備援。精簡重複狀態與終止欄位；商品工具描述依資料用途區分，不依特定題目硬編碼。已驗證查無的既有保守恢復只採本輪引用的可信查無原文，不能保證模型必然引用正確來源。沒有新增語意審查或固定模型呼叫；工具依賴順序仍是語意指引，而非主機強制。18 項單一 Agent 腳本回歸含新增終止混批、無效契約、結構化備援及查無證據恢復；須另行執行完整回歸與實機驗收。


## Single Agent：證據依賴展開

最終回答可只引用已取得的計算結果短 ID。主機依本輪可信證據內的 `運算依據` 展開多層來源，去重並保留穩定順序，再執行原有引用及適用條件驗證。未知 ID、缺失原文、循環或不合法依賴會使受影響的子問題降級；不新增模型呼叫，不自動引入未被引用的計算。舊 `legacy_answer`、`intent_answer` 的引用契約不變。此機制僅保證來源追溯，不證明運算式選擇、數字來源或回答語意正確。測試：`python -m unittest tests.test_evidence_dependencies tests.test_single_agent -v`。

依 [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling) 減少模型重複填寫主機已知資料的原則，以及 [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) 的衍生來源概念。這是本專案實作取捨，不是供應商強制規定。舊分類、審查、工具規劃仍被歷史入口及獨立評測引用，不視為無用程式；共用 TurnRuntime 仍為現行工具派發的基底。避免刪除歷史測試或改動其基準。此次移除 single_output.py 未使用的 json import。完整回歸與實機仍需驗證。

- [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)；[Anthropic Handle Tool Calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Parallel Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)：2026-11-26 核對；每個有效封套的工具呼叫都需對應結果（含未執行/拒絕），獨立唯讀工具可平行。採用於 single_agent.py 與 agent_runtime.py；終止工具必須單獨提交是本專案的停止契約。
- [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling)、[Chat API](https://docs.ollama.com/api/chat)、[Context Length](https://docs.ollama.com/context-length)、[Anthropic Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：參考多輪工具回饋、供應商回報用量、輸入資訊與配置容量分開觀測。不新增模型探測請求，不推定實際 Context 或輸入截斷。

## Multi-provider embedding

- NVIDIA hosted Nemotron 3.5 Embed API: https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3_5-embed-1b-infer (passage/query, float, no silent truncation, 2048 native dimensions).
- OpenAI embeddings: https://platform.openai.com/docs/api-reference/embeddings/create (input, model, float output).
- pgvector variable-dimensional vector column and exact filtering: https://github.com/pgvector/pgvector#frequently-asked-questions.

- [OpenAI Python SDK — Retries](https://github.com/openai/openai-python#retries)：參考唯讀請求的有限重試及指數退避；本專案針對通用 Embedding transport 自行選擇可重試 HTTP 狀態與上限，不宣稱 NVIDIA 的 HTTP 500 一定可恢復。

- [OpenAI Agents SDK Guardrails](https://openai.github.io/openai-agents-python/guardrails/)：拒絕未通過的候選輸出；本專案沿用既有 LangGraph 有限修復，非導入 OpenAI SDK。
- [LangChain Structured Output](https://docs.langchain.com/oss/python/langchain/structured-output)：參考 ToolStrategy 驗證失敗的錯誤回饋與重試，實際規則由本專案主機決定。
- [NVIDIA NIM FAQ](https://forums.developer.nvidia.com/t/nvidia-nim-faq/300317)：試用配額可能變動。20 RPM 是本專案可調節流預設，不代表服務保證。

## Evaluation V2 官方設計依據

- [OpenAI Evaluation Best Practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)：單一 Agent 分開功能正確性、工具選擇與參數；程式、人工與模型評分互補，模型 grader 需人工校準。
- [Anthropic Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)：task、trial、grader、trace、outcome 分離，多次試驗及多 grader。避免把固定路徑當成唯一成功解法。
- [Google Cloud Agent Evaluation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/evaluation-agents)：分開最終回答與工具軌跡、執行個體與匯總結果。

本專案選擇本地確定性 grader 與人工 rubric，非導入上述平台或聲稱自動語意證明；版本化證據群組、雜湊及節流紀錄為本專案設計取捨。

## 證據與不確定性（第一階段）

- [OpenAI — Why language models hallucinate](https://openai.com/index/why-language-models-hallucinate/)：區分正確、錯誤與適當拒答，避免鼓勵無證據猜測。
- [Anthropic — Reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)：允許不確定性，核對主張與原文支持。
- [Anthropic — Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)：工具描述應說明功能、適用時機與限制。
- [Google Cloud — Check grounding](https://docs.cloud.google.com/generative-ai-app-builder/docs/check-grounding)：以主張為單位判斷參考資料是否支持。此處僅參考設計原則，不導入外部 grounding API 或聲稱 Python 已能自動驗證語意。
- [OpenAI — Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)：結構合法不等於內容正確；需另行評測。

## 資料不足時的限制資訊（第二階段）

- [Google Agent Search Answer resource](https://docs.cloud.google.com/generative-ai-app-builder/docs/reference/rest/v1/projects.locations.collections.dataStores.sessions.answers)：answerSkippedReasons 分類回答跳過原因；本專案使用自身工具狀態與能力資訊，不使用 Google 的語意判定或假設分類等價。
- [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)：由程式處理已知資料、限制模型輸出契約。OpenAI Structured Outputs 的安全 refusal 與本專案資料不足狀態不同。
- [AWS Bedrock contextual grounding](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-contextual-grounding-check.html)：證據支持度和問題相關性分別處理。本次不引入自動語意驗證。

本專案以確定性主機呈現補充已知查詢範圍，非外部服務強制架構；全輪摘要不代表個別需求的原因已確認。

## 查詢效率及停止條件

- [Anthropic — Effective context engineering for AI agents](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)：保持高訊號上下文、漸進取得資訊，避免重疊工具與多餘回傳；過度壓縮可能犧牲證據。
- [Anthropic — Writing effective tools for AI agents](https://www.anthropic.com/research/writing-tools-for-agents)：明確工具用途、降低重疊並用評測觀測工具表現。
- [OpenAI — Running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)：區分實際終止、工具迴圈與執行限制/錯誤。
- [Microsoft — RAG information retrieval](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-information-retrieval)：查詢改寫及分解可改善檢索，不應任意改變原問。
- [Google Agent Search — retrieval and ranking](https://docs.cloud.google.com/generative-ai-app-builder/docs/ranking-overview)：檢索、排名與範圍限制分開。

本專案選擇完全相同 query vector 的同輪 single-flight 重用、保守無新增證據提示與停止原因觀測；不是以上供應商規定的唯一演算法，不聲稱已判定語意充分性。

## 終止契約與主張驗收（第四階段，2026-11-26 核對）

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)：JSON Schema 子集合及結構與語意差異；採全部必要欄位及閉合物件，不依賴根聯合型別。
- [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)：以明確參數契約及程式端驗證執行工具，模型提案不等於已執行。
- [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)：本機 format 可傳 JSON Schema，Pydantic 驗證；文件明示 Ollama Cloud 支援限制。本專案使用本機 Ollama。
- [NVIDIA NIM Structured Generation](https://docs.nvidia.com/nim/large-language-models/1.15.0/structured-generation.html)：不同 guided decoding backend 有支援差異；不將自架 NIM 規格視為 API Catalog 的保證，不更換既有 hosted adapter。
- [Anthropic Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)：獨立工作平行，需前序證據的工作順序執行；不為平行而增加 Agent。
- [Anthropic Effective Context Engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)：維持高訊號上下文，僅提供當前可用的工具；保留原始證據及安全必要資訊。
- [Google Cloud Check Grounding with RAG](https://docs.cloud.google.com/generative-ai-app-builder/docs/check-grounding?hl=en)：主張與來源的關聯及支持度應區分；此處只實作主張級來源可追溯及人工審查，不呼叫該服務或假稱已自動驗證語意。
