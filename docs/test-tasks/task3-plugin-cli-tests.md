# Task 3 — 插件 CLI（plugin_cli.py）：零覆盖转全绿

> 这份文档是给一个**全新 AI Agent** 看的任务书。按顺序读完、照做即可，不需要额外上下文。

---

## 0. 30 秒概况（先看这个）

| 项 | 值 |
|---|---|
| 仓库根目录 | `G:\dswork\AI\dsh-ai-platform` |
| 目标文件 | `dsh_core/plugin_cli.py`（306 行） |
| 覆盖率现状 | **193 语句全部缺失（0%）**，从未被任何测试 import 过 |
| 你要做的全部事情 | **只创建 1 个新测试文件** `tests/test_plugin_cli.py`，覆盖 `L34-302` |
| 完成后自检（3 条命令） | 见「第 7 节 运行与验收命令」 |
| 预计耗时 | 60–90 分钟 |

**绝对禁止**：修改 `plugin_cli.py` 源码、修改任何其他测试文件、使用 `pytest.skip` / `# pragma: no cover` / `coverage: ignore` 绕过缺失行。

---

## 1. 背景：为什么有这个任务

`plugin_cli.py` 是 DSH 插件管理命令行入口（list / load / unload / create / marketplace / i18n 六组子命令），是插件体系（M5）的用户门面。全量回归显示它是仓库里**最大的零覆盖文件**（193 语句 0%）。

平行任务 Task 4 负责 API 路由层（`api/marketplace_routes.py` + `api/tool_routes.py`），两个任务**文件零冲突、手法不同**（你测 CLI，它测 HTTP），可完全并行。

---

## 2. 目标：让这些行号从"缺失"变"命中"

跑覆盖率后（命令见第 7 节），`coverage report --show-missing dsh_core/plugin_cli.py` 的 missing 应只剩（或接近）：

```
305-306        # if __name__ == "__main__" 守卫，明确不在你的范围内
```

即 **L34-302 全部命中**。L12-31 是模块级 import，测试文件 import 后自动覆盖。

> 加分项（可选，非验收必需）：用 `runpy.run_path("dsh_core/plugin_cli.py", run_name="__main__")` + monkeypatch argv/sys.exit 把 L305-306 也盖上。失败就放弃，不要硬凑。

---

## 3. 动手前必须读的文件（按顺序）

| # | 文件 | 为什么读 | 重点看什么 |
|---|---|---|---|
| 1 | `dsh_core/plugin_cli.py` | 被测对象本体（306 行，全读） | `main()` 分发 L101-117、六个 `cmd_*` 函数、模块顶部 import 了 `get_marketplace`/`get_i18n` 到本模块命名空间（L22-31，这决定了 monkeypatch 的落点） |
| 2 | `tests/test_m5_marketplace.py` | 现有插件测试的构造手法 | `TestEnhancedPluginRegistry`(L162) 如何构造注册表并注册测试插件、`TestRegistryGlobals`(L683) 的全局保存/恢复模式 |
| 3 | `tests/test_m5_i18n.py` | I18nManager 构造范本 | L77-80 的 fixture：`I18nManager()` 零参可构造 |
| 4 | `dsh_core/plugins/_template/` | `cmd_create` 的复制源 | 里面有 `plugin.yaml`（JSON 格式，`cmd_create` 用 `json.loads` 改写它） |
| 5 | `tests/conftest.py` | 共享 fixture | 只有 `event_loop / plugin_context / mock_storage / _reset_rate_limiter`，与本任务无冲突 |

---

## 4. 测试手法（本任务的核心技巧）

### 4.1 调用 CLI：monkeypatch argv + capsys

```python
import sys
import pytest
from dsh_core import plugin_cli

def _run_cli(monkeypatch, *args):
    """设置 argv 后执行 main()，返回 (返回码, 捕获的 stdout)"""
    monkeypatch.setattr(sys, "argv", ["plugin_cli", *args])
    code = plugin_cli.main()
    return code
```

断言返回码（0 成功 / 1 失败）；用 pytest 的 `capsys` fixture 读 stdout 断言关键文案（如 `"暂无已注册插件"`）。

### 4.2 隔离服务层：monkeypatch 模块属性（关键！）

