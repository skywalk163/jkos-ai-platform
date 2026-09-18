# Task 2 — Registry 查询与市场元数据：缺失覆盖率补齐

> 这份文档是给一个**全新 AI Agent** 看的任务书。按顺序读完、照做即可，不需要额外上下文。

---

## 0. 30 秒概况（先看这个）

| 项 | 值 |
|---|---|
| 仓库根目录 | `G:\dswork\AI\dsh-ai-platform` |
| 目标文件 | `dsh_core/mcp/registry.py`（497 行） |
| 你要做的全部事情 | **只创建 1 个新测试文件** `tests/test_m14_registry_query.py`，补齐下表行号的缺失覆盖率 |
| 完成后自检（3 条命令） | 见「第 7 节 运行与验收命令」 |
| 预计耗时 | 45–75 分钟 |

**绝对禁止**：修改 `registry.py`、修改任何其他测试文件、使用 `pytest.skip` / `# pragma: no cover` / `coverage: ignore` 来"绕过"缺失行。

---

## 1. 背景：为什么有这个任务

`dsh_core/mcp/registry.py` 是平台工具注册表（M13 工具标准化）。当前按文件统计：**208 条语句，101 条缺失，覆盖率 51%**。

本任务负责其中 **查询 / 搜索 / 市场元数据** 一族的缺失行（约 88 行跨度）。另一个平行任务负责"注册 / 注销 / 版本管理"，两个任务**文件零冲突、行号零重叠**，可并行。

---

## 2. 目标：你必须让这些行号从"缺失"变"命中""

运行覆盖率后（命令见第 7 节），`coverage report --show-missing` 会输出"行号区间列表"。**你负责的行号清单如下**（已逐行对照源码核实，可直接信）：

```
302-303
307
325-346
350
358
366
374
382
390
394-405
411-412
420-429
433-438
442-454
458
466
474
478
```

> 验证方法：跑完你的测试文件后，执行 `python -m coverage report --show-missing dsh_core/mcp/registry.py`，**上述行号一个都不能出现在 missing 列**。其余行号是否缺失不归你管。

---

## 3. 动手前必须读的文件（按顺序）

| # | 文件 | 为什么读 | 重点看什么 |
|---|---|---|---|
| 1 | `dsh_core/mcp/registry.py` | 被测对象本体 | `ToolRegistry` 全部方法（重点 L300-478）、`ToolVisibility` / `ToolCategory` 枚举、`ToolInfo` / `ToolMarketplaceMetadata` dataclass |
| 2 | `dsh_core/mcp/server.py` | 拿到 `MCPTool` 真实形状 | `MCPTool` 字段（`name/description/input_schema/output_schema`） |
| 3 | `tests/test_m14_mcp_mt.py` | 多租户可见性的既有表达 | 尤其注意 `PREDEFINED_TOOLS[0].model_dump()` 构造工具的手法（L59-64），以及它对"匿名/租户可见性"的断言思路 |
| 4 | `tests/conftest.py` | 有无可用 fixture | 目前**没有** registry 相关 fixture，需自建 |
| 5 | `tests/test_mcp.py` | `MCPTool` 直接构造写法 | `MCPTool(name=..., description=..., input_schema={"type": "object"})` |

---

## 4. 关键 API 签名速查（已核实，但以源码为准——动手前再读一遍对应行）

