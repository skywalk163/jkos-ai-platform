"""M14: ToolRegistry 生命周期与版本管理单测

补齐 dsh_core/mcp/registry.py 中「注册 / 注销 / 版本管理 / 全局注册表」一族的缺失行：
L137 / L162 / L175 / L187-192 / L235-260 / L264-265 / L269-285 / L289-291 / L497。

约定：
- 每个用例自建独立的 ToolRegistry()，不复用长生命周期实例，避免用例间状态串扰。
- 唯一触碰全局状态的是 set_registry 用例，由模块级 autouse fixture 负责快照与恢复，
  避免污染依赖全局注册表的其它模块（如 dsh_core/mcp/server.py）。
"""

from datetime import datetime

import pytest

import dsh_core.mcp.registry as registry_mod
from dsh_core.mcp.registry import (
    ToolRegistry,
    ToolVisibility,
    get_registry,
    set_registry,
)
from dsh_core.mcp.server import MCPTool


def _make_tool(name: str, desc: str = "tool") -> MCPTool:
    """构造最小可用的 MCPTool"""
    return MCPTool(name=name, description=desc, input_schema={"type": "object"})


class _PlainTool:
    """不具备 model_copy 的鸭子类型工具

    publish_version 对非 pydantic 工具对象走 else 回退分支（L249「new_tool = current」）。
    该分支的语义是"无法拷贝时复用原实例"，用真实 MCPTool 永远走不到，
    因此这里用一个仅含快照所需四个属性的普通对象来验证回退语义。
    """

    def __init__(self, name: str, description: str = "plain") -> None:
        self.name = name
        self.description = description
        self.input_schema = {"type": "object"}
        self.output_schema = {}


@pytest.fixture(scope="module", autouse=True)
def _restore_global_registry():
    """set_registry 会改写全局 _global_registry；模块结束后恢复原值"""
    old = registry_mod._global_registry
    yield
    registry_mod._global_registry = old


class TestRegister:
    """注册路径：重名拒绝 / 行业索引填充"""

    def test_register_duplicate_raises(self):
        """同名工具重复注册必须抛 ValueError，且错误信息包含工具名（L137）"""
        r = ToolRegistry()
        r.register(_make_tool("dup"))
        with pytest.raises(ValueError, match="dup"):
            r.register(_make_tool("dup"))

    def test_register_with_industry_tags_fills_industry_index(self):
        """带行业标签注册时，每个标签都要在行业索引中登记该工具（L162）"""
        r = ToolRegistry()
        r.register(
            _make_tool("ind_tool"),
            visibility=ToolVisibility.INDUSTRY,
            industry_tags=["finance", "bank"],
        )
        assert "ind_tool" in r._industry_index["finance"]
        assert "ind_tool" in r._industry_index["bank"]


class TestUnregister:
    """注销路径：不存在返回 False / 行业索引与租户索引清理"""

    def test_unregister_missing_returns_false(self):
        """注销不存在的工具返回 False（L175）"""
        r = ToolRegistry()
        assert r.unregister("not_exist") is False

    def test_unregister_clears_industry_index(self):
        """注销带行业标签的工具后，行业索引中不再包含该工具（L187-188）

        实现只移除元素、保留空键（L186-188 无删除键动作），故断言值为空列表。
        """
        r = ToolRegistry()
        r.register(
            _make_tool("ind_tool"),
            visibility=ToolVisibility.INDUSTRY,
            industry_tags=["finance"],
        )
        assert "ind_tool" in r._industry_index["finance"]

        assert r.unregister("ind_tool") is True
        assert "ind_tool" not in r._industry_index["finance"]
        assert r._industry_index["finance"] == []

    def test_unregister_clears_tenant_index(self):
        """注销带 tenant_id 的 CUSTOM 工具后，租户索引中不再包含该工具（L191-192）

        与行业索引一致：实现保留空键而非删除键，故断言值为空列表。
        """
        r = ToolRegistry()
        r.register(
            _make_tool("ten_tool"),
            visibility=ToolVisibility.CUSTOM,
            tenant_id="t1",
        )
        assert "ten_tool" in r._tenant_index["t1"]

        assert r.unregister("ten_tool") is True
        assert "ten_tool" not in r._tenant_index["t1"]
        assert r._tenant_index["t1"] == []


