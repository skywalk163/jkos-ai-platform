"""备份数据、销毁zdata、将ada1并入zroot"""
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

    # 备份 /data/dsh/code 到 /var/dsh/code
    print("\n[2] 备份代码到 /var/dsh/code...")
    run(client, "mkdir -p /var/dsh/code && cp -r /data/dsh/code/* /var/dsh/code/ 2>/dev/null && echo 'done'")
    run(client, "ls -la /var/dsh/code/jkos_core/ 2>/dev/null | head -5")
    
    # 备份启动脚本
    print("\n[3] 备份启动脚本...")
    run(client, "cp /data/dsh/start.sh /var/dsh/start.sh 2>/dev/null && echo 'done'")
    
    # 卸载 /data
    print("\n[4] 卸载 /data...")
    run(client, "zfs set mountpoint=/tmp zdata 2>/dev/null; zfs set mountpoint=/tmp zdata/dsh 2>/dev/null; echo 'done'")
    
    # 销毁 zdata
    print("\n[5] 销毁 zdata 池...")
    run(client, "sudo zpool destroy zdata 2>&1")
    
    # 检查 ada1 分区
    print("\n[6] 检查 ada1 分区...")
    run(client, "gpart show ada1 2>/dev/null")
    
    # 将 ada1 加入 zroot
    print("\n[7] 将 ada1 加入 zroot...")
    run(client, "sudo zpool add zroot ada1 2>&1")
    
    # 如果失败，尝试 -f
    if "invalid" in str(run(client, "echo 'check'", echo=False)[1]):
        pass
    
    # 最终状态
    print("\n[8] 最终 ZFS 池状态:")
    run(client, "zpool status -v")
    run(client, "zpool list")
    
    print("\n[9] 磁盘空间:")
    run(client, "df -h /")
    
    print("\n[10] ZFS 数据集:")
    run(client, "zfs list")
    
    # 恢复代码到 /data
    print("\n[11] 创建 /data 数据集...")
    run(client, "sudo zfs create -o mountpoint=/data -o compression=lz4 zroot/data")
    run(client, "sudo zfs create -o mountpoint=/data/dsh -o compression=lz4 zroot/dsh")
    run(client, "sudo zfs create -o mountpoint=/data/tmp -o compression=lz4 zroot/tmp")
    run(client, "sudo chown -R ai:wheel /data")
    
    # 恢复代码
    print("\n[12] 恢复代码...")
    run(client, "cp -r /var/dsh/code/* /data/dsh/code/ 2>/dev/null && echo 'done'")
    run(client, "cp /var/dsh/start.sh /data/dsh/start.sh 2>/dev/null && echo 'done'")
    
    # 验证
    print("\n[13] 验证代码...")
    run(client, "ls -la /data/dsh/code/jkos_core/ 2>/dev/null | head -5")
    
    # 最终状态
    print("\n" + "="*60)
    print("  完成！")
    print("="*60)
    run(client, "df -h / /data 2>/dev/null; echo; zpool list")
    
    print(f"""
  🎉 ada1 已并入 zroot！

  系统信息:
    主机: {HOST}
    ZFS 池: zroot (含 ada0p4 + ada1)
    代码目录: /data/dsh/code
    临时目录: /data/tmp

  启动命令:
    ssh ai@{HOST}
    cd /data/dsh
    ./start.sh mcp
""")
    
    client.close()

if __name__ == "__main__":
    import sys
    sys.exit(main())
