"""尝试将 ada1 加入 zroot 池"""
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

    # 检查当前池状态
    print("\n[2] 当前 ZFS 池状态:")
    run(client, "zpool status -v")
    
    print("\n[3] 磁盘信息:")
    run(client, "geom disk list ada0 ada1 2>/dev/null | grep -E 'Geom name:|Mediasize|Sectorsize'")
    
    print("\n[4] 分区信息:")
    run(client, "gpart show ada0 2>/dev/null")
    run(client, "gpart show ada1 2>/dev/null")
    
    # 尝试将 ada1 加入 zroot
    print("\n[5] 尝试将 ada1 加入 zroot...")
    run(client, "sudo zpool add zroot ada1 2>&1")
    
    # 如果失败，尝试 ada1p1
    print("\n[6] 尝试将 ada1p1 加入 zroot...")
    run(client, "sudo zpool add zroot ada1p1 2>&1")
    
    # 如果还失败，尝试 gpt/dsh_data
    print("\n[7] 尝试将 gpt/dsh_data 加入 zroot...")
    run(client, "sudo zpool add zroot gpt/dsh_data 2>&1")
    
    # 最终状态
    print("\n[8] 最终 ZFS 池状态:")
    run(client, "zpool list")
    run(client, "zpool status -v")
    
    print("\n[9] 磁盘空间:")
    run(client, "df -h /")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
