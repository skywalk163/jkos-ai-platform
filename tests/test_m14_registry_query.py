"""M14: ToolRegistry 查询与市场元数据单测

补齐 `jkos_core/mcp/registry.py` 中「查询 / 搜索 / 市场元数据」一族的缺失覆盖率：
L302-303 / L307 / L325-346 / L350 / L358 / L366 / L374 / L382 / L390 /
L394-405 / L411-412 / L420-429 / L433-438 / L442-454 / L458 / L466 / L474 / L478。

约定：
- 一律使用自建的 `ToolRegistry()` 实例，不触碰全局 `_global_registry` / `set_registry`，
  因此本文件与「注册 / 注销 / 版本管理」测试天然无副作用冲突。
- `list_visible` 的可见性语义以源码 L325-346 为准（尤其 CUSTOM 匿名可见的反直觉行为），
  相关断言已在 docstring 中写明理由。
"""

from jkos_core.mcp.registry import (
    ToolCategory,
    ToolMarketplaceMetadata,
    ToolRegistry,
    ToolVisibility,
)
from jkos_core.mcp.server import MCPTool


def _make_tool(name: str, desc: str = "tool") -> MCPTool:
    """构造最小可用的 MCPTool 实例"""
    return MCPTool(name=name, description=desc, input_schema={"type": "object"})


def _fresh_registry() -> ToolRegistry:
    """构造样例数据集：core / industry(finance) / custom(tenant t1) 各一个"""
    r = ToolRegistry()
    r.register(_make_tool("core_tool"), visibility=ToolVisibility.CORE)
    r.register(
        _make_tool("fin_tool"),
        visibility=ToolVisibility.INDUSTRY,
        industry_tags=["finance"],
    )
    r.register(_make_tool("ten_tool"), visibility=ToolVisibility.CUSTOM, tenant_id="t1")
    return r


def _names(tools) -> set:
    """把工具列表归一化为名字集合，便于断言"""
    return {t.name for t in tools}


class TestGet:
    """L302-303 / L307：get 与 get_info 的命中与未命中分支"""

    def test_get_hit_and_miss(self):
        """get：已注册返回同名 MCPTool 实例，未注册返回 None"""
        r = ToolRegistry()
        tool = _make_tool("g")
        r.register(tool)

        assert r.get("g") is tool
        assert r.get("g").name == "g"
        assert r.get("nope") is None

    def test_get_info_hit_and_miss(self):
        """get_info：已注册返回 ToolInfo 且 .tool.name 正确，未注册返回 None"""
        r = ToolRegistry()
        r.register(_make_tool("g"))

        info = r.get_info("g")
        assert info is not None
        assert info.tool.name == "g"
        assert info.visibility == ToolVisibility.CORE
        assert r.get_info("nope") is None


class TestListVisible:
    """L325-346：可见性 / 行业 / 分类三重过滤"""

    def test_tenant_filter_for_custom_tools(self):
        """租户过滤：CUSTOM 工具对同租户可见、对异租户隐藏、对匿名可见

        匿名可见是源码 L330 `if tenant_id and ...` 的反直觉结果：
        tenant_id 为 None 时整个条件为假，CUSTOM 工具不会被 continue 掉。
        """
        r = _fresh_registry()

        assert "ten_tool" in _names(r.list_visible(tenant_id="t1"))
        assert "ten_tool" not in _names(r.list_visible(tenant_id="t2"))
        assert "ten_tool" in _names(r.list_visible(tenant_id=None))

    def test_industry_filter_for_industry_tools(self):
        """行业过滤：INDUSTRY 工具仅在传入匹配标签时可见

        industry="health" 时 fin_tool 在 L333 被 continue；
        industry="finance" 时 fin_tool 命中 L337 放行。
        """
        r = _fresh_registry()

        assert "fin_tool" in _names(r.list_visible(industry="finance"))
        assert "fin_tool" not in _names(r.list_visible(industry="health"))

    def test_industry_filter_applies_to_all_domains(self):
        """行业过滤附作用于所有可见域（L337）：无行业标签的工具被剔除"""
        r = _fresh_registry()

        visible = r.list_visible(industry="finance")
        assert "core_tool" not in _names(visible)  # 无标签 -> L337/L338 生效
        assert _names(visible) == {"fin_tool"}

    def test_category_filter(self):
        """分类过滤（L341-342）：仅返回指定分类的工具"""
        r = _fresh_registry()
        text_tool = _make_tool("text_tool")
        r.register(text_tool, category=ToolCategory.TEXT)

        assert _names(r.list_visible(category=ToolCategory.TEXT)) == {"text_tool"}
        assert "text_tool" not in _names(r.list_visible(category=ToolCategory.AUDIO))

    def test_core_tool_always_visible(self):
        """CORE 工具普适可见：任意租户 / 不传分类时都在列表中"""
        r = _fresh_registry()

        assert "core_tool" in _names(r.list_visible())
        assert "core_tool" in _names(r.list_visible(tenant_id="t1"))
        assert "core_tool" in _names(r.list_visible(tenant_id="t2"))
        assert "core_tool" in _names(r.list_visible(category=ToolCategory.CUSTOM))


