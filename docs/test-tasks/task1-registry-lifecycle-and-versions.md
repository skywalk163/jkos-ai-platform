# Task 1 — Registry 生命周期与版本管理：缺失覆盖率补齐

> 这份文档是给一个**全新 AI Agent** 看的任务书。按顺序读完、照做即可，不需要额外上下文。

---

## 0. 30 秒概况（先看这个）

| 项 | 值 |
|---|---|
| 仓库根目录 | `G:\dswork\AI\dsh-ai-platform` |
| 目标文件 | `dsh_core/mcp/registry.py`（497 行） |
| 你要做的全部事情 | **只创建 1 个新测试文件** `tests/test_m14_registry_lifecycle.py`，补齐下表行号的缺失覆盖率 |
| 完成后自检（3 条命令） | 见「第 7 节 运行与验收命令」 |
| 预计耗时 | 30–60 分钟 |

**绝对禁止**：修改 `registry.py`、修改任何其他测试文件、使用 `pytest.skip` / `# pragma: no cover` / `coverage: ignore` 来“绕过”缺失行。

---

## 1. 背景：为什么有这个任务

`dsh_core/mcp/registry.py` 是平台工具注册表（M13 工具标准化），功能很多但**几乎没被单元测试直接覆盖**。当前按文件统计：**208 条语句，101 条缺失，覆盖率 51%**。

本任务负责其中 **注册 / 注销 / 版本管理 / 全局注册表** 一族的缺失行（约 56 行跨度）。另一个平行任务负责"查询与市场元数据"一族，两个任务**文件零冲突、行号零重叠**，可并行。

---

## 2. 目标：你必须让这些行号从"缺失"变"命中"

运行覆盖率后（命令见第 7 节），`coverage report --show-missing` 会输出"行号区间列表"。**你负责的行号清单如下**（已逐行对照源码核实，可直接信）：

```
137
162
175
187-188
191-192
235-260
264-265
269-285
289-291
497
```

> 验证方法：跑完你的测试文件后，执行 `python -m coverage report --show-missing dsh_core/mcp/registry.py`，**上述行号一个都不能出现在 missing 列**。其余行号是否缺失不归你管。

---

## 3. 动手前必须读的文件（按顺序）

| # | 文件 | 为什么读 | 重点看什么 |
|---|---|---|---|
| 1 | `dsh_core/mcp/registry.py` | 被测对象本体 | `ToolRegistry` 类全部方法、`ToolInfo` / `ToolMarketplaceMetadata` 两个 dataclass、模块级 `get_registry` / `set_registry`。**对照第 5 节逐行看** |
| 2 | `dsh_core/mcp/server.py` | 拿到 `MCPTool` 的真实形状 | 只看 `MCPTool` 类：字段名（`name/description/input_schema/output_schema`）、是否有 `model_dump()` 和 `model_copy()`（pydantic v2） |
| 3 | `tests/test_mcp.py` | 现有测试怎么构造 `MCPTool` | `MCPTool(name=..., description=..., input_schema={"type": "object"})` 的写法，照抄风格 |
| 4 | `tests/test_m14_mcp_mt.py` | 同一个 M14 系列测试的约定 | 文件头 docstring、模块级 `pytest.fixture(scope="module")`、helper 函数风格 |
| 5 | `tests/conftest.py` | 有哪些共享 fixture | 目前只有 `event_loop / plugin_context / mock_storage / _reset_rate_limiter`，**没有** registry 相关 fixture，你需要自建 |

> 提示：1 和 2 是要久留的，其余扫一眼风格即可。生产 `python -m pytest -q` 环境不要动。

---

## 4. 关键 API 签名速查（已核实，但以源码为准——动手前再读一遍对应行）

```python
class ToolRegistry:
    def register(self, tool: MCPTool,
                 visibility: ToolVisibility = ToolVisibility.CORE,
                 category: ToolCategory = ToolCategory.CUSTOM,
                 marketplace: ToolMarketplaceMetadata | None = None,
                 industry_tags: list[str] | None = None,
                 tenant_id: str | None = None,
                 version: str = "1.0.0") -> None            # L113；重复名抛 ValueError

    def unregister(self, tool_name: str) -> bool             # L172；不存在返回 False

    def _make_version_snapshot(self, tool, version, changelog="") -> dict  # L200 内部

    def publish_version(self, tool_name, version, changelog="", **changes) -> bool  # L217

    def get_versions(self, tool_name) -> list[dict]          # L262；不存在返回 []

    def set_active_version(self, tool_name, version) -> bool # L267

    def _rebuild_tool(self, snapshot) -> MCPTool             # L287（延迟导入 MCPTool）

# 模块级（L481-497）
_global_registry: Optional[ToolRegistry] = None
def get_registry() -> ToolRegistry
def set_registry(registry: ToolRegistry) -> None
```

