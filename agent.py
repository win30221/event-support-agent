"""Public API and LangGraph orchestration for one isolated support turn."""
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from langsmith import tracing_context

from agent_runtime import SupportState, TurnRuntime
from model_client import TransientModelError, completion, ignore_event
from tool_selection import (AGENT_PROMPT, MAX_BATCH, MAX_TOOL_CALLS, MAX_TOOL_ROUNDS,
                            normalized_calls)


# Graph 控制執行順序；選工具及評估分開，以維持本地模型相容性。
def build_graph(runtime):
    graph = StateGraph(SupportState)
    graph.add_node("select", runtime.select,
                   retry_policy=RetryPolicy(max_attempts=2, retry_on=TransientModelError))
    graph.add_node("prepare_tools", runtime.prepare_tools)
    graph.add_node("tools", runtime.tool_node)
    graph.add_node("collect", runtime.collect)
    graph.add_node("assess", runtime.assess)
    graph.add_node("stop_gate", runtime.stop_gate)
    graph.add_node("finalize", runtime.finalize)
    graph.add_edge(START, "select")
    graph.add_edge("select", "prepare_tools")
    graph.add_conditional_edges("prepare_tools", runtime.route_tools,
                                {"tools": "tools", "collect": "collect"})
    graph.add_edge("tools", "collect")
    graph.add_conditional_edges("collect", runtime.route_collect,
                                {"assess": "assess", "finalize": "finalize"})
    graph.add_edge("assess", "stop_gate")
    graph.add_conditional_edges("stop_gate", runtime.route_stop,
                                {"select": "select", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()


async def answer(player, provider, history, question, complete=completion, observe=ignore_event):
    """Preserve the UI/eval API and injected scripted model seam."""
    runtime = TurnRuntime(player, provider, list(history), question, complete, observe)
    # 不預設向外部追蹤服務傳送玩家資料；工程紀錄留在既有觀測介面。
    with tracing_context(enabled=False):
        result = await build_graph(runtime).ainvoke(
            runtime.initial_state(), config={"recursion_limit": 24}
        )
    return result["result"], result["trace"]