```python
def get(self, tool_name) -> MCPTool | None                                  # L300
def get_info(self, tool_name) -> ToolInfo | None                            # L305

def list_visible(self, tenant_id=None, industry=None,
                 category: ToolCategory | None = None) -> list[MCPTool]     # L309
    # 可见性规则（务必读懂 L325-346 再写断言，勿凭直觉）：
    #  CUSTOM  : tenant_id 传入且不等于工具所属租户 -> 隐藏；tenant_id 为 None（匿名）-> 可见
    #  INDUSTRY: industry 传入且不在工具 industry_tags -> 隐藏；industry 为 None -> 可见
    #  CORE    : 人人可见
    #  附加：industry 过滤作用于所有可见域（L337）；category 过滤（L341）

def list_by_category(self, category) -> list[MCPTool]                       # L348
def list_by_visibility(self, visibility) -> list[MCPTool]                   # L356
def list_by_industry(self, industry_tag) -> list[MCPTool]                   # L364
def list_by_tenant(self, tenant_id) -> list[MCPTool]                        # L372
def all(self) -> dict[str, MCPTool]                                         # L380
def list_ids(self) -> list[str]                                             # L384
def count(self) -> int                                                      # L388
def search(self, query) -> list[MCPTool]                                    # L392  # 匹配 name / description / marketplace.tags（均小写化）

def get_marketplace_info(self, tool_name) -> ToolMarketplaceMetadata | None # L409
def update_marketplace_info(self, tool_name, **kwargs) -> bool              # L414  # 仅 setattr 到已存在字段
def increment_downloads(self, tool_name) -> bool                            # L431  # 存在则 downloads+1
def add_rating(self, tool_name, rating) -> bool                             # L440  # 1<=rating<=5，否则 False；存在才计入
def get_featured(self) -> list[MCPTool]                                     # L456
def get_verified(self) -> list[MCPTool]                                     # L464
def get_categories(self) -> list[str]                                       # L472  # 9 个分类 value
def get_industry_tags(self) -> list[str]                                    # L476
```

枚举值速查：

```python
ToolVisibility.CORE      # "core"
ToolVisibility.INDUSTRY  # "industry"
ToolVisibility.CUSTOM    # "custom"
# ToolCategory 九个：TEXT AUDIO IMAGE VIDEO RAG SESSION INTERCONNECT GOVERNANCE CUSTOM
```

`MCPTool` 构造示例：

```python
from dsh_core.mcp.server import MCPTool
tool = MCPTool(
    name="my_tool",
    description="描述",
    input_schema={"type": "object"},
)
```

---

## 5. 必须覆盖的行号 → 功能 → 测试场景（逐个对照，这是核心）

### 5.0 前置：构造 helper（每个用例用独立的 `ToolRegistry()`）

```python
from dsh_core.mcp.registry import ToolRegistry, ToolVisibility, ToolCategory

def _make_tool(name: str, desc: str = "tool") -> MCPTool:
    return MCPTool(name=name, description=desc, input_schema={"type": "object"})

def _fresh_registry() -> ToolRegistry:
    r = ToolRegistry()
    # 样例数据集（按需在你的用例里再叠加）：
    r.register(_make_tool("core_tool"), visibility=ToolVisibility.CORE)
    r.register(_make_tool("fin_tool"), visibility=ToolVisibility.INDUSTRY,
               industry_tags=["finance"])
    r.register(_make_tool("ten_tool"), visibility=ToolVisibility.CUSTOM, tenant_id="t1")
    return r
```

> 特别注意 `list_visible` 的两个反直觉点（**先读源码 L325-346 再写断言**）：
> - **匿名（tenant_id=None）能看到其他租户的 CUSTOM 工具**，因为 L330 `if tenant_id and info.tenant_id != tenant_id` 在 `tenant_id` 为 `None` 时整个条件为假 → 不隐藏。
> - 单个 `industry` 参数既会过滤 INDUSTRY 工具（L333），也会对所有可见域附加行业过滤（L337）。

---

### L302-303 — `get`

- **场景**：`register(_make_tool("g"))` 后 `r.get("g")` 返回同名 `MCPTool` 实例；`r.get("nope")` 返回 `None`（三元分支两端都覆盖）。

### L307 — `get_info`

- **场景**：已注册 → 返回 `ToolInfo` 且 `.tool.name` 正确；未注册 → `None`。

### L325-346 — `list_visible`（本任务最大一块，建议 4 个用例）

> 基准数据见 5.0 的 `_fresh_registry()`（含 core/fin(t1 行业)/ten(CUSTOM, t1) 各一个）。

1. **租户过滤**：
   - `list_visible(tenant_id="t1")` → 含 `ten_tool`；
   - `list_visible(tenant_id="t2")` → **不含** `ten_tool`（L330 生效）；
   - `list_visible()`（匿名）→ **含** `ten_tool`（L330 为假 → 可见）。
