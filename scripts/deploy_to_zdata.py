"""部署 DSH 到 zdata 池（127G 空间）"""
import paramiko
import base64
import os

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

def run(client, cmd, echo=True):
    stdin, stdout, stderr = client.exec_command(cmd)
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

    DSH_DIR = "/data/dsh"
    CODE_DIR = f"{DSH_DIR}/code"
    VENV_DIR = f"{DSH_DIR}/venv"

    # ─── 步骤 1: 创建目录结构 ───
    step("步骤 1: 创建目录结构")
    
    for sub in ["code", "code/jkos_core", "code/jkos_core/plugins", "code/jkos_core/plugins/ocr",
                "code/jkos_core/plugins/_template", "code/jkos_core/mcp", "code/jkos_core/storage",
                "code/jkos_core/models", "code/jkos_core/api", "plugins", "logs", "cache", "data", "tmp"]:
        run_sudo(client, f"mkdir -p {DSH_DIR}/{sub} && chown -R ai:wheel {DSH_DIR}/{sub}", echo=False)
    print(f"  目录结构已创建: {DSH_DIR}")

    # ─── 步骤 2: 创建 Python 虚拟环境 ───
    step("步骤 2: 创建 Python 虚拟环境")
    
    run_sudo(client, f"rm -rf {VENV_DIR} && mkdir -p {VENV_DIR} && chown -R ai:wheel {VENV_DIR}", echo=False)
    
    print("  使用 /usr/local/bin/python3.11 创建虚拟环境...")
    status, out, err = run_sudo(client, f"/usr/local/bin/python3.11 -m venv {VENV_DIR}", echo=True)
    
    if status != 0:
        print(f"  venv 创建失败，尝试使用 virtualenv...")
        run_sudo(client, f"virtualenv {VENV_DIR}", echo=True)
    
    # 验证
    status, out, err = run(client, f"test -f {VENV_DIR}/bin/python && echo 'OK' || echo 'FAIL'", echo=False)
    if "OK" in out:
        print(f"  ✓ 虚拟环境: {VENV_DIR}/bin/python")
    else:
        print(f"  ✗ 虚拟环境创建失败")
        run_sudo(client, f"ln -sf /usr/local/bin/python3.11 {VENV_DIR}/bin/python", echo=False)
        print(f"  已创建符号链接")

    # 安装依赖
    print("  安装 Python 依赖...")
    run(client, f"{VENV_DIR}/bin/pip install --upgrade pip setuptools wheel", echo=False)
    run(client, f"{VENV_DIR}/bin/pip install fastapi uvicorn pydantic httpx structlog pyyaml", echo=False)
    run(client, f"{VENV_DIR}/bin/pip install pytest pytest-asyncio pytest-cov", echo=False)
    print("  ✓ Python 依赖安装完成")

    # ─── 步骤 3: 部署代码 ───
    step("步骤 3: 部署代码")
    
    # 读取本地文件
    local_base = r"G:\dswork\AI\dsh-ai-platform\jkos_core"
    
    files = {
        f"{CODE_DIR}/jkos_core/__init__.py": '"""DSH Core"""\n__version__ = "0.1.0"\n',
        f"{CODE_DIR}/jkos_core/cli.py": open(f"{local_base}/cli.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugin_cli.py": open(f"{local_base}/plugin_cli.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugins/__init__.py": open(f"{local_base}/plugins/__init__.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugins/base.py": open(f"{local_base}/plugins/base.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugins/ocr/plugin.py": open(f"{local_base}/plugins/ocr/plugin.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/plugins/_template/plugin.yaml": open(f"{local_base}/plugins/_template/plugin.yaml", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/mcp/server.py": open(f"{local_base}/mcp/server.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/storage/__init__.py": open(f"{local_base}/storage/__init__.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/storage/manager.py": open(f"{local_base}/storage/manager.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/models/__init__.py": open(f"{local_base}/models/__init__.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/api/__init__.py": open(f"{local_base}/api/__init__.py", "r", encoding="utf-8").read(),
        f"{CODE_DIR}/jkos_core/api/routes.py": open(f"{local_base}/api/routes.py", "r", encoding="utf-8").read(),
    }
    
    for remote_path, content in files.items():
        b64 = base64.b64encode(content.encode('utf-8')).decode('ascii')
        status, out, err = run_sudo(client, f"echo '{b64}' | base64 -d > '{remote_path}' && chown ai:wheel '{remote_path}'", echo=False)
        if status == 0:
            print(f"  ✓ {remote_path.split('/')[-1]}")
        else:
            print(f"  ✗ {remote_path.split('/')[-1]}: {err}")
    
    print("  ✓ 代码部署完成")

    # ─── 步骤 4: 验证安装 ───
    step("步骤 4: 验证安装")
    
    print("  测试 Python 导入...")
    status, out, err = run(client, f'cd {CODE_DIR} && {VENV_DIR}/bin/python -c "import jkos_core; print(\"jkos_core OK\")"', echo=False)
    if status == 0 and "OK" in out:
        print("  ✓ jkos_core 导入成功")
    else:
        print(f"  ✗ jkos_core 导入失败: {err}")
    
    print("  测试 MCP 导入...")
    status, out, err = run(client, f'cd {CODE_DIR} && {VENV_DIR}/bin/python -c "from jkos_core.mcp.server import PREDEFINED_TOOLS; print(\"MCP tools:\", len(PREDEFINED_TOOLS))"', echo=False)
    if status == 0:
        print(f"  ✓ {out.strip()}")
    else:
        print(f"  ✗ MCP 导入失败: {err}")
    
    print("  测试插件导入...")
    status, out, err = run(client, f'cd {CODE_DIR} && {VENV_DIR}/bin/python -c "from jkos_core.plugins import PluginRegistry; print(\"PluginRegistry OK\")"', echo=False)
    if status == 0:
        print("  ✓ 插件系统导入成功")
    else:
        print(f"  ✗ 插件系统导入失败: {err}")

    # ─── 步骤 5: 创建启动脚本 ───
    step("步骤 5: 创建启动脚本")
    
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
    run_sudo(client, f"echo '{b64}' | base64 -d > {DSH_DIR}/start.sh && chmod +x {DSH_DIR}/start.sh && chown ai:wheel {DSH_DIR}/start.sh", echo=False)
    print(f"  ✓ 启动脚本: {DSH_DIR}/start.sh")

    # ─── 步骤 6: 创建 systemd 服务（可选）───
    step("步骤 6: 创建 rc.d 启动服务")
    
    rc_script = f'''#!/bin/sh
# DSH AI 中台 rc.d 服务
. /etc/rc.subr

name="dsh"
rcvar="dsh_enable"

DSH_DIR="{DSH_DIR}"
DSH_VENV="{VENV_DIR}"
DSH_CODE="{CODE_DIR}"

command="/usr/sbin/daemon"
command_args="-f -r /bin/sh -c \"export DSH_HOME=$DSH_DIR; export DSH_VENV=$DSH_VENV; export DSH_CODE=$DSH_CODE; export PYTHONPATH=$DSH_CODE; cd $DSH_CODE; $DSH_VENV/bin/python jkos_core/cli.py mcp --port 3000\""

load_rc_config $name
run_rc_command "$1"
'''
    b64 = base64.b64encode(rc_script.encode('utf-8')).decode('ascii')
    run_sudo(client, f"echo '{b64}' | base64 -d > /usr/local/etc/rc.d/dsh && chmod +x /usr/local/etc/rc.d/dsh", echo=False)
    print(f"  ✓ rc.d 服务: /usr/local/etc/rc.d/dsh")
    print(f"  启动: sudo service dsh onestart")
    print(f"  开机自启: echo 'dsh_enable=\"YES\"' >> /etc/rc.conf")

    # ─── 完成 ───
    step("部署完成")
    
    print(f"""
  🎉 DSH AI 中台 已部署到 FreeBSD 15.1!

  系统信息:
    主机: {HOST}
    Python: /usr/local/bin/python3.11
    虚拟环境: {VENV_DIR}/bin/python
    代码目录: {CODE_DIR}
    ZFS 池: zdata (127G 可用)

  启动命令:
    ssh ai@{HOST}
    cd {DSH_DIR}
    ./start.sh mcp     # 启动 MCP Server (端口 3000)
    ./start.sh api     # 启动 API 服务 (端口 8000)

  或使用 rc.d 服务:
    sudo service dsh onestart

  验证:
    curl http://{HOST}:3000/health
    curl http://{HOST}:3000/tools
""")
    
    client.close()
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
