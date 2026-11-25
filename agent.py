"""Public Agent API and typed graph orchestration.

Pydantic Graph executes state transitions. Existing model and business
contracts stay provider-neutral; the graph does not infer authorization.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext, StepContext

from agent_runtime import TurnState
from model_client import completion, ignore_event
# Preserve public imports used by the existing regression suite.
from tool_selection import (AGENT_PROMPT, MAX_BATCH, MAX_TOOL_CALLS, MAX_TOOL_ROUNDS,
                            normalized_calls)


TurnResult = tuple[str, list]


@dataclass
class SelectTools(BaseNode[TurnState, object, TurnResult]):
    async def run(self, ctx: GraphRunContext[TurnState]) -> ExecuteTools:
        await ctx.state.select_tools()
        return ExecuteTools()


@dataclass
class ExecuteTools(BaseNode[TurnState, object, TurnResult]):
    async def run(self, ctx: GraphRunContext[TurnState]) -> AssessEvidence | Finalize:
        if await ctx.state.execute_batch():
            return AssessEvidence()
        return Finalize()


@dataclass
class AssessEvidence(BaseNode[TurnState, object, TurnResult]):
    async def run(self, ctx: GraphRunContext[TurnState]) -> StopGate:
        await ctx.state.assess_evidence()
        return StopGate()


@dataclass
class StopGate(BaseNode[TurnState, object, TurnResult]):
    async def run(self, ctx: GraphRunContext[TurnState]) -> SelectTools | Finalize:
        return SelectTools() if ctx.state.stop_gate() else Finalize()


@dataclass
class Finalize(BaseNode[TurnState, object, TurnResult]):
    async def run(self, ctx: GraphRunContext[TurnState]) -> End[TurnResult]:
        return End(await ctx.state.finalize())


# Graph 只負責節點間的狀態轉移；業務規則及模型呼叫留在 TurnState。
def build_graph():
    builder = GraphBuilder(state_type=TurnState, output_type=TurnResult)

    @builder.step
    async def start(ctx: StepContext[TurnState, None, None]) -> SelectTools:
        return SelectTools()

    builder.add(builder.node(SelectTools), builder.node(ExecuteTools),
                builder.node(AssessEvidence), builder.node(StopGate),
                builder.node(Finalize), builder.edge_from(builder.start_node).to(start))
    return builder.build()


# 每個請求建立獨立狀態、工具登錄與證據快取，不跨帳號共用。
async def answer(player, provider, history, question, complete=completion, observe=ignore_event):
    """A new state and registry for every turn; no cross-account shared facts."""
    state = TurnState(player, provider, list(history), question, complete, observe)
    return await build_graph().run(state=state)