class TestListBy:
    """L350 / L358 / L366 / L374：各类索引查询"""

    def test_list_by_category(self):
        """list_by_category：命中分类返回工具，未命中分类返回空列表"""
        r = _fresh_registry()
        r.register(_make_tool("text_tool"), category=ToolCategory.TEXT)

        assert _names(r.list_by_category(ToolCategory.TEXT)) == {"text_tool"}
        assert r.list_by_category(ToolCategory.AUDIO) == []

    def test_list_by_visibility(self):
        """list_by_visibility：三个可见域各自只含对应工具"""
        r = _fresh_registry()

        assert _names(r.list_by_visibility(ToolVisibility.CORE)) == {"core_tool"}
        assert _names(r.list_by_visibility(ToolVisibility.INDUSTRY)) == {"fin_tool"}
        assert _names(r.list_by_visibility(ToolVisibility.CUSTOM)) == {"ten_tool"}

        empty = ToolRegistry()
        assert empty.list_by_visibility(ToolVisibility.CORE) == []

    def test_list_by_industry(self):
        """list_by_industry：命中标签返回工具，未命中标签返回空列表"""
        r = _fresh_registry()

        assert _names(r.list_by_industry("finance")) == {"fin_tool"}
        assert r.list_by_industry("health") == []

    def test_list_by_tenant(self):
        """list_by_tenant：返回该租户的自定义工具，其它租户为空"""
        r = _fresh_registry()

        assert _names(r.list_by_tenant("t1")) == {"ten_tool"}
        assert r.list_by_tenant("t2") == []


class TestAllAndCount:
    """L382 / L386 / L390：all / list_ids / count"""

    def test_all_returns_name_to_tool_mapping(self):
        """all：返回 dict，key 为工具名，value 为 MCPTool"""
        r = ToolRegistry()
        t1 = _make_tool("a")
        t2 = _make_tool("b")
        r.register(t1)
        r.register(t2)

        result = r.all()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"a", "b"}
        assert result["a"] is t1
        assert result["b"] is t2
        assert len(result) == 2

    def test_count_empty_and_filled(self):
        """count：空注册表为 0，注册 3 个后为 3"""
        r = ToolRegistry()
        assert r.count() == 0

        r.register(_make_tool("a"))
        r.register(_make_tool("b"))
        r.register(_make_tool("c"))
        assert r.count() == 3

    def test_list_ids_returns_registered_names(self):
        """list_ids：返回所有已注册工具名列表（L386）"""
        r = ToolRegistry()
        assert r.list_ids() == []

        r.register(_make_tool("x"))
        r.register(_make_tool("y"))
        assert sorted(r.list_ids()) == ["x", "y"]

    def test_list_ids_returns_registered_names(self):
        """list_ids：返回所有已注册工具名列表（L386）"""
        r = ToolRegistry()
        assert r.list_ids() == []

        r.register(_make_tool("x"))
        r.register(_make_tool("y"))
        assert sorted(r.list_ids()) == ["x", "y"]


class TestSearch:
    """L394-405：search 覆盖 name / description / marketplace.tags，均小写化"""

    def test_search_by_name_case_insensitive(self):
        """按名称命中，且大小写不敏感"""
        r = _fresh_registry()

        assert "core_tool" in _names(r.search("core"))
        assert "core_tool" in _names(r.search("CORE"))

    def test_search_by_description(self):
        """按描述命中"""
        r = ToolRegistry()
        r.register(_make_tool("billing_tool", "生成 billing 报表"))

        assert "billing_tool" in _names(r.search("bill"))

    def test_search_by_marketplace_tags(self):
        """按市场标签命中"""
        r = ToolRegistry()
        r.register(
            _make_tool("tagged_tool", "无相关描述"),
            marketplace=ToolMarketplaceMetadata(tags=["analytics"]),
        )

        assert "tagged_tool" in _names(r.search("analytic"))

    def test_search_no_match_returns_empty(self):
        """无匹配时返回空列表"""
        r = _fresh_registry()

        assert r.search("zzzz_none") == []


