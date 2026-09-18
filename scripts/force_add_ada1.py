"""强制将 ada1 加入 zroot"""
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

    # 检查 ada0p4 的扇区大小
    print("\n[2] 检查 ada0p4 扇区大小:")
    run(client, "geom disk list ada0 2>/dev/null | grep -E 'Sectorsize|Mediasize'")
    
    # 尝试强制添加
    print("\n[3] 尝试强制添加 ada1 到 zroot...")
    run(client, "sudo zpool add -f zroot ada1 2>&1")
    
    # 如果失败，尝试 ada1p1
    print("\n[4] 尝试强制添加 ada1p1 到 zroot...")
    run(client, "sudo zpool add -f zroot ada1p1 2>&1")
    
    # 最终状态
    print("\n[5] 最终 ZFS 池状态:")
    run(client, "zpool status -v")
    run(client, "zpool list")
    
    print("\n[6] 磁盘空间:")
    run(client, "df -h /")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
