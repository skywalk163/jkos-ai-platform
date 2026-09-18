"""DSH 插件 CLI

用法:
    python plugin_cli.py list              # 列出插件
    python plugin_cli.py load <dir>        # 加载插件
    python plugin_cli.py unload <id>       # 卸载插件
    python plugin_cli.py create <name>     # 创建插件
    python plugin_cli.py marketplace       # 插件市场命令
    python plugin_cli.py i18n              # 多语言命令
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

from dsh_core.plugins import PluginRegistry, PluginLoader
from dsh_core.plugins.marketplace import (
    PluginMarketplace,
    VisibilityDomain,
    get_marketplace,
)
from dsh_core.plugins.i18n import (
    I18nManager,
    LanguageCode,
    get_i18n,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="DSH 插件管理 CLI")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # list
    list_parser = subparsers.add_parser("list", help="列出已注册插件")
    list_parser.add_argument("--category", help="按分类过滤")
    list_parser.add_argument("--visibility", help="按可见域过滤 (core/industry/custom)")

    # load
    load_parser = subparsers.add_parser("load", help="从目录加载插件")
    load_parser.add_argument("directory", help="插件目录")

    # unload
    unload_parser = subparsers.add_parser("unload", help="卸载插件")
    unload_parser.add_argument("plugin_id", help="插件 ID")

    # create
    create_parser = subparsers.add_parser("create", help="创建插件模板")
    create_parser.add_argument("name", help="插件名称")
    create_parser.add_argument("--category", default="text", help="分类 (text/audio/image/video/interconnect/governance)")
    create_parser.add_argument("--output", default=".", help="输出目录")
    create_parser.add_argument("--visibility", default="custom", help="可见域 (core/industry/custom)")

    # marketplace
    market_parser = subparsers.add_parser("marketplace", help="插件市场管理")
    market_subparsers = market_parser.add_subparsers(dest="market_cmd", help="市场命令")

    # marketplace list
    market_list_parser = market_subparsers.add_parser("list", help="列出本地插件")
    market_list_parser.add_argument("--remote", action="store_true", help="列出远程插件")

    # marketplace install
    market_install_parser = market_subparsers.add_parser("install", help="安装插件")
    market_install_parser.add_argument("plugin_id", help="插件 ID")
    market_install_parser.add_argument("--version", help="版本")
    market_install_parser.add_argument("--visibility", default="custom", help="可见域")

    # marketplace uninstall
    market_uninstall_parser = market_subparsers.add_parser("uninstall", help="卸载插件")
    market_uninstall_parser.add_argument("plugin_id", help="插件 ID")

    # marketplace search
    market_search_parser = market_subparsers.add_parser("search", help="搜索插件")
    market_search_parser.add_argument("query", help="搜索关键词")

    # marketplace stats
    market_subparsers.add_parser("stats", help="显示市场统计")

    # i18n
    i18n_parser = subparsers.add_parser("i18n", help="多语言管理")
    i18n_subparsers = i18n_parser.add_subparsers(dest="i18n_cmd", help="i18n 命令")

    # i18n list
    i18n_subparsers.add_parser("list", help="列出可用语言")

    # i18n show
    i18n_show_parser = i18n_subparsers.add_parser("show", help="显示翻译")
    i18n_show_parser.add_argument("language", help="语言代码")
    i18n_show_parser.add_argument("--key", help="显示特定翻译键")

    # i18n add
    i18n_add_parser = i18n_subparsers.add_parser("add", help="添加翻译")
    i18n_add_parser.add_argument("language", help="语言代码")
    i18n_add_parser.add_argument("key", help="翻译键")
    i18n_add_parser.add_argument("value", help="翻译值")

    args = parser.parse_args()

    if args.command == "list":
        return cmd_list(args)
    elif args.command == "load":
        return cmd_load(args)
    elif args.command == "unload":
        return cmd_unload(args)
    elif args.command == "create":
        return cmd_create(args)
    elif args.command == "marketplace":
        return cmd_marketplace(args)
    elif args.command == "i18n":
        return cmd_i18n(args)
    else:
        parser.print_help()
        return 1


def cmd_list(args) -> int:
    """列出插件"""
    registry = get_registry()
    
    plugins = registry.list_ids()
    if not plugins:
        print("暂无已注册插件")
        return 0
    
    print(f"已注册插件: {len(plugins)} 个")
    
    for pid in plugins:
        info = registry.get_info(pid)
        if info:
            print(f"  - {pid}")
            print(f"      名称: {info.metadata.name}")
            print(f"      版本: {info.metadata.version}")
            print(f"      分类: {info.metadata.category.value}")
            print(f"      可见域: {info.visibility.value}")
            print(f"      类型: {info.metadata.plugin_type.value}")
    
    return 0


def cmd_load(args) -> int:
    """加载插件"""
    registry = get_registry()
    loader = PluginLoader(registry)
    count = loader.load_from_directory(args.directory)
    print(f"已加载 {count} 个插件")
    return 0


def cmd_unload(args) -> int:
    """卸载插件"""
    registry = get_registry()
    if registry.unregister(args.plugin_id):
        print(f"已卸载: {args.plugin_id}")
        return 0
    else:
        print(f"插件不存在: {args.plugin_id}")
        return 1


def cmd_create(args) -> int:
    """创建插件"""
    template_dir = Path(__file__).parent / "plugins" / "_template"
    output_dir = Path(args.output) / f"dsh-{args.name}"

    if output_dir.exists():
        print(f"目录已存在: {output_dir}")
        return 1

    shutil.copytree(template_dir, output_dir)
    
    # 更新 plugin.yaml（模板与输出均为 UTF-8 YAML；YAML 是 JSON 超集，兼容市场安装写入的 JSON 清单）
    plugin_yaml = output_dir / "plugin.yaml"
    if plugin_yaml.exists():
        config = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8")) or {}
        config["id"] = f"dsh.{args.category}.{args.name}"
        config["name"] = args.name.replace("-", " ").title()
        config["category"] = args.category
        config["visibility"] = args.visibility
        plugin_yaml.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    
    print(f"插件模板已创建: {output_dir}")
    print(f"\n下一步:")
    print(f"  1. 编辑 {output_dir / 'plugin.py'}")
    print(f"  2. 编辑 {output_dir / 'plugin.yaml'}")
    print(f"  3. 运行 `python plugin_cli.py load {output_dir}` 加载插件")
    
    return 0


def cmd_marketplace(args) -> int:
    """插件市场命令"""
    mp = get_marketplace()
    
    if args.market_cmd == "list":
        if args.remote:
            packages = mp.list_remote()
            print(f"远程插件: {len(packages)} 个")
            for pkg in packages:
                print(f"  - {pkg.plugin_id} ({pkg.version})")
        else:
            plugins = mp.list_local()
            print(f"本地插件: {len(plugins)} 个")
            for p in plugins:
                print(f"  - {p['plugin_id']} ({p['version']}, {p['visibility']})")
        
    elif args.market_cmd == "install":
        success = mp.install(
            plugin_id=args.plugin_id,
            version=args.version,
            visibility=VisibilityDomain(args.visibility),
        )
        if success:
            print(f"插件 {args.plugin_id} 安装成功")
            return 0
        else:
            print(f"插件 {args.plugin_id} 安装失败")
            return 1
    
    elif args.market_cmd == "uninstall":
        success = mp.uninstall(args.plugin_id)
        if success:
            print(f"插件 {args.plugin_id} 卸载成功")
            return 0
        else:
            print(f"插件 {args.plugin_id} 卸载失败")
            return 1
    
    elif args.market_cmd == "search":
        results = mp.search(args.query)
        print(f"搜索结果: {len(results)} 个")
        for p in results:
            print(f"  - {p['plugin_id']}: {p['name']}")
    
    elif args.market_cmd == "stats":
        stats = mp.get_statistics()
        print(f"市场统计:")
        print(f"  总插件数: {stats['total_plugins']}")
        print(f"  核心插件: {stats['core_plugins']}")
        print(f"  行业插件: {stats['industry_plugins']}")
        print(f"  自定义插件: {stats['custom_plugins']}")
        print(f"  总下载量: {stats['total_downloads']}")
        print(f"  行业标签: {', '.join(stats['industry_tags'])}")
    
    else:
        print("可用命令: list, install, uninstall, search, stats")
        return 1
    
    return 0


def cmd_i18n(args) -> int:
    """多语言命令"""
    i18n = get_i18n()
    
    if args.i18n_cmd == "list":
        languages = i18n.get_languages()
        print(f"可用语言: {len(languages)} 个")
        for lang in languages:
            print(f"  - {lang['code']}: {lang['name']} ({lang['native_name']})")
    
    elif args.i18n_cmd == "show":
        translations = i18n.get_translation_file(args.language)
        if not translations:
            print(f"语言 {args.language} 不存在")
            return 1
        
        if args.key:
            if args.key in translations:
                print(f"{args.key} = {translations[args.key]}")
            else:
                print(f"翻译键 {args.key} 不存在")
        else:
            print(f"翻译文件 ({args.language}): {len(translations)} 条")
            for key, value in list(translations.items())[:20]:
                print(f"  {key} = {value}")
            if len(translations) > 20:
                print(f"  ... 还有 {len(translations) - 20} 条")
    
    elif args.i18n_cmd == "add":
        success = i18n.add_translation(args.language, args.key, args.value)
        if success:
            print(f"翻译已添加: {args.language}.{args.key} = {args.value}")
            return 0
        else:
            print(f"翻译添加失败")
            return 1
    
    else:
        print("可用命令: list, show, add")
        return 1
    
    return 0


def get_registry():
    """获取增强注册表"""
    from dsh_core.plugins.registry import get_registry as get_enhanced_registry
    return get_enhanced_registry()


if __name__ == "__main__":
    sys.exit(main())
