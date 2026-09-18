"""DSH 工作流引擎 - 人工干预（M1 任务 1.4，§8.2 人工干预设计规范）

四要素落地：
  粒度  v1 为整体通过/驳回（字段级修改/局部重跑留 M2+）
  超时  按风险等级（§8.2.2）：low 24h 自动通过 / medium 8h 挂起+升级 / high 2h 自动驳回
  协作  serial 串行 / all 并行会签 / any 或签（§8.2.3），resolve_decision() 纯函数状态机
  留痕  每次决策（含超时自动决策/升级）均落 approval_task.decisions + audit_event

本模块只放与存储无关的纯逻辑（决策状态机、超时时刻计算、升级链推进），
仓储在 db.repos.ApprovalTaskRepo，引擎编排见 engine.py。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from jkos_core.db import (
    MODE_ALL,
    MODE_ANY,
    MODE_SERIAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    TASK_APPROVED,
    TASK_PENDING,
    TASK_REJECTED,
)

# ─── §8.2.2 超时策略（小时）与升级链 ───

RISK_TIMEOUT_HOURS: Dict[str, float] = {
    RISK_LOW: 24.0,     # 低风险：24h 超时自动通过
    RISK_MEDIUM: 8.0,   # 中风险：8h 挂起 + 提醒 + 升级
    RISK_HIGH: 2.0,     # 高风险：2h 自动驳回（宁慢勿错，禁止自动通过）
}

# 升级链（每级超时未响应继续上移；medium 简化为两级，high 完整三级）
RISK_ESCALATION: Dict[str, List[str]] = {
    RISK_LOW: [],
    RISK_MEDIUM: ["直属上级", "部门总监"],
    RISK_HIGH: ["部门总监", "VP", "值班负责人"],
}

# 升级后每级重新计时的窗口（小时）
ESCALATION_WINDOW_HOURS = 4.0


def parse_iso(ts: str) -> datetime:
    """解析 ISO 8601 UTC 文本（兼容带 Z / +00:00 / 无时区）"""
    text = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso_utc(dt: datetime) -> str:
    """datetime → ISO 8601 UTC 文本（与 db.utc_now 同格式）"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timeout_at_for(risk: str, override_hours: Optional[float] = None,
                   base: Optional[datetime] = None) -> str:
    """计算审批超时截止时刻（§8.2.2，override 可覆盖默认窗口）"""
    hours = override_hours if override_hours is not None else RISK_TIMEOUT_HOURS.get(risk, 8.0)
    start = base or datetime.now(timezone.utc)
    return iso_utc(start + timedelta(hours=hours))


def resolve_decision(task: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """多人协作决策状态机（§8.2.3，纯函数）。

    输入任务快照（含 mode/approvers/decisions），返回 (任务终态, 说明)：
      - 任务已终态 → (None, None)（不重复决策）
      - serial: 严格按 approvers 顺序逐人决策；未轮到者决策非法（抛 ValueError）；
        任一驳回即 REJECTED，全部通过即 APPROVED
      - all:    任一驳回即 REJECTED；全部通过才 APPROVED
      - any:    任一通过即 APPROVED；全部驳回才 REJECTED
    其余情况 → (None, None)（任务继续挂起等待）
    """
    mode = task["mode"]
    approvers: List[str] = task.get("approvers") or []
    decisions: List[Dict[str, Any]] = task.get("decisions") or []
    approved = {d["approver"] for d in decisions if d["decision"] == "approve"}
    rejected = {d["approver"] for d in decisions if d["decision"] == "reject"}

    if mode == MODE_ANY:
        if approved:
            return TASK_APPROVED, f"或签通过（{sorted(approved)[0]}）"
        if approvers and rejected >= set(approvers):
            return TASK_REJECTED, "或签全员驳回"
        return None, None

    if mode == MODE_ALL:
        if rejected:
            return TASK_REJECTED, f"会签驳回（{sorted(rejected)[0]}）"
        if approvers and set(approvers) <= approved:
            return TASK_APPROVED, "会签全员通过"
        return None, None

    if mode == MODE_SERIAL:
        # 顺序校验：只允许审批序列中下一个未决者决策（越序在 decide_task 入口拦截）
        if rejected:
            return TASK_REJECTED, f"串行驳回（{sorted(rejected)[0]}）"
        if approvers and set(approvers) <= approved:
            return TASK_APPROVED, "串行全链通过"
        return None, None

    raise ValueError(f"未知审批协作模式: {mode}")


def serial_next_approver(task: Dict[str, Any]) -> Optional[str]:
    """串行模式下当前应决策的人（队首未决者）；非串行返回 None"""
    if task["mode"] != MODE_SERIAL:
        return None
    decided = {d["approver"] for d in (task.get("decisions") or [])}
    for approver in task.get("approvers") or []:
        if approver not in decided:
            return approver
    return None


def next_escalation(task: Dict[str, Any]) -> Optional[str]:
    """返回升级链上的下一级（§8.2.2 规则 2）；已到链尾返回 None"""
    chain = RISK_ESCALATION.get(task["risk"], [])
    done: List[str] = task.get("escalated_to") or []
    for level in chain:
        if level not in done:
            return level
    return None


def timeout_action_for(risk: str) -> str:
    """超时后动作（§8.2.2）：low 自动通过 / high 自动驳回 / medium 保持挂起升级。

    规则 1：高风险禁止自动通过。
    返回: "auto_pass" | "auto_reject" | "escalate"
    """
    if risk == RISK_LOW:
        return "auto_pass"
    if risk == RISK_HIGH:
        return "auto_reject"
    return "escalate"
