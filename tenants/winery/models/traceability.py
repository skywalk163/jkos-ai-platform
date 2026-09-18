"""酒厂中台 - 质量追溯数据模型（设计文档 §2.6）

M4 任务 4.2：批次追溯链（trace_node 链 + LIMS 适配）

数据模型：
- batch：批次主表（批次号、产品、生产线、状态）
- trace_node：追溯节点（批次、节点类型、时间戳、数据、操作员）
- quality_inspection：质检记录（批次、指标、结果、检验员）
- raw_material：原料溯源（批次、原料、供应商、产地）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from jkos_core.db import Database, DatabaseConfig
from jkos_core.db.ulid import new_ulid as generate_ulid

logger = logging.getLogger("dsh.tenants.winery.models")


# ─── 枚举类型 ───

class BatchStatus(str, Enum):
    """批次状态"""
    CREATED = "created"
    PRODUCING = "producing"
    INSPECTING = "inspecting"
    PASSED = "passed"
    REJECTED = "rejected"
    SHIPPED = "shipped"


class TraceNodeType(str, Enum):
    """追溯节点类型"""
    RAW_MATERIAL = "raw_material"       # 原料入库
    FERMENTATION = "fermentation"       # 发酵
    DISTILLATION = "distillation"       # 蒸馏
    AGING = "aging"                     # 陈酿
    BLENDING = "blending"               # 勾兑
    FILLING = "filling"                 # 灌装
    INSPECTION = "inspection"           # 质检
    SHIPPING = "shipping"               # 出库


class InspectionResult(str, Enum):
    """质检结果"""
    PASS = "pass"
    FAIL = "fail"
    CONDITIONAL = "conditional"         # 有条件通过


# ─── 数据类 ───

@dataclass
class Batch:
    """批次主表"""
    id: str
    batch_no: str
    product_code: str
    product_name: str
    line_code: str
    status: BatchStatus
    planned_quantity: float
    actual_quantity: float
    planned_start: datetime
    planned_end: datetime
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class TraceNode:
    """追溯节点"""
    id: str
    batch_id: str
    node_type: TraceNodeType
    seq: int
    timestamp: datetime
    data: Dict[str, Any]                # 节点数据（温度、湿度、时间等）
    operator_id: str
    notes: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class QualityInspection:
    """质检记录"""
    id: str
    batch_id: str
    inspector_id: str
    inspection_time: datetime
    alcohol_content: float              # 酒精度 (%)
    ph_value: float                     # pH 值
    turbidity: float                    # 浊度 (NTU)
    sensory_score: float                # 感官评分 (0-100)
    result: InspectionResult
    notes: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class RawMaterial:
    """原料溯源"""
    id: str
    batch_id: str
    material_type: str                  # grain, yeast, oak_barrel, etc.
    supplier_id: str
    supplier_name: str
    origin: str                         # 产地
    quantity: float
    unit: str
    delivery_date: datetime
    quality_cert: Optional[str] = None  # 质检证书编号
    created_at: datetime = field(default_factory=datetime.now)


# ─── 追溯仓储 ───

class TraceabilityRepo:
    """批次追溯仓储（SQLite 实现）

    设计文档 §2.6 数据模型落地：
    - batch：批次主表
    - trace_node：追溯节点链
    - quality_inspection：质检记录
    - raw_material：原料溯源
    """

    def __init__(self, db: Database):
        self.db = db
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """确保追溯表存在（幂等）"""
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_batch (
                id              TEXT PRIMARY KEY,
                batch_no        TEXT NOT NULL UNIQUE,
                product_code    TEXT NOT NULL,
                product_name    TEXT NOT NULL,
                line_code       TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'created',
                planned_quantity REAL NOT NULL,
                actual_quantity REAL DEFAULT 0,
                planned_start   TEXT NOT NULL,
                planned_end     TEXT NOT NULL,
                actual_start    TEXT,
                actual_end      TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_node (
                id              TEXT PRIMARY KEY,
                batch_id        TEXT NOT NULL REFERENCES trace_batch(id),
                node_type       TEXT NOT NULL,
                seq             INTEGER NOT NULL,
                timestamp       TEXT NOT NULL,
                data_json       TEXT NOT NULL DEFAULT '{}',
                operator_id     TEXT NOT NULL,
                notes           TEXT,
                created_at      TEXT NOT NULL
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS quality_inspection (
                id                  TEXT PRIMARY KEY,
                batch_id            TEXT NOT NULL REFERENCES trace_batch(id),
                inspector_id        TEXT NOT NULL,
                inspection_time     TEXT NOT NULL,
                alcohol_content     REAL NOT NULL,
                ph_value            REAL NOT NULL,
                turbidity           REAL NOT NULL,
                sensory_score       REAL NOT NULL,
                result              TEXT NOT NULL,
                notes               TEXT,
                created_at          TEXT NOT NULL
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_material (
                id              TEXT PRIMARY KEY,
                batch_id        TEXT NOT NULL REFERENCES trace_batch(id),
                material_type   TEXT NOT NULL,
                supplier_id     TEXT NOT NULL,
                supplier_name   TEXT NOT NULL,
                origin          TEXT NOT NULL,
                quantity        REAL NOT NULL,
                unit            TEXT NOT NULL,
                delivery_date   TEXT NOT NULL,
                quality_cert    TEXT,
                created_at      TEXT NOT NULL
            )
            """
        )

    def create_batch(self, batch: Batch) -> Batch:
        """创建批次"""
        self.db.execute(
            """
            INSERT INTO trace_batch
            (id, batch_no, product_code, product_name, line_code, status,
             planned_quantity, actual_quantity, planned_start, planned_end,
             actual_start, actual_end, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch.id, batch.batch_no, batch.product_code, batch.product_name,
                batch.line_code, batch.status.value, batch.planned_quantity,
                batch.actual_quantity, batch.planned_start.isoformat(),
                batch.planned_end.isoformat(),
                batch.actual_start.isoformat() if batch.actual_start else None,
                batch.actual_end.isoformat() if batch.actual_end else None,
                batch.created_at.isoformat(), batch.updated_at.isoformat(),
            ),
        )
        return batch

    def get_batch(self, batch_id: str) -> Optional[Batch]:
        """获取批次"""
        row = self.db.query_one("SELECT * FROM trace_batch WHERE id = ?", (batch_id,))
        if not row:
            return None
        return Batch(
            id=row["id"], batch_no=row["batch_no"], product_code=row["product_code"],
            product_name=row["product_name"], line_code=row["line_code"],
            status=BatchStatus(row["status"]), planned_quantity=row["planned_quantity"],
            actual_quantity=row["actual_quantity"],
            planned_start=datetime.fromisoformat(row["planned_start"]),
            planned_end=datetime.fromisoformat(row["planned_end"]),
            actual_start=datetime.fromisoformat(row["actual_start"]) if row["actual_start"] else None,
            actual_end=datetime.fromisoformat(row["actual_end"]) if row["actual_end"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def add_trace_node(self, node: TraceNode) -> TraceNode:
        """添加追溯节点"""
        self.db.execute(
            """
            INSERT INTO trace_node (id, batch_id, node_type, seq, timestamp,
                                    data_json, operator_id, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node.id, node.batch_id, node.node_type.value, node.seq,
                node.timestamp.isoformat(),
                __import__("json").dumps(node.data, ensure_ascii=False),
                node.operator_id, node.notes, node.created_at.isoformat(),
            ),
        )
        return node

    def get_trace_chain(self, batch_id: str) -> List[TraceNode]:
        """获取批次追溯链（按 seq 排序）"""
        rows = self.db.query(
            "SELECT * FROM trace_node WHERE batch_id = ? ORDER BY seq", (batch_id,)
        )
        import json as json_lib
        return [
            TraceNode(
                id=r["id"], batch_id=r["batch_id"],
                node_type=TraceNodeType(r["node_type"]), seq=r["seq"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                data=json_lib.loads(r["data_json"]),
                operator_id=r["operator_id"], notes=r["notes"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def add_inspection(self, inspection: QualityInspection) -> QualityInspection:
        """添加质检记录"""
        self.db.execute(
            """
            INSERT INTO quality_inspection
            (id, batch_id, inspector_id, inspection_time, alcohol_content,
             ph_value, turbidity, sensory_score, result, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inspection.id, inspection.batch_id, inspection.inspector_id,
                inspection.inspection_time.isoformat(), inspection.alcohol_content,
                inspection.ph_value, inspection.turbidity, inspection.sensory_score,
                inspection.result.value, inspection.notes, inspection.created_at.isoformat(),
            ),
        )
        return inspection

    def add_raw_material(self, material: RawMaterial) -> RawMaterial:
        """添加原料溯源"""
        self.db.execute(
            """
            INSERT INTO raw_material
            (id, batch_id, material_type, supplier_id, supplier_name, origin,
             quantity, unit, delivery_date, quality_cert, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                material.id, material.batch_id, material.material_type,
                material.supplier_id, material.supplier_name, material.origin,
                material.quantity, material.unit, material.delivery_date.isoformat(),
                material.quality_cert, material.created_at.isoformat(),
            ),
        )
        return material

    def trace_batch(self, batch_no: str) -> Optional[Dict[str, Any]]:
        """批次全链路追溯（批次 + 节点 + 质检 + 原料）"""
        row = self.db.query_one(
            "SELECT * FROM trace_batch WHERE batch_no = ?", (batch_no,)
        )
        if not row:
            return None

        batch = Batch(
            id=row["id"], batch_no=row["batch_no"], product_code=row["product_code"],
            product_name=row["product_name"], line_code=row["line_code"],
            status=BatchStatus(row["status"]), planned_quantity=row["planned_quantity"],
            actual_quantity=row["actual_quantity"],
            planned_start=datetime.fromisoformat(row["planned_start"]),
            planned_end=datetime.fromisoformat(row["planned_end"]),
            actual_start=datetime.fromisoformat(row["actual_start"]) if row["actual_start"] else None,
            actual_end=datetime.fromisoformat(row["actual_end"]) if row["actual_end"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

        nodes = self.get_trace_chain(batch.id)

        import json as json_lib
        inspections = [
            QualityInspection(
                id=r["id"], batch_id=r["batch_id"], inspector_id=r["inspector_id"],
                inspection_time=datetime.fromisoformat(r["inspection_time"]),
                alcohol_content=r["alcohol_content"], ph_value=r["ph_value"],
                turbidity=r["turbidity"], sensory_score=r["sensory_score"],
                result=InspectionResult(r["result"]), notes=r["notes"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in self.db.query(
                "SELECT * FROM quality_inspection WHERE batch_id = ?", (batch.id,)
            )
        ]

        materials = [
            RawMaterial(
                id=r["id"], batch_id=r["batch_id"], material_type=r["material_type"],
                supplier_id=r["supplier_id"], supplier_name=r["supplier_name"],
                origin=r["origin"], quantity=r["quantity"], unit=r["unit"],
                delivery_date=datetime.fromisoformat(r["delivery_date"]),
                quality_cert=r["quality_cert"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in self.db.query(
                "SELECT * FROM raw_material WHERE batch_id = ?", (batch.id,)
            )
        ]

        return {
            "batch": batch,
            "trace_chain": nodes,
            "inspections": inspections,
            "raw_materials": materials,
        }
