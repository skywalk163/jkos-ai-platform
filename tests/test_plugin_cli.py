"""插件 CLI 单测：main() 分发与六个 cmd_* 子命令（覆盖 jkos_core/plugin_cli.py L34-302）

覆盖范围：
- main() 的子命令分发（L101-117）与 __main__ 守卫（L305-306）
- cmd_list / cmd_load / cmd_unload / cmd_create / cmd_marketplace / cmd_i18n
- get_registry 的延迟 import 分支（L299-302）

隔离策略：
- get_registry / get_marketplace / get_i18n 在 plugin_cli 模块命名空间里是按名查找的
  （get_registry 为本模块函数，另两个是顶部 import 进来的引用），
  因此全部用 monkeypatch 注入到 plugin_cli.*，避免触碰进程级全局单例、污染其它测试。
- I18nManager 是零参可构造的实例，直接 new 一个真实例使用，天然无全局副作用。
"""

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jkos_core import plugin_cli
from jkos_core.plugins.i18n import I18nManager
from jkos_core.plugins.marketplace import VisibilityDomain
from jkos_core.plugins.registry import EnhancedPluginRegistry


# ─── 测试替身（对照 plugin_cli.py 各处所需的最小接口） ───


class _FakeRegistry:
    """cmd_list / cmd_load / cmd_unload 所需的最小注册表接口（对照 L122-L161）"""

    def __init__(self, plugins=None, unregister_ok=True):
        self._plugins = plugins if plugins is not None else {}
        self._unregister_ok = unregister_ok

    def list_ids(self):
        return list(self._plugins)

    def get_info(self, plugin_id):
        return self._plugins.get(plugin_id)

    def unregister(self, plugin_id):
        return self._unregister_ok


class _FakeInfo:
    """cmd_list 打印所需的信息形状（对照 L135-L139 的五个属性访问链）"""

    def __init__(self, name="demo", version="1.0.0", category="text",
                 visibility="core", plugin_type="builtin"):
        self.metadata = SimpleNamespace(
            name=name,
            version=version,
            category=SimpleNamespace(value=category),
            plugin_type=SimpleNamespace(value=plugin_type),
        )
        self.visibility = SimpleNamespace(value=visibility)


class _FakeMarketplace:
    """cmd_marketplace 所需的最小市场接口（对照 L198-L246）"""

    def __init__(self, install_ok=True, uninstall_ok=True):
        self._install_ok = install_ok
        self._uninstall_ok = uninstall_ok
        self.last_install = None

    def list_local(self):
        return [{"plugin_id": "p1", "version": "1.0.0", "visibility": "core"}]

    def list_remote(self):
        return [SimpleNamespace(plugin_id="p2", version="2.0.0")]

    def install(self, plugin_id, version=None, visibility=None):
        self.last_install = {
            "plugin_id": plugin_id, "version": version, "visibility": visibility,
        }
        return self._install_ok

    def uninstall(self, plugin_id):
        return self._uninstall_ok

    def search(self, query):
        return [{"plugin_id": "p1", "name": "Demo"}]

    def get_statistics(self):
        return {
            "total_plugins": 4,
            "core_plugins": 1,
            "industry_plugins": 1,
            "custom_plugins": 2,
            "total_downloads": 10,
            "industry_tags": ["finance", "health"],
        }


class _FakeI18nFailAdd:
    """仅用于触发 add 失败分支：真实 I18nManager.add_translation 恒返回 True（对照 L283-L290）"""

    def add_translation(self, language, key, value):
        return False


# ─── 工具函数 ───


def _run_cli(monkeypatch, *args) -> int:
    """把 argv 设为 ["plugin_cli", *args] 后调用 main()，返回其返回码"""
    monkeypatch.setattr(sys, "argv", ["plugin_cli", *args])
    return plugin_cli.main()


