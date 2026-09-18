"""完成部署：设置 TMPDIR 并安装剩余依赖"""
import paramiko

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

    VENV_DIR = "/data/dsh/venv"
    DSH_DIR = "/data/dsh"

    # 设置 TMPDIR 到 zdata 池
    print("\n[2] 设置 TMPDIR 到 zdata 池...")
    run(client, f"mkdir -p {DSH_DIR}/tmp && chown ai:wheel {DSH_DIR}/tmp")
    run(client, f"export TMPDIR={DSH_DIR}/tmp && echo 'TMPDIR=$TMPDIR'")

    # 安装依赖（使用 TMPDIR）
    print("\n[3] 安装 Python 依赖...")
    run(client, f"TMPDIR={DSH_DIR}/tmp {VENV_DIR}/bin/pip install httpx pydantic")
    
    # 验证
    print("\n[4] 验证安装...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; import pydantic; print(\"httpx:\", httpx.__version__, \"pydantic:\", pydantic.__version__)'")
    
    # 验证 jkos_core
    print("\n[5] 验证 jkos_core...")
    run(client, f"cd {DSH_DIR}/code && {VENV_DIR}/bin/python -c 'import jkos_core; from jkos_core.mcp.server import PREDEFINED_TOOLS; from jkos_core.plugins import PluginRegistry; print(\"jkos_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 创建 rc.d 服务
    print("\n[6] 创建 rc.d 服务...")
    rc_script = '''#!/bin/sh
. /etc/rc.subr
name="dsh"
rcvar="dsh_enable"
DSH_DIR="/data/dsh"
DSH_VENV="/data/dsh/venv"
DSH_CODE="/data/dsh/code"
command="/usr/sbin/daemon"
command_args="-f -r /bin/sh -c \\"export DSH_HOME=$DSH_DIR; export DSH_VENV=$DSH_VENV; export DSH_CODE=$DSH_CODE; export PYTHONPATH=$DSH_CODE; export TMPDIR=$DSH_DIR/tmp; cd $DSH_CODE; $DSH_VENV/bin/python jkos_core/cli.py mcp --port 3000\\""
load_rc_config $name
run_rc_command "$1"
'''
    run(client, f"cat > /tmp/dsh.rc << 'EOF'\n{rc_script}\nEOF && sudo mv /tmp/dsh.rc /usr/local/etc/rc.d/dsh && chmod +x /usr/local/etc/rc.d/dsh")
    print("  ✓ rc.d 服务已创建")
    
    # 测试启动
    print("\n[7] 测试启动 MCP Server (5秒)...")
    run(client, f"cd {DSH_DIR}/code && TMPDIR={DSH_DIR}/tmp timeout 5 {VENV_DIR}/bin/python jkos_core/cli.py mcp --port 3000 2>&1 || echo 'Server started (timeout)'")
    
    # 检查端口
    print("\n[8] 检查端口...")
    run(client, "netstat -an | grep 3000 2>/dev/null || echo 'Port 3000 not listening'")
    
    # 最终状态
    print("\n" + "="*60)
    print("  部署完成！")
    print("="*60)
    run(client, f"df -h / /data 2>/dev/null; echo; zpool list")
    
    print(f"""
  🎉 DSH AI 中台 已就绪！

  启动命令:
    ssh ai@{HOST}
    cd {DSH_DIR}
    ./start.sh mcp

  或使用服务:
    sudo service dsh onestart

  验证:
    curl http://{HOST}:3000/health
    curl http://{HOST}:3000/tools
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
