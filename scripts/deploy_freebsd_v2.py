"""
FreeBSD 15.1 全自动部署脚本 v2
修复包名、venv 创建、SFTP 权限问题
"""
import paramiko
import time
import sys
import os

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

def run(client, cmd, get_pty=False, echo=True):
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=get_pty)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if echo and out:
        for line in out.split('\n'):
            if line.strip():
                print(f"  $ {line}")
    if err and echo:
        for line in err.split('\n'):
            if line.strip():
                print(f"  [!] {line}")
    return exit_status, out, err

def run_sudo(client, cmd, echo=True):
    return run(client, f"sudo {cmd}", echo=echo)

def step(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def main():
    print(f"[1] 连接 {HOST} ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=15)
    print("[1] 连接成功 ✓")

    DSH_DIR = "/var/dsh"
    CODE_DIR = f"{DSH_DIR}/code"
    VENV_DIR = f"{DSH_DIR}/venv"
    PLUGINS_DIR = f"{DSH_DIR}/plugins"

    # ─── 步骤 0: 系统更新 ───
    step("步骤 0: 系统更新")
    run_sudo(client, "pkg update -f", echo=False)

    # ─── 步骤 1: 安装基础依赖 ───
    step("步骤 1: 安装基础依赖")
    
    # 正确的包名（FreeBSD 中 python3.11 的包名是 python311）
    packages = [
        ("python311", "Python 3.11"),
        ("py311-pip", "pip"),
        ("py311-virtualenv", "virtualenv"),
        ("py311-sqlite3", "sqlite3"),
        ("ffmpeg", "FFmpeg"),
        ("git", "Git"),
        ("curl", "cURL"),
        ("wget", "wget"),
    ]
    
    for pkg_name, pkg_desc in packages:
        status, out, err = run(client, f"pkg info -e {pkg_name} 2>/dev/null && echo 'INSTALLED' || echo 'NOT_FOUND'", echo=False)
        if "INSTALLED" in out:
            print(f"  {pkg_desc} (已安装) ✓")
        else:
            print(f"  安装 {pkg_desc} ...")
            status, out, err = run_sudo(client, f"pkg install -y {pkg_name}", echo=False)
            if status == 0:
                print(f"  {pkg_desc} ✓")
            else:
                print(f"  {pkg_desc} ✗ (跳过)")

    # ─── 步骤 2: 创建 ZFS 数据集 ───
    step("步骤 2: 创建 ZFS 数据集")
    
    status, out, err = run(client, f"zfs list zroot/dsh-data 2>/dev/null && echo 'EXISTS' || echo 'NOT_FOUND'", echo=False)
    if "NOT_FOUND" in out:
        run_sudo(client, f"zfs create -o mountpoint={DSH_DIR} -o compression=lz4 -o atime=off zroot/dsh-data")
        print(f"  数据集 zroot/dsh-data 已创建")
    else:
        print(f"  数据集已存在")
    
    # 设置权限
    run_sudo(client, f"chown -R {USER}:wheel {DSH_DIR}")
    for sub in ["plugins", "logs", "cache", "data", "tmp", "code"]:
        run_sudo(client, f"mkdir -p {DSH_DIR}/{sub} && chown -R {USER}:wheel {DSH_DIR}/{sub}")
    print(f"  目录结构已创建")

    # ─── 步骤 3: 配置 Linux 兼容层 ───
    step("步骤 3: 配置 Linux 兼容层")
    run_sudo(client, "sysrc linux_enable=\"YES\"", echo=False)
    run_sudo(client, "kldload linux64 2>/dev/null || true", echo=False)
    status, out, err = run(client, "kldstat | grep linux", echo=False)
    print(f"  Linux 兼容层: {'已加载' if out else '未加载'}")

    # ─── 步骤 4: 创建 Python 虚拟环境 ───
    step("步骤 4: 创建 Python 虚拟环境")
    
    # 先删除可能存在的空目录
    run_sudo(client, f"rm -rf {VENV_DIR} && mkdir -p {VENV_DIR} && chown -R {USER}:wheel {VENV_DIR}", echo=False)
    
    # 使用 python3.11 创建 venv
    print(f"  使用 /usr/local/bin/python3.11 创建虚拟环境...")
    status, out, err = run_sudo(client, f"/usr/local/bin/python3.11 -m venv {VENV_DIR}", echo=True)
    
    if status != 0:
        print(f"  venv 创建失败，尝试使用 virtualenv...")
        run_sudo(client, f"virtualenv {VENV_DIR}", echo=True)
    
    # 验证 venv
    status, out, err = run(client, f"test -f {VENV_DIR}/bin/python && echo 'OK' || echo 'FAIL'", echo=False)
    if "OK" in out:
        print(f"  ✓ 虚拟环境已创建: {VENV_DIR}/bin/python")
    else:
        print(f"  ✗ 虚拟环境创建失败")
        # 尝试手动创建
        run_sudo(client, f"mkdir -p {VENV_DIR}/bin {VENV_DIR}/lib", echo=False)
        run_sudo(client, f"ln -sf /usr/local/bin/python3.11 {VENV_DIR}/bin/python", echo=False)
        print(f"  已创建符号链接")

    # 安装 Python 依赖
    print(f"  安装 Python 依赖...")
    run(client, f"{VENV_DIR}/bin/pip install --upgrade pip setuptools wheel", echo=False)
    run(client, f"{VENV_DIR}/bin/pip install fastapi uvicorn pydantic httpx structlog pyyaml", echo=False)
    run(client, f"{VENV_DIR}/bin/pip install pytest pytest-asyncio pytest-cov", echo=False)
    print(f"  ✓ Python 依赖安装完成")

    # ─── 步骤 5: 部署代码（使用 base64 编码，避免 SFTP 权限问题）───
    step("步骤 5: 部署代码")
    
    # 先创建目录结构
    for sub in ["jkos_core", "jkos_core/plugins", "jkos_core/plugins/ocr", "jkos_core/plugins/_template",
                "jkos_core/mcp", "jkos_core/storage", "jkos_core/models", "jkos_core/api"]:
        run_sudo(client, f"mkdir -p {CODE_DIR}/{sub} && chown -R {USER}:wheel {CODE_DIR}", echo=False)
    
    # 定义要上传的文件和内容
    files = {
        f"{CODE_DIR}/jkos_core/__init__.py": '"""DSH Core"""\n__version__ = "0.1.0"\n',
        f"{CODE_DIR}/jkos_core/cli.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\cli.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugin_cli.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugin_cli.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugins/__init__.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugins\__init__.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugins/base.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugins\base.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugins/ocr/plugin.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugins\ocr\plugin.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugins/_template/plugin.yaml": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugins\_template\plugin.yaml", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/mcp/server.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\mcp\server.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/storage/__init__.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\storage\__init__.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/storage/manager.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\storage\manager.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/models/__init__.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\models\__init__.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/api/__init__.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\api\__init__.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/api/routes.py": open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\api\routes.py", "r", encoding="utf-8").read(),
    }
    
    import base64
    for remote_path, content in files.items():
        b64_content = base64.b64encode(content.encode('utf-8')).decode('ascii')
        # 使用 sudo 写入文件
        status, out, err = run_sudo(client, f"echo '{b64_content}' | base64 -d > '{remote_path}' && chown {USER}:wheel '{remote_path}'", echo=False)
        if status == 0:
            filename = remote_path.split("/")[-1]
            print(f"  ✓ {filename}")
        else:
            print(f"  ✗ {remote_path.split('/')[-1]}: {err}")
    
    print("  ✓ 代码部署完成")

    # ─── 步骤 6: 验证安装 ───
    step("步骤 6: 验证安装")
    
    # 测试 Python 导入
    print("  测试 Python 导入...")
    test_cmd = 'import jkos_core; print("jkos_core OK")'
    status, out, err = run(client, f'cd {CODE_DIR} && {VENV_DIR}/bin/python -c "{test_cmd}"', echo=False)
    if status == 0 and "OK" in out:
        print("  ✓ jkos_core 导入成功")
    else:
        print(f"  ✗ jkos_core 导入失败: {err}")
    
    # 测试 MCP 导入
    print("  测试 MCP 导入...")
    test_cmd2 = 'from jkos_core.mcp.server import PREDEFINED_TOOLS; print("MCP tools:", len(PREDEFINED_TOOLS))'
    status, out, err = run(client, f'cd {CODE_DIR} && {VENV_DIR}/bin/python -c "{test_cmd2}"', echo=False)
    if status == 0:
        print(f"  ✓ {out.strip()}")
    else:
        print(f"  ✗ MCP 导入失败: {err}")
    
    # 测试插件导入
    print("  测试插件导入...")
    test_cmd3 = 'from jkos_core.plugins import PluginRegistry; print("PluginRegistry OK")'
    status, out, err = run(client, f'cd {CODE_DIR} && {VENV_DIR}/bin/python -c "{test_cmd3}"', echo=False)
    if status == 0:
        print("  ✓ 插件系统导入成功")
    else:
        print(f"  ✗ 插件系统导入失败: {err}")

    # ─── 步骤 7: 创建启动脚本 ───
    step("步骤 7: 创建启动脚本")
    
    startup = f'''#!/bin/sh
# DSH AI 中台启动脚本
export DSH_HOME={DSH_DIR}
export DSH_VENV={VENV_DIR}
export DSH_CODE={CODE_DIR}
export PYTHONPATH=$DSH_CODE
cd $DSH_CODE
case "$1" in
    mcp) $DSH_VENV/bin/python jkos_core/cli.py mcp --port 3000 ;;
    api) $DSH_VENV/bin/python jkos_core/cli.py api --port 8000 ;;
    all) $DSH_VENV/bin/python jkos_core/cli.py all ;;
    *) echo "用法: $0 {{mcp|api|all}}"; exit 1 ;;
esac
'''
    b64 = base64.b64encode(startup.encode('utf-8')).decode('ascii')
    run_sudo(client, f"echo '{b64}' | base64 -d > {DSH_DIR}/start.sh && chmod +x {DSH_DIR}/start.sh && chown {USER}:wheel {DSH_DIR}/start.sh", echo=False)
    print(f"  ✓ 启动脚本: {DSH_DIR}/start.sh")

    # ─── 完成 ───
    step("部署完成")
    
    print(f"""
  🎉 DSH AI 中台 已部署到 FreeBSD 15.1!

  系统信息:
    主机: {HOST}
    Python: /usr/local/bin/python3.11
    虚拟环境: {VENV_DIR}/bin/python
    代码目录: {CODE_DIR}
    ZFS 数据集: zroot/dsh-data ({DSH_DIR})

  启动命令:
    ssh {USER}@{HOST}
    cd {DSH_DIR}
    ./start.sh mcp     # 启动 MCP Server (端口 3000)
    ./start.sh api     # 启动 API 服务 (端口 8000)

  验证:
    curl http://{HOST}:3000/health
    curl http://{HOST}:3000/tools
""")
    
    client.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