class TestMarketplace:
    """L411-412 / L420-429 / L433-438 / L442-454 / L458 / L466：市场元数据读写"""

    def test_get_marketplace_info_hit_and_miss(self):
        """get_marketplace_info：已注册返回元数据实例，未注册返回 None"""
        r = _fresh_registry()

        meta = r.get_marketplace_info("core_tool")
        assert isinstance(meta, ToolMarketplaceMetadata)
        assert r.get_marketplace_info("nope") is None

    def test_update_marketplace_info_missing_tool(self):
        """update_marketplace_info：工具不存在返回 False"""
        r = ToolRegistry()

        assert r.update_marketplace_info("nope", tags=["x"]) is False

    def test_update_marketplace_info_success(self):
        """update_marketplace_info：成功写入已存在字段并刷新 updated_at"""
        r = _fresh_registry()
        before = r.get_marketplace_info("core_tool").updated_at

        assert r.update_marketplace_info("core_tool", tags=["x"], featured=True) is True

        meta = r.get_marketplace_info("core_tool")
        assert meta.tags == ["x"]
        assert meta.featured is True
        assert meta.updated_at >= before

    def test_update_marketplace_info_ignores_unknown_field(self):
        """update_marketplace_info：未知字段被 hasattr 过滤，不报错仍返回 True"""
        r = _fresh_registry()

        assert r.update_marketplace_info("core_tool", no_such="v") is True
        assert not hasattr(r.get_marketplace_info("core_tool"), "no_such")

    def test_increment_downloads(self):
        """increment_downloads：存在则每次 +1 且返回 True，不存在返回 False"""
        r = _fresh_registry()

        assert r.increment_downloads("core_tool") is True
        assert r.increment_downloads("core_tool") is True
        assert r.get_marketplace_info("core_tool").downloads == 2

        assert r.increment_downloads("nope") is False

    def test_add_rating_out_of_range(self):
        """add_rating：rating 越界（<1 或 >5）直接返回 False（先于工具存在性判断）"""
        r = _fresh_registry()

        assert r.add_rating("core_tool", 0) is False
        assert r.add_rating("core_tool", 6) is False

    def test_add_rating_missing_tool(self):
        """add_rating：rating 合法但工具不存在返回 False"""
        r = _fresh_registry()

        assert r.add_rating("nope", 4) is False

    def test_add_rating_recomputes_average(self):
        """add_rating：平均分与计数被正确重算"""
        r = _fresh_registry()

        assert r.add_rating("core_tool", 4) is True
        meta = r.get_marketplace_info("core_tool")
        assert meta.rating == 4.0
        assert meta.rating_count == 1

        assert r.add_rating("core_tool", 5) is True
        assert meta.rating == round((4 * 1 + 5) / 2, 2) == 4.5
        assert meta.rating_count == 2

    def test_get_featured(self):
        """get_featured：只返回 featured=True 的工具，全否时为空列表"""
        r = _fresh_registry()

        assert r.get_featured() == []

        assert r.update_marketplace_info("core_tool", featured=True) is True
        assert _names(r.get_featured()) == {"core_tool"}

    def test_get_verified(self):
        """get_verified：只返回 verified=True 的工具，全否时为空列表"""
        r = _fresh_registry()

        assert r.get_verified() == []

        assert r.update_marketplace_info("ten_tool", verified=True) is True
        assert _names(r.get_verified()) == {"ten_tool"}


class TestMeta:
    """L474 / L478：分类与行业标签枚举"""

    def test_get_categories(self):
        """get_categories：返回全部 9 个分类 value"""
        r = ToolRegistry()

        categories = r.get_categories()
        assert len(categories) == 9
        assert {"text", "custom", "governance"} <= set(categories)

    def test_get_industry_tags(self):
        """get_industry_tags：汇总注册时出现的行业标签"""
        r = ToolRegistry()
        r.register(
            _make_tool("multi_industry_tool"),
            visibility=ToolVisibility.INDUSTRY,
            industry_tags=["finance", "health"],
        )

        tags = r.get_industry_tags()
        assert {"finance", "health"} <= set(tags)


class TestIndexCleanupOnUnregister:
    """查询侧回归：注销后各索引查询不应再返回该工具"""

    def test_queries_exclude_unregistered_tool(self):
        """unregister 后 list_by_* / search / count 均不再包含该工具"""
        r = _fresh_registry()

        assert r.unregister("fin_tool") is True
        assert r.list_by_industry("finance") == []
        assert r.list_by_visibility(ToolVisibility.INDUSTRY) == []
        assert "fin_tool" not in _names(r.search("fin"))
        assert r.count() == 2