枚举：`ToolVisibility.CORE / INDUSTRY / CUSTOM`；`ToolCategory` 共 9 个（`TEXT/AUDIO/IMAGE/VIDEO/RAG/SESSION/INTERCONNECT/GOVERNANCE/CUSTOM`）。

`MCPTool` 构造示例（抄 `tests/test_mcp.py`）：

```python
from dsh_core.mcp.server import MCPTool
tool = MCPTool(
    name="my_tool",
    description="描述",
    input_schema={"type": "object", "properties": {}},
    output_schema={"type": "object"},
)
```

---

## 5. 必须覆盖的行号 → 功能 → 测试场景（逐个对照，这是核心）

### 5.0 前置：构造一个干净的工具注册表

> 每个测试用例都新建独立的 `registry = ToolRegistry()`，**不要复用一个长生命周期实例**，避免用例间状态串扰。用工厂 helper：

```python
from dsh_core.mcp.registry import ToolRegistry

def _make_tool(name: str, desc: str = "tool") -> MCPTool:
    return MCPTool(name=name, description=desc, input_schema={"type": "object"})
```

---

### L137 — 同名工具重复注册

- **为什么缺失**：现有测试从未对同一名字注册两次。
- **场景**：先 `registry.register(_make_tool("dup"))`，再对同名工具注册。
- **断言**：第二次注册必须抛 `ValueError`，且内容包含工具名：

```python
import pytest
def test_register_duplicate_raises():
    r = ToolRegistry()
    r.register(_make_tool("dup"))
    with pytest.raises(ValueError, match="dup"):
        r.register(_make_tool("dup"))
```

### L162 — 带行业标签注册 → 行业索引填充

- **场景**：`register(tool, visibility=ToolVisibility.INDUSTRY, industry_tags=["finance", "bank"])`。
- **断言**：`r._industry_index["finance"]` 包含工具名；`r._industry_index["bank"]` 也包含。

> 注：访问 `_industry_index` 属于灰色访问私有属性，但与 Intranet 内现有测试风格一致（见 `conftest.py` 对 `_global_limiter` 的访问），可接受。

### L175 — 注销不存在的工具

- **场景**：`r.unregister("not_exist")`。
- **断言**：返回 `False`。

### L187-188 — 注销带行业标签的工具 → 行业索引清理

- **场景**：注册 `industry_tags=["finance"]` 的工具，然后 `unregister`。
- **断言**：返回 `True`；`r._industry_index["finance"]` 中不再含该工具名（可能已删空键或留空列表——**以实际实现为准**，先读 L186-192 再断言）。

### L191-192 — 注销带 `tenant_id` 的 CUSTOM 工具 → 租户索引清理

- **场景**：`register(tool, visibility=ToolVisibility.CUSTOM, tenant_id="t1")`，然后 `unregister(tool.name)`。
- **断言**：返回 `True`；`r._tenant_index["t1"]` 中不再含该工具名（也可能整体变成空值——以实际实现为准）。

### L235-260 — `publish_version` 全流程

这是本任务里覆盖行最多的一段，建议拆 3 个用例：

1. **工具不存在**（L236-237）：`r.publish_version("nope", "2.0.0")` → `False`。
2. **发布成功，字段更新生效**（L239-251）：
   - 注册 `_make_tool("pv", desc="old")`；
   - `publish_version("pv", "2.0.0", changelog="fix", description="new")` → `True`；
   - 断言 `info = r.get_info("pv")` 后：`info.tool` 是新实例（`info.tool is not` 原实例，验证走了 `model_copy` 分支 L247），`info.tool.description == "new"`，`info.version == "2.0.0"`。
   - 传一个不存在的字段名（如 `not_a_field="x"`）验证 L241-243 的 `hasattr` 过滤被跳过、不报错。
3. **版本历史追加 + 时间戳刷新**（L254-257）：
   - 继续上例，`len(info.marketplace.versions) == 2`；`versions[-1]["version"] == "2.0.0"`；`versions[-1]["changelog"] == "fix"`；`versions[-1].get("name") == "pv"`。
   - 断言 `info.marketplace.updated_at` 被刷新（可在首次注册后记录旧值再比较，或断言其为 `datetime` 实例）。

### L264-265 — `get_versions`

- **场景**：已注册工具 → 返回列表且第一项 `["version"] == "1.0.0"`；未注册工具 → `[]`（L265 的三元分支两端都要跑到）。

### L269-285 — `set_active_version` 全流程

1. **工具不存在**（L270-271）：`set_active_version("nope", "1.0.0")` → `False`。
2. **版本不在历史里**（L278-279）：注册后 `set_active_version("sv", "9.9.9")` → `False`。
3. **成功切换**（L281-284）：
   - 注册 `_make_tool("sv")`（自动记录版本 `1.0.0` 快照）→ `publish_version("sv", "2.0.0", description="v2")` → `set_active_version("sv", "2.0.0")` → `True`；
   - 断言 `info.tool.description == "v2"`（从快照重建而来）、`info.version == "2.0.0"`。

