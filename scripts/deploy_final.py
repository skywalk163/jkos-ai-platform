"""直接 SSH 部署 DSH 到 FreeBSD"""
import paramiko
import base64

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
                print(f"  {line}")
    if err and echo:
        for line in err.split('\n'):
            if line.strip():
                print(f"  [!] {line}")
    return exit_status, out, err

def main():
    print(f"[1] 连接 {HOST} ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=15)
    print("[1] 连接成功 ✓")

    DSH_DIR = "/data/dsh"
    CODE_DIR = f"{DSH_DIR}/code"
    VENV_DIR = f"{DSH_DIR}/venv"

    # 先设置目录权限
    print("\n[2] 设置目录权限...")
    run(client, f"sudo chown -R ai:wheel {DSH_DIR} 2>/dev/null || mkdir -p {DSH_DIR} && sudo chown -R ai:wheel {DSH_DIR}")
    
    # 创建目录
    for sub in ["code/dsh_core", "code/dsh_core/plugins", "code/dsh_core/plugins/ocr",
                "code/dsh_core/plugins/_template", "code/dsh_core/mcp", "code/dsh_core/storage",
                "code/dsh_core/models", "code/dsh_core/api"]:
        run(client, f"mkdir -p {DSH_DIR}/{sub}")
    
    # 检查目录权限
    run(client, f"ls -la {DSH_DIR}")

    # 创建虚拟环境
    print("\n[3] 创建虚拟环境...")
    run(client, f"/usr/local/bin/python3.11 -m venv {VENV_DIR}")
    
    # 安装依赖
    print("\n[4] 安装 Python 依赖...")
    run(client, f"{VENV_DIR}/bin/pip install --upgrade pip setuptools wheel")
    run(client, f"{VENV_DIR}/bin/pip install fastapi uvicorn pydantic httpx structlog pyyaml")
    run(client, f"{VENV_DIR}/bin/pip install pytest pytest-asyncio pytest-cov")

    # 部署代码 - 使用 heredoc 方式
    print("\n[5] 部署代码...")
    
    # __init__.py
    run(client, f"cat > {CODE_DIR}/dsh_core/__init__.py << 'EOF'\n\"\"\"DSH Core\"\"\"\n__version__ = '0.1.0'\nEOF")
    
    # cli.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\cli.py", "r", encoding="utf-8") as f:
        cli_content = f.read()
    b64 = base64.b64encode(cli_content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/cli.py")
    print("  ✓ cli.py")
    
    # plugin_cli.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\plugin_cli.py", "r", encoding="utf-8") as f:
        plugin_cli_content = f.read()
    b64 = base64.b64encode(plugin_cli_content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/plugin_cli.py")
    print("  ✓ plugin_cli.py")
    
    # base.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\plugins\base.py", "r", encoding="utf-8") as f:
        base_content = f.read()
    b64 = base64.b64encode(base_content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/plugins/base.py")
    print("  ✓ base.py")
    
    # plugins/__init__.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\plugins\__init__.py", "r", encoding="utf-8") as f:
        plugins_init = f.read()
    b64 = base64.b64encode(plugins_init.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/plugins/__init__.py")
    print("  ✓ plugins/__init__.py")
    
    # ocr/plugin.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\plugins\ocr\plugin.py", "r", encoding="utf-8") as f:
        ocr_content = f.read()
    b64 = base64.b64encode(ocr_content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/plugins/ocr/plugin.py")
    print("  ✓ ocr/plugin.py")
    
    # _template/plugin.yaml
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\plugins\_template\plugin.yaml", "r", encoding="utf-8") as f:
        template_content = f.read()
    b64 = base64.b64encode(template_content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/plugins/_template/plugin.yaml")
    print("  ✓ _template/plugin.yaml")
    
    # mcp/server.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\mcp\server.py", "r", encoding="utf-8") as f:
        mcp_content = f.read()
    b64 = base64.b64encode(mcp_content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/mcp/server.py")
    print("  ✓ mcp/server.py")
    
    # storage/manager.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\storage\manager.py", "r", encoding="utf-8") as f:
        storage_content = f.read()
    b64 = base64.b64encode(storage_content.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/storage/manager.py")
    print("  ✓ storage/manager.py")
    
    # storage/__init__.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\storage\__init__.py", "r", encoding="utf-8") as f:
        storage_init = f.read()
    b64 = base64.b64encode(storage_init.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/storage/__init__.py")
    print("  ✓ storage/__init__.py")
    
    # models/__init__.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\models\__init__.py", "r", encoding="utf-8") as f:
        models_init = f.read()
    b64 = base64.b64encode(models_init.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/models/__init__.py")
    print("  ✓ models/__init__.py")
    
    # api/routes.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\api\routes.py", "r", encoding="utf-8") as f:
        api_routes = f.read()
    b64 = base64.b64encode(api_routes.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/api/routes.py")
    print("  ✓ api/routes.py")
    
    # api/__init__.py
    with open(r"G:\dswork\AI\dsh-ai-platform\dsh_core\api\__init__.py", "r", encoding="utf-8") as f:
        api_init = f.read()
    b64 = base64.b64encode(api_init.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/dsh_core/api/__init__.py")
    print("  ✓ api/__init__.py")
    
    # 设置权限
    run(client, f"chown -R ai:wheel {CODE_DIR}")
    print("  ✓ 代码部署完成")

    # 验证
    print("\n[6] 验证安装...")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'import dsh_core; print(\"dsh_core OK\")'")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'from dsh_core.mcp.server import PREDEFINED_TOOLS; print(\"MCP tools:\", len(PREDEFINED_TOOLS))'")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'from dsh_core.plugins import PluginRegistry; print(\"PluginRegistry OK\")'")

    # 创建启动脚本
    print("\n[7] 创建启动脚本...")
    startup = f'''#!/bin/sh
export DSH_HOME={DSH_DIR}
export DSH_VENV={VENV_DIR}
export DSH_CODE={CODE_DIR}
export PYTHONPATH=$DSH_CODE
cd $DSH_CODE
case "$1" in
    mcp) $DSH_VENV/bin/python dsh_core/cli.py mcp --port 3000 ;;
    api) $DSH_VENV/bin/python dsh_core/cli.py api --port 8000 ;;
    all) $DSH_VENV/bin/python dsh_core/cli.py all ;;
    *) echo "用法: $0 {{mcp|api|all}}"; exit 1 ;;
esac
'''
    b64 = base64.b64encode(startup.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {DSH_DIR}/start.sh && chmod +x {DSH_DIR}/start.sh && chown ai:wheel {DSH_DIR}/start.sh")
    print("  ✓ 启动脚本")

    # 创建 rc.d 服务
    rc_script = f'''#!/bin/sh
. /etc/rc.subr
name="dsh"
rcvar="dsh_enable"
DSH_DIR="{DSH_DIR}"
DSH_VENV="{VENV_DIR}"
DSH_CODE="{CODE_DIR}"
command="/usr/sbin/daemon"
command_args="-f -r /bin/sh -c \"export DSH_HOME=$DSH_DIR; export DSH_VENV=$DSH_VENV; export DSH_CODE=$DSH_CODE; export PYTHONPATH=$DSH_CODE; cd $DSH_CODE; $DSH_VENV/bin/python dsh_core/cli.py mcp --port 3000\""
load_rc_config $name
run_rc_command "$1"
'''
    b64 = base64.b64encode(rc_script.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | sudo tee /usr/local/etc/rc.d/dsh > /dev/null && chmod +x /usr/local/etc/rc.d/dsh")
    print("  ✓ rc.d 服务")

    print(f"""
  🎉 部署完成！

  启动:
    ssh ai@{HOST}
    cd {DSH_DIR}
    ./start.sh mcp

  验证:
    curl http://{HOST}:3000/health
    curl http://{HOST}:3000/tools
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