`plugin_cli.py` 顶部把 `get_marketplace` / `get_i18n` import 进了**本模块命名空间**（L25/L30），`get_registry` 是本模块函数（L299，内部延迟 import）。所以注入落点全部是 `plugin_cli.*`：

```python
class _FakeRegistry:
    """cmd_list / cmd_unload 需要的最小接口"""
    def __init__(self, plugins=None, unregister_ok=True):
        self._plugins = plugins or {}   # {pid: info}
        self._unregister_ok = unregister_ok
    def list_ids(self): return list(self._plugins)
    def get_info(self, pid): return self._plugins.get(pid)
    def unregister(self, pid): return self._unregister_ok

class _FakeInfo:
    """cmd_list 打印所需的 info 形状（对照 plugin_cli.py L132-139）"""
    def __init__(self, name="demo", version="1.0.0", category="text", visibility="core", plugin_type="builtin"):
        from types import SimpleNamespace
        self.metadata = SimpleNamespace(name=name, version=version,
                                        category=SimpleNamespace(value=category),
                                        plugin_type=SimpleNamespace(value=plugin_type))
        self.visibility = SimpleNamespace(value=visibility)

class _FakeMarketplace:
    """cmd_marketplace 需要的最小接口（对照 L198-250）"""
    def list_local(self):  return [{"plugin_id": "p1", "version": "1.0.0", "visibility": "core"}]
    def list_remote(self): return [SimpleNamespace(plugin_id="p2", version="2.0.0")]
    def install(self, **kw):  return kw.get("plugin_id") == "good"
    def uninstall(self, pid): return pid == "good"
    def search(self, q):      return [{"plugin_id": "p1", "name": "Demo"}]
    def get_statistics(self): return {"total_plugins": 4, "core_plugins": 1, "industry_plugins": 1,
                                      "custom_plugins": 2, "total_downloads": 10,
                                      "industry_tags": ["finance", "health"]}

monkeypatch.setattr(plugin_cli, "get_registry", lambda: _FakeRegistry(...))
monkeypatch.setattr(plugin_cli, "get_marketplace", lambda: _FakeMarketplace())
monkeypatch.setattr(plugin_cli, "get_i18n",      lambda: I18nManager())   # 真实例，零参构造
```

> 为什么用 stub 而不是真实单例：`get_marketplace()/get_i18n()` 是进程级全局单例（`plugins/marketplace.py` L371-385、`plugins/i18n.py` L520-534），直接调用会污染其他测试。monkeypatch 自动恢复，天然隔离。**i18n 也可以整体 stub**（接口只需 `get_languages / get_translation_file / add_translation`），二选一。

### 4.3 `cmd_create` 用 `tmp_path` 走真实路径

模板目录 `dsh_core/plugins/_template/` 真实存在，直接：

```python
def test_cmd_create_success(monkeypatch, tmp_path, capsys):
    code = _run_cli(monkeypatch, "create", "demo", "--output", str(tmp_path))
    assert code == 0
    out = capsys.readouterr().out
    assert "插件模板已创建" in out
    # 断言 plugin.yaml 被改写
    import json
    cfg = json.loads((tmp_path / "dsh-demo" / "plugin.yaml").read_text())
    assert cfg["id"] == "dsh.text.demo"
```

---

## 5. 必须覆盖的行号 → 场景清单（逐个对照）

### main() 分发 L101-117
- 六个子命令各至少 1 次真实进入（`list` / `load` / `unload` / `create` / `marketplace` / `i18n`）→ 覆盖 L103-114
- **无参数**（argv 只有 `["plugin_cli"]`）→ `parse_args` 后 `args.command` 为 None → 走 else：`print_help()` + `return 1`（L115-117）

### cmd_list L120-141
- **空注册表**：stub `list_ids()` 返回 `[]` → 打印"暂无已注册插件"、返回 0（L125-127）
- **有插件**：stub 返回 1 个 `_FakeInfo` → 覆盖 L129-141 的循环打印；stub 中放一个 `get_info` 返回 None 的 pid 可顺带覆盖 L133-134 的 `if info` 为假分支

### cmd_load L144-150
- 传入 `tmp_path` **空目录**：真实 `PluginLoader(registry).load_from_directory()` 返回 0 → 打印"已加载 0 个插件"、返回 0（L148-150）
- （可选）从 `_template` copytree 出的目录做一次真实加载

### cmd_unload L153-161
- stub `unregister` 返回 True → "已卸载"、0（L156-158）
- 返回 False → "插件不存在"、**1**（L159-161）