def _patch_services(monkeypatch, registry=None, marketplace=None, i18n=None):
    """把 plugin_cli 命名空间里的三个服务入口替换为替身（默认给最小可用实现）"""
    monkeypatch.setattr(plugin_cli, "get_registry", lambda: registry or _FakeRegistry())
    monkeypatch.setattr(plugin_cli, "get_marketplace", lambda: marketplace or _FakeMarketplace())
    monkeypatch.setattr(plugin_cli, "get_i18n", lambda: i18n or I18nManager())


# ─── 用例 ───


class TestMainDispatch:
    """main() 的子命令分发与无参数兜底（L101-L117）"""

    def test_no_args_prints_help_and_returns_1(self, monkeypatch, capsys):
        """无子命令时打印帮助并返回 1（L115-L117）"""
        assert _run_cli(monkeypatch) == 1
        assert "DSH 插件管理 CLI" in capsys.readouterr().out

    @pytest.mark.parametrize("argv,expected", [
        (["list"], "cmd_list"),
        (["load", "."], "cmd_load"),
        (["unload", "x"], "cmd_unload"),
        (["create", "demo"], "cmd_create"),
        (["marketplace", "list"], "cmd_marketplace"),
        (["i18n", "list"], "cmd_i18n"),
    ])
    def test_dispatch_routes_to_expected_cmd(self, monkeypatch, argv, expected):
        """六个子命令各自路由到对应 cmd_* 函数（L103-L114）"""
        called = []

        def _stub(name):
            def _fn(args):
                called.append(name)
                return 0
            return _fn

        for name in ("cmd_list", "cmd_load", "cmd_unload",
                     "cmd_create", "cmd_marketplace", "cmd_i18n"):
            monkeypatch.setattr(plugin_cli, name, _stub(name))

        assert _run_cli(monkeypatch, *argv) == 0
        assert called == [expected]


class TestCmdList:
    """cmd_list：空注册表 / 混合 info（含 None）（L120-L141）"""

    def test_empty_registry_returns_0(self, monkeypatch, capsys):
        """注册表为空时打印提示并返回 0（L125-L127）"""
        _patch_services(monkeypatch, registry=_FakeRegistry(plugins={}))
        assert _run_cli(monkeypatch, "list") == 0
        assert "暂无已注册插件" in capsys.readouterr().out

    def test_lists_plugins_and_skips_unknown_info(self, monkeypatch, capsys):
        """逐个打印插件详情；get_info 返回 None 的 pid 被跳过（L129-L141）"""
        reg = _FakeRegistry(plugins={"p1": _FakeInfo(), "ghost": None})
        _patch_services(monkeypatch, registry=reg)

        assert _run_cli(monkeypatch, "list") == 0
        out = capsys.readouterr().out
        assert "已注册插件: 2 个" in out
        assert "  - p1" in out
        assert "      名称: demo" in out
        assert "      版本: 1.0.0" in out
        assert "      分类: text" in out
        assert "      可见域: core" in out
        assert "      类型: builtin" in out
        assert "ghost" not in out  # info 为 None -> 走 if 假分支，不打印


class TestCmdLoadUnload:
    """cmd_load 空目录加载；cmd_unload 成功与不存在（L144-L161）"""

    def test_load_empty_directory_reports_zero(self, monkeypatch, tmp_path, capsys):
        """目录下无插件子目录时加载数为 0（L144-L150）"""
        _patch_services(monkeypatch, registry=_FakeRegistry())
        assert _run_cli(monkeypatch, "load", str(tmp_path)) == 0
        assert "已加载 0 个插件" in capsys.readouterr().out

    def test_unload_success_returns_0(self, monkeypatch, capsys):
        """注销成功时打印已卸载并返回 0（L156-L158）"""
        _patch_services(monkeypatch, registry=_FakeRegistry(unregister_ok=True))
        assert _run_cli(monkeypatch, "unload", "p1") == 0
        assert "已卸载: p1" in capsys.readouterr().out

    def test_unload_missing_returns_1(self, monkeypatch, capsys):
        """注销失败时打印插件不存在并返回 1（L159-L161）"""
        _patch_services(monkeypatch, registry=_FakeRegistry(unregister_ok=False))
        assert _run_cli(monkeypatch, "unload", "nope") == 1
        assert "插件不存在: nope" in capsys.readouterr().out


