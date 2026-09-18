"""tests/test_cli.py — M6 覆盖率提升：dsh_core.cli（CLI 主入口）

目标：dsh_core/cli.py（272 语句，此前 0%）覆盖：
  - _build_engine：bootstrap + WorkflowEngine + tenants.dev 工作流注册 / ImportError 回退
  - cmd_all：存储初始化失败、数据库初始化告警、插件加载、MCP Server 启停、中断清理
  - cmd_mcp / cmd_api：uvicorn 惰性加载与 create_app 装配
  - cmd_plugin：list / load / unload（成功与不存在）
  - cmd_init：数据库初始化
  - cmd_db：migrate（有变更/已最新）/ stats / 未知动作（finally 关闭连接）
  - cmd_token：签发成功 / 租户不存在
  - cmd_llm：模拟与真实模式输出
  - cmd_workflow：run（含 JSON context、FAILED）/ status / resume / list /
    approve（同意/驳回）/ cancel / 无动作 / 未知动作 / WorkflowError
  - main：全部子命令路由 + 默认 all + 未知命令

说明：
  - 函数体内惰性导入（from dsh_core.bootstrap import build_components 等）在调用时
    才执行属性查找，因此直接 monkeypatch 对应源模块属性即可生效。
  - cmd_mcp/cmd_api 内部 `import uvicorn`，测试通过向 sys.modules 注入假模块避免
    真实启动服务。
  - cmd_all 通过替换 cli.StorageManager / MCPServer 等顶层导入类进行隔离。
"""

import argparse
import asyncio
import importlib
import logging
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import dsh_core.cli as cli
from dsh_core import db as db_module
from dsh_core.auth import dependencies as auth_module
from dsh_core.bootstrap import build_components as bootstrap_build_components
from dsh_core.workflow.base import WorkflowError


# ─── 测试替身 ───

def get_module(name):
    """取已加载模块（避免重复导入副作用）"""
    return sys.modules[name] if name in sys.modules else importlib.import_module(name)


def make_args(**kw):
    """构造带默认全局选项的 argparse.Namespace"""
    ns = argparse.Namespace(host="0.0.0.0", port=8000, mcp_port=3000,
                            plugins_dir="/tmp/dsh-plugins",
                            prompt="测试提示词")
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class FakeResult:
    """llm.chat_text 返回值（覆盖 simulated/真实两分支所需字段）"""

    def __init__(self, simulated=True, content="FreeBSD 是一个开源的类 Unix 操作系统。"):
        self.simulated = simulated
        self.provider = "deepseek"
        self.model = "deepseek-chat"
        self.prompt_tokens = 12
        self.completion_tokens = 34
        self.total_tokens = 46
        self.duration_ms = 88
        self.content = content


class FakeComps:
    """build_components 返回值：tenants/jwt/llm/audit + close"""

    def __init__(self, tenant=None, events=None, llm_result=None):
        self.tenants = MagicMock(name="tenants")
        self.tenants.get_by_code.return_value = tenant
        self.jwt = MagicMock(name="jwt")
        self.llm = MagicMock(name="llm")
        self.llm.chat_text = AsyncMock(return_value=llm_result or FakeResult())
        self.audit = MagicMock(name="audit")
        self.audit.query.return_value = events or []
        self.closed = False

    def close(self):
        self.closed = True


class FakeDB:
    """cmd_db 的 Database 实例替身"""

    def __init__(self, migrate_return=0, version="1.2.3", counts=None):
        self.migrate_calls = 0
        self._migrate_return = migrate_return
        self._version = version
        self.counts = counts or {}
        self.config = MagicMock(name="db.config")
        self.config.path = ":memory:"
        self.closed = False

    def migrate(self):
        self.migrate_calls += 1
        return self._migrate_return

    def version(self):
        return self._version

    def query_one(self, sql):
        table = sql.split("FROM ")[-1].split(" ")[0]
        return {"c": self.counts.get(table, 3)}

    def close(self):
        self.closed = True


