"""DSH 工作流引擎 - 内置节点与工作流注册表（M0 任务 0.2）

M0 内置节点（全部幂等，可安全重跑）：
  echo      回显上一节点输出（链路验证）
  llm_chat  调用 LLM 路由（真实供应商失败自动降级模拟）
  fail      人为注入失败（验证 FAILED 路径与审计）
  crash     首次执行模拟进程崩溃（验证崩溃续跑）
  noop      空操作（approval 节点类型的占位处理器）
"""
from __future__ import annotations

import logging
from typing import Dict

from jkos_core.workflow.base import (
    EngineCrash,
    NodeContext,
    NodeHandler,
    NodeSpec,
    WorkflowDef,
    WorkflowError,
    WorkflowStepError,
)

logger = logging.getLogger("dsh.workflow")


# ─── 内置节点处理器 ───

async def node_echo(ctx: NodeContext):
    payload = ctx.prev_output or ctx.instance.get("context") or {}
    return {"echo": payload, "node": ctx.step["node_code"], "seq": ctx.step["seq"]}


async def node_llm_chat(ctx: NodeContext):
    prev = ctx.prev_output or {}
    context = ctx.instance.get("context") or {}
    prompt = prev.get("prompt") or context.get("prompt") or "你好，请用一句话做自我介绍"
    result = await ctx.engine.llm.chat_text(
        prompt,
        tenant_id=ctx.instance["tenant_id"],
        purpose="workflow",
        ref_type="workflow_instance",
        ref_id=ctx.instance["id"],
    )
    return {
        "prompt": prompt,
        "content": result.content,
        "provider": result.provider,
        "tokens": result.total_tokens,
        "simulated": result.simulated,
    }


async def node_fail(ctx: NodeContext):
    reason = (ctx.instance.get("context") or {}).get(
        "fail_reason", "人为注入的失败（fail 节点）"
    )
    raise WorkflowStepError(str(reason))


async def node_crash(ctx: NodeContext):
    # 首次执行（attempts==1）模拟崩溃；resume 重跑时放行
    if ctx.step["attempts"] <= 1:
        raise EngineCrash("模拟进程崩溃（首次执行，等价 kill -9 遗留 RUNNING 态）")
    return {"recovered": True, "attempts": ctx.step["attempts"]}


async def node_noop(ctx: NodeContext):
    return {"node": ctx.step["node_code"], "via": "manual"}


BUILTIN_NODES: Dict[str, NodeHandler] = {
    "echo": node_echo,
    "llm_chat": node_llm_chat,
    "fail": node_fail,
    "crash": node_crash,
    "noop": node_noop,
}


# ─── 媒体工作流节点（M2 任务 2.1/2.2）───

try:
    from tenants.media.nodes import MEDIA_NODES
    BUILTIN_NODES.update(MEDIA_NODES)
except ImportError:
    pass


# ─── 工作流注册表 ───

WORKFLOW_REGISTRY: Dict[str, WorkflowDef] = {
    "hello": WorkflowDef(
        code="hello",
        description="M0 验收工作流: 回显 → LLM → 回显（顺序链）",
        nodes=[
            NodeSpec("greet", "echo"),
            NodeSpec("ask_llm", "llm_chat", node_type="llm"),
            NodeSpec("summarize", "echo"),
        ],
    ),
    "hello_fail": WorkflowDef(
        code="hello_fail",
        description="失败路径: 回显 → 强制失败（实例 FAILED + 审计）",
        nodes=[
            NodeSpec("greet", "echo"),
            NodeSpec("boom", "fail"),
        ],
    ),
    "hello_crash": WorkflowDef(
        code="hello_crash",
        description="崩溃续跑: 回显 → 模拟崩溃（RUNNING 悬挂）→ resume 恢复",
        nodes=[
            NodeSpec("greet", "echo"),
            NodeSpec("danger", "crash"),
            NodeSpec("after", "echo"),
        ],
    ),
    "hello_approval": WorkflowDef(
        code="hello_approval",
        description="审批流: 回显 → 人工审批（WAITING_APPROVAL）→ 回显",
        nodes=[
            NodeSpec("prepare", "echo"),
            NodeSpec("manual_review", "noop", node_type="approval"),
            NodeSpec("finish", "echo"),
        ],
    ),
}


# ─── 媒体工作流注册（M2 任务 2.1/2.2）───

try:
    from tenants.media.workflows import MEDIA_WORKFLOWS
    WORKFLOW_REGISTRY.update(MEDIA_WORKFLOWS)
except ImportError:
    pass


def get_workflow(code: str) -> WorkflowDef:
    definition = WORKFLOW_REGISTRY.get(code)
    if definition is None:
        raise WorkflowError(f"未知工作流: {code}（可用: {sorted(WORKFLOW_REGISTRY)}）")
    return definition
