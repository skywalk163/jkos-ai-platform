"""插件市场 / i18n / 工具 REST 路由集成测试

覆盖目标：
- ``jkos_core/api/tool_routes.py`` 此前缺失的全部行（序列化辅助、可见域/分类解析的 400 分支、
  分类/行业标签/推荐/已验证、列表搜索与过滤、注册、详情、版本、发布、激活、注销、评分、
  下载数、市场信息更新含旧式 ``dict()`` 兼容分支）。
- ``jkos_core/api/marketplace_routes.py`` 全文件（插件市场 10 个端点、i18n 6 个端点、
  以及 ``register_marketplace_routes`` / ``register_i18n_routes`` 两个挂载 helper）。

认证策略：照抄 ``tests/test_m14_mcp_mt.py`` 的全局 JWTManager 注入模式——模块级 autouse
fixture 注入 ``auth_deps._jwt_manager``，模块结束后恢复原值，避免污染其它测试模块。
所有 app 均通过工厂的**实例注入参数**构造，绝不调用无参 ``get_registry()`` /
``get_marketplace()`` / ``get_i18n()`` 全局工厂。

匿名访问说明：``get_tenant_context``（``jkos_core/auth/dependencies.py`` L127-142）在缺少
Bearer Token 时直接抛 401（``_jwt_manager`` 为 None 时才是 500）。因此 ``/api/v1/tools``
匿名访问返回 **401** 而非 500，本文件按源码实际行为断言 401，未发现 500 缺陷。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jkos_core.api.marketplace_routes import (
    create_i18n_router,
    create_plugin_router,
    register_i18n_routes,
    register_marketplace_routes,
)
from jkos_core.api.tool_routes import create_tool_router, register_tool_routes
from jkos_core.auth import AuthConfig, JWTManager, issue_access_token
from jkos_core.auth import dependencies as auth_deps
from jkos_core.mcp.registry import ToolCategory, ToolRegistry, ToolVisibility
from jkos_core.mcp.server import MCPTool
from jkos_core.plugins.base import (
    DeterministicPlugin,
    PluginCategory,
    PluginResult,
    PluginStatus,
)
from jkos_core.plugins.i18n import I18nManager
from jkos_core.plugins.marketplace import PluginMarketplace
from jkos_core.plugins.registry import EnhancedPluginRegistry, VisibilityDomain

_SECRET = "m14-test-secret-0123456789abcdef"
_TENANT_A = ("tenant-a", "TA")
_TENANT_B = ("tenant-b", "TB")

_TOOLS_BASE = "/api/v1/tools"
_PLUGINS_BASE = "/api/v1/plugins"
_I18N_BASE = "/api/v1/i18n"


# ─── 认证与请求辅助 ───

@pytest.fixture(scope="module", autouse=True)
def _setup_global_auth():
    """注入全局 JWTManager，模块结束后恢复原状态"""
    old = auth_deps._jwt_manager
    auth_deps._jwt_manager = JWTManager(AuthConfig(secret=_SECRET))
    yield
    auth_deps._jwt_manager = old


def _auth(tenant: tuple[str, str] = _TENANT_A) -> dict:
    """签发指定租户的 Bearer 请求头"""
    token = issue_access_token(
        user_id="admin",
        tenant_id=tenant[0],
        tenant_code=tenant[1],
        roles=["admin"],
        secret=_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


# ─── 测试用插件 ───

class _StubPlugin(DeterministicPlugin):
    """路由测试用确定性插件（类名不以 Test 开头，避免 pytest 收集告警）"""

    def __init__(self, plugin_id: str, category: PluginCategory = PluginCategory.TEXT):
        self._plugin_id = plugin_id
        self._category = category
        super().__init__()

    def _get_plugin_id(self) -> str:
        return self._plugin_id

    def _get_version(self) -> str:
        return "1.0.0"

    def _get_name(self) -> str:
        return f"{self._plugin_id} 名称"

    def _get_description(self) -> str:
        return "路由测试插件"

    def _get_category(self) -> PluginCategory:
        return self._category

    async def initialize(self) -> None:
        self.status = PluginStatus.ACTIVE

    async def health_check(self) -> bool:
        return True

    async def execute(self, input_data, context) -> PluginResult:
        return PluginResult(success=True, data={"ok": True})


class _LegacyUpdateRequest:
    """无 ``model_dump`` 的旧式（pydantic v1 风格）请求对象

    用于覆盖 ``tool_routes.update_marketplace`` 中 ``req.dict()`` 兼容分支；
    该分支在 FastAPI 依赖注入下不可达（注入的模型必有 ``model_dump``）。
    """

    def dict(self, exclude_none: bool = True) -> dict:
        return {"featured": True}


# ─── 环境构造 ───

class _Env:
    """测试环境：独立注册表 / 市场 / i18n 实例 + TestClient"""

    def __init__(self, tmp_path) -> None:
        self.registry = ToolRegistry()
        self.marketplace = PluginMarketplace(
            registry=EnhancedPluginRegistry(),
            plugins_dir=str(tmp_path),
        )
        self.i18n = I18nManager()
        self.app = FastAPI()
        self.app.include_router(create_tool_router(self.registry))
        self.app.include_router(create_plugin_router(self.marketplace))
        self.app.include_router(create_i18n_router(self.i18n))
        self.client: TestClient


@pytest.fixture
def env(tmp_path) -> _Env:
    """构造注入式 app（不经全局工厂），每个用例独立实例"""
    environment = _Env(tmp_path)
    with TestClient(environment.app) as client:
        environment.client = client
        yield environment


def _register_tool(
    env: _Env,
    name: str,
    visibility: str = "core",
    category: str = "text",
    industry_tags: list | None = None,
    tenant: tuple[str, str] = _TENANT_A,
) -> dict:
    """经 REST 注册工具并返回响应体"""
    payload = {
        "name": name,
        "description": f"{name} 描述",
        "input_schema": {},
        "output_schema": {},
        "visibility": visibility,
        "category": category,
        "industry_tags": industry_tags or [],
        "version": "1.0.0",
    }
    resp = env.client.post(f"{_TOOLS_BASE}/register", json=payload, headers=_auth(tenant))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_tools(env: _Env) -> None:
    """注册覆盖各可见域/分类/行业的工具（delta_tool 归属 tenant-b 用于租户隔离）"""
    _register_tool(env, "alpha_tool", visibility="core", category="text",
                   industry_tags=["finance"])
    _register_tool(env, "beta_tool", visibility="industry", category="audio",
                   industry_tags=["finance"])
    _register_tool(env, "gamma_tool", visibility="custom", category="text")
    _register_tool(env, "delta_tool", visibility="custom", category="text",
                   tenant=_TENANT_B)


def _names(payload: dict) -> set:
    """从列表响应中提取工具名集合"""
    return {item["name"] for item in payload["tools"]}


def _list_tools(env: _Env, query: str = "") -> dict:
    """调用工具列表端点并断言 200"""
    resp = env.client.get(f"{_TOOLS_BASE}{query}", headers=_auth())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _register_plugin(
    env: _Env,
    plugin_id: str,
    category: PluginCategory = PluginCategory.TEXT,
    visibility: VisibilityDomain = VisibilityDomain.CORE,
    industry_tags: list | None = None,
    tenant_id: str | None = None,
) -> None:
    """向市场注册表写入插件"""
    env.marketplace.registry.register(
        _StubPlugin(plugin_id, category),
        visibility=visibility,
        industry_tags=industry_tags,
        tenant_id=tenant_id,
    )


def _seed_plugins(env: _Env) -> None:
    """注册覆盖各可见域的插件（两个 CUSTOM 插件分属不同租户）"""
    _register_plugin(env, "plug.core", industry_tags=["finance"])
    _register_plugin(env, "plug.ind", category=PluginCategory.AUDIO,
                     visibility=VisibilityDomain.INDUSTRY, industry_tags=["finance"])
    _register_plugin(env, "plug.cust.a", visibility=VisibilityDomain.CUSTOM,
                     tenant_id=_TENANT_A[0])
    _register_plugin(env, "plug.cust.b", visibility=VisibilityDomain.CUSTOM,
                     tenant_id=_TENANT_B[0])


# ─── 5.1 tool_routes.py ───

class TestToolRoutes:
    """工具注册 / 发现 / 版本 / 市场路由（prefix /api/v1/tools）"""

    def test_list_categories(self, env: _Env):
        """分类列表：返回 9 个内置分类"""
        resp = env.client.get(f"{_TOOLS_BASE}/categories")
        assert resp.status_code == 200
        assert len(resp.json()["categories"]) == 9

    def test_list_industry_tags(self, env: _Env):
        """行业标签：注册带标签的工具后可查询到该标签"""
        _register_tool(env, "tagged_tool", industry_tags=["finance", "retail"])
        resp = env.client.get(f"{_TOOLS_BASE}/industry-tags")
        assert resp.status_code == 200
        assert set(resp.json()["industry_tags"]) == {"finance", "retail"}

    def test_featured_and_verified(self, env: _Env):
        """推荐 / 已验证列表：置位后各返回 1 个工具"""
        _seed_tools(env)
        patch = env.client.patch(
            f"{_TOOLS_BASE}/alpha_tool/marketplace",
            json={"featured": True, "verified": True},
            headers=_auth(),
        )
        assert patch.status_code == 200

        featured = env.client.get(f"{_TOOLS_BASE}/featured")
        assert featured.status_code == 200
        assert featured.json()["total"] == 1
        assert _names(featured.json()) == {"alpha_tool"}

        verified = env.client.get(f"{_TOOLS_BASE}/verified")
        assert verified.status_code == 200
        assert verified.json()["total"] == 1
        assert verified.json()["tools"][0]["name"] == "alpha_tool"

    def test_list_search(self, env: _Env):
        """列表搜索：命中 alpha，未命中时返回空列表"""
        _seed_tools(env)
        hit = _list_tools(env, "?search=alpha")
        assert _names(hit) == {"alpha_tool"}

        miss = _list_tools(env, "?search=不存在的工具")
        assert miss == {"tools": [], "total": 0}

    def test_list_filters(self, env: _Env):
        """列表过滤：可见域 / 分类 / 行业单独与组合过滤均生效，含租户隔离"""
        _seed_tools(env)

        # 无过滤：delta_tool 属 tenant-b，被租户隔离剔除
        base = _list_tools(env)
        assert _names(base) == {"alpha_tool", "beta_tool", "gamma_tool"}

        assert _names(_list_tools(env, "?visibility=core")) == {"alpha_tool"}
        assert _names(_list_tools(env, "?visibility=industry")) == {"beta_tool"}
        assert _names(_list_tools(env, "?category=text")) == {"alpha_tool", "gamma_tool"}
        assert _names(_list_tools(env, "?industry=finance")) == {"alpha_tool", "beta_tool"}
        assert _names(
            _list_tools(env, "?visibility=core&category=text&industry=finance")
        ) == {"alpha_tool"}

        # 换成 tenant-b：delta_tool 可见、gamma_tool 被隔离
        resp = env.client.get(f"{_TOOLS_BASE}/?category=text", headers=_auth(_TENANT_B))
        assert resp.status_code == 200
        assert {item["name"] for item in resp.json()["tools"]} == {"alpha_tool", "delta_tool"}

    def test_list_invalid_visibility_and_category(self, env: _Env):
        """列表过滤：非法可见域 / 非法分类均返回 400"""
        bad_visibility = env.client.get(
            f"{_TOOLS_BASE}?visibility=bogus", headers=_auth()
        )
        assert bad_visibility.status_code == 400
        assert bad_visibility.json()["detail"] == "无效的可见域"

        bad_category = env.client.get(f"{_TOOLS_BASE}?category=bogus", headers=_auth())
        assert bad_category.status_code == 400
        assert bad_category.json()["detail"] == "无效的工具分类"

    def test_list_anonymous_returns_401(self, env: _Env):
        """匿名访问列表：get_tenant_context 缺 Bearer 时抛 401（非 500）"""
        resp = env.client.get(_TOOLS_BASE)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "缺少 Bearer Token"

    def test_register_success(self, env: _Env):
        """注册成功：返回 registered 状态并写入注入的注册表"""
        body = _register_tool(env, "new_tool", visibility="industry",
                             category="rag", industry_tags=["finance"])
        assert body == {"tool_name": "new_tool", "status": "registered", "version": "1.0.0"}

        info = env.registry.get_info("new_tool")
        assert info is not None
        assert info.visibility == ToolVisibility.INDUSTRY
        assert info.category == ToolCategory.RAG
        assert info.industry_tags == ["finance"]

    def test_register_duplicate_returns_400(self, env: _Env):
        """注册重名：第二次注册同名工具返回 400"""
        _register_tool(env, "dup_tool")
        resp = env.client.post(
            f"{_TOOLS_BASE}/register",
            json={"name": "dup_tool", "visibility": "core", "category": "text"},
            headers=_auth(),
        )
        assert resp.status_code == 400
        assert "已注册" in resp.json()["detail"]

    def test_register_invalid_visibility_returns_400(self, env: _Env):
        """注册非法可见域：返回 400"""
        resp = env.client.post(
            f"{_TOOLS_BASE}/register",
            json={"name": "bad_vis", "visibility": "bogus"},
            headers=_auth(),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "无效的可见域"

    def test_register_invalid_category_returns_400(self, env: _Env):
        """注册非法分类：返回 400"""
        resp = env.client.post(
            f"{_TOOLS_BASE}/register",
            json={"name": "bad_cat", "category": "bogus"},
            headers=_auth(),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "无效的工具分类"

    def test_register_custom_tool_binds_tenant(self, env: _Env):
        """注册自定义工具：tenant_id 绑定为请求方租户"""
        _register_tool(env, "custom_tool", visibility="custom")
        info = env.registry.get_info("custom_tool")
        assert info.visibility == ToolVisibility.CUSTOM
        assert info.tenant_id == _TENANT_A[0]

    def test_tool_detail_and_404(self, env: _Env):
        """工具详情：存在返回完整序列化形状，不存在返回 404"""
        _seed_tools(env)
        resp = env.client.get(f"{_TOOLS_BASE}/alpha_tool")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "alpha_tool"
        assert body["version"] == "1.0.0"
        assert body["visibility"] == "core"
        assert body["category"] == "text"
        assert body["industry_tags"] == ["finance"]
        assert body["tenant_id"] is None
        assert body["marketplace"]["downloads"] == 0
        assert "created_at" in body["marketplace"]

        missing = env.client.get(f"{_TOOLS_BASE}/ghost_tool")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "工具不存在"

    def test_tool_versions(self, env: _Env):
        """版本历史：注册后含 1 条初始版本快照"""
        _register_tool(env, "ver_tool")
        resp = env.client.get(f"{_TOOLS_BASE}/ver_tool/versions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tool_name"] == "ver_tool"
        assert [v["version"] for v in body["versions"]] == ["1.0.0"]

        # 不存在的工具返回空版本列表（源码不抛 404）
        empty = env.client.get(f"{_TOOLS_BASE}/ghost_tool/versions")
        assert empty.status_code == 200
        assert empty.json()["versions"] == []

    def test_publish_version_and_404(self, env: _Env):
        """发布版本：成功追加快照，工具不存在返回 404"""
        _register_tool(env, "pub_tool", category="image")
        resp = env.client.post(
            f"{_TOOLS_BASE}/pub_tool/versions",
            json={"version": "2.0.0", "changelog": "优化", "changes": {"description": "v2"}},
        )
        assert resp.status_code == 200
        assert resp.json() == {"tool_name": "pub_tool", "version": "2.0.0", "status": "published"}

        versions = env.client.get(f"{_TOOLS_BASE}/pub_tool/versions").json()["versions"]
        assert [v["version"] for v in versions] == ["1.0.0", "2.0.0"]

        missing = env.client.post(
            f"{_TOOLS_BASE}/ghost_tool/versions", json={"version": "2.0.0"}
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "工具不存在"

    def test_activate_version_and_404(self, env: _Env):
        """激活版本：已发布版本可激活，未知版本 / 未知工具返回 404"""
        _register_tool(env, "act_tool")
        env.client.post(f"{_TOOLS_BASE}/act_tool/versions", json={"version": "2.0.0"})

        resp = env.client.post(f"{_TOOLS_BASE}/act_tool/versions/2.0.0/activate")
        assert resp.status_code == 200
        assert resp.json() == {"tool_name": "act_tool", "version": "2.0.0", "status": "active"}
        assert env.registry.get_info("act_tool").version == "2.0.0"

        bad_version = env.client.post(f"{_TOOLS_BASE}/act_tool/versions/9.9.9/activate")
        assert bad_version.status_code == 404
        assert bad_version.json()["detail"] == "版本激活失败"

        missing = env.client.post(f"{_TOOLS_BASE}/ghost_tool/versions/1.0.0/activate")
        assert missing.status_code == 404

    def test_unregister_and_404(self, env: _Env):
        """注销工具：成功返回 unregistered，重复注销返回 404"""
        _register_tool(env, "del_tool")
        resp = env.client.delete(f"{_TOOLS_BASE}/del_tool")
        assert resp.status_code == 200
        assert resp.json() == {"tool_name": "del_tool", "status": "unregistered"}
        assert env.registry.get_info("del_tool") is None

        again = env.client.delete(f"{_TOOLS_BASE}/del_tool")
        assert again.status_code == 404
        assert again.json()["detail"] == "工具不存在"

    def test_rate_tool_and_400(self, env: _Env):
        """评分工具：成功返回评分，对不存在工具返回 400"""
        _register_tool(env, "rate_tool")
        resp = env.client.post(f"{_TOOLS_BASE}/rate_tool/rate", json={"rating": 4.5})
        assert resp.status_code == 200
        assert resp.json() == {"tool_name": "rate_tool", "rating": 4.5}
        assert env.registry.get_marketplace_info("rate_tool").rating == 4.5

        missing = env.client.post(f"{_TOOLS_BASE}/ghost_tool/rate", json={"rating": 4.5})
        assert missing.status_code == 400
        assert missing.json()["detail"] == "评分失败"

    def test_increment_downloads_and_404(self, env: _Env):
        """下载计数：成功 +1 并回显，不存在返回 404"""
        _register_tool(env, "dl_tool")
        resp = env.client.post(f"{_TOOLS_BASE}/dl_tool/downloads")
        assert resp.status_code == 200
        assert resp.json() == {"tool_name": "dl_tool", "downloads": 1, "status": "incremented"}

        missing = env.client.post(f"{_TOOLS_BASE}/ghost_tool/downloads")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "工具不存在"

    def test_patch_marketplace_success(self, env: _Env):
        """更新市场信息：返回更新后的市场元数据"""
        _register_tool(env, "mk_tool")
        resp = env.client.patch(
            f"{_TOOLS_BASE}/mk_tool/marketplace",
            json={"tags": ["ai"], "author_url": "https://example.com", "featured": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["tool_name"] == "mk_tool"
        assert body["marketplace"]["tags"] == ["ai"]
        assert body["marketplace"]["author_url"] == "https://example.com"
        assert body["marketplace"]["featured"] is True

    def test_patch_marketplace_empty_body_400_and_404(self, env: _Env):
        """更新市场信息：空 body（无有效字段）返回 400，工具不存在返回 404"""
        _register_tool(env, "mk2_tool")
        empty = env.client.patch(f"{_TOOLS_BASE}/mk2_tool/marketplace", json={})
        assert empty.status_code == 400
        assert empty.json()["detail"] == "无更新字段"

        missing = env.client.patch(
            f"{_TOOLS_BASE}/ghost_tool/marketplace", json={"featured": True}
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "工具不存在"

    def test_patch_marketplace_legacy_dict_branch(self, env: _Env):
        """更新市场信息：无 model_dump 的旧式请求对象走 dict() 兼容分支

        该分支在 FastAPI 注入下不可达（注入模型必有 model_dump），
        故直接调用路由的处理函数并传入旧式请求对象以覆盖源码行。
        """
        env.registry.register(
            MCPTool(name="legacy_tool", description="legacy"),
            visibility=ToolVisibility.CORE,
            category=ToolCategory.TEXT,
        )
        router = create_tool_router(env.registry)
        endpoint = next(
            route.endpoint
            for route in router.routes
            if getattr(route, "path", "") == "/api/v1/tools/{tool_name}/marketplace"
            and "PATCH" in getattr(route, "methods", set())
        )

        result = asyncio.run(endpoint(tool_name="legacy_tool", req=_LegacyUpdateRequest()))
        assert result["tool_name"] == "legacy_tool"
        assert result["marketplace"]["featured"] is True


# ─── 5.2 marketplace_routes.py · 插件市场 ───

class TestPluginRoutes:
    """插件市场路由（prefix /api/v1/plugins）"""

    def test_categories_statistics_featured(self, env: _Env):
        """分类 / 统计 / 推荐：三类只读端点均返回 200"""
        _seed_plugins(env)
        env.marketplace.set_featured("plug.core", True)

        categories = env.client.get(f"{_PLUGINS_BASE}/categories")
        assert categories.status_code == 200
        assert categories.json()["categories"] == [{"category": "text", "count": 3},
                                                   {"category": "audio", "count": 1}]

        statistics = env.client.get(f"{_PLUGINS_BASE}/statistics")
        assert statistics.status_code == 200
        assert statistics.json()["total_plugins"] == 4
        assert statistics.json()["core_plugins"] == 1
        assert statistics.json()["industry_plugins"] == 1
        assert statistics.json()["custom_plugins"] == 2

        featured = env.client.get(f"{_PLUGINS_BASE}/featured")
        assert featured.status_code == 200
        assert [p["plugin_id"] for p in featured.json()["plugins"]] == ["plug.core"]

    def test_list_search(self, env: _Env):
        """列表搜索：命中插件 ID，返回 total 字段"""
        _seed_plugins(env)
        resp = env.client.get(f"{_PLUGINS_BASE}?search=plug.core", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["plugins"][0]["plugin_id"] == "plug.core"

    def test_list_filters_with_tenant_isolation(self, env: _Env):
        """列表过滤：分类 / 可见域 / 行业生效，CUSTOM 插件按租户隔离"""
        _seed_plugins(env)

        base = env.client.get(_PLUGINS_BASE, headers=_auth())
        assert base.status_code == 200
        assert {p["plugin_id"] for p in base.json()["plugins"]} == {
            "plug.core", "plug.ind", "plug.cust.a",
        }

        by_category = env.client.get(f"{_PLUGINS_BASE}?category=audio", headers=_auth())
        assert [p["plugin_id"] for p in by_category.json()["plugins"]] == ["plug.ind"]

        by_visibility = env.client.get(f"{_PLUGINS_BASE}?visibility=core", headers=_auth())
        assert [p["plugin_id"] for p in by_visibility.json()["plugins"]] == ["plug.core"]

        by_industry = env.client.get(f"{_PLUGINS_BASE}?industry=finance", headers=_auth())
        assert {p["plugin_id"] for p in by_industry.json()["plugins"]} == {
            "plug.core", "plug.ind",
        }

        # tenant-b 视角：只看到自己的 CUSTOM 插件 + CORE/INDUSTRY 插件
        other = env.client.get(_PLUGINS_BASE, headers=_auth(_TENANT_B))
        assert {p["plugin_id"] for p in other.json()["plugins"]} == {
            "plug.core", "plug.ind", "plug.cust.b",
        }

    def test_get_plugin_detail_and_404(self, env: _Env):
        """插件详情：存在返回完整详情，不存在返回 404"""
        _seed_plugins(env)
        resp = env.client.get(f"{_PLUGINS_BASE}/plug.core")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plugin_id"] == "plug.core"
        assert body["category"] == "text"
        assert body["visibility"] == "core"

        missing = env.client.get(f"{_PLUGINS_BASE}/ghost")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "插件不存在"

    def test_install_invalid_visibility_returns_400(self, env: _Env):
        """安装插件：非法可见域返回 400"""
        resp = env.client.post(
            f"{_PLUGINS_BASE}/install",
            json={"plugin_id": "plug.new", "visibility": "bogus"},
            headers=_auth(),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "无效的可见域"

    def test_install_failure_returns_400(self, env: _Env):
        """安装插件：插件已存在时安装失败返回 400"""
        _seed_plugins(env)
        resp = env.client.post(
            f"{_PLUGINS_BASE}/install",
            json={"plugin_id": "plug.core"},
            headers=_auth(),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "插件安装失败"

    def test_install_success(self, env: _Env):
        """安装插件：CUSTOM 插件绑定请求方租户并写入 plugin.yaml"""
        resp = env.client.post(
            f"{_PLUGINS_BASE}/install",
            json={"plugin_id": "plug.fresh", "version": "1.2.0"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json() == {"plugin_id": "plug.fresh", "status": "installed"}
        assert (env.marketplace.plugins_dir / "plug.fresh" / "plugin.yaml").exists()

        core = env.client.post(
            f"{_PLUGINS_BASE}/install",
            json={"plugin_id": "plug.fresh.core", "visibility": "core"},
            headers=_auth(),
        )
        assert core.status_code == 200

    def test_uninstall_failure_returns_400(self, env: _Env):
        """卸载插件：未安装插件返回 400"""
        resp = env.client.post(
            f"{_PLUGINS_BASE}/uninstall", json={"plugin_id": "ghost"}, headers=_auth()
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "插件卸载失败"

    def test_uninstall_success(self, env: _Env):
        """卸载插件：已安装插件卸载成功并清理目录"""
        _register_plugin(env, "plug.un")
        plugin_dir = env.marketplace.plugins_dir / "plug.un"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        resp = env.client.post(
            f"{_PLUGINS_BASE}/uninstall", json={"plugin_id": "plug.un"}, headers=_auth()
        )
        assert resp.status_code == 200
        assert resp.json() == {"plugin_id": "plug.un", "status": "uninstalled"}
        assert not plugin_dir.exists()

    def test_rate_plugin_and_400(self, env: _Env):
        """插件评分：成功返回评分，不存在插件返回 400"""
        _seed_plugins(env)
        resp = env.client.post(f"{_PLUGINS_BASE}/plug.core/rate", json={"rating": 5})
        assert resp.status_code == 200
        assert resp.json() == {"plugin_id": "plug.core", "rating": 5}

        missing = env.client.post(f"{_PLUGINS_BASE}/ghost/rate", json={"rating": 5})
        assert missing.status_code == 400
        assert missing.json()["detail"] == "评分失败"

    def test_set_featured_and_400(self, env: _Env):
        """置推荐：成功置位，不存在插件返回 400"""
        _seed_plugins(env)
        resp = env.client.post(f"{_PLUGINS_BASE}/plug.core/featured")
        assert resp.status_code == 200
        assert resp.json() == {"plugin_id": "plug.core", "featured": True}
        assert env.marketplace.get_details("plug.core")["featured"] is True

        missing = env.client.post(f"{_PLUGINS_BASE}/ghost/featured")
        assert missing.status_code == 400
        assert missing.json()["detail"] == "设置失败"

    def test_set_verified_and_400(self, env: _Env):
        """置已验证：成功置位（可用 query 关闭），不存在插件返回 400"""
        _seed_plugins(env)
        resp = env.client.post(f"{_PLUGINS_BASE}/plug.core/verified")
        assert resp.status_code == 200
        assert resp.json() == {"plugin_id": "plug.core", "verified": True}

        off = env.client.post(f"{_PLUGINS_BASE}/plug.core/verified?verified=false")
        assert off.status_code == 200
        assert off.json()["verified"] is False

        missing = env.client.post(f"{_PLUGINS_BASE}/ghost/verified")
        assert missing.status_code == 400
        assert missing.json()["detail"] == "设置失败"


# ─── 5.3 marketplace_routes.py · i18n ───

class TestI18nRoutes:
    """多语言路由（prefix /api/v1/i18n）"""

    def test_list_languages(self, env: _Env):
        """语言列表：含内置 zh / en"""
        resp = env.client.get(f"{_I18N_BASE}/languages")
        assert resp.status_code == 200
        codes = {item["code"] for item in resp.json()["languages"]}
        assert {"zh", "en"}.issubset(codes)

    def test_get_translations_and_404(self, env: _Env):
        """单语言翻译：zh 存在返回翻译字典，未知语言返回 404"""
        resp = env.client.get(f"{_I18N_BASE}/zh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "zh"
        assert body["translations"]["app.name"] == "极快AI操作系统"

        missing = env.client.get(f"{_I18N_BASE}/de")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "语言不存在"

    def test_get_current_translations(self, env: _Env):
        """当前语言翻译：默认 zh，lang 查询参数与 Accept-Language 头均生效"""
        default = env.client.get(_I18N_BASE)
        assert default.status_code == 200
        assert default.json()["language"] == "zh"

        by_param = env.client.get(f"{_I18N_BASE}?lang=en")
        assert by_param.status_code == 200
        assert by_param.json()["language"] == "en"
        assert by_param.json()["translations"]["app.name"] == "Jikuai AI OS"

        by_header = env.client.get(_I18N_BASE, headers={"Accept-Language": "en-US,en;q=0.9"})
        assert by_header.status_code == 200
        assert by_header.json()["language"] == "en"

    def test_translate(self, env: _Env):
        """翻译单个键：按 lang 参数返回对应语言文本"""
        resp = env.client.post(
            f"{_I18N_BASE}/translate?lang=en",
            json={"key": "app.name", "params": {}},
        )
        assert resp.status_code == 200
        assert resp.json() == {"key": "app.name", "text": "Jikuai AI OS", "language": "en"}

        fallback = env.client.post(f"{_I18N_BASE}/translate", json={"key": "unknown.key"})
        assert fallback.status_code == 200
        assert fallback.json()["text"] == "unknown.key"

    def test_translate_batch(self, env: _Env):
        """批量翻译：body 为 JSON 数组，返回键值映射"""
        resp = env.client.post(
            f"{_I18N_BASE}/translate/batch?lang=en",
            json=["app.name", "button.submit"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "en"
        assert body["translations"] == {
            "app.name": "Jikuai AI OS",
            "button.submit": "Submit",
        }

    def test_set_language_and_400(self, env: _Env):
        """设置语言：合法码返回 set 状态并更新管理器，非法码返回 400"""
        resp = env.client.post(f"{_I18N_BASE}/set-language?language=en")
        assert resp.status_code == 200
        assert resp.json() == {"language": "en", "status": "set"}
        assert env.i18n.get_language() == "en"

        invalid = env.client.post(f"{_I18N_BASE}/set-language?language=xx")
        assert invalid.status_code == 400
        assert invalid.json()["detail"] == "无效的语言代码"


# ─── 5.4 挂载 helper ───

class TestRegisterHelpers:
    """register_marketplace_routes / register_i18n_routes / register_tool_routes"""

    def test_register_helpers_mount_routers(self, env: _Env):
        """挂载 helper：三个 helper 均把路由挂到 app 上，且挂载后的端点可正常访问"""
        app = FastAPI()
        before = len(app.routes)

        register_marketplace_routes(app, env.marketplace)
        register_i18n_routes(app, env.i18n)
        register_tool_routes(app, env.registry)
        assert len(app.routes) > before

        with TestClient(app) as client:
            assert client.get(f"{_PLUGINS_BASE}/categories").status_code == 200
            assert client.get(f"{_I18N_BASE}/languages").status_code == 200
            tools = client.get(f"{_TOOLS_BASE}/categories", headers=_auth())
            assert tools.status_code == 200
            assert len(tools.json()["categories"]) == 9


if __name__ == "__main__":
    pytest.main([__file__, "-v"])