class TestCmdCreate:
    """cmd_create：真实模板复制与 plugin.yaml 改写；目标目录已存在（L164-L194）

    说明：源码修复记录 —— cmd_create 原实现用 json.loads 读 YAML 模板且
    read_text() 未指定 encoding（GBK 环境下必崩）。已随本任务修复为
    yaml.safe_load/safe_dump + encoding='utf-8'，模板同步修正为
    合法 YAML 扁平清单（与 marketplace install 写入格式一致），
    故这里走真实模板路径断言成功结果。
    """

    def test_create_success_rewrites_plugin_yaml(self, monkeypatch, tmp_path, capsys):
        """默认参数：复制模板并改写 id/name/category/visibility，返回 0（L164-L194）"""
        out_dir = tmp_path / "dsh-demo"
        assert _run_cli(monkeypatch, "create", "demo", "--output", str(tmp_path)) == 0

        out = capsys.readouterr().out
        assert f"插件模板已创建: {out_dir}" in out
        assert (out_dir / "plugin.yaml").exists()

        cfg = yaml.safe_load((out_dir / "plugin.yaml").read_text(encoding="utf-8"))
        assert cfg["id"] == "dsh.text.demo"
        assert cfg["name"] == "Demo"
        assert cfg["category"] == "text"
        assert cfg["visibility"] == "custom"
        assert cfg["version"] == "1.0.0"  # 模板原有字段被保留
        assert cfg["health_check"]["expected_exit_code"] == 0  # 非改写字段也被保留

    def test_create_with_category_visibility_and_hyphen_name(self, monkeypatch, tmp_path):
        """--category/--visibility 生效，连字符名称被转为标题格式（L182-L185）"""
        out_dir = tmp_path / "dsh-my-plugin"
        assert _run_cli(
            monkeypatch, "create", "my-plugin",
            "--category", "audio", "--visibility", "industry",
            "--output", str(tmp_path),
        ) == 0

        cfg = yaml.safe_load((out_dir / "plugin.yaml").read_text(encoding="utf-8"))
        assert cfg["id"] == "dsh.audio.my-plugin"
        assert cfg["name"] == "My Plugin"
        assert cfg["category"] == "audio"
        assert cfg["visibility"] == "industry"

    def test_create_existing_dir_returns_1(self, monkeypatch, tmp_path, capsys):
        """目标目录已存在时不复制、返回 1（L169-L171）"""
        (tmp_path / "dsh-demo").mkdir()
        assert _run_cli(monkeypatch, "create", "demo", "--output", str(tmp_path)) == 1
        assert "目录已存在" in capsys.readouterr().out


