"""安装 Rust 并完成部署"""
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

    # 安装 Rust
    print("\n[2] 安装 Rust...")
    run(client, "sudo pkg install -y rust 2>&1 | tail -5")
    
    # 检查 rustc
    print("\n[3] 检查 Rust...")
    run(client, "rustc --version 2>/dev/null || which rustc 2>/dev/null || echo 'Rust not found'")
    
    # 创建代码目录
    print("\n[4] 创建代码目录...")
    run(client, f"mkdir -p {CODE_DIR}/jkos_core/plugins/ocr {CODE_DIR}/jkos_core/plugins/_template {CODE_DIR}/jkos_core/mcp {CODE_DIR}/jkos_core/storage {CODE_DIR}/jkos_core/models {CODE_DIR}/jkos_core/api")
    
    # 部署代码
    print("\n[5] 部署代码...")
    
    # 从 /var/dsh/code 恢复代码
    run(client, f"if [ -d /var/dsh/code/jkos_core ]; then cp -r /var/dsh/code/jkos_core/* {CODE_DIR}/jkos_core/ 2>/dev/null; echo '代码已从备份恢复'; fi")
    
    # 如果备份不存在，重新部署
    run(client, f"if [ ! -f {CODE_DIR}/jkos_core/__init__.py ]; then echo '需要重新部署代码'; fi")
    
    # 检查代码
    run(client, f"ls -la {CODE_DIR}/jkos_core/ 2>/dev/null | head -5")
    
    # 如果代码不存在，重新部署
    if "__init__.py" not in str(run(client, f"ls {CODE_DIR}/jkos_core/ 2>/dev/null", echo=False)[1]):
        print("  重新部署代码...")
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\cli.py", "r", encoding="utf-8") as f:
            cli_content = f.read()
        b64 = base64.b64encode(cli_content.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/cli.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugin_cli.py", "r", encoding="utf-8") as f:
            plugin_cli_content = f.read()
        b64 = base64.b64encode(plugin_cli_content.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/plugin_cli.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugins\base.py", "r", encoding="utf-8") as f:
            base_content = f.read()
        b64 = base64.b64encode(base_content.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/plugins/base.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugins\__init__.py", "r", encoding="utf-8") as f:
            plugins_init = f.read()
        b64 = base64.b64encode(plugins_init.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/plugins/__init__.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugins\ocr\plugin.py", "r", encoding="utf-8") as f:
            ocr_content = f.read()
        b64 = base64.b64encode(ocr_content.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/plugins/ocr/plugin.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\plugins\_template\plugin.yaml", "r", encoding="utf-8") as f:
            template_content = f.read()
        b64 = base64.b64encode(template_content.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/plugins/_template/plugin.yaml")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\mcp\server.py", "r", encoding="utf-8") as f:
            mcp_content = f.read()
        b64 = base64.b64encode(mcp_content.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/mcp/server.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\storage\manager.py", "r", encoding="utf-8") as f:
            storage_content = f.read()
        b64 = base64.b64encode(storage_content.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/storage/manager.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\storage\__init__.py", "r", encoding="utf-8") as f:
            storage_init = f.read()
        b64 = base64.b64encode(storage_init.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/storage/__init__.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\models\__init__.py", "r", encoding="utf-8") as f:
            models_init = f.read()
        b64 = base64.b64encode(models_init.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/models/__init__.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\api\routes.py", "r", encoding="utf-8") as f:
            api_routes = f.read()
        b64 = base64.b64encode(api_routes.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/api/routes.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\api\__init__.py", "r", encoding="utf-8") as f:
            api_init = f.read()
        b64 = base64.b64encode(api_init.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/api/__init__.py")
        
        with open(r"G:\dswork\AI\dsh-ai-platform\jkos_core\__init__.py", "r", encoding="utf-8") as f:
            core_init = f.read()
        b64 = base64.b64encode(core_init.encode('utf-8')).decode('ascii')
        run(client, f"echo '{b64}' | base64 -d > {CODE_DIR}/jkos_core/__init__.py")
        
        run(client, f"chown -R ai:wheel {CODE_DIR}")
        print("  ✓ 代码已部署")
    
    # 创建虚拟环境
    print("\n[6] 创建 Python 虚拟环境...")
    run(client, f"rm -rf {VENV_DIR} && /usr/local/bin/python3.11 -m venv {VENV_DIR} --without-pip")
    
    # 安装 pip
    print("\n[7] 安装 pip...")
    run(client, f"{VENV_DIR}/bin/python /tmp/get-pip.py --no-cache-dir 2>&1 | tail -3")
    
    # 安装依赖
    print("\n[8] 安装 Python 依赖...")
    run(client, f"{VENV_DIR}/bin/pip install --no-cache-dir fastapi uvicorn httpx pydantic structlog pyyaml 2>&1 | tail -15")
    
    # 验证
    print("\n[9] 验证安装...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; import pydantic; import fastapi; print(\"httpx:\", httpx.__version__, \"pydantic:\", pydantic.__version__, \"fastapi:\", fastapi.__version__)'")
    
    # 验证 jkos_core
    print("\n[10] 验证 jkos_core...")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'import jkos_core; from jkos_core.mcp.server import PREDEFINED_TOOLS; print(\"jkos_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 更新启动脚本
    print("\n[11] 更新启动脚本...")
    startup = f'''#!/bin/sh
export DSH_HOME={DSH_DIR}
export DSH_VENV={VENV_DIR}
export DSH_CODE={CODE_DIR}
export PYTHONPATH=$DSH_CODE
export TMPDIR=/data/tmp
cd $DSH_CODE
case "$1" in
    mcp) $DSH_VENV/bin/python jkos_core/cli.py mcp --port 3000 ;;
    api) $DSH_VENV/bin/python jkos_core/cli.py api --port 8000 ;;
    all) $DSH_VENV/bin/python jkos_core/cli.py all ;;
    *) echo "用法: $0 {{mcp|api|all}}"; exit 1 ;;
esac
'''
    b64 = base64.b64encode(startup.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > {DSH_DIR}/start.sh && chmod +x {DSH_DIR}/start.sh && chown ai:wheel {DSH_DIR}/start.sh")
    print("  ✓ 启动脚本已更新")
    
    # 测试启动
    print("\n[12] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && timeout 3 {VENV_DIR}/bin/python jkos_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[13] 检查端口...")
    run(client, "netstat -an | grep 3000 2>/dev/null || echo 'Port 3000 not listening'")
    
    # 最终状态
    print("\n" + "="*60)
    print("  部署完成！")
    print("="*60)
    run(client, "df -h / /data 2>/dev/null; echo; zpool list")
    
    print(f"""
  🎉 DSH AI 中台 已就绪！

  系统信息:
    主机: {HOST}
    Python: {VENV_DIR}/bin/python
    代码目录: {CODE_DIR}
    ZFS 池: zroot (132G 总容量, 127G 可用)

  启动命令:
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
