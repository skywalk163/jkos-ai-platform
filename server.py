#!/usr/bin/env python3
"""极快AI操作系统 - 便捷入口（兼容生产部署脚本）

与 docs/部署指南.md 的启动流保持一致，内部委托 jkos_core 标准装配逻辑。

用法:
    python server.py                  # 只启动 API 服务（默认 0.0.0.0:8000）
    python server.py --init           # 初始化数据库
    python server.py --mcp            # 只启动 MCP Server（默认 3000）
    python server.py --mcp --port 3000
    python server.py --worker         # 启动后台 Worker（MCP 服务角色）

等价于主入口 jkos_core.cli 的子命令（api / init / mcp / all）。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# ─── 路径设置 ───
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─── 日志配置 ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("jkos.server")


def _build_engine():
    """组装工作流引擎（与 jkos_core.cli._build_engine 保持一致）。

    dev 租户工作流由 register_tenant_workflows 显式注册；media/winery
    由 jkos_core/workflow/nodes.py 模块级惰性挂载，无需在此处手动注册。
    """
    from jkos_core.bootstrap import build_components, register_tenant_workflows
    from jkos_core.workflow import WorkflowEngine

    comps = build_components()
    engine = WorkflowEngine(comps)
    register_tenant_workflows(engine)
    return engine


def cmd_init() -> int:
    """初始化数据库（与 cli cmd_init 语义一致，幂等）。"""
    from jkos_core.storage import StorageConfig, init_database

    config = StorageConfig.from_env()
    asyncio.run(init_database(config))
    print("数据库初始化完成")
    return 0


def cmd_api(host: str, port: int) -> int:
    """只启动 API 服务（同步入口，uvicorn.run 自管事件循环）。"""
    import uvicorn
    from jkos_core.api.routes import create_app

    logger.info("启动 API 服务: %s:%d", host, port)
    app = create_app(engine=_build_engine())
    uvicorn.run(app, host=host, port=port)
    return 0


def cmd_mcp(host: str, port: int) -> int:
    """只启动 MCP Server（同步入口，同 cli cmd_mcp）。"""
    import uvicorn
    from jkos_core.mcp.server import create_app

    logger.info("启动 MCP Server: %s:%d", host, port)
    app = create_app(engine=_build_engine())
    uvicorn.run(app, host=host, port=port)
    return 0


def cmd_worker(host: str, port: int) -> int:
    """启动后台 Worker（MCP 后台服务角色，对应 docker-compose --worker）。"""
    import uvicorn
    from jkos_core.mcp.server import create_app

    logger.info("启动 Worker（MCP Server）: %s:%d", host, port)
    app = create_app(engine=_build_engine())
    uvicorn.run(app, host=host, port=port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="极快AI操作系统便捷入口")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="端口（API 默认 8000；MCP/Worker 默认 3000）",
    )
    parser.add_argument("--mcp", action="store_true", help="只启动 MCP Server")
    parser.add_argument(
        "--worker", action="store_true", help="启动后台 Worker（MCP 服务角色）"
    )
    parser.add_argument("--init", action="store_true", help="初始化数据库")
    args = parser.parse_args(argv)

    if args.init:
        return cmd_init()
    if args.mcp:
        return cmd_mcp(args.host, args.port or 3000)
    if args.worker:
        return cmd_worker(args.host, args.port or 3000)
    return cmd_api(args.host, args.port or 8000)


if __name__ == "__main__":
    sys.exit(main())