def make_status(status="SUCCESS", error=None, result=None, current_step="collect_diff"):
    """构造 cmd_workflow print_status 消耗的 status 结构"""
    return {
        "instance": {
            "id": "wf-1",
            "workflow_code": "d1_code_review",
            "created_by": "admin",
            "status": status,
            "current_step": current_step,
            "error": error,
            "result": result if result is not None else {"ok": 1},
        },
        "steps": [
            {"seq": 1, "node_code": "collect_diff", "status": "SUCCESS",
             "attempts": 1, "output": {"files": 2}},
            {"seq": 2, "node_code": "summarize", "status": "SUCCESS",
             "attempts": 1, "output": None},
        ],
    }


class FakeEngine:
    """WorkflowEngine 替身：记录调用并返回固定 status/rows"""

    def __init__(self, status=None, rows=None, start_error=None):
        self.status = status or make_status()
        self.register_node = MagicMock(name="register_node")
        self.start = (AsyncMock(side_effect=start_error) if start_error
                      else AsyncMock(return_value=self.status))
        self.resume = AsyncMock(return_value=self.status)
        self.approve = AsyncMock(return_value=self.status)
        self.cancel = AsyncMock(return_value=self.status)
        self.list_instances = MagicMock(return_value=rows or [])


def install_fake_uvicorn(monkeypatch):
    """向 sys.modules 注入假 uvicorn（cmd_all/cmd_mcp/cmd_api 惰性导入可见）"""
    fake = types.ModuleType("uvicorn")
    fake.run = MagicMock(name="uvicorn.run")
    fake.Config = MagicMock(name="uvicorn.Config")
    fake.Server = MagicMock(name="uvicorn.Server")
    fake.Server.return_value.serve = AsyncMock(name="uvicorn.Server.serve")
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    return fake


def patch_build_components(monkeypatch, comps=None):
    monkeypatch.setattr(sys.modules["dsh_core.bootstrap"], "build_components",
                        MagicMock(return_value=comps or FakeComps()))
    return comps


# ─── _build_engine ───

class TestBuildEngine:
    def test_build_engine_registers_dev_workflows(self, monkeypatch):
        """成功路径：build_components + WorkflowEngine + tenants.dev 注册（4 个节点）"""
        comps = FakeComps()
        engine = FakeEngine()
        patch_build_components(monkeypatch, comps)
        monkeypatch.setattr(sys.modules["dsh_core.workflow"], "WorkflowEngine",
                            MagicMock(return_value=engine))

        result = cli._build_engine()

        assert result is engine
        sys.modules["dsh_core.bootstrap"].build_components.assert_called_once()
        sys.modules["dsh_core.workflow"].WorkflowEngine.assert_called_once_with(comps)
        assert engine.register_node.call_count == 4
        names = {c.args[0] for c in engine.register_node.call_args_list}
        assert names == {"dev_collect_diff", "dev_rule_scan", "dev_llm_review",
                         "dev_summarize"}

    def test_build_engine_skips_dev_workflows_on_import_error(self, monkeypatch, caplog):
        """ImportError 回退：dev 工作流未注册，引擎仍照常返回"""
        comps = FakeComps()
        engine = FakeEngine()
        patch_build_components(monkeypatch, comps)
        monkeypatch.setattr(sys.modules["dsh_core.workflow"], "WorkflowEngine",
                            MagicMock(return_value=engine))
        monkeypatch.setitem(sys.modules, "tenants.dev.workflows", None)
        caplog.set_level(logging.WARNING, logger="dsh.cli")

        result = cli._build_engine()

        assert result is engine
        engine.register_node.assert_not_called()
        assert "dev 工作流未注册" in caplog.text


# ─── cmd_all ───

