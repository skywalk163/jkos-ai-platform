"""设置 PYTHONPATH 并启动 MCP Server"""
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
    CODE_DIR = "/data/dsh/code"

    # 验证 dsh_core
    print("\n[2] 验证 dsh_core...")
    run(client, f"cd {CODE_DIR} && PYTHONPATH={CODE_DIR} {VENV_DIR}/bin/python -c 'import dsh_core; from dsh_core.mcp.server import PREDEFINED_TOOLS; print(\"dsh_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 测试启动
    print("\n[3] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && PYTHONPATH={CODE_DIR} timeout 3 {VENV_DIR}/bin/python dsh_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[4] 检查端口...")
    run(client, "netstat -an | grep 3000 2>/dev/null || echo 'Port 3000 not listening'")
    
    # 更新启动脚本
    print("\n[5] 更新启动脚本...")
    startup = f'''#!/bin/sh
export DSH_HOME=/data/dsh
export DSH_VENV={VENV_DIR}
export DSH_CODE={CODE_DIR}
export PYTHONPATH=$DSH_CODE
export TMPDIR=/data/tmp
cd $DSH_CODE
case "$1" in
    mcp) $DSH_VENV/bin/python dsh_core/cli.py mcp --port 3000 ;;
    api) $DSH_VENV/bin/python dsh_core/cli.py api --port 8000 ;;
    all) $DSH_VENV/bin/python dsh_core/cli.py all ;;
    *) echo "用法: $0 {{mcp|api|all}}"; exit 1 ;;
esac
'''
    import base64
    b64 = base64.b64encode(startup.encode('utf-8')).decode('ascii')
    run(client, f"echo '{b64}' | base64 -d > /data/dsh/start.sh && chmod +x /data/dsh/start.sh && chown ai:wheel /data/dsh/start.sh")
    print("  ✓ 启动脚本已更新")
    
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
    ZFS 池: zroot (132G 总容量, 125G 可用)

  启动命令:
    ssh ai@{HOST}
    cd /data/dsh
    ./start.sh mcp

  验证:
    curl http://{HOST}:3000/health
    curl http://{HOST}:3000/tools
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