### L289-291 — `_rebuild_tool` 延迟导入重建

- 该行由上面的"成功切换"路径自动触发（L281 调用了 `_rebuild_tool`）。所以 `set_active_version` 成功用例即覆盖它。
- 额外断言：重建出的 `info.tool` 是 `MCPTool` 实例，且 `name` 与快照一致。

### L497 — `set_registry` 全局注册表替换

> **高危操作**：这会改全局 `_global_registry`，影响依赖全局注册表的其他模块（如 `dsh_core/mcp/server.py`）。**必须**在模块级做"拍摄快照 → 用例执行 → 恢复"。

```python
import dsh_core.mcp.registry as registry_mod
from dsh_core.mcp.registry import ToolRegistry, set_registry, get_registry

@pytest.fixture(scope="module", autouse=True)
def _restore_global_registry():
    old = registry_mod._global_registry
    yield
    registry_mod._global_registry = old

def test_set_registry():
    new_reg = ToolRegistry()
    new_reg.register(_make_tool("in_global"))
    set_registry(new_reg)
    assert get_registry() is new_reg
    assert get_registry().get("in_global") is not None
```

> 记忆点：`set_registry` 直接赋值全局变量并把新实例返回给 `get_registry`（`get_registry` 只负责在 `None` 时新建，L489-490）。

---

## 6. 测试文件骨架建议（可照着搭）

```python
"""M14: ToolRegistry 生命周期与版本管理单测（补齐 L137/162/175/187-192/235-285/289-291/497 缺失覆盖率）"""

import pytest

import dsh_core.mcp.registry as registry_mod
from dsh_core.mcp.registry import ToolRegistry, ToolVisibility, set_registry, get_registry
from dsh_core.mcp.server import MCPTool

def _make_tool(name: str, desc: str = "tool") -> MCPTool:
    return MCPTool(name=name, description=desc, input_schema={"type": "object"})

# —— 全局恢复 fixture ——
@pytest.fixture(scope="module", autouse=True)
def _restore_global_registry():
    old = registry_mod._global_registry
    yield
    registry_mod._global_registry = old

class TestRegister:
    ...  # L137 / L162

class TestUnregister:
    ...  # L175 / L187-188 / L191-192

class TestVersions:
    ...  # L235-260 / L264-265 / L269-285 / L289-291

class TestGlobalRegistry:
    ...  # L497
```

---

## 7. 运行与验收命令（PowerShell，在仓库根目录 `G:\dswork\AI\dsh-ai-platform` 执行）

```powershell
# 1) 只跑你的文件（确认用例全绿）
python -m pytest tests/test_m14_registry_lifecycle.py -q

# 2) 生成覆盖率 + 看 registry.py 剩余缺失行（重点看你的行号是否消失）
python -m coverage run -m pytest tests/test_m14_registry_lifecycle.py -q
python -m coverage report --show-missing dsh_core/mcp/registry.py

# 3) 跑全量回归，确认你没熏到别的测试
python -m pytest -q
```

> 说明：`pyproject.toml` 里 `addopts = "-v --tb=short --cov=dsh_core --cov-report=term-missing"`，所以第 1 条命令也会顺带出覆盖率；第 2 条用 `coverage run` 手动控制更精确。环境是 Windows PowerShell，命令里没有 `&&`，逐条执行即可。

---

## 8. 验收标准（全满足才叫完成）

- [ ] `tests/test_m14_registry_lifecycle.py` 存在，且只这一个新文件被创建/修改
- [ ] 命令 1 全部用例通过，无 `pytest.skip`、无 `# pragma: no cover`、无 `coverage: ignore`
- [ ] 命令 2 的 missing 列表中**不再出现**：`137`、`162`、`175`、`187-188`、`191-192`、`235-260`、`264-265`、`269-285`、`289-291`、`497`
- [ ] 命令 3 全量测试通过（特别是 `test_m14_mcp_mt.py` / `test_mcp_server.py` 没被你破坏——全局注册表已恢复原样）
- [ ] 每个测试有中文 docstring 说明在测什么；文件头有模块级 docstring（风格参考 `tests/test_m14_mcp_mt.py`）

---

## 9. 红线（违反即返工）

1. 不修改 `dsh_core/mcp/registry.py` 源码。
2. 不修改、不删除 `tests/` 下任何已有文件。
3. 不使用 `pytest.skip`、`pytest.xfail`、`# pragma: no cover`、`coverage: ignore`、`# type: ignore` 掩盖缺失行。
4. 不在用例里对不确定的实现行为"硬编码"——先读懂源码，再写断言；实现行为冲突时**以源码为准并记录理由**（例如 `_tenant_index` 清理后值是空列表还是删除键）。
5. 全局注册表改动必须恢复（第 5 节 fixture），否则污染其他测试。