class TestCmdAll:
    def _install(self, monkeypatch, storage=None, serve_side_effect=KeyboardInterrupt):
        storage = storage or MagicMock(name="storage")
        storage.initialize = AsyncMock()
        storage.cleanup = AsyncMock()
        monkeypatch.setattr(cli.StorageConfig, "from_env",
                            classmethod(lambda cls: "cfg"))
        monkeypatch.setattr(cli, "StorageManager", MagicMock(return_value=storage))
        monkeypatch.setattr(cli, "init_database", AsyncMock())
        registry = MagicMock(name="registry")
        loader = MagicMock(name="loader")
        loader.load_from_directory.return_value = 3
        monkeypatch.setattr(cli, "PluginRegistry", MagicMock(return_value=registry))
        monkeypatch.setattr(cli, "PluginLoader", MagicMock(return_value=loader))
        mcp = MagicMock(name="mcp")
        mcp.initialize = AsyncMock()
        mcp.cleanup = AsyncMock()
        monkeypatch.setattr(cli, "MCPServer", MagicMock(return_value=mcp))
        fake_uvicorn = install_fake_uvicorn(monkeypatch)
        fake_uvicorn.Server.return_value.serve.side_effect = serve_side_effect
        return storage, mcp, fake_uvicorn

    async def test_cmd_all_success(self, monkeypatch, caplog):
        """完整启动：存储→数据库→插件→MCP→serve 中断→清理"""
        storage, mcp, fake_uvicorn = self._install(monkeypatch)
        caplog.set_level(logging.INFO, logger="dsh.cli")
        args = make_args()

        rc = await cli.cmd_all(args)

        assert rc == 0
        storage.initialize.assert_awaited_once()
        cli.init_database.assert_awaited_once_with("cfg")
        assert cli.StorageConfig.from_env()  # sanity: 类方法已替换
        cli.PluginLoader.return_value.load_from_directory.assert_called_once_with(
            "/tmp/dsh-plugins")
        cli.MCPServer.assert_called_once_with(host="0.0.0.0", port=3000)
        mcp.initialize.assert_awaited_once()
        fake_uvicorn.Config.assert_called_once()  # (mcp.app, host=mcp.host, port=mcp.port)
        assert fake_uvicorn.Config.call_args.args[0] is mcp.app
        assert fake_uvicorn.Config.call_args.kwargs["host"] is mcp.host
        assert fake_uvicorn.Config.call_args.kwargs["port"] is mcp.port
        fake_uvicorn.Server.return_value.serve.assert_awaited_once()
        storage.cleanup.assert_awaited_once()
        mcp.cleanup.assert_awaited_once()
        assert "✓ 已加载 3 个插件" in caplog.text
        assert "收到中断信号，正在关闭..." in caplog.text
        assert "DSH AI 中台 已关闭" in caplog.text

    async def test_cmd_all_storage_initialize_failure(self, monkeypatch):
        """存储初始化失败 → 返回 1 且不再继续"""
        storage = MagicMock(name="storage")
        self._install(monkeypatch, storage=storage)
        # 注意：副作用必须在 _install 之后设置（_install 会覆盖 initialize）
        storage.initialize = AsyncMock(side_effect=RuntimeError("no db"))

        rc = await cli.cmd_all(make_args())

        assert rc == 1
        cli.init_database.assert_not_called()
        cli.PluginLoader.assert_not_called()

    async def test_cmd_all_database_warning(self, monkeypatch, caplog):
        """数据库初始化异常仅告警，不中断启动流程"""
        storage, mcp, _ = self._install(monkeypatch)
        cli.init_database = AsyncMock(side_effect=RuntimeError("migrate failed"))
        caplog.set_level(logging.WARNING, logger="dsh.cli")

        rc = await cli.cmd_all(make_args())

        assert rc == 0
        assert "数据库初始化警告: migrate failed" in caplog.text
        cli.PluginLoader.return_value.load_from_directory.assert_called_once()
        mcp.initialize.assert_awaited_once()

    async def test_cmd_all_normal_shutdown(self, monkeypatch):
        """serve() 正常返回（非中断）时 finally 清理路径同样执行"""
        storage, mcp, _ = self._install(monkeypatch, serve_side_effect=None)

        rc = await cli.cmd_all(make_args())

        assert rc == 0
        storage.cleanup.assert_awaited_once()
        mcp.cleanup.assert_awaited_once()


# ─── cmd_mcp / cmd_api ───

class TestCmdMcp:
    def test_cmd_mcp_starts_uvicorn(self, monkeypatch):
        """mcp 命令：create_app(engine=_build_engine()) + uvicorn.run"""
        fake_uvicorn = install_fake_uvicorn(monkeypatch)
        mcp_server_module = get_module("dsh_core.mcp.server")
        monkeypatch.setattr(mcp_server_module, "create_app",
                            MagicMock(return_value="app"))
        monkeypatch.setattr(cli, "_build_engine", MagicMock(return_value="ENGINE"))
        args = make_args(port=3000)

        rc = cli.cmd_mcp(args)

        assert rc == 0
        mcp_server_module.create_app.assert_called_once_with(engine="ENGINE")
        fake_uvicorn.run.assert_called_once_with("app", host="0.0.0.0", port=3000)


