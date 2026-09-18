"""DSH 数据层 - 表结构与迁移定义（设计文档 §2.6 的 M0 子集）

M0 表清单（开发计划任务 0.1/0.4）：
  tenants           租户表（三案例 = 三租户：dev / media / winery）
  workflow_instance 工作流实例（0.2 引擎状态机使用）
  workflow_step     工作流步骤（seq 唯一，支持崩溃续跑）
  audit_event       审计事件（0.4，append-only 由触发器强制，§8.2.4）
  llm_usage         LLM Token 计量（0.3，§8.5.4 成本报表数据源）

约定：主键统一 ULID；时间统一 ISO 8601 UTC 文本；JSON 字段以 _json 后缀命名。
"""

# 每项: (版本号, 描述, [SQL 语句列表])
MIGRATION_V1 = (
    1,
    "M0 基础表: tenants/workflow_instance/workflow_step/audit_event/llm_usage + 三租户种子",
    [
        # ── 租户 ──
        """
        CREATE TABLE tenants (
            id              TEXT PRIMARY KEY,
            code            TEXT NOT NULL UNIQUE,
            name            TEXT NOT NULL,
            isolation_level TEXT NOT NULL DEFAULT 'L3',
            config_json     TEXT NOT NULL DEFAULT '{}',
            status          TEXT NOT NULL DEFAULT 'active',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """,
        # ── 工作流实例 ──
        # status: PENDING/RUNNING/WAITING_APPROVAL/SUSPENDED/COMPLETED/FAILED/CANCELLED
        """
        CREATE TABLE workflow_instance (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL REFERENCES tenants(id),
            workflow_code TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'PENDING',
            current_step  TEXT,
            context_json  TEXT NOT NULL DEFAULT '{}',
            result_json   TEXT,
            error_json    TEXT,
            created_by    TEXT NOT NULL DEFAULT 'system',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            finished_at   TEXT
        )
        """,
        "CREATE INDEX idx_wf_instance_tenant_status ON workflow_instance(tenant_id, status)",
        "CREATE INDEX idx_wf_instance_code ON workflow_instance(workflow_code)",
        # ── 工作流步骤 ──
        # status: PENDING/RUNNING/WAITING/SUCCEEDED/FAILED/SKIPPED/COMPENSATED
        """
        CREATE TABLE workflow_step (
            id          TEXT PRIMARY KEY,
            instance_id TEXT NOT NULL REFERENCES workflow_instance(id),
            seq         INTEGER NOT NULL,
            node_code   TEXT NOT NULL,
            node_type   TEXT NOT NULL DEFAULT 'tool',
            status      TEXT NOT NULL DEFAULT 'PENDING',
            input_json  TEXT,
            output_json TEXT,
            error_json  TEXT,
            attempts    INTEGER NOT NULL DEFAULT 0,
            started_at  TEXT,
            finished_at TEXT,
            created_at  TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_wf_step_instance ON workflow_step(instance_id, seq)",
        "CREATE UNIQUE INDEX idx_wf_step_uniq ON workflow_step(instance_id, seq)",
        # ── 审计事件（append-only，§8.2.4）──
        # actor_type: HUMAN/AGENT/SYSTEM；before/after 记录变更前后快照
        """
        CREATE TABLE audit_event (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL,
            actor_type    TEXT NOT NULL,
            actor_id      TEXT NOT NULL,
            action        TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id   TEXT,
            before_json   TEXT,
            after_json    TEXT,
            reason        TEXT,
            trace_id      TEXT,
            occurred_at   TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_audit_tenant_time ON audit_event(tenant_id, occurred_at)",
        "CREATE INDEX idx_audit_resource ON audit_event(resource_type, resource_id)",
        # append-only 强制：拒绝 UPDATE / DELETE（§8.2.4 审计记录不可篡改）
        """
        CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_event
        BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END
        """,
        """
        CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_event
        BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END
        """,
        # ── LLM Token 计量（§8.5.4）──
        # status: ok / error / simulated
        """
        CREATE TABLE llm_usage (
            id                TEXT PRIMARY KEY,
            tenant_id         TEXT NOT NULL DEFAULT 'system',
            provider          TEXT NOT NULL,
            model             TEXT NOT NULL,
            purpose           TEXT,
            ref_type          TEXT,
            ref_id            TEXT,
            prompt_tokens     INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens      INTEGER NOT NULL DEFAULT 0,
            duration_ms       INTEGER NOT NULL DEFAULT 0,
            status            TEXT NOT NULL DEFAULT 'ok',
            error_message     TEXT,
            created_at        TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_llm_usage_tenant_time ON llm_usage(tenant_id, created_at)",
        # ── 三租户种子（幂等，§8.4；M0 仅 dev 实际使用）──
        """
        INSERT INTO tenants (id, code, name, isolation_level, status, created_at, updated_at)
        SELECT '01ARZ3NDEKTSV4RRFFQ69G5FAV', 'dev', '编程项目AI中台（案例A·自举）', 'L3', 'active',
               strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now')
        WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE code = 'dev')
        """,
        """
        INSERT INTO tenants (id, code, name, isolation_level, status, created_at, updated_at)
        SELECT '01ARZ3NDEKTSV4RRFFQ69G5FAW', 'media', '自媒体运营AI中台（案例B）', 'L3', 'active',
               strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now')
        WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE code = 'media')
        """,
        """
        INSERT INTO tenants (id, code, name, isolation_level, status, created_at, updated_at)
        SELECT '01ARZ3NDEKTSV4RRFFQ69G5FAX', 'winery', '酒厂AI中台（案例C）', 'L3', 'active',
               strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now')
        WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE code = 'winery')
        """,
    ],
)

# ─── M1 任务 1.4：审批任务表（§8.2 人工干预四要素：粒度/超时/协作/留痕）───
# risk: low/medium/high（§8.2.2 超时策略：低24h自动通过/中8h挂起+升级/高2h自动驳回）
# mode: serial 串行 / all 并行会签 / any 或签（§8.2.3）
# status: PENDING/APPROVED/REJECTED/CANCELLED
MIGRATION_V2 = (
    2,
    "M1 审批任务表: approval_task（任务级审批 + 风险等级超时 + 多人协作模式）",
    [
        """
        CREATE TABLE approval_task (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL REFERENCES tenants(id),
            instance_id   TEXT NOT NULL REFERENCES workflow_instance(id),
            step_id       TEXT NOT NULL REFERENCES workflow_step(id),
            node_code     TEXT NOT NULL,
            risk          TEXT NOT NULL DEFAULT 'medium',
            mode          TEXT NOT NULL DEFAULT 'any',
            approvers     TEXT NOT NULL DEFAULT '[]',
            status        TEXT NOT NULL DEFAULT 'PENDING',
            decisions     TEXT NOT NULL DEFAULT '[]',
            timeout_at    TEXT,
            escalated_to  TEXT,
            created_by    TEXT NOT NULL DEFAULT 'system',
            created_at    TEXT NOT NULL,
            decided_at    TEXT
        )
        """,
        "CREATE INDEX idx_approval_task_status ON approval_task(status, timeout_at)",
        "CREATE INDEX idx_approval_task_instance ON approval_task(instance_id)",
    ],
)

MIGRATIONS = [MIGRATION_V1, MIGRATION_V2]
