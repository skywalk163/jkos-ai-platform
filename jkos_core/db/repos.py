"""DSH 数据层 - 仓储（Repo）

约定：JSON 字段（*_json）在本层边界编解码，上层只见 dict/list；
所有方法返回可序列化的普通字典，便于 API 层直接输出。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from jkos_core.db.connection import Database, utc_now
from jkos_core.db.ulid import new_ulid

logger = logging.getLogger("dsh.db")

# ─── 工作流状态常量（0.2 引擎状态机使用，§8.1/§8.2）──

INSTANCE_PENDING = "PENDING"
INSTANCE_RUNNING = "RUNNING"
INSTANCE_WAITING_APPROVAL = "WAITING_APPROVAL"
INSTANCE_SUSPENDED = "SUSPENDED"
INSTANCE_COMPLETED = "COMPLETED"
INSTANCE_FAILED = "FAILED"
INSTANCE_CANCELLED = "CANCELLED"

STEP_PENDING = "PENDING"
STEP_RUNNING = "RUNNING"
STEP_WAITING = "WAITING"
STEP_SUCCEEDED = "SUCCEEDED"
STEP_FAILED = "FAILED"
STEP_SKIPPED = "SKIPPED"
STEP_COMPENSATED = "COMPENSATED"

# 实例终态：进入终态后不可再更新步骤
INSTANCE_TERMINAL = (INSTANCE_COMPLETED, INSTANCE_FAILED, INSTANCE_CANCELLED)

# ─── 审批任务常量（M1 任务 1.4，§8.2 人工干预）──

TASK_PENDING = "PENDING"
TASK_APPROVED = "APPROVED"
TASK_REJECTED = "REJECTED"
TASK_CANCELLED = "CANCELLED"

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISKS = (RISK_LOW, RISK_MEDIUM, RISK_HIGH)

MODE_SERIAL = "serial"
MODE_ALL = "all"
MODE_ANY = "any"
APPROVAL_MODES = (MODE_SERIAL, MODE_ALL, MODE_ANY)
def _dumps(obj: Any) -> Optional[str]:
    return None if obj is None else json.dumps(obj, ensure_ascii=False)


def _loads(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text  # 非法 JSON 原样返回，不丢数据


class TenantRepo:
    """租户表访问"""

    def __init__(self, db: Database):
        self.db = db

    def get_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        return self.db.query_one("SELECT * FROM tenants WHERE code = ?", (code,))

    def list_all(self) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM tenants ORDER BY code")

    def ensure(self, code: str, name: str) -> Dict[str, Any]:
        """存在即返回，不存在则创建（幂等）"""
        row = self.get_by_code(code)
        if row:
            return row
        now = utc_now()
        self.db.execute(
            "INSERT INTO tenants (id, code, name, isolation_level, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (new_ulid(), code, name, "L3", "active", now, now),
        )
        return self.get_by_code(code)  # type: ignore[return-value]


class WorkflowRepo:
    """工作流实例 + 步骤的持久化（0.2 引擎的存储接口）"""

    def __init__(self, db: Database):
        self.db = db

    # ─── 实例 ───

    def create_instance(
        self,
        tenant_id: str,
        workflow_code: str,
        context: Optional[Dict[str, Any]] = None,
        created_by: str = "system",
    ) -> Dict[str, Any]:
        now = utc_now()
        row = {
            "id": new_ulid(),
            "tenant_id": tenant_id,
            "workflow_code": workflow_code,
            "status": INSTANCE_PENDING,
            "current_step": None,
            "context_json": _dumps(context or {}),
            "result_json": None,
            "error_json": None,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
        }
        self.db.execute(
            "INSERT INTO workflow_instance (id, tenant_id, workflow_code, status, current_step,"
            " context_json, result_json, error_json, created_by, created_at, updated_at, finished_at)"
            " VALUES (:id,:tenant_id,:workflow_code,:status,:current_step,"
            " :context_json,:result_json,:error_json,:created_by,:created_at,:updated_at,:finished_at)",
            row,
        )
        return self.get_instance(row["id"])  # type: ignore[return-value]

    def get_instance(self, instance_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM workflow_instance WHERE id = ?", (instance_id,))
        if row:
            row["context"] = _loads(row.pop("context_json"))
            row["result"] = _loads(row.pop("result_json"))
            row["error"] = _loads(row.pop("error_json"))
        return row

    def update_context(self, instance_id: str, context: Dict[str, Any]) -> None:
        """整体覆写实例上下文（M1 黑板式协作：blackboard 分区随节点写入持久化）"""
        self.db.execute(
            "UPDATE workflow_instance SET context_json = ?, updated_at = ? WHERE id = ?",
            (_dumps(context), utc_now(), instance_id),
        )

    def update_status(
        self,
        instance_id: str,
        status: str,
        current_step: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        sets = ["status = ?", "updated_at = ?"]
        params: List[Any] = [status, utc_now()]
        if current_step is not None:
            sets.append("current_step = ?")
            params.append(current_step)
        if result is not None:
            sets.append("result_json = ?")
            params.append(_dumps(result))
        if error is not None:
            sets.append("error_json = ?")
            params.append(_dumps(error))
        if status in INSTANCE_TERMINAL:
            sets.append("finished_at = ?")
            params.append(utc_now())
        params.append(instance_id)
        self.db.execute(
            f"UPDATE workflow_instance SET {', '.join(sets)} WHERE id = ?", tuple(params)
        )

    def list_instances(
        self,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM workflow_instance WHERE 1=1"
        params: List[Any] = []
        if tenant_id:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, tuple(params))
        for row in rows:  # 与 get_instance 一致：JSON 列反序列化
            row["context"] = _loads(row.pop("context_json"))
            row["result"] = _loads(row.pop("result_json"))
            row["error"] = _loads(row.pop("error_json"))
        return rows

    # ─── 步骤 ───

    def append_step(
        self,
        instance_id: str,
        seq: int,
        node_code: str,
        node_type: str = "tool",
        input_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """追加步骤；(instance_id, seq) 唯一约束保证重复提交被拒绝"""
        self.db.execute(
            "INSERT INTO workflow_step (id, instance_id, seq, node_code, node_type, status,"
            " input_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (new_ulid(), instance_id, seq, node_code, node_type, STEP_PENDING,
             _dumps(input_data), utc_now()),
        )
        return self.get_step_by_seq(instance_id, seq)  # type: ignore[return-value]

    def get_step_by_seq(self, instance_id: str, seq: int) -> Optional[Dict[str, Any]]:
        row = self.db.query_one(
            "SELECT * FROM workflow_step WHERE instance_id = ? AND seq = ?", (instance_id, seq)
        )
        return self._decode_step(row) if row else None

    def get_steps(self, instance_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM workflow_step WHERE instance_id = ? ORDER BY seq", (instance_id,)
        )
        return [self._decode_step(r) for r in rows]

    def start_step(self, step_id: str) -> None:
        """步骤开始执行：状态 → RUNNING，attempts 自增（重试计数）"""
        self.db.execute(
            "UPDATE workflow_step SET status = ?, attempts = attempts + 1, started_at = ?"
            " WHERE id = ?",
            (STEP_RUNNING, utc_now(), step_id),
        )

    def finish_step(
        self,
        step_id: str,
        status: str,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.db.execute(
            "UPDATE workflow_step SET status = ?, output_json = ?, error_json = ?, finished_at = ?"
            " WHERE id = ?",
            (status, _dumps(output), _dumps(error), utc_now(), step_id),
        )

    def requeue_running_steps(self, instance_id: str) -> int:
        """崩溃恢复：遗留 RUNNING 步骤重置为 PENDING（引擎要求节点幂等）。

        注意：WAITING（等待审批）步骤不动——审批任务在恢复后依然有效。
        返回受影响行数。
        """
        cur = self.db.execute(
            "UPDATE workflow_step SET status = ?, started_at = NULL"
            " WHERE instance_id = ? AND status = ?",
            (STEP_PENDING, instance_id, STEP_RUNNING),
        )
        return cur.rowcount

    @staticmethod
    def _decode_step(row: Dict[str, Any]) -> Dict[str, Any]:
        row["input"] = _loads(row.pop("input_json"))
        row["output"] = _loads(row.pop("output_json"))
        row["error"] = _loads(row.pop("error_json"))
        return row


class AuditRepo:
    """审计事件仓储（append-only，写入由触发器强制不可改）"""

    def __init__(self, db: Database):
        self.db = db

    def append(
        self,
        *,
        tenant_id: str,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> str:
        event_id = new_ulid()
        self.db.execute(
            "INSERT INTO audit_event (id, tenant_id, actor_type, actor_id, action, resource_type,"
            " resource_id, before_json, after_json, reason, trace_id, occurred_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, tenant_id, actor_type, actor_id, action, resource_type,
             resource_id, _dumps(before), _dumps(after), reason, trace_id, utc_now()),
        )
        return event_id

    def list_events(
        self,
        tenant_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM audit_event WHERE 1=1"
        params: List[Any] = []
        if tenant_id:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        if resource_type:
            sql += " AND resource_type = ?"
            params.append(resource_type)
        if resource_id:
            sql += " AND resource_id = ?"
            params.append(resource_id)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, tuple(params))
        for r in rows:
            r["before"] = _loads(r.pop("before_json"))
            r["after"] = _loads(r.pop("after_json"))
        return rows


class LlmUsageRepo:
    """LLM Token 计量仓储（§8.5.4 成本报表数据源）"""

    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        *,
        tenant_id: str = "system",
        provider: str,
        model: str,
        purpose: Optional[str] = None,
        ref_type: Optional[str] = None,
        ref_id: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: int = 0,
        status: str = "ok",
        error_message: Optional[str] = None,
    ) -> str:
        record_id = new_ulid()
        self.db.execute(
            "INSERT INTO llm_usage (id, tenant_id, provider, model, purpose, ref_type, ref_id,"
            " prompt_tokens, completion_tokens, total_tokens, duration_ms, status, error_message,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record_id, tenant_id, provider, model, purpose, ref_type, ref_id,
             prompt_tokens, completion_tokens, prompt_tokens + completion_tokens,
             duration_ms, status, error_message, utc_now()),
        )
        return record_id

    def summary(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """按 provider+status 汇总调用次数与 token 消耗"""
        sql = (
            "SELECT provider, status, COUNT(*) AS calls,"
            " SUM(prompt_tokens) AS prompt_tokens,"
            " SUM(completion_tokens) AS completion_tokens,"
            " SUM(total_tokens) AS tokens, AVG(duration_ms) AS avg_ms"
            " FROM llm_usage"
        )
        params: List[Any] = []
        if tenant_id:
            sql += " WHERE tenant_id = ?"
            params.append(tenant_id)
        sql += " GROUP BY provider, status ORDER BY provider"
        return self.db.query(sql, tuple(params))


class ApprovalTaskRepo:
    """审批任务仓储（M1 任务 1.4，§8.2）——一个审批节点对应一条任务记录"""

    def __init__(self, db: Database):
        self.db = db

    def create_task(
        self,
        *,
        tenant_id: str,
        instance_id: str,
        step_id: str,
        node_code: str,
        risk: str = RISK_MEDIUM,
        mode: str = MODE_ANY,
        approvers: Optional[List[str]] = None,
        timeout_at: Optional[str] = None,
        created_by: str = "system",
    ) -> Dict[str, Any]:
        now = utc_now()
        task_id = new_ulid()
        self.db.execute(
            "INSERT INTO approval_task (id, tenant_id, instance_id, step_id, node_code, risk,"
            " mode, approvers, status, decisions, timeout_at, escalated_to, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, tenant_id, instance_id, step_id, node_code, risk, mode,
             _dumps(approvers or []), TASK_PENDING, "[]", timeout_at, None, created_by, now),
        )
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM approval_task WHERE id = ?", (task_id,))
        return self._decode(row) if row else None

    def get_pending_by_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one(
            "SELECT * FROM approval_task WHERE step_id = ? AND status = ? ORDER BY created_at DESC",
            (step_id, TASK_PENDING),
        )
        return self._decode(row) if row else None

    def list_tasks(
        self,
        tenant_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM approval_task WHERE 1=1"
        params: List[Any] = []
        if tenant_id:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)
        if instance_id:
            sql += " AND instance_id = ?"
            params.append(instance_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._decode(r) for r in self.db.query(sql, tuple(params))]

    def due_tasks(self, now: Optional[str] = None) -> List[Dict[str, Any]]:
        """已到期仍未决的任务（超时扫描数据源，§8.2.2）"""
        rows = self.db.query(
            "SELECT * FROM approval_task WHERE status = ? AND timeout_at IS NOT NULL"
            " AND timeout_at <= ? ORDER BY timeout_at",
            (TASK_PENDING, now or utc_now()),
        )
        return [self._decode(r) for r in rows]

    def record_decision(
        self, task_id: str, *, approver: str, decision: str, reason: Optional[str] = None
    ) -> None:
        """追加一条审批意见（decisions 为 JSON 数组，幂等重入安全）"""
        task = self.get_task(task_id)
        if task is None:
            return
        decisions = task.get("decisions") or []
        decisions.append({
            "approver": approver, "decision": decision,
            "reason": reason, "at": utc_now(),
        })
        self.db.execute(
            "UPDATE approval_task SET decisions = ?, decided_at = ? WHERE id = ?",
            (_dumps(decisions), utc_now(), task_id),
        )

    def finish_task(self, task_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE approval_task SET status = ?, decided_at = ? WHERE id = ?",
            (status, utc_now(), task_id),
        )

    def escalate(self, task_id: str, escalated_to: List[str]) -> None:
        """升级链前移（§8.2.2 中/高风险超时升级）：escalated_to 为已升级层级链"""
        self.db.execute(
            "UPDATE approval_task SET escalated_to = ? WHERE id = ?",
            (_dumps(escalated_to), task_id),
        )

    def extend_timeout(self, task_id: str, timeout_at: str) -> None:
        """升级后顺延本轮超时窗口"""
        self.db.execute(
            "UPDATE approval_task SET timeout_at = ? WHERE id = ?", (timeout_at, task_id)
        )

    @staticmethod
    def _decode(row: Dict[str, Any]) -> Dict[str, Any]:
        row["approvers"] = _loads(row.get("approvers")) or []
        row["decisions"] = _loads(row.get("decisions")) or []
        row["escalated_to"] = _loads(row.get("escalated_to"))
        return row