class TestCmdMarketplace:
    """cmd_marketplace：list/remote/install/uninstall/search/stats/无子命令（L194-L252）"""

    def test_list_local(self, monkeypatch, capsys):
        """默认列出本地插件（L204-L208）"""
        _patch_services(monkeypatch, marketplace=_FakeMarketplace())
        assert _run_cli(monkeypatch, "marketplace", "list") == 0
        out = capsys.readouterr().out
        assert "本地插件: 1 个" in out
        assert "  - p1 (1.0.0, core)" in out

    def test_list_remote(self, monkeypatch, capsys):
        """--remote 列出远程插件（L199-L203）"""
        _patch_services(monkeypatch, marketplace=_FakeMarketplace())
        assert _run_cli(monkeypatch, "marketplace", "list", "--remote") == 0
        out = capsys.readouterr().out
        assert "远程插件: 1 个" in out
        assert "  - p2 (2.0.0)" in out

    def test_install_success_passes_visibility_domain(self, monkeypatch, capsys):
        """安装成功返回 0，且可见域按 VisibilityDomain 转换后传入（L210-L218）"""
        mp = _FakeMarketplace(install_ok=True)
        _patch_services(monkeypatch, marketplace=mp)

        assert _run_cli(monkeypatch, "marketplace", "install", "good") == 0
        assert "插件 good 安装成功" in capsys.readouterr().out
        assert mp.last_install["plugin_id"] == "good"
        assert isinstance(mp.last_install["visibility"], VisibilityDomain)
        assert mp.last_install["visibility"] is VisibilityDomain.CUSTOM

    def test_install_failure_returns_1(self, monkeypatch, capsys):
        """安装失败返回 1（L219-L221）"""
        _patch_services(monkeypatch, marketplace=_FakeMarketplace(install_ok=False))
        assert _run_cli(monkeypatch, "marketplace", "install", "bad") == 1
        assert "插件 bad 安装失败" in capsys.readouterr().out

    def test_uninstall_success_returns_0(self, monkeypatch, capsys):
        """卸载成功返回 0（L223-L227）"""
        _patch_services(monkeypatch, marketplace=_FakeMarketplace(uninstall_ok=True))
        assert _run_cli(monkeypatch, "marketplace", "uninstall", "good") == 0
        assert "插件 good 卸载成功" in capsys.readouterr().out

    def test_uninstall_failure_returns_1(self, monkeypatch, capsys):
        """卸载失败返回 1（L228-L230）"""
        _patch_services(monkeypatch, marketplace=_FakeMarketplace(uninstall_ok=False))
        assert _run_cli(monkeypatch, "marketplace", "uninstall", "bad") == 1
        assert "插件 bad 卸载失败" in capsys.readouterr().out

    def test_search(self, monkeypatch, capsys):
        """搜索结果逐条打印（L232-L236）"""
        _patch_services(monkeypatch, marketplace=_FakeMarketplace())
        assert _run_cli(monkeypatch, "marketplace", "search", "demo") == 0
        out = capsys.readouterr().out
        assert "搜索结果: 1 个" in out
        assert "  - p1: Demo" in out

    def test_stats(self, monkeypatch, capsys):
        """市场统计各字段打印（L238-L246）"""
        _patch_services(monkeypatch, marketplace=_FakeMarketplace())
        assert _run_cli(monkeypatch, "marketplace", "stats") == 0
        out = capsys.readouterr().out
        assert "市场统计:" in out
        assert "  总插件数: 4" in out
        assert "  核心插件: 1" in out
        assert "  行业插件: 1" in out
        assert "  自定义插件: 2" in out
        assert "  总下载量: 10" in out
        assert "  行业标签: finance, health" in out

    def test_no_subcommand_returns_1(self, monkeypatch, capsys):
        """未给子命令时 market_cmd 为 None，落到 else 分支返回 1（L248-L250）

        注：argparse 会直接拒绝未注册的子命令，因此该分支只能由“缺省子命令”触发。
        """
        _patch_services(monkeypatch, marketplace=_FakeMarketplace())
        assert _run_cli(monkeypatch, "marketplace") == 1
        assert "可用命令: list, install, uninstall, search, stats" in capsys.readouterr().out