class TestCmdApi:
    def test_cmd_api_starts_uvicorn(self, monkeypatch):
        """api 命令（同步）：create_app(engine=_build_engine()) + uvicorn.run"""
        fake_uvicorn = install_fake_uvicorn(monkeypatch)
        routes_module = get_module("dsh_core.api.routes")
        monkeypatch.setattr(routes_module, "create_app",
                            MagicMock(return_value="fastapi-app"))
        monkeypatch.setattr(cli, "_build_engine", MagicMock(return_value="ENGINE"))
        args = make_args(port=8000)

        rc = cli.cmd_api(args)

        assert rc == 0
        routes_module.create_app.assert_called_once_with(engine="ENGINE")
        fake_uvicorn.run.assert_called_once_with("fastapi-app", host="0.0.0.0",
                                                 port=8000)


# ─── cmd_plugin ───

class TestCmdPlugin:
    def _install(self, monkeypatch):
        registry = MagicMock(name="registry")
        loader = MagicMock(name="loader")
        monkeypatch.setattr(cli, "PluginRegistry", MagicMock(return_value=registry))
        monkeypatch.setattr(cli, "PluginLoader", MagicMock(return_value=loader))
        return registry, loader

    async def test_list(self, monkeypatch, capsys):
        registry, _ = self._install(monkeypatch)
        registry.count.return_value = 2
        registry.list_ids.return_value = ["p1", "p2"]

        rc = await cli.cmd_plugin(make_args(action="list"))

        assert rc == 0
        out = capsys.readouterr().out
        assert "已注册插件: 2 个" in out
        assert "  - p1" in out and "  - p2" in out

    async def test_load(self, monkeypatch, capsys):
        _, loader = self._install(monkeypatch)
        loader.load_from_directory.return_value = 4

        rc = await cli.cmd_plugin(make_args(action="load", directory="/tmp/extra"))

        assert rc == 0
        loader.load_from_directory.assert_called_once_with("/tmp/extra")
        assert "已加载 4 个插件" in capsys.readouterr().out

    async def test_unload_success(self, monkeypatch, capsys):
        _, loader = self._install(monkeypatch)
        loader.unload.return_value = True

        rc = await cli.cmd_plugin(make_args(action="unload", plugin_id="p1"))

        assert rc == 0
        loader.unload.assert_called_once_with("p1")
        assert "已卸载插件: p1" in capsys.readouterr().out

    async def test_unload_missing(self, monkeypatch, capsys):
        _, loader = self._install(monkeypatch)
        loader.unload.return_value = False

        rc = await cli.cmd_plugin(make_args(action="unload", plugin_id="p1"))

        assert rc == 0
        assert "插件不存在: p1" in capsys.readouterr().out


# ─── cmd_init ───

class TestCmdInit:
    async def test_cmd_init(self, monkeypatch, capsys):
        monkeypatch.setattr(cli.StorageConfig, "from_env",
                            classmethod(lambda cls: "cfg"))
        monkeypatch.setattr(cli, "init_database", AsyncMock())

        rc = await cli.cmd_init(make_args())

        assert rc == 0
        cli.init_database.assert_awaited_once_with("cfg")
        assert "数据库初始化完成" in capsys.readouterr().out


# ─── cmd_db ───

