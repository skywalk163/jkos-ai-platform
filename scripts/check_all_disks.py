"""检查所有磁盘并挂载到 zdata"""
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

    # 检查所有磁盘
    print("\n[2] 所有磁盘:")
    run(client, "geom disk list 2>/dev/null | grep -E 'Geom name:|Mediasize:'")
    
    print("\n[3] ZFS 池状态:")
    run(client, "zpool status -v")
    
    print("\n[4] 所有 ZFS 池:")
    run(client, "zpool list")
    
    print("\n[5] 所有 ZFS 数据集:")
    run(client, "zfs list")
    
    print("\n[6] 检查 /dev 下所有设备:")
    run(client, "ls -la /dev | grep -E 'ada|da|cd' | head -20")
    
    print("\n[7] 检查 camcontrol:")
    run(client, "camcontrol devlist 2>/dev/null")
    
    print("\n[8] 检查 /var/log 中的磁盘信息:")
    run(client, "dmesg | grep -i 'ada\|da' | tail -20")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
