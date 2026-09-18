#!/usr/bin/env python3
"""极快AI操作系统 - 生产环境启动脚本

用法:
    python server.py              # 启动 API 服务 (默认 0.0.0.0:8000)
    python server.py --port 8080  # 指定端口
    python server.py --mcp        # 只启动 MCP Server
    python server.py --worker     # 启动后台工作进程
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

# ─── 路径设置 ───
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─── 日志配置 ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / "logs" / "dsh.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("dsh.server")


# ─── 生命周期 ───

@asynccontextmanager
async def lifespan(app):
    """应用生命周期：启动时初始化，关闭时清理"""
    # ─── 启动 ───
    logger.info("极快AI操作系统启动中...")
    
    # 加载环境变量
    from dotenv import load_dotenv
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        logger.info("已加载环境变量: %s", env_file)
    else:
        logger.warning("未找到 .env 文件，使用系统环境变量")
    
    # 初始化组件
    from jkos_core.bootstrap import build_components
    from jkos_core.db import DatabaseConfig
    
    db_path = os.getenv("DSH_DB_PATH", str(PROJECT_ROOT / "data" / "dsh.db"))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    comps = build_components(db_path=db_path)
    logger.info("组件初始化完成")
    
    # 注册租户工作流
    from tenants.dev.workflows import register as register_dev
    from tenants.media.workflows import MEDIA_WORKFLOWS
    from tenants.winery.workflows.production import WINERY_WORKFLOWS, register as register_winery
    
    register_dev(comps)
    register_winery(comps)
    
    # 注册媒体工作流
    from jkos_core.workflow.nodes import WORKFLOW_REGISTRY
    for code, wf in MEDIA_WORKFLOWS.items():
        WORKFLOW_REGISTRY[code] = wf
    
    logger.info("租户工作流注册完成")
    
    # 启动后台任务
    from jkos_core.workflow.engine import WorkflowEngine
    engine = WorkflowEngine(comps)
    
    # 启动超时扫描
    asyncio.create_task(sweep_timeouts(engine))
    
    # 保存组件引用
    app.state.comps = comps
    app.state.engine = engine
    
    logger.info("极快AI操作系统启动完成")
    
    yield
    
    # ─── 关闭 ───
    logger.info("极快AI操作系统关闭中...")
    comps.close()
    logger.info("极快AI操作系统已关闭")


async def sweep_timeouts(engine):
    """后台任务：定期扫描超时审批"""
    import asyncio
    while True:
        try:
            await engine.sweep_timeouts()
        except Exception as e:
            logger.error("超时扫描失败: %s", e)
        await asyncio.sleep(60)  # 每分钟扫描一次


# ─── FastAPI 应用 ───

def create_app():
    """创建 FastAPI 应用"""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    
    app = FastAPI(
        title="极快AI操作系统",
        description="企业级多模态 AI Agent 中台",
        version="1.0.0",
        lifespan=lifespan,
    )
    
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # 路由
    from jkos_core.api.routes import router
    app.include_router(router, prefix="/api/v1")
    
    # M5: 插件市场路由
    from jkos_core.api.marketplace_routes import create_plugin_router, create_i18n_router
    app.include_router(create_plugin_router(), prefix="/api/v1")
    app.include_router(create_i18n_router(), prefix="/api/v1")
    
    # 健康检查
    @app.get("/health")
    async def health():
        return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
    
    return app


# ─── MCP Server ───

async def run_mcp_server(port: int = 3000):
    """启动 MCP Server"""
    from jkos_core.mcp.server import run_server
    logger.info("MCP Server 启动中: %d", port)
    await run_server(port=port)


# ─── 后台 Worker ───

async def run_worker():
    """启动后台工作进程"""
    from jkos_core.bootstrap import build_components
    from jkos_core.db import DatabaseConfig
    from jkos_core.workflow.engine import WorkflowEngine
    
    db_path = os.getenv("DSH_DB_PATH", str(PROJECT_ROOT / "data" / "dsh.db"))
    comps = build_components(db_path=db_path)
    engine = WorkflowEngine(comps)
    
    logger.info("后台工作进程启动中...")
    
    # 定期扫描超时
    while True:
        try:
            await engine.sweep_timeouts()
        except Exception as e:
            logger.error("超时扫描失败: %s", e)
        await asyncio.sleep(60)


# ─── 主入口 ───

def main():
    parser = argparse.ArgumentParser(description="极快AI操作系统")
    parser.add_argument("--port", type=int, default=8000, help="API 端口")
    parser.add_argument("--mcp-port", type=int, default=3000, help="MCP 端口")
    parser.add_argument("--mcp", action="store_true", help="只启动 MCP Server")
    parser.add_argument("--worker", action="store_true", help="启动后台工作进程")
    args = parser.parse_args()
    
    # 创建日志目录
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)
    (PROJECT_ROOT / "data").mkdir(exist_ok=True)
    
    if args.mcp:
        # MCP Server
        asyncio.run(run_mcp_server(args.mcp_port))
    elif args.worker:
        # 后台 Worker
        asyncio.run(run_worker())
    else:
        # API Server
        import uvicorn
        app = create_app()
        
        # 信号处理
        def signal_handler(signum, frame):
            logger.info("收到信号 %d，正在关闭...", signum)
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 启动
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=args.port,
            log_level="info",
        )


if __name__ == "__main__":
    main()