2. **行业过滤（INDUSTRY 路径）**：
   - `list_visible(industry="finance")` → 含 `fin_tool`；
   - `list_visible(industry="health")` → 不含 `fin_tool`（L333 或 L337 生效，两者都走到）。
3. **分类过滤**：给 `_fresh_registry` 注册一个 `category=ToolCategory.TEXT` 的工具，`list_visible(category=ToolCategory.TEXT)` 只返回该工具；`category=ToolCategory.AUDIO` 不返回它（L341-342）。
4. **CORE 普适可见**：`core_tool` 在任意 `tenant_id` / `industry` / `category` 组合下（category 匹配时）都在列表里。

### L350 — `list_by_category`

- **场景**：注册 `category=ToolCategory.TEXT` 的工具 → `list_by_category(ToolCategory.TEXT)` 含它；`list_by_category(ToolCategory.AUDIO)` 为空列表。

### L358 — `list_by_visibility`

- **场景**：三个可见域各注册一个 → 分别按 `CORE / INDUSTRY / CUSTOM` 过滤，各自只含对应工具；空可见域返回 `[]`。

### L366 — `list_by_industry`

- **场景**：注册 `industry_tags=["finance"]` 的工具 → `list_by_industry("finance")` 含它；`list_by_industry("health")` 为空。

### L374 — `list_by_tenant`

- **场景**：`register(visibility=ToolVisibility.CUSTOM, tenant_id="t1")` → `list_by_tenant("t1")` 含它；`list_by_tenant("t2")` 为空。

### L382 — `all`

- **场景**：注册 2 个工具 → `all()` 返回 `dict`，key 为工具名，value 为 `MCPTool`，长度 2。

### L390 — `count`

- **场景**：空注册表 → `0`；注册 3 个 → `3`。

### L394-405 — `search`

1. 按**名称**命中：`search("core")` 能搜到 `core_tool`（大小写不敏感：试 `search("CORE")`）。
2. 按**描述**命中：注册描述含"billing"的工具 → `search("bill")` 能搜到。
3. 按**市场标签**命中：注册时给 `marketplace=ToolMarketplaceMetadata(tags=["analytics"])` → `search("analytic")` 能搜到。
4. **无匹配**：`search("zzzz_none")` → `[]`。

> `marketplace` 用 `from dsh_core.mcp.registry import ToolMarketplaceMetadata`。

### L411-412 — `get_marketplace_info`

- **场景**：已注册 → 返回 `ToolMarketplaceMetadata` 实例；未注册 → `None`。

### L420-429 — `update_marketplace_info`

1. **工具不存在** → `False`（L421-422）。
2. **成功更新**：对已注册工具 `update_marketplace_info("core_tool", tags=["x"], featured=True)` → `True`；断言 `get_marketplace_info("core_tool").tags == ["x"]`、`.featured is True`、`.updated_at` 被刷新。
3. 传**未知字段**（如 `no_such="v"`）不报错（L425 `hasattr` 过滤掉）。

### L433-438 — `increment_downloads`

- **场景**：已注册工具调用两次 → 每次返回 `True`，`downloads` 从 0 → 2；未注册 → `False`。

### L442-454 — `add_rating`（注意边界判断顺序：先查 rating 再查工具）

1. **越界**：`add_rating("core_tool", 0)` → `False`（L442 命中）；`add_rating("core_tool", 6)` → `False`。
2. **工具不存在且 rating 合法**：`add_rating("nope", 4)` → `False`（L446-447 命中）。
3. **平均分重算**：注册工具后 `add_rating(t, 4)` → `True`，`.rating == 4.0`、`.rating_count == 1`；再 `add_rating(t, 5)` → `.rating == round((4*1+5)/2, 2) == 4.5`、`.rating_count == 2`（覆盖 L450-452 的算术）。

### L458 — `get_featured`

- **场景**：注册两个工具，`update_marketplace_info(a, featured=True)` → `get_featured()` 只含 a；全不 featured → `[]`。

### L466 — `get_verified`

- **场景**：同上逻辑换成 `verified=True`。