class TestCmdI18n:
    """cmd_i18n：list / show 三分支 / add 成功与失败 / 无子命令（L255-L296）"""

    def test_list_languages(self, monkeypatch, capsys):
        """列出可用语言（L259-L263）"""
        i18n = I18nManager()
        _patch_services(monkeypatch, i18n=i18n)

        assert _run_cli(monkeypatch, "i18n", "list") == 0
        out = capsys.readouterr().out
        assert f"可用语言: {len(i18n.get_languages())} 个" in out
        assert "  - zh: 简体中文 (简体中文)" in out

    def test_show_translation_file_truncates_over_20(self, monkeypatch, capsys):
        """无 --key 时打印条数与前 20 条，超过 20 条追加省略提示（L266 / L276-L281）"""
        i18n = I18nManager()
        total = len(i18n.get_translation_file("zh"))
        assert total > 20, "内置 zh 词条需超过 20 条才能覆盖省略提示分支"
        _patch_services(monkeypatch, i18n=i18n)

        assert _run_cli(monkeypatch, "i18n", "show", "zh") == 0
        out = capsys.readouterr().out
        assert f"翻译文件 (zh): {total} 条" in out
        assert f"  ... 还有 {total - 20} 条" in out

    def test_show_specific_key(self, monkeypatch, capsys):
        """带 --key 且键存在时打印键值（L271-L273）"""
        i18n = I18nManager()
        key = next(iter(i18n.get_translation_file("zh")))
        _patch_services(monkeypatch, i18n=i18n)

        assert _run_cli(monkeypatch, "i18n", "show", "zh", "--key", key) == 0
        assert f"{key} = " in capsys.readouterr().out

    def test_show_missing_key(self, monkeypatch, capsys):
        """带 --key 但键不存在时打印提示（L275）"""
        _patch_services(monkeypatch, i18n=I18nManager())
        assert _run_cli(monkeypatch, "i18n", "show", "zh", "--key", "zzz_none") == 0
        assert "翻译键 zzz_none 不存在" in capsys.readouterr().out

    def test_show_unknown_language_returns_1(self, monkeypatch, capsys):
        """语言不存在时打印提示并返回 1（L267-L269）"""
        _patch_services(monkeypatch, i18n=I18nManager())
        assert _run_cli(monkeypatch, "i18n", "show", "nope") == 1
        assert "语言 nope 不存在" in capsys.readouterr().out

    def test_add_translation_success_returns_0(self, monkeypatch, capsys):
        """新增翻译成功返回 0，且真实写入实例（L283-L287）"""
        i18n = I18nManager()
        _patch_services(monkeypatch, i18n=i18n)

        assert _run_cli(monkeypatch, "i18n", "add", "zh", "k_new", "新值") == 0
        assert "翻译已添加: zh.k_new = 新值" in capsys.readouterr().out
        assert i18n.get_translation_file("zh")["k_new"] == "新值"

    def test_add_translation_failure_returns_1(self, monkeypatch, capsys):
        """add_translation 返回 False 时打印失败并返回 1（L288-L290）"""
        _patch_services(monkeypatch, i18n=_FakeI18nFailAdd())
        assert _run_cli(monkeypatch, "i18n", "add", "zh", "k", "v") == 1
        assert "翻译添加失败" in capsys.readouterr().out

    def test_no_subcommand_returns_1(self, monkeypatch, capsys):
        """未给子命令时 i18n_cmd 为 None，落到 else 分支返回 1（L292-L294）"""
        _patch_services(monkeypatch, i18n=I18nManager())
        assert _run_cli(monkeypatch, "i18n") == 1
        assert "可用命令: list, show, add" in capsys.readouterr().out


class TestGetRegistryReal:
    """get_registry 的延迟 import 分支（L299-L302）"""

    def test_returns_process_singleton_enhanced_registry(self):
        """直调用真实 get_registry()，返回进程级增强注册表单例

        本用例是红线 4 允许的唯一例外：只读地取得注册表实例，不做任何写操作。
        """
        reg = plugin_cli.get_registry()
        assert isinstance(reg, EnhancedPluginRegistry)
        assert plugin_cli.get_registry() is reg


class TestMainGuard:
    """__main__ 守卫（L305-L306，任务书加分项）"""

    def test_run_as_main_exits_with_main_return_code(self, monkeypatch):
        """以 __main__ 方式执行脚本时，退出码等于 main() 的返回码"""
        monkeypatch.setattr(sys, "argv", ["plugin_cli"])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(Path(plugin_cli.__file__)), run_name="__main__")
        assert exc.value.code == 1  # 无子命令 -> main() 返回 1