class TestCmdDb:
    def _install(self, monkeypatch, db_obj=None, **kw):
        db_obj = db_obj or FakeDB(**kw)
        monkeypatch.setattr(db_module, "Database",
                            MagicMock(return_value=MagicMock(
                                connect=MagicMock(return_value=db_obj))))
        monkeypatch.setattr(db_module, "DatabaseConfig", MagicMock())
        return db_obj

    def test_migrate_applied(self, monkeypatch, capsys):
        db = self._install(monkeypatch, migrate_return=3)

        rc = cli.cmd_db(make_args(db_action="migrate"))

        assert rc == 0
        out = capsys.readouterr().out
        assert "当前版本: v1.2.3 （本次应用: 3）" in out
        assert db.migrate_calls == 1 and db.closed

    def test_migrate_latest(self, monkeypatch, capsys):
        db = self._install(monkeypatch, migrate_return=0)

        rc = cli.cmd_db(make_args(db_action="migrate"))

        assert rc == 0
        assert "当前版本: v1.2.3 （已是最新）" in capsys.readouterr().out
        assert db.closed

    def test_stats(self, monkeypatch, capsys):
        counts = {"tenants": 5, "workflow_instance": 2, "workflow_step": 9,
                  "audit_event": 4, "llm_usage": 1}
        db = self._install(monkeypatch, counts=counts)

        rc = cli.cmd_db(make_args(db_action="stats"))

        assert rc == 0
        out = capsys.readouterr().out
        assert "数据库: :memory:  版本: v1.2.3" in out
        for label in ("tenants", "workflow_instance", "workflow_step",
                      "audit_event", "llm_usage"):
            assert f"  {label:<18} {counts[label]}" in out
        assert db.migrate_calls == 1 and db.closed

    def test_unknown_action(self, monkeypatch, capsys):
        db = self._install(monkeypatch)

        rc = cli.cmd_db(make_args(db_action="bogus"))

        assert rc == 1
        assert "用法: dsh-server db <migrate|stats>" in capsys.readouterr().out
        assert db.closed


# ─── cmd_token ───

