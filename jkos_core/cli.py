#!/usr/bin/env python3
"""
极快AI操作系统 - 主入口

用法:
    jkos-server              # 启动所有服务
    jkos-server mcp          # 只启动 MCP Server
    jkos-server api          # 只启动 API 服务
    jkos-server plugin       # 插件管理
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from jkos_core.mcp.server import MCPServer
from jkos_core.plugins import PluginRegistry, PluginLoader
from jkos_core.storage import StorageManager, StorageConfig, init_database

# ─── 日志配置 ───

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("dsh.cli")


# ─── 命令实现 ───

def _build_engine():
    """组装工作流引擎（M1）：基础组件 + 审批仓储 + 租户工作流定义"""
    from jkos_core.bootstrap import build_components, register_tenant_workflows
    from jkos_core.workflow import WorkflowEngine

    comps = build_components()
    engine = WorkflowEngine(comps)
    register_tenant_workflows(engine)
    return engine


async def cmd_all(args) -> int:
    """启动所有服务"""
    logger.info("=" * 60)
    logger.info("极快AI操作系统 启动中...")
    logger.info(f"版本: 0.1.0")
    logger.info(f"时间: {datetime.now().isoformat()}")
    logger.info("=" * 60)

    # 1. 初始化存储
    config = StorageConfig.from_env()
    storage = StorageManager(config)

    try:
        await storage.initialize()
        logger.info("✓ 存储层初始化完成")
    except Exception as e:
        logger.error(f"✗ 存储层初始化失败: {e}")
        return 1

    # 2. 初始化数据库
    try:
        await init_database(config)
        logger.info("✓ 数据库初始化完成")
    except Exception as e:
        logger.warning(f"数据库初始化警告: {e}")

    # 3. 加载插件
    registry = PluginRegistry()
    loader = PluginLoader(registry)

    plugin_dir = args.plugins_dir or "/var/dsh/plugins"
    count = loader.load_from_directory(plugin_dir)
    logger.info(f"✓ 已加载 {count} 个插件")

    # 4. 启动 MCP Server
    mcp_server = MCPServer(host=args.host, port=args.mcp_port)
    await mcp_server.initialize()
    logger.info(f"✓ MCP Server 启动: {args.host}:{args.mcp_port}")

    # 5. 运行（阻塞）：在事件循环内用 Server.serve()，避免嵌套 asyncio.run
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(
        mcp_server.app, host=mcp_server.host, port=mcp_server.port))
    try:
        await server.serve()
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
    finally:
        await storage.cleanup()
        await mcp_server.cleanup()
        logger.info("极快AI操作系统 已关闭")

    return 0


def cmd_mcp(args) -> int:
    """只启动 MCP Server"""
    import uvicorn
    from jkos_core.mcp.server import create_app

    logger.info(f"启动 MCP Server: {args.host}:{args.port}")

    app = create_app(engine=_build_engine())
    uvicorn.run(app, host=args.host, port=args.port)
    
    return 0


def cmd_api(args) -> int:
    """只启动 API 服务（同步入口，uvicorn.run 自管事件循环）"""
    import uvicorn
    from jkos_core.api.routes import create_app

    logger.info(f"启动 API 服务: {args.host}:{args.port}")
    app = create_app(engine=_build_engine())
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


async def cmd_plugin(args) -> int:
    """插件管理"""
    registry = PluginRegistry()
    loader = PluginLoader(registry)

    if args.action == "list":
        print(f"已注册插件: {registry.count()} 个")
        for pid in registry.list_ids():
            print(f"  - {pid}")

    elif args.action == "load":
        count = loader.load_from_directory(args.directory)
        print(f"已加载 {count} 个插件")

    elif args.action == "unload":
        if loader.unload(args.plugin_id):
            print(f"已卸载插件: {args.plugin_id}")
        else:
            print(f"插件不存在: {args.plugin_id}")

    return 0


async def cmd_init(args) -> int:
    """初始化数据库"""
    config = StorageConfig.from_env()
    await init_database(config)
    print("数据库初始化完成")
    return 0


def cmd_db(args) -> int:
    """M0 数据层管理：migrate / stats"""
    from jkos_core.db import Database, DatabaseConfig

    db = Database(DatabaseConfig.from_env()).connect()
    try:
        if args.db_action == "migrate":
            applied = db.migrate()
            suffix = f"（本次应用: {applied}）" if applied else "（已是最新）"
            print(f"当前版本: v{db.version()} {suffix}")
        elif args.db_action == "stats":
            db.migrate()
            print(f"数据库: {db.config.path}  版本: v{db.version()}")
            for label, sql in [
                ("tenants", "SELECT COUNT(*) c FROM tenants"),
                ("workflow_instance", "SELECT COUNT(*) c FROM workflow_instance"),
                ("workflow_step", "SELECT COUNT(*) c FROM workflow_step"),
                ("audit_event", "SELECT COUNT(*) c FROM audit_event"),
                ("llm_usage", "SELECT COUNT(*) c FROM llm_usage"),
            ]:
                print(f"  {label:<18} {db.query_one(sql)['c']}")
        else:
            print("用法: jkos-server db <migrate|stats>")
            return 1
    finally:
        db.close()
    return 0


def cmd_token(args) -> int:
    """M0 自测：签发测试 JWT"""
    from jkos_core.auth.dependencies import mint_dev_token
    from jkos_core.bootstrap import build_components

    comps = build_components()
    try:
        tenant = comps.tenants.get_by_code(args.tenant)
        if not tenant:
            codes = [t["code"] for t in comps.tenants.list_all()]
            print(f"租户不存在: {args.tenant}（可用: {codes}）")
            return 1
        token = mint_dev_token(comps.jwt, tenant["id"], tenant["code"], args.user, args.days)
        print(f"租户: {tenant['code']}  用户: {args.user}  有效期: {args.days} 天")
        print(token)
    finally:
        comps.close()
    return 0


async def cmd_llm(args) -> int:
    """M0 自检：LLM 路由链测试（真实供应商失败自动降级到模拟）"""
    from jkos_core.bootstrap import build_components

    comps = build_components()
    try:
        result = await comps.llm.chat_text(args.prompt, purpose="llm-ping", ref_type="cli")
        mode = "模拟" if result.simulated else "真实"
        print(f"provider={result.provider} model={result.model} mode={mode}")
        print(f"tokens: prompt={result.prompt_tokens} completion={result.completion_tokens}"
              f" total={result.total_tokens} 耗时={result.duration_ms}ms")
        print(f"输出: {result.content}")
    finally:
        comps.close()
    return 0


def cmd_workflow(args) -> int:
    """M0 0.2：工作流引擎（run / status / resume / list / approve / cancel）"""
    import json as _json

    from jkos_core.bootstrap import build_components
    from jkos_core.workflow import WorkflowEngine, WorkflowError

    def print_status(status, comps=None):
        inst = status["instance"]
        print(f"实例 {inst['id']}  [{inst['workflow_code']}]  触发人: {inst['created_by']}")
        print(f"  状态: {inst['status']}   当前步骤: {inst.get('current_step') or '-'}")
        if inst.get("error"):
            print(f"  错误: {inst['error']}")
        if inst.get("result") is not None:
            print(f"  结果: {_json.dumps(inst['result'], ensure_ascii=False)[:300]}")
        for s in status["steps"]:
            print(f"  #{s['seq']} {s['node_code']:<14} {s['status']:<10} attempts={s['attempts']}")
            if s.get("output"):
                print(f"      输出: {_json.dumps(s['output'], ensure_ascii=False)[:200]}")
        if comps is not None:
            events = comps.audit.query(resource_id=inst["id"], limit=20)
            if events:
                print("  审计轨迹:")
                for e in reversed(events):
                    print(f"    {e['occurred_at']}  {e['action']:<28} {e['actor_type']}/{e['actor_id']}")

    if not getattr(args, "wf_action", None):
        print("用法: jkos-server workflow <run|status|resume|list|approve|cancel>")
        return 1

    comps = build_components()
    try:
        engine = WorkflowEngine(comps)
        action = args.wf_action
        if action == "run":
            context = _json.loads(args.context) if args.context else {}
            status = asyncio.run(engine.start(args.workflow_code, args.tenant, context,
                                              created_by=args.user))
            print_status(status, comps)
            return 0 if status["instance"]["status"] != "FAILED" else 1
        if action == "status":
            print_status(engine.status(args.instance_id), comps)
            return 0
        if action == "resume":
            print_status(asyncio.run(engine.resume(args.instance_id, actor=args.user)), comps)
            return 0
        if action == "list":
            rows = engine.list_instances(tenant_code=args.tenant, status=args.status,
                                         limit=args.limit)
            print(f"共 {len(rows)} 个实例")
            for r in rows:
                print(f"  {r['id']}  {r['workflow_code']:<14} {r['status']:<18}"
                      f" {r['created_at']}  by {r['created_by']}")
            return 0
        if action == "approve":
            decision = not args.reject
            print_status(asyncio.run(engine.approve(args.instance_id, decision, args.by,
                                                    args.reason)), comps)
            return 0
        if action == "cancel":
            print_status(asyncio.run(engine.cancel(args.instance_id, reason=args.reason)), comps)
            return 0
        print(f"未知子命令: {action}")
        return 1
    except WorkflowError as e:
        print(f"工作流错误: {e}")
        return 1
    finally:
        comps.close()


# ─── 主函数 ───

def main() -> int:
    """主入口"""
    parser = argparse.ArgumentParser(
        description="极快AI操作系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  jkos-server                  启动所有服务
  jkos-server mcp --port 3000  只启动 MCP Server
  jkos-server init             初始化数据库
  jkos-server plugin list      列出插件
  jkos-server db stats         查看数据层表记录数
  jkos-server token --tenant dev   签发测试 JWT
  jkos-server llm              LLM 路由链自检
        """,
    )

    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="API 端口")
    parser.add_argument("--mcp-port", type=int, default=3000, help="MCP 端口")
    parser.add_argument("--plugins-dir", default="/var/dsh/plugins", help="插件目录")

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # 默认命令（无子命令时）
    subparsers.add_parser("all", help="启动所有服务")

    # mcp 命令
    mcp_parser = subparsers.add_parser("mcp", help="只启动 MCP Server")
    mcp_parser.add_argument("--port", type=int, default=3000)

    # api 命令
    api_parser = subparsers.add_parser("api", help="只启动 API 服务")
    api_parser.add_argument("--port", type=int, default=8000)

    # plugin 命令
    plugin_parser = subparsers.add_parser("plugin", help="插件管理")
    plugin_sub = plugin_parser.add_subparsers(dest="action", help="动作")

    list_parser = plugin_sub.add_parser("list", help="列出插件")
    load_parser = plugin_sub.add_parser("load", help="加载插件")
    load_parser.add_argument("directory", default="/var/dsh/plugins")
    unload_parser = plugin_sub.add_parser("unload", help="卸载插件")
    unload_parser.add_argument("plugin_id")

    # init 命令
    subparsers.add_parser("init", help="初始化数据库")

    # db 命令（M0）
    db_parser = subparsers.add_parser("db", help="数据层管理（SQLite）")
    db_sub = db_parser.add_subparsers(dest="db_action", help="动作")
    db_sub.add_parser("migrate", help="创建/迁移 SQLite 数据层")
    db_sub.add_parser("stats", help="查看各表记录数")

    # token 命令（M0 自测）
    token_parser = subparsers.add_parser("token", help="签发测试 JWT")
    token_parser.add_argument("--tenant", default="dev", help="租户代码（dev/media/winery）")
    token_parser.add_argument("--user", default="admin", help="用户 ID")
    token_parser.add_argument("--days", type=int, default=7, help="有效期（天）")

    # llm 命令（M0 自检）
    llm_parser = subparsers.add_parser("llm", help="LLM 路由链自检")
    llm_parser.add_argument("--prompt", default="用一句话介绍 FreeBSD", help="测试提示词")

    # workflow 命令（M0 任务 0.2）
    wf_parser = subparsers.add_parser("workflow", help="工作流引擎（0.2）")
    wf_sub = wf_parser.add_subparsers(dest="wf_action")
    wf_run = wf_sub.add_parser("run", help="启动工作流")
    wf_run.add_argument("workflow_code", help="工作流: hello/hello_fail/hello_crash/hello_approval")
    wf_run.add_argument("--tenant", default="dev", help="租户代码（dev/media/winery）")
    wf_run.add_argument("--user", default="admin", help="触发人")
    wf_run.add_argument("--context", default=None, help="JSON 上下文，如 '{\"prompt\":\"...\"}'")
    wf_st = wf_sub.add_parser("status", help="实例与步骤轨迹")
    wf_st.add_argument("instance_id")
    wf_rs = wf_sub.add_parser("resume", help="崩溃/中断后续跑")
    wf_rs.add_argument("instance_id")
    wf_rs.add_argument("--user", default="system", help="操作人")
    wf_ls = wf_sub.add_parser("list", help="实例列表")
    wf_ls.add_argument("--tenant", default=None, help="按租户过滤")
    wf_ls.add_argument("--status", default=None, help="按状态过滤")
    wf_ls.add_argument("--limit", type=int, default=20)
    wf_ap = wf_sub.add_parser("approve", help="审批 WAITING_APPROVAL 实例")
    wf_ap.add_argument("instance_id")
    wf_ap.add_argument("--reject", action="store_true", help="驳回（默认同意）")
    wf_ap.add_argument("--by", default="admin", help="审批人")
    wf_ap.add_argument("--reason", default=None, help="审批意见")
    wf_cc = wf_sub.add_parser("cancel", help="取消实例")
    wf_cc.add_argument("instance_id")
    wf_cc.add_argument("--reason", default=None, help="取消原因")

    args = parser.parse_args()

    # 默认执行 all 命令
    if not args.command:
        args.command = "all"

    # 路由到对应命令
    if args.command == "all":
        return asyncio.run(cmd_all(args))
    elif args.command == "mcp":
        return cmd_mcp(args)  # 同步入口：uvicorn.run 自管事件循环，不可再包 asyncio.run
    elif args.command == "api":
        return cmd_api(args)  # 同上
    elif args.command == "plugin":
        return asyncio.run(cmd_plugin(args))
    elif args.command == "init":
        return asyncio.run(cmd_init(args))
    elif args.command == "db":
        return cmd_db(args)
    elif args.command == "token":
        return cmd_token(args)
    elif args.command == "llm":
        return asyncio.run(cmd_llm(args))
    elif args.command == "workflow":
        return cmd_workflow(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
