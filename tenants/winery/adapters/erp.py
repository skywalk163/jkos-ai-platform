"""酒厂中台 - ERP/MES 适配器 POC（M4 任务 4.3）

设计文档 §8.7 风险应对：客户现场 ERP/MES 接口文档缺失时，
适配器先做文件导出/Excel 导入降级模式。

适配器模式：
- 真实模式：对接 ERP/MES API（需现场配置）
- 降级模式：Excel/CSV 文件导入（POC 阶段默认）
- 模拟模式：生成模拟数据（测试/演示）
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from dsh_core.db import Database

logger = logging.getLogger("dsh.tenants.winery.adapters")


# ─── 枚举类型 ───

class AdapterMode(str, Enum):
    """适配器模式"""
    REAL = "real"           # 真实 API 对接
    FALLBACK = "fallback"   # 文件导入降级
    SIMULATED = "simulated" # 模拟数据


class ErpSystem(str, Enum):
    """ERP 系统类型"""
    SAP = "sap"
    ORACLE = "oracle"
    KINGDEE = "kingdee"
    YONYOU = "yonyou"
    CUSTOM = "custom"


class MesSystem(str, Enum):
    """MES 系统类型"""
    SIEMENS = "siemens"
    ROCKWELL = "rockwell"
    FANUC = "fanuc"
    CUSTOM = "custom"


# ─── 数据类 ───

@dataclass
class SalesData:
    """销售数据"""
    store_id: str
    store_name: str
    product_code: str
    product_name: str
    quantity: float
    amount: float
    sales_date: datetime


@dataclass
class ProductionOrder:
    """生产工单"""
    order_no: str
    product_code: str
    product_name: str
    line_code: str
    planned_quantity: float
    actual_quantity: float
    planned_start: datetime
    planned_end: datetime
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    status: str = "pending"


@dataclass
class InventoryData:
    """库存数据"""
    material_code: str
    material_name: str
    quantity: float
    unit: str
    warehouse: str
    last_update: datetime


# ─── ERP 适配器 ───

class ErpAdapter:
    """ERP 适配器（POC 阶段使用降级模式）

    设计文档 §8.7：合同前 POC，适配器先做文件导出/Excel 导入降级模式。
    """

    def __init__(self, mode: AdapterMode = AdapterMode.FALLBACK,
                 erp_type: ErpSystem = ErpSystem.CUSTOM,
                 config: Optional[Dict[str, Any]] = None):
        self.mode = mode
        self.erp_type = erp_type
        self.config = config or {}
        self._data_dir = Path(self.config.get("data_dir", "./data/erp"))
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def get_sales_data(self, start_date: datetime, end_date: datetime,
                       store_ids: Optional[List[str]] = None) -> List[SalesData]:
        """获取销售数据"""
        if self.mode == AdapterMode.REAL:
            return self._get_sales_data_real(start_date, end_date, store_ids)
        elif self.mode == AdapterMode.FALLBACK:
            return self._get_sales_data_fallback(start_date, end_date, store_ids)
        else:
            return self._get_sales_data_simulated(start_date, end_date, store_ids)

    def _get_sales_data_real(self, start_date: datetime, end_date: datetime,
                             store_ids: Optional[List[str]] = None) -> List[SalesData]:
        """真实 API 对接（需现场配置）"""
        logger.warning("ERP 真实 API 对接未实现，使用降级模式")
        return self._get_sales_data_fallback(start_date, end_date, store_ids)

    def _get_sales_data_fallback(self, start_date: datetime, end_date: datetime,
                                 store_ids: Optional[List[str]] = None) -> List[SalesData]:
        """降级模式：从 Excel/CSV 文件导入"""
        sales_file = self._data_dir / "sales.csv"
        if not sales_file.exists():
            logger.info("销售数据文件不存在，生成模拟数据")
            return self._get_sales_data_simulated(start_date, end_date, store_ids)

        sales = []
        with open(sales_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sales_date = datetime.fromisoformat(row["sales_date"])
                if start_date <= sales_date <= end_date:
                    if store_ids and row["store_id"] not in store_ids:
                        continue
                    sales.append(SalesData(
                        store_id=row["store_id"],
                        store_name=row["store_name"],
                        product_code=row["product_code"],
                        product_name=row["product_name"],
                        quantity=float(row["quantity"]),
                        amount=float(row["amount"]),
                        sales_date=sales_date,
                    ))
        return sales

    def _get_sales_data_simulated(self, start_date: datetime, end_date: datetime,
                                  store_ids: Optional[List[str]] = None) -> List[SalesData]:
        """模拟数据（测试/演示）"""
        stores = [
            ("S001", "旗舰店"),
            ("S002", "万达店"),
            ("S003", "万象城店"),
        ]
        products = [
            ("P001", "52度经典"),
            ("P002", "42度清爽"),
            ("P003", "60度原浆"),
        ]
        sales = []
        current = start_date
        while current <= end_date:
            for store_id, store_name in stores:
                if store_ids and store_id not in store_ids:
                    continue
                for product_code, product_name in products:
                    import random
                    quantity = random.randint(5, 50)
                    amount = quantity * random.uniform(100, 500)
                    sales.append(SalesData(
                        store_id=store_id, store_name=store_name,
                        product_code=product_code, product_name=product_name,
                        quantity=quantity, amount=round(amount, 2),
                        sales_date=current,
                    ))
            current = datetime(current.year, current.month, current.day + 1)
        return sales

    def get_inventory(self, material_codes: Optional[List[str]] = None) -> List[InventoryData]:
        """获取库存数据"""
        if self.mode == AdapterMode.REAL:
            return self._get_inventory_real(material_codes)
        elif self.mode == AdapterMode.FALLBACK:
            return self._get_inventory_fallback(material_codes)
        else:
            return self._get_inventory_simulated(material_codes)

    def _get_inventory_fallback(self, material_codes: Optional[List[str]] = None) -> List[InventoryData]:
        """降级模式：从文件导入库存数据"""
        inventory_file = self._data_dir / "inventory.csv"
        if not inventory_file.exists():
            return self._get_inventory_simulated(material_codes)

        inventory = []
        with open(inventory_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if material_codes and row["material_code"] not in material_codes:
                    continue
                inventory.append(InventoryData(
                    material_code=row["material_code"],
                    material_name=row["material_name"],
                    quantity=float(row["quantity"]),
                    unit=row["unit"],
                    warehouse=row["warehouse"],
                    last_update=datetime.fromisoformat(row["last_update"]),
                ))
        return inventory

    def _get_inventory_simulated(self, material_codes: Optional[List[str]] = None) -> List[InventoryData]:
        """模拟库存数据"""
        materials = [
            ("M001", "高粱", "吨", "原料仓"),
            ("M002", "小麦", "吨", "原料仓"),
            ("M003", "橡木桶", "个", "酒窖"),
            ("M004", "酵母", "公斤", "辅料仓"),
        ]
        import random
        return [
            InventoryData(
                material_code=code, material_name=name, unit=unit, warehouse=warehouse,
                quantity=random.uniform(100, 1000),
                last_update=datetime.now(),
            )
            for code, name, unit, warehouse in materials
            if not material_codes or code in material_codes
        ]

    def _get_sales_data_real(self, start_date: datetime, end_date: datetime,
                             store_ids: Optional[List[str]] = None) -> List[SalesData]:
        raise NotImplementedError("ERP 真实 API 对接需现场配置")

    def _get_inventory_real(self, material_codes: Optional[List[str]] = None) -> List[InventoryData]:
        raise NotImplementedError("ERP 真实 API 对接需现场配置")


# ─── MES 适配器 ───

class MesAdapter:
    """MES 适配器（POC 阶段使用降级模式）

    设计文档 §8.7：合同前 POC，适配器先做文件导出/Excel 导入降级模式。
    """

    def __init__(self, mode: AdapterMode = AdapterMode.FALLBACK,
                 mes_type: MesSystem = MesSystem.CUSTOM,
                 config: Optional[Dict[str, Any]] = None):
        self.mode = mode
        self.mes_type = mes_type
        self.config = config or {}
        self._data_dir = Path(self.config.get("data_dir", "./data/mes"))
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def get_production_orders(self, start_date: datetime, end_date: datetime) -> List[ProductionOrder]:
        """获取生产工单"""
        if self.mode == AdapterMode.REAL:
            return self._get_production_orders_real(start_date, end_date)
        elif self.mode == AdapterMode.FALLBACK:
            return self._get_production_orders_fallback(start_date, end_date)
        else:
            return self._get_production_orders_simulated(start_date, end_date)

    def _get_production_orders_fallback(self, start_date: datetime, end_date: datetime) -> List[ProductionOrder]:
        """降级模式：从文件导入生产工单"""
        orders_file = self._data_dir / "production_orders.csv"
        if not orders_file.exists():
            return self._get_production_orders_simulated(start_date, end_date)

        orders = []
        with open(orders_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                planned_start = datetime.fromisoformat(row["planned_start"])
                if start_date <= planned_start <= end_date:
                    orders.append(ProductionOrder(
                        order_no=row["order_no"],
                        product_code=row["product_code"],
                        product_name=row["product_name"],
                        line_code=row["line_code"],
                        planned_quantity=float(row["planned_quantity"]),
                        actual_quantity=float(row["actual_quantity"]),
                        planned_start=planned_start,
                        planned_end=datetime.fromisoformat(row["planned_end"]),
                        status=row["status"],
                    ))
        return orders

    def _get_production_orders_simulated(self, start_date: datetime, end_date: datetime) -> List[ProductionOrder]:
        """模拟生产工单"""
        import random
        products = [
            ("PO-2026-001", "P001", "52度经典", "L01", 500),
            ("PO-2026-002", "P002", "42度清爽", "L02", 800),
            ("PO-2026-003", "P003", "60度原浆", "L01", 200),
        ]
        orders = []
        current = start_date
        for order_no, product_code, product_name, line_code, planned_qty in products:
            planned_start = current
            planned_end = datetime(current.year, current.month, current.day + 3)
            orders.append(ProductionOrder(
                order_no=order_no, product_code=product_code, product_name=product_name,
                line_code=line_code, planned_quantity=planned_qty,
                actual_quantity=int(planned_qty * random.uniform(0.9, 1.0)),
                planned_start=planned_start, planned_end=planned_end,
                status="completed",
            ))
        return orders

    def _get_production_orders_real(self, start_date: datetime, end_date: datetime) -> List[ProductionOrder]:
        raise NotImplementedError("MES 真实 API 对接需现场配置")

    def push_production_order(self, order: ProductionOrder) -> bool:
        """推送生产工单到 MES"""
        if self.mode == AdapterMode.REAL:
            return self._push_production_order_real(order)
        elif self.mode == AdapterMode.FALLBACK:
            return self._push_production_order_fallback(order)
        else:
            return self._push_production_order_simulated(order)

    def _push_production_order_fallback(self, order: ProductionOrder) -> bool:
        """降级模式：保存到文件"""
        orders_file = self._data_dir / "production_orders.csv"
        file_exists = orders_file.exists()
        with open(orders_file, "a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["order_no", "product_code", "product_name", "line_code",
                                 "planned_quantity", "actual_quantity", "planned_start",
                                 "planned_end", "status"])
            writer.writerow([
                order.order_no, order.product_code, order.product_name, order.line_code,
                order.planned_quantity, order.actual_quantity,
                order.planned_start.isoformat(), order.planned_end.isoformat(),
                order.status,
            ])
        return True

    def _push_production_order_simulated(self, order: ProductionOrder) -> bool:
        logger.info("模拟推送生产工单: %s", order.order_no)
        return True

    def _push_production_order_real(self, order: ProductionOrder) -> bool:
        raise NotImplementedError("MES 真实 API 对接需现场配置")


# ─── 适配器工厂 ───

class AdapterFactory:
    """适配器工厂"""

    @staticmethod
    def create_erp_adapter() -> ErpAdapter:
        """创建 ERP 适配器（根据环境变量选择模式）"""
        import os
        mode_str = os.getenv("DSH_ERP_MODE", "fallback").lower()
        mode = AdapterMode(mode_str) if mode_str in [m.value for m in AdapterMode] else AdapterMode.FALLBACK
        erp_type = ErpSystem(os.getenv("DSH_ERP_TYPE", "custom"))
        return ErpAdapter(mode=mode, erp_type=erp_type)

    @staticmethod
    def create_mes_adapter() -> MesAdapter:
        """创建 MES 适配器（根据环境变量选择模式）"""
        import os
        mode_str = os.getenv("DSH_MES_MODE", "fallback").lower()
        mode = AdapterMode(mode_str) if mode_str in [m.value for m in AdapterMode] else AdapterMode.FALLBACK
        mes_type = MesSystem(os.getenv("DSH_MES_TYPE", "custom"))
        return MesAdapter(mode=mode, mes_type=mes_type)