### cmd_create L164-191
- **成功**：`--output tmp_path` → copytree + plugin.yaml 改写 + 返回 0（L173-191）
- **目录已存在**：先 `mkdir` 目标目录再执行 → "目录已存在"、**1**（L169-171）

### cmd_marketplace L194-252
- `list`（本地，默认）：L204-208
- `list --remote`：L199-203
- `install good` → 成功 0（L216-218）；`install bad` → 失败 **1**（L219-221）
- `uninstall good` → 0（L225-227）；`uninstall bad` → **1**（L228-230）
- `search 关键词`：L232-236
- `stats`：L238-246（stub 字典字段名必须与 L241-246 一致）
- `marketplace` 后接未知子命令：L248-250 返回 1

### cmd_i18n L255-296
- `list`：L259-263
- `show <lang>`：语言存在无 `--key` → L266、L276-281；带 `--key` 且键存在 → L271-273；键不存在 → L275；**语言不存在**（`get_translation_file` 返回 None/空）→ L267-269 返回 1
- `add`：成功 → L285-287 返回 0；失败（stub 返回 False）→ L288-290 返回 1
- 未知子命令：L292-294 返回 1

### get_registry L299-302
- 单独一个用例**直接调用** `plugin_cli.get_registry()`（不 monkeypatch），断言返回真实增强注册表实例——这是覆盖延迟 import 分支的最省事方式

---

## 6. 测试文件骨架建议（可照着搭）

```python
"""插件 CLI 单测：main() 分发与六个 cmd_* 子命令（覆盖 plugin_cli.py L34-302）"""

import json
import sys
from types import SimpleNamespace

import pytest

from dsh_core import plugin_cli
from dsh_core.plugins.i18n import I18nManager

# —— 第 4 节的 _FakeRegistry / _FakeInfo / _FakeMarketplace / _run_cli —— 

class TestMainDispatch:      # 无参数 → help + 1；六个子命令能进入对应 cmd_*
class TestCmdList:           # 空 / 非空 / info 为 None
class TestCmdLoadUnload:     # 空目录加载 0；卸载成功/失败
class TestCmdCreate:         # 成功(tmp_path) / 目录已存在
class TestCmdMarketplace:    # list/remote/install/uninstall/search/stats/未知
class TestCmdI18n:           # list/show(3 分支)/add/未知
class TestGetRegistryReal:   # 直接调用 plugin_cli.get_registry()
```

---

## 7. 运行与验收命令（PowerShell，仓库根目录执行）

```powershell
# 1) 只跑你的文件
python -m pytest tests/test_plugin_cli.py -q

# 2) 精确覆盖率（注意：必须带 --no-cov！pyproject.toml 的 addopts 自带
#    --cov=dsh_core，与手动 coverage run 冲突会采不到数据）
python -m coverage run -m pytest tests/test_plugin_cli.py -q --no-cov
python -m coverage report --show-missing dsh_core/plugin_cli.py

# 3) 全量回归
python -m pytest -q
```

---

## 8. 验收标准（全满足才叫完成）

- [ ] `tests/test_plugin_cli.py` 存在，且只这一个新文件被创建/修改
- [ ] 命令 1 全部通过，无 skip/xfail/pragma/ignore
- [ ] 命令 2 的 missing 只剩 `305-306`（或你完成加分项后为空）
- [ ] 命令 3 全量通过，`test_m5_marketplace.py` / `test_m5_i18n.py` 不受影响
- [ ] 每个测试有中文 docstring；stub 类放在模块顶部并注明"对照 plugin_cli.py Lxxx"

---

## 9. 红线（违反即返工）

1. 不修改 `dsh_core/plugin_cli.py` 及 `dsh_core/plugins/**` 源码。
2. 不修改、不删除 `tests/` 下任何已有文件。
3. 不用 `pytest.skip` / `# pragma: no cover` / `coverage: ignore` / `# type: ignore` 掩盖缺失行。
4. 不调用真实全局单例 `get_marketplace()/get_i18n()`（plugins 包里的）做写操作——必须经 monkeypatch 注入；唯一例外是 L299-302 的只读直调用例。
5. 断言文案时对照源码实际 print 内容，不要凭本文档转述猜字符串（本文档引用可能与源码有细微出入，**以源码为准**）。