### L474 — `get_categories`

- **场景**：返回长度 9，且 `"text" / "custom" / "governance"` 等值在列表中。

### L478 — `get_industry_tags`

- **场景**：注册 `industry_tags=["finance", "health"]` 的工具 → `get_industry_tags()` 含这两个标签。

---

## 6. 测试文件骨架建议（可照着搭）

```python
"""M14: ToolRegistry 查询与市场元数据单测（补齐 L302-307/325-346/350-478 缺失覆盖率）"""

from dsh_core.mcp.registry import ToolRegistry, ToolVisibility, ToolCategory, ToolMarketplaceMetadata
from dsh_core.mcp.server import MCPTool

def _make_tool(name: str, desc: str = "tool") -> MCPTool:
    return MCPTool(name=name, description=desc, input_schema={"type": "object"})

def _fresh_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_make_tool("core_tool"), visibility=ToolVisibility.CORE)
    r.register(_make_tool("fin_tool"), visibility=ToolVisibility.INDUSTRY, industry_tags=["finance"])
    r.register(_make_tool("ten_tool"), visibility=ToolVisibility.CUSTOM, tenant_id="t1")
    return r

class TestGet:                        # L302-303 / L307
class TestListVisible:                # L325-346
class TestListBy:                     # L350 / L358 / L366 / L374
class TestAllAndCount:                # L382 / L390
class TestSearch:                     # L394-405
class TestMarketplace:                # L411-412 / L420-429 / L433-438 / L442-454 / L458 / L466
class TestMeta:                       # L474 / L478
```

> 本任务**不需要**动全局 `_global_registry` / `set_registry`——一律用自建的 `ToolRegistry()` 实例，天然无副作用，这也是它可与 Task 1 并行的原因。

---

## 7. 运行与验收命令（PowerShell，在仓库根目录 `G:\dswork\AI\dsh-ai-platform` 执行）

```powershell
# 1) 只跑你的文件（确认用例全绿）
python -m pytest tests/test_m14_registry_query.py -q

# 2) 生成覆盖率 + 看 registry.py 剩余缺失行（重点看你的行号是否消失）
python -m coverage run -m pytest tests/test_m14_registry_query.py -q
python -m coverage report --show-missing dsh_core/mcp/registry.py

# 3) 跑全量回归，确认你没影响别的测试
python -m pytest -q
```

> 说明：`pyproject.toml` 里 `addopts = "-v --tb=short --cov=dsh_core --cov-report=term-missing"`，所以第 1 条命令会顺带出覆盖率；第 2 条用 `coverage run` 手动控制更精确。环境是 Windows PowerShell，命令里没有 `&&`，逐条执行即可。

---

## 8. 验收标准（全满足才叫完成）

- [ ] `tests/test_m14_registry_query.py` 存在，且只这一个新文件被创建/修改
- [ ] 命令 1 全部用例通过，无 `pytest.skip`、无 `# pragma: no cover`、无 `coverage: ignore`
- [ ] 命令 2 的 missing 列表中**不再出现**：`302-303`、`307`、`325-346`、`350`、`358`、`366`、`374`、`382`、`390`、`394-405`、`411-412`、`420-429`、`433-438`、`442-454`、`458`、`466`、`474`、`478`
- [ ] 命令 3 全量测试通过
- [ ] 每个测试有中文 docstring；文件头有模块级 docstring（参考 `tests/test_m14_mcp_mt.py` 风格）

---

## 9. 红线（违反即返工）

1. 不修改 `dsh_core/mcp/registry.py` 源码。
2. 不修改、不删除 `tests/` 下任何已有文件。
3. 不使用 `pytest.skip`、`pytest.xfail`、`# pragma: no cover`、`coverage: ignore`、`# type: ignore` 掩盖缺失行。
4. `list_visible` 的 CUSTOM 匿名可见性等**反直觉行为，以源码 L325-346 为准**——先读源码再断言；若与预期不符，按实际实现断言并在测试 docstring 里写明理由。
5. 不触碰全局 `_global_registry` / `set_registry`（那是 Task 1 的职责）。