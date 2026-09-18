"""验证 MCP Server API"""
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

    # 验证 /health
    print("\n[2] 验证 /health...")
    run(client, "curl -s http://localhost:3000/health")
    
    # 验证 /tools
    print("\n[3] 验证 /tools...")
    run(client, "curl -s http://localhost:3000/tools")
    
    # 验证 /api/v1/health
    print("\n[4] 验证 /api/v1/health...")
    run(client, "curl -s http://localhost:3000/api/v1/health")
    
    # 检查进程
    print("\n[5] 检查进程...")
    run(client, "ps aux | grep dsh | grep -v grep | head -5")
    
    # 检查日志
    print("\n[6] 检查日志...")
    run(client, "tail -5 /data/dsh/logs/mcp.log")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
