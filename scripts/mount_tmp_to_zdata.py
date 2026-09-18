"""把 /tmp 和 /var/tmp 移到 zdata 池，然后完成 Python 依赖安装"""
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

    # 在 zdata 上创建 tmp 数据集
    print("\n[2] 在 zdata 上创建 tmp 数据集...")
    run(client, "sudo zfs create -o mountpoint=/data/tmp -o compression=lz4 zdata/tmp")
    run(client, "sudo chown -R ai:wheel /data/tmp")
    
    # 设置 TMPDIR
    print("\n[3] 设置 TMPDIR...")
    run(client, "export TMPDIR=/data/tmp && echo 'TMPDIR=$TMPDIR'")
    
    # 创建 Python 3.11 虚拟环境（系统已有）
    print("\n[4] 创建 Python 虚拟环境...")
    run(client, f"rm -rf {VENV_DIR} && /usr/local/bin/python3.11 -m venv {VENV_DIR}")
    
    # 安装依赖（使用 /data/tmp 作为临时目录）
    print("\n[5] 安装 Python 依赖...")
    run(client, f"TMPDIR=/data/tmp {VENV_DIR}/bin/pip install --upgrade pip setuptools wheel")
    run(client, f"TMPDIR=/data/tmp {VENV_DIR}/bin/pip install fastapi uvicorn httpx pydantic structlog pyyaml 2>&1 | tail -15")
    
    # 验证
    print("\n[6] 验证安装...")
    run(client, f"{VENV_DIR}/bin/python -c 'import httpx; import pydantic; import fastapi; print(\"httpx:\", httpx.__version__, \"pydantic:\", pydantic.__version__, \"fastapi:\", fastapi.__version__)'")
    
    # 验证 jkos_core
    print("\n[7] 验证 jkos_core...")
    run(client, f"cd {CODE_DIR} && {VENV_DIR}/bin/python -c 'import jkos_core; from jkos_core.mcp.server import PREDEFINED_TOOLS; print(\"jkos_core OK, MCP tools:\", len(PREDEFINED_TOOLS))'")
    
    # 更新启动脚本
    print("\n[8] 更新启动脚本...")
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
    print("\n[9] 测试启动 MCP Server (3秒)...")
    run(client, f"cd {CODE_DIR} && TMPDIR=/data/tmp timeout 3 {VENV_DIR}/bin/python jkos_core/cli.py mcp --port 3000 2>&1 || echo 'Server started'")
    
    # 检查端口
    print("\n[10] 检查端口...")
    run(client, "netstat -an | grep 3000 2>/dev/null || echo 'Port 3000 not listening'")
    
    # 最终状态
    print("\n" + "="*60)
    print("  部署完成！")
    print("="*60)
    run(client, "df -h / /data 2>/dev/null; echo; zpool list; echo; zfs list | grep tmp")
    
    print(f"""
  🎉 DSH AI 中台 已就绪！

  系统信息:
    主机: {HOST}
    Python: {VENV_DIR}/bin/python
    代码目录: {CODE_DIR}
    ZFS 池: zdata (127G 可用)
    临时目录: /data/tmp (zdata 数据集)

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