class TestPublishVersion:
    """publish_version 全流程：不存在 / 成功发布 / 版本历史 / 无 model_copy 回退"""

    def test_publish_version_missing_tool_returns_false(self):
        """对未注册工具发布版本返回 False（L235-237）"""
        r = ToolRegistry()
        assert r.publish_version("nope", "2.0.0") is False

    def test_publish_version_updates_tool_and_history(self):
        """发布成功：走 model_copy 生成新实例、字段更新生效、版本历史追加、时间戳刷新（L239-260）"""
        r = ToolRegistry()
        original = _make_tool("pv", desc="old")
        r.register(original)
        before = r.get_info("pv").marketplace.updated_at

        # not_a_field 不存在于 MCPTool，应被 hasattr 过滤（L241-243）而不报错
        assert (
            r.publish_version(
                "pv", "2.0.0", changelog="fix", description="new", not_a_field="x"
            )
            is True
        )

        info = r.get_info("pv")
        assert info.tool is not original  # 走了 model_copy 分支，产生新实例
        assert info.tool.name == "pv"
        assert info.tool.description == "new"
        assert not hasattr(info.tool, "not_a_field")  # 未知字段被过滤掉
        assert info.version == "2.0.0"

        # 版本历史：初始 1.0.0 + 本次 2.0.0
        versions = r.get_versions("pv")
        assert len(versions) == 2
        assert versions[0]["version"] == "1.0.0"
        assert versions[-1]["version"] == "2.0.0"
        assert versions[-1]["changelog"] == "fix"
        assert versions[-1]["name"] == "pv"

        after = r.get_info("pv").marketplace.updated_at
        assert isinstance(after, datetime)
        assert after >= before

    def test_publish_version_without_model_copy_reuses_instance(self):
        """工具对象没有 model_copy 时走回退分支，复用原实例（L249）"""
        r = ToolRegistry()
        plain = _PlainTool("plain_pv")
        r.register(plain)

        assert r.publish_version("plain_pv", "2.0.0", description="ignored") is True

        info = r.get_info("plain_pv")
        assert info.tool is plain  # 无法拷贝 -> 原实例
        assert info.tool.description == "plain"  # 回退分支下 changes 不生效
        assert info.version == "2.0.0"
        assert r.get_versions("plain_pv")[-1]["version"] == "2.0.0"


class TestGetVersions:
    """get_versions 三元分支"""

    def test_get_versions_registered_and_missing(self):
        """已注册返回历史副本且首项为 1.0.0；未注册返回空列表（L264-265）"""
        r = ToolRegistry()
        assert r.get_versions("nope") == []

        r.register(_make_tool("gv"))
        versions = r.get_versions("gv")
        assert isinstance(versions, list)
        assert len(versions) == 1
        assert versions[0]["version"] == "1.0.0"

        # 返回的是副本，外部修改不影响内部历史
        versions.append({"version": "fake"})
        assert len(r.get_versions("gv")) == 1


class TestSetActiveVersion:
    """set_active_version 全流程：不存在 / 版本缺失 / 成功切换并重建工具"""

    def test_set_active_version_missing_tool_returns_false(self):
        """对未注册工具切换版本返回 False（L269-271）"""
        r = ToolRegistry()
        assert r.set_active_version("nope", "1.0.0") is False

    def test_set_active_version_unknown_version_returns_false(self):
        """版本号不在历史中时返回 False（L273-279）"""
        r = ToolRegistry()
        r.register(_make_tool("sv"))
        assert r.set_active_version("sv", "9.9.9") is False
        assert r.get_info("sv").version == "1.0.0"  # 激活版本保持不变

    def test_set_active_version_rebuilds_tool_from_snapshot(self):
        """成功切换：从快照重建 MCPTool，描述与版本号同步更新（L281-285 / L289-291）"""
        r = ToolRegistry()
        r.register(_make_tool("sv", desc="v1"))
        assert r.publish_version("sv", "2.0.0", description="v2") is True

        assert r.set_active_version("sv", "2.0.0") is True
        info = r.get_info("sv")
        assert isinstance(info.tool, MCPTool)  # 由 _rebuild_tool 惰性导入重建
        assert info.tool.name == "sv"
        assert info.tool.description == "v2"
        assert info.version == "2.0.0"

        # 回切到 1.0.0 同样从快照重建
        assert r.set_active_version("sv", "1.0.0") is True
        assert r.get_info("sv").tool.description == "v1"
        assert r.get_info("sv").version == "1.0.0"


class TestGlobalRegistry:
    """全局注册表的设置与读取（模块级 fixture 负责恢复原值）"""

    def test_set_registry_replaces_global_and_get_registry_returns_it(self):
        """set_registry 直接替换全局实例，get_registry 返回该实例（L497）"""
        new_reg = ToolRegistry()
        new_reg.register(_make_tool("in_global"))

        set_registry(new_reg)

        assert registry_mod._global_registry is new_reg
        assert get_registry() is new_reg
        assert get_registry().get("in_global") is not None

    def test_get_registry_auto_creates_when_global_is_none(self):
        """get_registry 在全局未初始化(None)时自动创建新实例（L489-490）"""
        registry_mod._global_registry = None

        fresh = get_registry()

        assert isinstance(fresh, ToolRegistry)
        assert registry_mod._global_registry is fresh

    def test_get_registry_auto_creates_when_global_is_none(self):
        """get_registry 在全局未初始化(None)时自动创建新实例（L489-490）"""
        registry_mod._global_registry = None

        fresh = get_registry()

        assert isinstance(fresh, ToolRegistry)
        assert registry_mod._global_registry is fresh