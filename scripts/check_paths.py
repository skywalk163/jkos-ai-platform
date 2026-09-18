"""检查并修复路径问题"""
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

    # 检查目录结构
    print("\n[2] 检查目录结构...")
    run(client, "ls -la /data/dsh/")
    run(client, "ls -la /data/dsh/code/ 2>/dev/null || echo 'code dir not found'")
    run(client, "ls -la /data/dsh/code/jkos_core/ 2>/dev/null || echo 'jkos_core not found'")
    
    # 检查 cli.py 路径
    print("\n[3] 检查 cli.py...")
    run(client, "ls -la /data/dsh/code/jkos_core/cli.py 2>/dev/null || echo 'cli.py not found'")
    
    # 检查 mcp/server.py
    print("\n[4] 检查 mcp/server.py...")
    run(client, "ls -la /data/dsh/code/jkos_core/mcp/server.py 2>/dev/null || echo 'mcp/server.py not found'")
    
    # 检查 index.html
    print("\n[5] 检查 index.html...")
    run(client, "ls -la /data/dsh/index.html 2>/dev/null || echo 'index.html not found'")
    
    # 检查当前工作目录
    print("\n[6] 检查当前目录...")
    run(client, "pwd")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
