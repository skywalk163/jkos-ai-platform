"""DSH 工作流引擎 - 状态机与执行（M0 0.2 顺序链 + M1 黑板并行/审批任务化）

职责（§8.1/§8.2）：
  1. 执行工作流节点链，步骤轨迹落 workflow_step；
     - 管道式：顺序节点 prev_output 传递（M0 兼容）；
     - 黑板式：parallel_group 相同的相邻节点并行执行（§8.1.1 范式 B），
       各节点按 write_keys 把输出合并进共享黑板（存实例 context.blackboard），
       同组 write_keys 定义期互斥校验，收敛时审计 blackboard.completed；
  2. 崩溃恢复：resume() 把遗留 RUNNING 步骤重置 PENDING 后续跑
     （要求节点幂等；已完成步骤自动跳过）；
  3. 人工干预（M1 任务 1.4，§8.2）：approval 节点 → approval_task 记录
     （风险等级超时 / serial/all/any 协作模式 / 决策留痕），
     approve_task() 决策、sweep_timeouts() 超时扫描（低=自动通过/中=升级/高=自动驳回）；
  4. 全程审计埋点：启动/步骤/崩溃/恢复/审批/超时/终态均写 audit_event。

设计约定：引擎无内存状态，全部状态在 SQLite——进程随时可死，resume 即恢复。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from jkos_core.audit import ACTOR_HUMAN, ACTOR_SYSTEM
from jkos_core.db import (
    INSTANCE_CANCELLED,
    INSTANCE_COMPLETED,
    INSTANCE_RUNNING,
    INSTANCE_TERMINAL,
    INSTANCE_WAITING_APPROVAL,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
    STEP_WAITING,
    ApprovalTaskRepo,
    WorkflowRepo,
)
from jkos_core.workflow.approval import (
    ESCALATION_WINDOW_HOURS,
    next_escalation,
    resolve_decision,
    serial_next_approver,
    timeout_action_for,
    timeout_at_for,
)
from jkos_core.workflow.base import (
    ApprovalSpec,
    EngineCrash,
    NodeContext,
    NodeSpec,
    WorkflowDef,
    WorkflowError,
)
from jkos_core.workflow.nodes import BUILTIN_NODES, get_workflow

logger = logging.getLogger("dsh.workflow")


class WorkflowEngine:
    """工作流引擎（依赖经 AppComponents 注入，可 SimpleNamespace 替换以便测试）"""

    def __init__(self, comps: Any):
        self.repo: WorkflowRepo = comps.workflows
        self.audit = comps.audit
        self.llm = comps.llm
        self.tenants = comps.tenants
        # M1 新依赖（可选注入，保持 M0 测试兼容）
        self.approvals: Optional[ApprovalTaskRepo] = getattr(comps, "approvals", None)
        self.notify = getattr(comps, "notify", None)
        self.jwt = getattr(comps, "jwt", None)
        self.comps = comps  # 保存组件引用，便于测试访问
        self._nodes: Dict[str, Any] = dict(BUILTIN_NODES)

    def register_node(self, name: str, handler) -> None:
        """注册自定义节点处理器（M1 插件化节点入口）"""
        self._nodes[name] = handler

    # ── 对外操作 ──

    async def start(
        self,
        workflow_code: str,
        tenant_code: str = "dev",
        context: Optional[Dict[str, Any]] = None,
        created_by: str = "system",
    ) -> Dict[str, Any]:
        """创建实例并执行到终态/悬挂点，返回 {instance, steps} 快照"""
        definition = get_workflow(workflow_code)
        self._validate_definition(definition)
        tenant = self.tenants.get_by_code(tenant_code)
        if not tenant:
            raise WorkflowError(f"租户不存在: {tenant_code}")
        inst = self.repo.create_instance(
            tenant["id"], definition.code, context or {}, created_by=created_by
        )
        self._audit(inst, "workflow.started", after={
            "workflow": definition.code,
            "nodes": [n.node_code for n in definition.nodes],
        })
        logger.info("工作流启动 code=%s instance=%s tenant=%s", definition.code, inst["id"], tenant_code)
        return await self._execute(inst, definition)

    async def resume(self, instance_id: str, actor: str = "system") -> Dict[str, Any]:
        """崩溃/中断后续跑：遗留 RUNNING 步骤重置 PENDING，从未完成处继续"""
        inst = self._must_get(instance_id)
        if inst["status"] in INSTANCE_TERMINAL:
            raise WorkflowError(f"实例已终态 {inst['status']}，不可续跑")
        definition = get_workflow(inst["workflow_code"])
        requeued = self.repo.requeue_running_steps(instance_id)
        self._audit(inst, "workflow.resumed", actor_id=actor, after={"requeued_steps": requeued})
        logger.info("工作流续跑 instance=%s 重置步骤=%d", instance_id, requeued)
        return await self._execute(inst, definition)

    async def approve(
        self,
        instance_id: str,
        decision: bool,
        decided_by: str = "admin",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """审批实例当前等待的审批步骤（M0 兼容入口，内部转发审批任务）"""
        inst = self._must_get(instance_id)
        if inst["status"] != INSTANCE_WAITING_APPROVAL:
            raise WorkflowError(f"实例不在等待审批状态: {inst['status']}")
        waiting = next(
            (s for s in self.repo.get_steps(instance_id) if s["status"] == STEP_WAITING), None
        )
        if waiting is None:
            raise WorkflowError("找不到 WAITING 状态的审批步骤")
        if self.approvals:
            task = self.approvals.get_pending_by_step(waiting["id"])
            if task:
                return await self.approve_task(task["id"], decision, decided_by, reason)
        # 旧数据兜底（无任务记录）：沿用 M0 行为
        return await self._apply_decision(inst, waiting, decision, decided_by, reason, task_id=None)

    async def approve_task(
        self,
        task_id: str,
        decision: bool,
        approver: str = "admin",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """审批任务决策（M1 任务 1.4）：校验资格/顺序 → 记录意见 → 按协作模式收敛"""
        if self.approvals is None:
            raise WorkflowError("审批仓储未注入（comps.approvals）")
        task = self.approvals.get_task(task_id)
        if not task:
            raise WorkflowError(f"审批任务不存在: {task_id}")
        if task["status"] != "PENDING":
            raise WorkflowError(f"审批任务已终态: {task['status']}")
        approvers = task.get("approvers") or []
        if approvers and approver not in approvers:
            raise WorkflowError(f"{approver} 不在审批人列表: {approvers}")
        nxt = serial_next_approver(task)
        if nxt is not None and approver != nxt:
            raise WorkflowError(f"串行审批未轮到 {approver}（当前应 {nxt} 决策）")
        self.approvals.record_decision(
            task_id, approver=approver,
            decision="approve" if decision else "reject", reason=reason,
        )
        fresh = self.approvals.get_task(task_id)
        final, note = resolve_decision(fresh or task)
        inst = self._must_get(task["instance_id"])
        if final == "APPROVED":
            self.approvals.finish_task(task_id, "APPROVED")
            self._audit(inst, "approval.approved", actor_type=ACTOR_HUMAN, actor_id=approver,
                        after={"task": task_id, "note": note, "reason": reason})
            logger.info("审批通过 task=%s instance=%s (%s)", task_id, inst["id"], note)
            return await self._apply_decision(inst, {"id": task["step_id"], "node_code": task["node_code"]},
                                              True, approver, reason, task_id)
        if final == "REJECTED":
            self.approvals.finish_task(task_id, "REJECTED")
            self._audit(inst, "approval.rejected", actor_type=ACTOR_HUMAN, actor_id=approver,
                        after={"task": task_id, "note": note, "reason": reason})
            logger.info("审批驳回 task=%s instance=%s (%s)", task_id, inst["id"], note)
            return await self._apply_decision(inst, {"id": task["step_id"], "node_code": task["node_code"]},
                                              False, approver, reason, task_id)
        # 未收敛（会签/或签等待其余人）：决策已留痕，任务继续挂起
        self._audit(inst, "approval.decision_recorded", actor_type=ACTOR_HUMAN, actor_id=approver,
                    after={"task": task_id, "decision": "approve" if decision else "reject",
                           "reason": reason})
        return self.status(inst["id"])

    async def sweep_timeouts(self) -> Dict[str, List[str]]:
        """审批超时扫描（§8.2.2，M1 任务 1.4）——由 CLI/API/定时器周期触发

        low    → 超时自动通过（审计 TIMEOUT 语义，禁止高风险自动通过的规则由
                 timeout_action_for 保证：high 只会 auto_reject）
        medium → 保持挂起 + 升级链前移 + 顺延窗口 + 通知
        high   → 超时自动驳回（宁慢勿错）
        """
        handled: Dict[str, List[str]] = {"auto_passed": [], "auto_rejected": [], "escalated": []}
        if self.approvals is None:
            return handled
        for task in self.approvals.due_tasks():
            action = timeout_action_for(task["risk"])
            inst = self._must_get(task["instance_id"])
            if inst["status"] in INSTANCE_TERMINAL:
                self.approvals.finish_task(task["id"], "CANCELLED")
                continue
            if action == "auto_pass":
                self.approvals.record_decision(
                    task["id"], approver="system", decision="approve",
                    reason="低风险超时自动通过（§8.2.2）",
                )
                self.approvals.finish_task(task["id"], "APPROVED")
                self._audit(inst, "approval.timeout_auto_pass",
                            after={"task": task["id"], "risk": task["risk"]})
                handled["auto_passed"].append(task["id"])
                await self._apply_decision(
                    inst, {"id": task["step_id"], "node_code": task["node_code"]},
                    True, "system:timeout", "低风险超时自动通过", task["id"],
                )
            elif action == "auto_reject":
                self.approvals.record_decision(
                    task["id"], approver="system", decision="reject",
                    reason="高风险超时自动驳回——宁慢勿错（§8.2.2）",
                )
                self.approvals.finish_task(task["id"], "REJECTED")
                self._audit(inst, "approval.timeout_auto_reject",
                            after={"task": task["id"], "risk": task["risk"]})
                handled["auto_rejected"].append(task["id"])
                await self._apply_decision(
                    inst, {"id": task["step_id"], "node_code": task["node_code"]},
                    False, "system:timeout", "高风险超时自动驳回", task["id"],
                )
            else:  # escalate
                chain = list(task.get("escalated_to") or [])
                nxt = next_escalation(task)
                if nxt:
                    chain.append(nxt)
                    self.approvals.escalate(task["id"], chain)
                    self.approvals.extend_timeout(
                        task["id"], timeout_at_for("medium", ESCALATION_WINDOW_HOURS)
                    )
                    self._audit(inst, "approval.escalated",
                                after={"task": task["id"], "escalated_to": nxt, "chain": chain})
                    self._notify("approval.escalated", inst,
                                 f"审批升级：{task['node_code']} → {nxt}",
                                 {"task": task["id"], "escalated_to": nxt})
                    handled["escalated"].append(task["id"])
                else:
                    # 升级链耗尽仍无响应：保持挂起，顺延窗口继续提醒
                    self.approvals.extend_timeout(
                        task["id"], timeout_at_for("medium", ESCALATION_WINDOW_HOURS)
                    )
                    self._audit(inst, "approval.escalation_exhausted",
                                after={"task": task["id"], "note": "升级链已耗尽，保持挂起提醒"})
                    handled["escalated"].append(task["id"])
        return handled

    async def cancel(
        self, instance_id: str, actor: str = "system", reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """取消非终态实例（同时取消其未决审批任务）"""
        inst = self._must_get(instance_id)
        if inst["status"] in INSTANCE_TERMINAL:
            raise WorkflowError(f"实例已终态 {inst['status']}，不可取消")
        if self.approvals:
            for task in self.approvals.list_tasks(instance_id=instance_id, status="PENDING"):
                self.approvals.finish_task(task["id"], "CANCELLED")
        payload = {"reason": reason or "手动取消", "by": actor}
        self.repo.update_status(instance_id, INSTANCE_CANCELLED, error=payload)
        self._audit(inst, "workflow.cancelled", actor_id=actor, after=payload)
        return self.status(instance_id)

    def status(self, instance_id: str) -> Dict[str, Any]:
        """实例 + 步骤轨迹快照"""
        return {"instance": self._must_get(instance_id), "steps": self.repo.get_steps(instance_id)}

    def list_instances(self, tenant_code: Optional[str] = None, status: Optional[str] = None,
                       limit: int = 20) -> List[Dict[str, Any]]:
        tenant_id = None
        if tenant_code:
            tenant = self.tenants.get_by_code(tenant_code)
            if not tenant:
                raise WorkflowError(f"租户不存在: {tenant_code}")
            tenant_id = tenant["id"]
        return self.repo.list_instances(tenant_id=tenant_id, status=status, limit=limit)

    def list_pending_approvals(self, tenant_code: Optional[str] = None,
                               limit: int = 20) -> List[Dict[str, Any]]:
        """待审批任务列表（M1 1.4 CLI/API 数据源）"""
        if self.approvals is None:
            return []
        tenant_id = None
        if tenant_code:
            tenant = self.tenants.get_by_code(tenant_code)
            if not tenant:
                raise WorkflowError(f"租户不存在: {tenant_code}")
            tenant_id = tenant["id"]
        return self.approvals.list_tasks(tenant_id=tenant_id, status="PENDING", limit=limit)

    # ── 定义期校验（黑板写冲突等）──

    def _validate_definition(self, definition: WorkflowDef) -> None:
        """定义期静态校验：并行组 write_keys 互斥（§8.1.1 写冲突校验）、
        approval 节点不得入并行组"""
        groups: Dict[str, List[NodeSpec]] = {}
        for spec in definition.nodes:
            if spec.parallel_group:
                if spec.node_type == "approval":
                    raise WorkflowError(
                        f"工作流 {definition.code}: 审批节点 {spec.node_code} 不可加入并行组"
                    )
                groups.setdefault(spec.parallel_group, []).append(spec)
        for group, members in groups.items():
            seen: Dict[str, str] = {}
            for spec in members:
                for key in spec.write_keys:
                    if key in seen:
                        raise WorkflowError(
                            f"工作流 {definition.code}: 并行组 {group} 写冲突——"
                            f"{seen[key]} 与 {spec.node_code} 都要写分区 '{key}'"
                        )
                    seen[key] = spec.node_code

    # ── 执行内核 ──

    async def _execute(self, inst: Dict[str, Any], definition: Any) -> Dict[str, Any]:
        """执行节点链（幂等，可重复进入——崩溃恢复即重入）。

        相邻同 parallel_group 的节点合并为并行批次（黑板式），
        其余保持 M0 顺序管道语义。
        """
        instance_id = inst["id"]
        self.repo.update_status(instance_id, INSTANCE_RUNNING)
        steps = {s["seq"]: s for s in self.repo.get_steps(instance_id)}
        prev_output: Optional[Dict[str, Any]] = None
        batch: List[Tuple[int, NodeSpec]] = []   # (seq, spec) 当前并行批次
        for seq, spec in enumerate(definition.nodes, start=1):
            if spec.parallel_group and batch and batch[0][1].parallel_group == spec.parallel_group:
                batch.append((seq, spec))
                continue
            if batch:  # 收敛上一批次
                kind, payload, prev_output = await self._run_batch(inst, batch, prev_output)
                if kind != "done":
                    return payload
                batch = []
            batch = [(seq, spec)] if spec.parallel_group else []
            if spec.parallel_group:
                continue
            step = steps.get(seq) or self.repo.append_step(instance_id, seq, spec.node_code, spec.node_type)
            if step["status"] in (STEP_SUCCEEDED, STEP_SKIPPED):
                prev_output = step.get("output") or prev_output
                # 恢复重放：步骤已成功但黑板合并可能未落盘（崩溃窗口），幂等补合并
                self._merge_blackboard(inst, [(spec, step.get("output") or {})])
                continue
            if step["status"] == STEP_WAITING:
                # 恢复执行时遇到等待审批的步骤 → 实例回到等待审批
                self.repo.update_status(instance_id, INSTANCE_WAITING_APPROVAL,
                                        current_step=spec.node_code)
                return self.status(instance_id)
            if spec.node_type == "approval":
                return self._enter_approval(inst, spec, step)
            kind, payload = await self._run_step(inst, spec, step, prev_output)
            if kind == "crash":
                return self.status(instance_id)   # 悬挂在崩溃点，等 resume()
            if kind == "failed":
                return payload                     # 已是失败快照
            self._merge_blackboard(inst, [(spec, payload)])   # 单节点 write_keys → 黑板
            prev_output = payload
        if batch:  # 收敛末尾批次
            kind, payload, prev_output = await self._run_batch(inst, batch, prev_output)
            if kind != "done":
                return payload
        final = self._must_get(instance_id)
        self.repo.update_status(instance_id, INSTANCE_COMPLETED, result=prev_output)
        self._audit(final, "workflow.completed", after={"result": prev_output})
        logger.info("工作流完成 instance=%s code=%s", instance_id, definition.code)
        return self.status(instance_id)

    async def _run_batch(
        self, inst: Dict[str, Any], batch: List[Tuple[int, NodeSpec]], prev_output: Optional[Dict[str, Any]]
    ) -> Tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
        """并行执行一个黑板批次（§8.1.1 范式 B），返回 (kind, payload, prev_output)

        各节点并发执行；成功后按各自 write_keys 合并进黑板并持久化；
        任一 crash → 整体悬挂（成功节点的输出照常合并，resume 幂等跳过）；
        任一 failed → 实例失败。
        """
        instance_id = inst["id"]
        results = await asyncio.gather(*[
            self._run_batch_member(inst, seq, spec, prev_output) for seq, spec in batch
        ])
        # 黑板合并：成员输出按 write_keys 归并（定义期已校验互斥）
        self._merge_blackboard(inst, [
            (spec, output) for (_, spec), (kind, output) in zip(batch, results) if kind == "done"
        ])
        # 失败/崩溃优先级：failed > crash > done
        for (seq, spec), (kind, output) in zip(batch, results):
            if kind == "failed":
                return "failed", output, prev_output
        for (seq, spec), (kind, output) in zip(batch, results):
            if kind == "crash":
                return "crash", self.status(instance_id), prev_output
        group_outputs = {spec.node_code: output for (_, spec), (kind, output) in zip(batch, results) if kind == "done"}
        return "done", {"parallel_group": batch[0][1].parallel_group, "outputs": group_outputs}, group_outputs

    async def _run_batch_member(
        self, inst: Dict[str, Any], seq: int, spec: NodeSpec, prev_output: Optional[Dict[str, Any]]
    ) -> Tuple[str, Dict[str, Any]]:
        """并行批次的单个成员：append/幂等跳过 + 执行（不判终态，由 _run_batch 收敛）"""
        steps = {s["seq"]: s for s in self.repo.get_steps(inst["id"])}
        step = steps.get(seq) or self.repo.append_step(inst["id"], seq, spec.node_code, spec.node_type)
        if step["status"] in (STEP_SUCCEEDED, STEP_SKIPPED):
            return "done", step.get("output") or {}
        kind, payload = await self._run_step(inst, spec, step, prev_output)
        if kind == "done":
            return "done", payload
        if kind == "crash":
            return "crash", {}
        return "failed", payload

    async def _run_step(self, inst: Dict[str, Any], spec: NodeSpec,
                        step: Dict[str, Any], prev_output: Optional[Dict[str, Any]]):
        """执行单个节点。返回 (kind, payload)：done/output、crash/None、failed/status"""
        instance_id = inst["id"]
        self.repo.start_step(step["id"])
        self.repo.update_status(instance_id, INSTANCE_RUNNING, current_step=spec.node_code)
        current = self.repo.get_step_by_seq(instance_id, step["seq"]) or step
        blackboard = self._load_blackboard(inst)
        ctx = NodeContext(
            instance=self.repo.get_instance(instance_id) or inst,
            step=current,
            prev_output=prev_output,
            engine=self,
            blackboard=blackboard,
        )
        handler = self._nodes.get(spec.handler)
        try:
            if handler is None:
                raise WorkflowError(f"未注册的节点处理器: {spec.handler}")
            output = await handler(ctx) or {}
        except EngineCrash as crash:
            # 模拟进程崩溃：步骤与实例都留在 RUNNING（等价 kill -9 后的遗留态）
            self._audit(ctx.instance, "workflow.crashed",
                        after={"step": spec.node_code, "detail": str(crash)})
            logger.warning("工作流模拟崩溃 instance=%s step=%s", instance_id, spec.node_code)
            return "crash", None
        except Exception as exc:
            return "failed", self._fail(ctx.instance, current, spec, exc)
        self.repo.finish_step(step["id"], STEP_SUCCEEDED, output=output)
        self._audit(ctx.instance, "workflow.step_succeeded",
                    after={"seq": step["seq"], "node": spec.node_code, "output": output})
        return "done", output

    def _enter_approval(self, inst: Dict[str, Any], spec: NodeSpec,
                        step: Dict[str, Any]) -> Dict[str, Any]:
        """approval 节点：步骤 WAITING + 创建审批任务 + 实例 WAITING_APPROVAL（§8.2）"""
        self.repo.start_step(step["id"])
        self.repo.finish_step(step["id"], STEP_WAITING)
        spec_appr = spec.approval or ApprovalSpec()
        timeout_at = timeout_at_for(spec_appr.risk, spec_appr.timeout_hours)
        task_id = None
        if self.approvals:
            task = self.approvals.create_task(
                tenant_id=inst["tenant_id"], instance_id=inst["id"], step_id=step["id"],
                node_code=spec.node_code, risk=spec_appr.risk, mode=spec_appr.mode,
                approvers=spec_appr.approvers, timeout_at=timeout_at,
            )
            task_id = task["id"]
        payload = {"step": spec.node_code, "risk": spec_appr.risk, "mode": spec_appr.mode,
                   "approvers": spec_appr.approvers, "timeout_at": timeout_at, "task": task_id}
        self._audit(inst, "workflow.waiting_approval", after=payload)
        self._notify("approval.created", inst,
                     f"待审批：{inst['workflow_code']}/{spec.node_code}（风险 {spec_appr.risk}）",
                     payload)
        self.repo.update_status(inst["id"], INSTANCE_WAITING_APPROVAL, current_step=spec.node_code)
        return self.status(inst["id"])

    async def _apply_decision(
        self, inst: Dict[str, Any], waiting: Dict[str, Any], decision: bool,
        decided_by: str, reason: Optional[str], task_id: Optional[str],
    ) -> Dict[str, Any]:
        """审批决策落地：通过 → 继续执行；驳回 → 步骤 SKIPPED + 实例 CANCELLED"""
        definition = get_workflow(inst["workflow_code"])
        if decision:
            self.repo.finish_step(waiting["id"], STEP_SUCCEEDED, output={
                "decision": "approved", "decided_by": decided_by, "task": task_id,
            })
            self._audit(inst, "workflow.approved", actor_type=ACTOR_HUMAN, actor_id=decided_by,
                        after={"step": waiting["node_code"], "reason": reason})
            self.repo.update_status(inst["id"], INSTANCE_RUNNING)
            fresh = self._must_get(inst["id"])
            return await self._execute(fresh, definition)
        self.repo.finish_step(waiting["id"], STEP_SKIPPED, output={
            "decision": "rejected", "decided_by": decided_by, "task": task_id,
        })
        self._audit(inst, "workflow.rejected", actor_type=ACTOR_HUMAN, actor_id=decided_by,
                    after={"step": waiting["node_code"], "reason": reason})
        return await self.cancel(inst["id"], actor=decided_by, reason=reason or "审批驳回")

    def _load_blackboard(self, inst: Dict[str, Any]) -> Dict[str, Any]:
        """读取实例黑板（黑板数据持久化于 context.blackboard）"""
        fresh = self.repo.get_instance(inst["id"]) or inst
        context = fresh.get("context") or {}
        return dict(context.get("blackboard") or {})


    def _merge_blackboard(
        self, inst: Dict[str, Any], pairs: List[Tuple[NodeSpec, Dict[str, Any]]]
    ) -> List[str]:
        """按各节点 write_keys 把输出归并进黑板并持久化（§8.1.1），返回实际合并的 key。

        幂等：相同输出重复合并结果不变（恢复路径会重放补合并）。
        """
        if not pairs:
            return []
        fresh = self.repo.get_instance(inst["id"]) or inst
        context = dict(fresh.get("context") or {})
        blackboard = dict(context.get("blackboard") or {})
        merged: List[str] = []
        for spec, output in pairs:
            if not spec.write_keys or not output:
                continue
            for key in spec.write_keys:
                if key in output:
                    blackboard[key] = output[key]
                    merged.append(key)
        if not merged:
            return []
        context["blackboard"] = blackboard
        self.repo.update_context(inst["id"], context)
        self._audit(inst, "blackboard.completed",
                    after={"group": pairs[0][0].parallel_group, "keys": sorted(set(merged))})
        return merged

    def _fail(self, inst: Dict[str, Any], step: Dict[str, Any], spec: NodeSpec,
              exc: Exception) -> Dict[str, Any]:
        err = {"type": type(exc).__name__, "message": str(exc), "step": spec.node_code}
        self.repo.finish_step(step["id"], "FAILED", error=err)
        self._audit(inst, "workflow.step_failed",
                    after={"seq": step["seq"], "node": spec.node_code, "error": err})
        self.repo.update_status(inst["id"], "FAILED", error=err)
        self._audit(inst, "workflow.failed", after=err)
        logger.error("工作流失败 instance=%s step=%s: %s", inst["id"], spec.node_code, exc)
        return self.status(inst["id"])

    # ── 内部工具 ──

    def _notify(self, event: str, inst: Dict[str, Any], title: str, meta: Any = None) -> None:
        """通知钩子（M1 任务 1.5）：未注入或发送失败都不影响工作流主流程"""
        if self.notify is None:
            return
        try:
            self.notify.send(event, title=title, body=str(meta),
                             tenant_id=inst.get("tenant_id"),
                             ref_type="workflow_instance", ref_id=inst.get("id"))
        except Exception as exc:  # 通知是旁路，失败仅告警
            logger.warning("通知发送失败 event=%s: %s", event, exc)

    def _audit(self, inst: Dict[str, Any], action: str, *, actor_type: str = ACTOR_SYSTEM,
               actor_id: str = "system", before: Any = None, after: Any = None) -> None:
        """工作流审计埋点（trace_id = 实例 ID，串起全链路事件）"""
        self.audit.log(
            tenant_id=inst["tenant_id"], action=action,
            resource_type="workflow_instance", resource_id=inst["id"],
            actor_type=actor_type, actor_id=actor_id,
            before=before, after=after, trace_id=inst["id"],
        )

    def _must_get(self, instance_id: str) -> Dict[str, Any]:
        inst = self.repo.get_instance(instance_id)
        if not inst:
            raise WorkflowError(f"工作流实例不存在: {instance_id}")
        return inst
