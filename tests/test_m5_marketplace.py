"""
DSH 插件市场测试 (M5)

测试插件市场核心功能：注册、安装、卸载、搜索、评分
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import pytest

# 添加项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import jkos_core.plugins.marketplace as marketplace_mod
import jkos_core.plugins.registry as registry_mod

from jkos_core.plugins.base import (
    AIPlugin,
    DeterministicPlugin,
    PluginBase,
    PluginCategory,
    PluginConfig,
    PluginContext,
    PluginLoader,
    PluginMetadata,
    PluginRegistry as BasePluginRegistry,
    PluginResult,
    PluginStatus,
    PluginType,
)
from jkos_core.plugins.marketplace import (
    PluginMarketplace,
    PluginPackage,
    PluginSource,
)
from jkos_core.plugins.registry import (
    EnhancedPluginRegistry,
    MarketplaceMetadata,
    PluginInfo,
    VisibilityDomain,
)


# ─── 测试插件 ───

class TestPluginImpl(DeterministicPlugin):
    """测试用插件"""
    
    def __init__(self, plugin_id: str = "dsh.test.plugin"):
        self._plugin_id = plugin_id
        super().__init__()
    
    def _get_plugin_id(self) -> str:
        return self._plugin_id
    
    def _get_version(self) -> str:
        return "1.0.0"
    
    def _get_name(self) -> str:
        return "测试插件"
    
    def _get_description(self) -> str:
        return "用于测试的插件"
    
    def _get_category(self) -> PluginCategory:
        return PluginCategory.TEXT
    
    async def initialize(self) -> None:
        self.status = PluginStatus.ACTIVE
    
    async def health_check(self) -> bool:
        return True
    
    async def execute(self, input_data, context: PluginContext) -> PluginResult:
        return PluginResult(success=True, data={"test": "ok"})


# ─── 扩展测试插件（类名不以 Test 开头，避免 pytest 收集告警） ───

class _PluginImpl(DeterministicPlugin):
    """灵活的测试插件：可指定 ID、可模拟清理失败"""

    def __init__(self, plugin_id: str = "dsh.test.impl"):
        self._plugin_id = plugin_id
        self.fail_cleanup = False
        super().__init__()

    def _get_plugin_id(self) -> str:
        return self._plugin_id

    def _get_version(self) -> str:
        return "1.0.0"

    def _get_name(self) -> str:
        return "测试插件"

    def _get_description(self) -> str:
        return "用于测试的插件"

    def _get_category(self) -> PluginCategory:
        return PluginCategory.TEXT

    async def initialize(self) -> None:
        self.status = PluginStatus.ACTIVE

    async def health_check(self) -> bool:
        return True

    async def execute(self, input_data, context: PluginContext) -> PluginResult:
        return PluginResult(success=True, data={"ok": True})

    async def cleanup(self) -> None:
        if self.fail_cleanup:
            raise RuntimeError("cleanup failed")


class _AIPluginImpl(AIPlugin):
    """AI 插件测试实现"""

    def __init__(self, plugin_id: str = "dsh.test.ai"):
        self._plugin_id = plugin_id
        super().__init__()

    def _get_plugin_id(self) -> str:
        return self._plugin_id

    def _get_version(self) -> str:
        return "1.0.0"

    def _get_name(self) -> str:
        return "AI 测试插件"

    def _get_description(self) -> str:
        return "AI 插件实现"

    def _get_category(self) -> PluginCategory:
        return PluginCategory.IMAGE

    async def get_model_client(self) -> str:
        return "mock-client"

    async def initialize(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    async def execute(self, input_data, context: PluginContext) -> PluginResult:
        return PluginResult(success=True, data=None)


# ─── 增强注册表测试 ───

class TestEnhancedPluginRegistry:
    
    def test_register_core_plugin(self):
        """测试注册核心插件"""
        registry = EnhancedPluginRegistry()
        plugin = TestPluginImpl()
        
        registry.register(
            plugin,
            visibility=VisibilityDomain.CORE,
        )
        
        assert registry.get("dsh.test.plugin") is not None
        assert registry.count() == 1
    
    def test_register_industry_plugin(self):
        """测试注册行业插件"""
        registry = EnhancedPluginRegistry()
        plugin = TestPluginImpl()
        
        registry.register(
            plugin,
            visibility=VisibilityDomain.INDUSTRY,
            industry_tags=["winery", "media"],
        )
        
        plugins = registry.list_by_industry("winery")
        assert len(plugins) == 1
    
    def test_register_custom_plugin(self):
        """测试注册自定义插件"""
        registry = EnhancedPluginRegistry()
        plugin = TestPluginImpl()
        
        registry.register(
            plugin,
            visibility=VisibilityDomain.CUSTOM,
            tenant_id="tenant-001",
        )
        
        plugins = registry.list_by_tenant("tenant-001")
        assert len(plugins) == 1
    
    def test_unregister_plugin(self):
        """测试卸载插件"""
        registry = EnhancedPluginRegistry()
        plugin = TestPluginImpl("dsh.test.plugin1")
        
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        assert registry.count() == 1
        
        registry.unregister("dsh.test.plugin1")
        assert registry.count() == 0
    
    def test_list_visible_core(self):
        """测试列出可见插件（核心）"""
        registry = EnhancedPluginRegistry()
        
        # 注册核心插件
        plugin1 = TestPluginImpl("dsh.test.plugin1")
        registry.register(plugin1, visibility=VisibilityDomain.CORE)
        
        # 注册自定义插件
        plugin2 = TestPluginImpl("dsh.test.plugin2")
        registry.register(
            plugin2,
            visibility=VisibilityDomain.CUSTOM,
            tenant_id="tenant-001",
        )
        
        # 核心租户可以看到所有核心插件（不包括自定义插件）
        visible = registry.list_visible()
        assert len(visible) == 2  # 两个插件都可见（因为没传 tenant_id）
        
        # 自定义租户只能看到自己的插件
        visible_tenant = registry.list_visible(tenant_id="tenant-001")
        assert len(visible_tenant) == 2  # 核心 + 自定义
    
    def test_list_by_visibility(self):
        """测试按可见域列出插件"""
        registry = EnhancedPluginRegistry()
        
        plugin1 = TestPluginImpl("dsh.test.plugin1")
        registry.register(plugin1, visibility=VisibilityDomain.CORE)
        
        plugin2 = TestPluginImpl("dsh.test.plugin2")
        registry.register(
            plugin2,
            visibility=VisibilityDomain.CUSTOM,
            tenant_id="tenant-001",
        )
        
        core_plugins = registry.list_by_visibility(VisibilityDomain.CORE)
        assert len(core_plugins) == 1
        
        custom_plugins = registry.list_by_visibility(VisibilityDomain.CUSTOM)
        assert len(custom_plugins) == 1
    
    def test_search_plugins(self):
        """测试搜索插件"""
        registry = EnhancedPluginRegistry()
        plugin = TestPluginImpl("dsh.test.plugin1")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        results = registry.search("test")
        assert len(results) == 1
        
        results = registry.search("nonexistent")
        assert len(results) == 0
    
    def test_rating(self):
        """测试评分功能"""
        registry = EnhancedPluginRegistry()
        plugin = TestPluginImpl("dsh.test.plugin1")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        # 添加评分
        assert registry.add_rating("dsh.test.plugin1", 5.0) is True
        assert registry.add_rating("dsh.test.plugin1", 4.0) is True
        
        info = registry.get_info("dsh.test.plugin1")
        assert info.marketplace.rating_count == 2
        assert info.marketplace.rating == 4.5
    
    def test_downloads(self):
        """测试下载次数"""
        registry = EnhancedPluginRegistry()
        plugin = TestPluginImpl("dsh.test.plugin1")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        assert registry.increment_downloads("dsh.test.plugin1") is True
        
        info = registry.get_info("dsh.test.plugin1")
        assert info.marketplace.downloads == 1
    
    def test_featured(self):
        """测试推荐功能"""
        registry = EnhancedPluginRegistry()
        plugin = TestPluginImpl("dsh.test.plugin1")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        registry.update_marketplace_info("dsh.test.plugin1", featured=True)
        
        featured = registry.get_featured()
        assert len(featured) == 1
    
    def test_get_categories(self):
        """测试获取分类"""
        registry = EnhancedPluginRegistry()
        categories = registry.get_categories()
        
        assert len(categories) > 0
        assert "text" in categories


# ─── 插件市场测试 ───

class TestPluginMarketplace:
    
    @pytest.fixture
    def marketplace(self):
        """创建插件市场实例"""
        registry = EnhancedPluginRegistry()
        return PluginMarketplace(registry=registry)
    
    def test_list_local(self, marketplace):
        """测试列出本地插件"""
        plugin = TestPluginImpl()
        marketplace.registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        plugins = marketplace.list_local()
        assert len(plugins) == 1
        assert plugins[0]["plugin_id"] == "dsh.test.plugin"
    
    def test_search(self, marketplace):
        """测试搜索"""
        plugin = TestPluginImpl()
        marketplace.registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        results = marketplace.search("test")
        assert len(results) == 1
    
    def test_get_details(self, marketplace):
        """测试获取详情"""
        plugin = TestPluginImpl()
        marketplace.registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        details = marketplace.get_details("dsh.test.plugin")
        assert details is not None
        assert details["plugin_id"] == "dsh.test.plugin"
    
    def test_rate(self, marketplace):
        """测试评分"""
        plugin = TestPluginImpl()
        marketplace.registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        assert marketplace.rate("dsh.test.plugin", 5.0) is True
        
        details = marketplace.get_details("dsh.test.plugin")
        assert details["rating"] == 5.0
    
    def test_get_statistics(self, marketplace):
        """测试统计"""
        plugin = TestPluginImpl()
        marketplace.registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        stats = marketplace.get_statistics()
        assert stats["total_plugins"] == 1
        assert stats["core_plugins"] == 1
    
    def test_get_categories(self, marketplace):
        """测试分类统计"""
        plugin = TestPluginImpl()
        marketplace.registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        categories = marketplace.get_categories()
        assert len(categories) > 0
    
    def test_featured(self, marketplace):
        """测试推荐"""
        plugin = TestPluginImpl()
        marketplace.registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        marketplace.set_featured("dsh.test.plugin", True)
        featured = marketplace.get_featured()
        assert len(featured) == 1


# ─── 可见域测试 ───

class TestVisibilityDomain:
    
    def test_core_visibility(self):
        """测试核心可见域"""
        registry = EnhancedPluginRegistry()
        
        plugin = TestPluginImpl()
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        
        # 核心插件对所有租户可见
        visible = registry.list_visible(tenant_id="tenant-001")
        assert len(visible) == 1
        
        visible = registry.list_visible(tenant_id="tenant-002")
        assert len(visible) == 1
    
    def test_industry_visibility(self):
        """测试行业可见域"""
        registry = EnhancedPluginRegistry()
        
        plugin = TestPluginImpl()
        registry.register(
            plugin,
            visibility=VisibilityDomain.INDUSTRY,
            industry_tags=["winery"],
        )
        
        # 行业插件只对匹配行业的租户可见
        visible = registry.list_visible(industry="winery")
        assert len(visible) == 1
        
        visible = registry.list_visible(industry="media")
        assert len(visible) == 0
    
    def test_custom_visibility(self):
        """测试自定义可见域"""
        registry = EnhancedPluginRegistry()
        
        plugin = TestPluginImpl()
        registry.register(
            plugin,
            visibility=VisibilityDomain.CUSTOM,
            tenant_id="tenant-001",
        )
        
        # 自定义插件只对创建租户可见
        visible = registry.list_visible(tenant_id="tenant-001")
        assert len(visible) == 1
        
        visible = registry.list_visible(tenant_id="tenant-002")
        assert len(visible) == 0


# ─── 插件数据类 / 基类辅助方法测试（Task 6.3） ───

class TestPluginDataClasses:
    """PluginConfig / PluginContext / PluginResult 序列化"""

    def test_plugin_config_defaults(self):
        cfg = PluginConfig()
        assert cfg.enabled is True
        assert cfg.config == {}
        assert cfg.rate_limit == 100
        assert cfg.timeout == 300
        assert cfg.retries == 3

    def test_plugin_result_to_dict_and_json(self):
        result = PluginResult(
            success=True,
            data={"key": "value"},
            duration_ms=12.5,
            metadata={"trace": "abc"},
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["data"] == {"key": "value"}
        assert d["duration_ms"] == 12.5
        assert d["metadata"] == {"trace": "abc"}
        assert isinstance(result.to_json(), str)
        assert '"key": "value"' in result.to_json()


class TestPluginBaseAux:
    """PluginBase 具体方法：validate_config / get_config / set_config / 计时与日志"""

    def test_validate_config_and_config_management(self):
        plugin = _PluginImpl("dsh.test.aux")
        assert asyncio.run(plugin.validate_config()) is True
        assert plugin.get_config("missing", "fallback") == "fallback"
        plugin.set_config("key", 42)
        assert plugin.get_config("key") == 42
        elapsed = plugin._measure_duration(time.time())
        assert elapsed >= 0.0
        ctx = PluginContext(request_id="r1")
        result = PluginResult(success=True, data=None)
        plugin._log_execution(ctx, result)


class TestPluginBaseAbstractStubs:
    """PluginBase 抽象成员：临时清空 __abstractmethods__ 绕过抽象检查（仅测试用）"""

    def test_stub_members(self):
        saved = PluginBase.__abstractmethods__
        PluginBase.__abstractmethods__ = frozenset()
        try:
            plugin = object.__new__(PluginBase)
            assert plugin.metadata is None
            assert asyncio.run(plugin.initialize()) is None
            assert asyncio.run(plugin.health_check()) is None
            assert asyncio.run(plugin.execute(None, None)) is None
        finally:
            PluginBase.__abstractmethods__ = saved


class TestAIPluginAbstractStubs:
    """AIPlugin 抽象基类体：临时清空 __abstractmethods__ 覆盖基类默认实现（仅测试用）"""

    def test_stub_abstract_base(self):
        saved = AIPlugin.__abstractmethods__
        AIPlugin.__abstractmethods__ = frozenset()
        try:
            plugin = object.__new__(AIPlugin)
            assert asyncio.run(plugin.get_model_client()) is None
        finally:
            AIPlugin.__abstractmethods__ = saved


class TestAIPluginBase:
    """AIPlugin 元数据与模型客户端"""

    def test_metadata_and_model_client(self):
        plugin = _AIPluginImpl("dsh.test.ai")
        meta = plugin.metadata
        assert meta.id == "dsh.test.ai"
        assert meta.plugin_type == PluginType.AI_POWERED
        assert meta.category == PluginCategory.IMAGE
        assert asyncio.run(plugin.get_model_client()) == "mock-client"


class TestBasePluginRegistry:
    """基础 PluginRegistry（无可见域）"""

    def test_register_unregister_and_queries(self):
        registry = BasePluginRegistry()
        p1 = _PluginImpl("dsh.test.r1")
        p2 = _PluginImpl("dsh.test.r2")
        assert registry.count() == 0
        registry.register(p1)
        with pytest.raises(ValueError):
            registry.register(p1)
        registry.register(p2)
        assert registry.get("dsh.test.r1") is p1
        assert registry.get("missing") is None
        assert registry.get_by_category(PluginCategory.TEXT) == [p1, p2]
        assert registry.get_by_category(PluginCategory.AUDIO) == []
        assert registry.all() == {"dsh.test.r1": p1, "dsh.test.r2": p2}
        assert set(registry.list_ids()) == {"dsh.test.r1", "dsh.test.r2"}
        registry.unregister("dsh.test.r2")
        assert registry.count() == 1
        registry.unregister("missing")


class TestPluginLoaderOps:
    """PluginLoader 目录加载与卸载"""

    def test_load_from_directory_missing(self, tmp_path):
        loader = PluginLoader(BasePluginRegistry())
        assert loader.load_from_directory(str(tmp_path / "missing")) == 0

    def test_load_from_directory_success_and_skip(self, tmp_path):
        loader = PluginLoader(BasePluginRegistry())
        good = tmp_path / "good"
        good.mkdir()
        (good / "plugin.yaml").write_text("id: dsh.test.loaded", encoding="utf-8")
        (tmp_path / "noyaml").mkdir()
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")
        assert loader.load_from_directory(str(tmp_path)) == 1

    def test_load_from_directory_exception(self, tmp_path, monkeypatch):
        loader = PluginLoader(BasePluginRegistry())
        (tmp_path / "bad").mkdir()

        def _boom(plugin_dir):
            raise RuntimeError("boom")

        monkeypatch.setattr(loader, "_load_plugin_from_dir", _boom)
        assert loader.load_from_directory(str(tmp_path)) == 0

    def test_unload(self):
        registry = BasePluginRegistry()
        loader = PluginLoader(registry)
        plugin = _PluginImpl("dsh.test.l1")
        registry.register(plugin)
        assert loader.unload("dsh.test.l1") is True
        assert registry.get("dsh.test.l1") is None
        assert loader.unload("dsh.test.l1") is False


class TestEnhancedRegistryBranches:
    """EnhancedPluginRegistry 异常分支与索引维护（Task 6.3）"""

    def test_register_duplicate_raises(self):
        registry = EnhancedPluginRegistry()
        plugin = _PluginImpl("dsh.test.dup")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        with pytest.raises(ValueError):
            registry.register(plugin, visibility=VisibilityDomain.CORE)

    def test_unregister_missing(self):
        registry = EnhancedPluginRegistry()
        assert registry.unregister("ghost") is False

    def test_unregister_cleanup_exception_warns(self):
        registry = EnhancedPluginRegistry()
        plugin = _PluginImpl("dsh.test.cleanup")
        plugin.fail_cleanup = True
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        # 清理异常被捕获并记录 WARNING，卸载仍成功
        assert registry.unregister("dsh.test.cleanup") is True

    def test_industry_and_tenant_index_removal(self):
        registry = EnhancedPluginRegistry()
        p1 = _PluginImpl("dsh.test.i1")
        registry.register(
            p1,
            visibility=VisibilityDomain.INDUSTRY,
            industry_tags=["winery"],
        )
        assert len(registry.list_by_industry("winery")) == 1
        registry.unregister("dsh.test.i1")
        assert registry.list_by_industry("winery") == []

        p2 = _PluginImpl("dsh.test.t1")
        registry.register(
            p2,
            visibility=VisibilityDomain.CUSTOM,
            tenant_id="tenant-001",
        )
        assert len(registry.list_by_tenant("tenant-001")) == 1
        registry.unregister("dsh.test.t1")
        assert registry.list_by_tenant("tenant-001") == []

    def test_list_visible_filters(self):
        registry = EnhancedPluginRegistry()
        plugin = _PluginImpl("dsh.test.vis")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        assert registry.list_visible(industry="winery") == []
        assert registry.list_visible(category=PluginCategory.AUDIO) == []
        assert registry.list_visible(industry="winery", category=PluginCategory.AUDIO) == []

    def test_list_by_category_and_all_ids(self):
        registry = EnhancedPluginRegistry()
        plugin = _PluginImpl("dsh.test.cat")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        assert registry.list_by_category(PluginCategory.TEXT) == [plugin]
        assert registry.list_by_category(PluginCategory.AUDIO) == []
        assert registry.all() == {"dsh.test.cat": plugin}
        assert registry.list_ids() == ["dsh.test.cat"]

    def test_marketplace_info_missing_branches(self):
        registry = EnhancedPluginRegistry()
        assert registry.get_marketplace_info("ghost") is None
        assert registry.update_marketplace_info("ghost", featured=True) is False
        assert registry.increment_downloads("ghost") is False
        assert registry.get_verified() == []

    def test_marketplace_info_existing(self):
        registry = EnhancedPluginRegistry()
        plugin = _PluginImpl("dsh.test.info")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        info = registry.get_marketplace_info("dsh.test.info")
        assert info is not None
        assert info.rating == 0.0
        assert info.downloads == 0

    def test_add_rating_out_of_range_and_missing(self):
        registry = EnhancedPluginRegistry()
        plugin = _PluginImpl("dsh.test.rate")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        assert registry.add_rating("dsh.test.rate", 0.5) is False
        assert registry.add_rating("dsh.test.rate", 5.5) is False
        assert registry.add_rating("ghost", 4.0) is False

    def test_verified_plugin(self):
        registry = EnhancedPluginRegistry()
        plugin = _PluginImpl("dsh.test.ver")
        registry.register(plugin, visibility=VisibilityDomain.CORE)
        assert registry.update_marketplace_info("dsh.test.ver", verified=True) is True
        assert registry.get_verified() == [plugin]


class TestRegistryGlobals:
    """模块级 get_registry / set_registry"""

    def test_set_and_get_global(self):
        saved = registry_mod._global_registry
        try:
            new_reg = EnhancedPluginRegistry()
            registry_mod.set_registry(new_reg)
            assert registry_mod.get_registry() is new_reg
        finally:
            registry_mod._global_registry = saved

    def test_get_registry_lazy_init(self):
        saved = registry_mod._global_registry
        try:
            registry_mod._global_registry = None
            reg = registry_mod.get_registry()
            assert isinstance(reg, EnhancedPluginRegistry)
            assert registry_mod._global_registry is reg
        finally:
            registry_mod._global_registry = saved


class TestMarketplaceSourcesAndRemote:
    """插件源增删与远程列表（Task 6.3）"""

    @pytest.fixture
    def mp(self, tmp_path):
        return PluginMarketplace(registry=EnhancedPluginRegistry(), plugins_dir=str(tmp_path))

    def test_add_and_remove_source(self, mp):
        source = PluginSource(name="alpha", url="https://example.com/x", description="测试源")
        mp.add_source(source)
        assert mp.remove_source("alpha") is True
        assert mp.remove_source("alpha") is False

    def test_list_remote_skips_disabled_and_catches_errors(self, mp, monkeypatch):
        pkg = PluginPackage(
            name="demo",
            version="1.0.0",
            plugin_id="dsh.test.remote",
            description="远程插件",
            category=PluginCategory.TEXT,
            author="DSH",
            license="Apache-2.0",
            dependencies=[],
            config_schema={},
            visibility=VisibilityDomain.INDUSTRY,
            industry_tags=["media"],
            download_url="http://x/pkg.zip",
            checksum="abc",
            size=1024,
        )
        called = {}

        def _fake_fetch(source):
            called[source.name] = True
            if source.name == "broken":
                raise RuntimeError("fetch failed")
            return [pkg]

        monkeypatch.setattr(mp, "_fetch_from_source", _fake_fetch)
        mp.add_source(PluginSource(name="good", url="http://g", description="ok"))
        mp.add_source(PluginSource(name="broken", url="http://b", description="bad"))
        mp.add_source(PluginSource(name="off", url="http://o", description="disabled", enabled=False))
        remote = mp.list_remote()
        assert len(remote) == 1
        assert remote[0].plugin_id == "dsh.test.remote"
        assert "off" not in called
        assert "broken" in called

    def test_fetch_from_source_returns_empty(self, mp):
        source = PluginSource(name="s", url="http://s", description="d")
        assert mp._fetch_from_source(source) == []


class TestMarketplaceInstallUninstall:
    """插件安装 / 卸载 / 更新分支（Task 6.3）"""

    @pytest.fixture
    def mp(self, tmp_path):
        return PluginMarketplace(registry=EnhancedPluginRegistry(), plugins_dir=str(tmp_path))

    def test_install_already_registered(self, mp):
        plugin = _PluginImpl("dsh.test.exist")
        mp.registry.register(plugin, visibility=VisibilityDomain.CORE)
        assert mp.install("dsh.test.exist", tenant_id="tenant-001") is False

    def test_install_custom_requires_tenant(self, mp):
        assert mp.install("dsh.test.notenant") is False

    def test_install_success_writes_plugin_yaml(self, mp, tmp_path):
        assert mp.install("dsh.test.new1", tenant_id="tenant-001") is True
        yaml_path = tmp_path / "dsh.test.new1" / "plugin.yaml"
        assert yaml_path.exists()
        content = json.loads(yaml_path.read_text(encoding="utf-8"))
        assert content["id"] == "dsh.test.new1"
        assert content["tenant_id"] == "tenant-001"
        assert content["visibility"] == "custom"

    def test_install_exception_returns_false(self, mp, tmp_path):
        # 用同名文件占位，mkdir 抛 FileExistsError 被捕获
        (tmp_path / "dsh.test.boom").write_text("occupied", encoding="utf-8")
        assert mp.install("dsh.test.boom", tenant_id="tenant-001") is False

    def test_uninstall_not_installed(self, mp):
        assert mp.uninstall("ghost") is False

    def test_uninstall_tenant_mismatch(self, mp):
        plugin = _PluginImpl("dsh.test.tenant")
        mp.registry.register(
            plugin,
            visibility=VisibilityDomain.CUSTOM,
            tenant_id="tenant-a",
        )
        assert mp.uninstall("dsh.test.tenant", tenant_id="tenant-b") is False

    def test_uninstall_success_cleans_directory(self, mp, tmp_path):
        plugin = _PluginImpl("dsh.test.un")
        mp.registry.register(plugin, visibility=VisibilityDomain.CORE)
        yaml_dir = tmp_path / "dsh.test.un"
        yaml_dir.mkdir()
        (yaml_dir / "plugin.yaml").write_text("id: dsh.test.un", encoding="utf-8")
        assert mp.uninstall("dsh.test.un") is True
        assert not yaml_dir.exists()
        assert mp.registry.get("dsh.test.un") is None

    def test_update_returns_true(self, mp):
        assert mp.update("any.plugin.id", "2.0.0") is True

    def test_get_details_missing(self, mp):
        assert mp.get_details("ghost") is None

    def test_set_verified(self, mp):
        plugin = _PluginImpl("dsh.test.ver")
        mp.registry.register(plugin, visibility=VisibilityDomain.CORE)
        assert mp.set_verified("dsh.test.ver", True) is True
        assert mp.registry.get_verified() == [plugin]
        assert mp.set_verified("ghost", True) is False


class TestMarketplaceGlobals:
    """模块级 get_marketplace / set_marketplace"""

    def test_get_and_set_marketplace_global(self):
        saved = marketplace_mod._global_marketplace
        try:
            marketplace_mod._global_marketplace = None
            mp = marketplace_mod.get_marketplace()
            assert isinstance(mp, PluginMarketplace)
            assert marketplace_mod._global_marketplace is mp
            other = PluginMarketplace(registry=EnhancedPluginRegistry(), plugins_dir=".")
            marketplace_mod.set_marketplace(other)
            assert marketplace_mod.get_marketplace() is other
        finally:
            marketplace_mod._global_marketplace = saved


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
