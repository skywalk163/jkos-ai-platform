"""DSH 工作流引擎 - 基础类型（M0 任务 0.2）

引擎与节点的基础契约：异常、节点上下文、工作流定义。
状态机（§8.1/§8.2）：
  实例: PENDING → RUNNING → (WAITING_APPROVAL|SUSPENDED) → COMPLETED|FAILED|CANCELLED
  步骤: PENDING → RUNNING → SUCCEEDED|FAILED|WAITING|SKIPPED|COMPENSATED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

# ─── 异常 ───


class WorkflowError(Exception):
    """工作流引擎业务错误（未知工作流/非法状态迁移等）"""


class WorkflowStepError(Exception):
    """节点执行失败（由节点处理器抛出，引擎转换为步骤 FAILED）"""


class EngineCrash(Exception):
    """模拟进程崩溃：引擎捕获后步骤/实例停留在 RUNNING，等待 resume() 恢复。

    用于验收"崩溃续跑"——等价于 kill -9 后数据库遗留的中间态。
    """


# ─── 节点 ───

@dataclass
class NodeContext:
    """传给节点处理器的执行上下文"""
    instance: Dict[str, Any]                 # 实例快照（含 context/result）
    step: Dict[str, Any]                     # 当前步骤（attempts 已自增）
    prev_output: Optional[Dict[str, Any]]    # 上一节点输出（首节点为实例 context 的回显）
    engine: Any                              # WorkflowEngine（访问 llm/audit/repo）
    blackboard: Optional[Dict[str, Any]] = None  # 黑板共享分区快照（黑板式协作）


# 处理器签名：async (NodeContext) -> Optional[dict]（返回值作为步骤输出）
NodeHandler = Callable[[NodeContext], Awaitable[Optional[Dict[str, Any]]]]


@dataclass
class ApprovalSpec:
    """审批声明（M1 任务 1.4，§8.2 人工干预四要素）

    risk 超时策略（§8.2.2）：
      low    24h 超时自动通过（记录 TIMEOUT_AUTO_PASS）
      medium 8h  超时保持挂起 + 升级直属上级
      high   2h  超时自动驳回（宁慢勿错，记录 TIMEOUT_AUTO_REJECT）
    mode 协作模式（§8.2.3）：serial 串行 / all 会签 / any 或签
    """
    approvers: List[str] = field(default_factory=list)
    mode: str = "any"                  # serial / all / any
    risk: str = "medium"               # low / medium / high
    timeout_hours: Optional[float] = None  # 覆盖风险等级默认超时


@dataclass
class NodeSpec:
    """工作流定义中的单个节点

    黑板式协作（M1 任务 1.3，§8.1.1 范式 B）：
      - parallel_group 相同的相邻节点并行执行（黑板并行读写）；
      - write_keys 声明节点输出写入黑板的分区，同组节点 write_keys 互斥
        （定义期校验，杜绝写冲突）；
      - read_keys 声明节点依赖的分区（文档化 + 校验辅助）；
      - 未声明 write_keys 的节点沿用管道式 prev_output 传递。

    执行器派发（M21 任务 21.1，executor SPI）：
      - executor 声明节点由外部执行器（如 dsh 开发助手）异步执行：
        引擎 dispatch 后实例进入 WAITING_AGENT 挂起等结果事件续跑；
      - task 携带派发给执行器的任务载荷（任意 dict，执行器可写回结果）；
      - sla_hours 挂起超时（超时扫描时人工兜底提醒/动作）；
      - 未声明 executor 的节点保持 M0/M1 本机执行语义（向后兼容）。
    """
    node_code: str                    # 节点标识（步骤轨迹展示用）
    handler: str                      # 节点处理器名（引擎节点注册表 key）
    node_type: str = "tool"           # tool / llm / approval / agent / ...
    read_keys: List[str] = field(default_factory=list)
    write_keys: List[str] = field(default_factory=list)
    parallel_group: Optional[str] = None
    approval: Optional[ApprovalSpec] = None
    # M21 executor SPI（可选）：派发外部执行器异步执行
    executor: Optional[str] = None
    task: Optional[Dict[str, Any]] = None
    sla_hours: Optional[float] = None


@dataclass
class WorkflowDef:
    """工作流定义（M0 顺序链 + M1 黑板并行组）"""
    code: str
    description: str
    nodes: List[NodeSpec] = field(default_factory=list)