class TestCmdToken:
    def test_token_success(self, monkeypatch, capsys):
        comps = FakeComps(tenant={"id": "t1", "code": "dev"})
        patch_build_components(monkeypatch, comps)
        monkeypatch.setattr(auth_module, "mint_dev_token",
                            MagicMock(return_value="TOKEN-123"))
        args = make_args(tenant="dev", user="admin", days=7)

        rc = cli.cmd_token(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "租户: dev  用户: admin  有效期: 7 天" in out
        assert "TOKEN-123" in out
        auth_module.mint_dev_token.assert_called_once_with(
            comps.jwt, "t1", "dev", "admin", 7)
        assert comps.closed

    def test_token_tenant_not_found(self, monkeypatch, capsys):
        comps = FakeComps(tenant=None)
        comps.tenants.list_all.return_value = [{"code": "dev"}, {"code": "media"}]
        patch_build_components(monkeypatch, comps)
        monkeypatch.setattr(auth_module, "mint_dev_token", MagicMock())

        rc = cli.cmd_token(make_args(tenant="xyz"))

        assert rc == 1
        out = capsys.readouterr().out
        assert "租户不存在: xyz（可用: ['dev', 'media']）" in out
        auth_module.mint_dev_token.assert_not_called()
        assert comps.closed


# ─── cmd_llm ───

class TestCmdLlm:
    async def test_llm_simulated(self, monkeypatch, capsys):
        comps = FakeComps(llm_result=FakeResult(simulated=True, content="你好 FreeBSD"))
        patch_build_components(monkeypatch, comps)
        args = make_args(prompt="测试提示词")

        rc = await cli.cmd_llm(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "provider=deepseek model=deepseek-chat mode=模拟" in out
        assert "tokens: prompt=12 completion=34 total=46 耗时=88ms" in out
        assert "输出: 你好 FreeBSD" in out
        comps.llm.chat_text.assert_awaited_once_with(
            "测试提示词", purpose="llm-ping", ref_type="cli")
        assert comps.closed

    async def test_llm_real(self, monkeypatch, capsys):
        comps = FakeComps(llm_result=FakeResult(simulated=False))
        patch_build_components(monkeypatch, comps)

        rc = await cli.cmd_llm(make_args())

        assert rc == 0
        assert "mode=真实" in capsys.readouterr().out
        assert comps.closed


# ─── cmd_workflow ───

class TestCmdWorkflow:
    def _install(self, monkeypatch, engine=None, comps=None):
        comps = comps or FakeComps(events=[
            {"occurred_at": "2026-09-15T10:00:00", "action": "wf.start",
             "actor_type": "user", "actor_id": "admin"},
        ])
        engine = engine or FakeEngine()
        patch_build_components(monkeypatch, comps)
        monkeypatch.setattr(sys.modules["dsh_core.workflow"], "WorkflowEngine",
                            MagicMock(return_value=engine))
        return comps, engine

    def test_no_action(self, monkeypatch, capsys):
        comps, _ = self._install(monkeypatch)

        rc = cli.cmd_workflow(make_args())

        assert rc == 1
        assert "用法: dsh-server workflow <run|status|resume|list|approve|cancel>" \
            in capsys.readouterr().out
        assert not comps.closed  # 无动作分支在 build_components 之前返回

    def test_run_success(self, monkeypatch, capsys):
        comps, engine = self._install(monkeypatch)
        args = make_args(wf_action="run", workflow_code="d1_code_review",
                         tenant="dev", user="admin", context=None)

        rc = cli.cmd_workflow(args)

        assert rc == 0
        engine.start.assert_awaited_once_with("d1_code_review", "dev", {},
                                              created_by="admin")
        out = capsys.readouterr().out
        assert "实例 wf-1  [d1_code_review]  触发人: admin" in out
        assert "状态: SUCCESS   当前步骤: collect_diff" in out
        assert "结果: {\"ok\": 1}" in out
        assert "#1" in out and "collect_diff" in out and "attempts=1" in out
        assert "输出: {\"files\": 2}" in out
        assert "审计轨迹:" in out
        assert "wf.start" in out and "user/admin" in out
        assert comps.closed

    def test_run_with_context_json(self, monkeypatch, capsys):
        _, engine = self._install(monkeypatch)

        rc = cli.cmd_workflow(make_args(
            wf_action="run", workflow_code="hello", tenant="dev", user="admin",
            context='{"prompt": "hi"}'))

        assert rc == 0
        engine.start.assert_awaited_once_with("hello", "dev", {"prompt": "hi"},
                                              created_by="admin")

    def test_run_failed_instance(self, monkeypatch, capsys):
        status = make_status(status="FAILED", error="boom",
                             current_step="rule_scan")
        _, engine = self._install(monkeypatch,
                                  engine=FakeEngine(status=status))

        rc = cli.cmd_workflow(make_args(
            wf_action="run", workflow_code="hello_fail", tenant="dev",
            user="admin", context=None))

        assert rc == 1
        out = capsys.readouterr().out
        assert "状态: FAILED   当前步骤: rule_scan" in out
        assert "错误: boom" in out

    def test_status_command(self, monkeypatch, capsys):
        comps, engine = self._install(monkeypatch)
        engine.status = MagicMock(return_value=engine.status)

        rc = cli.cmd_workflow(make_args(wf_action="status", instance_id="wf-1"))

        assert rc == 0
        engine.status.assert_called_once_with("wf-1")
        comps.audit.query.assert_called_once_with(resource_id="wf-1", limit=20)
        assert "实例 wf-1" in capsys.readouterr().out
        assert comps.closed

    def test_resume_command(self, monkeypatch, capsys):
        comps, engine = self._install(monkeypatch)

        rc = cli.cmd_workflow(make_args(wf_action="resume", instance_id="wf-1",
                                        user="system"))

        assert rc == 0
        engine.resume.assert_awaited_once_with("wf-1", actor="system")
        assert comps.closed

    def test_list_command(self, monkeypatch, capsys):
        rows = [{"id": "i1", "workflow_code": "hello", "status": "SUCCESS",
                 "created_at": "2026-09-15T09:00:00", "created_by": "admin"},
                {"id": "i2", "workflow_code": "hello", "status": "FAILED",
                 "created_at": "2026-09-15T09:30:00", "created_by": "ops"}]
        comps, engine = self._install(monkeypatch, engine=FakeEngine(rows=rows))

        rc = cli.cmd_workflow(make_args(wf_action="list", tenant=None,
                                        status=None, limit=20))

        assert rc == 0
        engine.list_instances.assert_called_once_with(tenant_code=None,
                                                      status=None, limit=20)
        out = capsys.readouterr().out
        assert "共 2 个实例" in out
        assert "i1" in out and "i2" in out
        assert "hello" in out
        assert "by admin" in out and "by ops" in out
        assert comps.closed

    def test_approve_agreed(self, monkeypatch, capsys):
        comps, engine = self._install(monkeypatch)

        rc = cli.cmd_workflow(make_args(wf_action="approve", instance_id="wf-1",
                                        reject=False, by="boss", reason=None))

        assert rc == 0
        engine.approve.assert_awaited_once_with("wf-1", True, "boss", None)
        assert comps.closed

    def test_approve_rejected(self, monkeypatch, capsys):
        comps, engine = self._install(monkeypatch)

        rc = cli.cmd_workflow(make_args(wf_action="approve", instance_id="wf-1",
                                        reject=True, by="boss", reason="重写"))

        assert rc == 0
        engine.approve.assert_awaited_once_with("wf-1", False, "boss", "重写")
        assert comps.closed

    def test_cancel_command(self, monkeypatch, capsys):
        comps, engine = self._install(monkeypatch)

        rc = cli.cmd_workflow(make_args(wf_action="cancel", instance_id="wf-1",
                                        reason="重复提交"))

        assert rc == 0
        engine.cancel.assert_awaited_once_with("wf-1", reason="重复提交")
        assert comps.closed

    def test_unknown_subcommand(self, monkeypatch, capsys):
        comps, _ = self._install(monkeypatch)

        rc = cli.cmd_workflow(make_args(wf_action="bogus"))

        assert rc == 1
        assert "未知子命令: bogus" in capsys.readouterr().out
        assert comps.closed

    def test_workflow_error_handled(self, monkeypatch, capsys):
        engine = FakeEngine(start_error=WorkflowError("引擎繁忙"))
        comps, _ = self._install(monkeypatch, engine=engine)

        rc = cli.cmd_workflow(make_args(
            wf_action="run", workflow_code="hello", tenant="dev",
            user="admin", context=None))

        assert rc == 1
        assert "工作流错误: 引擎繁忙" in capsys.readouterr().out
        assert comps.closed


# ─── main ───

class TestMain:
    def _patch_run(self, monkeypatch):
        """main() 对 mcp 这类同步处理函数也套了 asyncio.run（见 cli.py 396-399），
        测试中替换为恒等函数以便对返回值直接断言。"""
        monkeypatch.setattr(cli.asyncio, "run", lambda c: c)

    def test_default_command_all(self, monkeypatch):
        """无子命令 → 默认 all"""
        self._patch_run(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["dsh-server"])
        monkeypatch.setattr(cli, "cmd_all", MagicMock(return_value=7))

        rc = cli.main()

        assert rc == 7
        cli.cmd_all.assert_called_once()
        assert cli.cmd_all.call_args.args[0].command == "all"

    @pytest.mark.parametrize("argv,attr,expected", [
        (["dsh-server", "mcp", "--port", "3000"], "cmd_mcp", 2),
        (["dsh-server", "api"], "cmd_api", 3),
        (["dsh-server", "plugin", "list"], "cmd_plugin", 4),
        (["dsh-server", "init"], "cmd_init", 5),
        (["dsh-server", "db", "migrate"], "cmd_db", 6),
        (["dsh-server", "token", "--tenant", "dev"], "cmd_token", 8),
        (["dsh-server", "llm"], "cmd_llm", 9),
        (["dsh-server", "workflow", "run", "hello"], "cmd_workflow", 10),
    ])
    def test_command_routing(self, monkeypatch, argv, attr, expected):
        self._patch_run(monkeypatch)
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.setattr(cli, attr, MagicMock(return_value=expected))

        rc = cli.main()

        assert rc == expected
        getattr(cli, attr).assert_called_once()

    def test_unknown_command_prints_help(self, monkeypatch, capsys):
        """路由兜底分支：command 不在路由表时打印帮助并返回 1。
        （argparse 对未知子命令会在 parse_args 阶段直接 SystemExit(2)，
        因此注入 parse_args 返回值来驱动 main() 的 else 兜底分支）"""
        self._patch_run(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["dsh-server", "bogus"])
        monkeypatch.setattr(
            argparse.ArgumentParser, "parse_args",
            lambda self: argparse.Namespace(
                host="0.0.0.0", port=8000, mcp_port=3000,
                plugins_dir="/var/dsh/plugins", command="bogus"))

        rc = cli.main()

        assert rc == 1
        assert "usage:" in capsys.readouterr().out