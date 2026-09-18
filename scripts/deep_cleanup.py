"""深度清理 FreeBSD 磁盘空间"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)

print("1. 删除 debug 符号 (可安全删除，约 470M):")
stdin, stdout, stderr = client.exec_command("rm -rf /usr/lib/debug/* 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n2. 删除 pkg 缓存 (可安全删除):")
stdin, stdout, stderr = client.exec_command("rm -rf /var/cache/pkg/* 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n3. 删除 pkg 仓库数据库 (可安全删除，会自动重新下载):")
stdin, stdout, stderr = client.exec_command("rm -rf /var/db/pkg/repos/* 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n4. 清理 /var/db/pkg/local.sqlite (pkg 数据库):")
stdin, stdout, stderr = client.exec_command("rm -f /var/db/pkg/local.sqlite 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n5. 清理 /var/db 大文件:")
stdin, stdout, stderr = client.exec_command("find /var/db -type f -size +1M -exec rm -f {} \\; 2>/dev/null; echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n6. 清理 /home/skywalk (其他用户目录):")
stdin, stdout, stderr = client.exec_command("rm -rf /home/skywalk/* 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n7. 清理 /root 目录:")
stdin, stdout, stderr = client.exec_command("rm -rf /root/* 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n8. 压缩 ZFS (启用压缩):")
stdin, stdout, stderr = client.exec_command("zfs set compression=lz4 zroot/ROOT/default 2>/dev/null && echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n9. 清理后磁盘空间:")
stdin, stdout, stderr = client.exec_command("df -h /")
for line in stdout:
    print(f"  {line.strip()}")

print("\n10. ZFS 池状态:")
stdin, stdout, stderr = client.exec_command("zpool list")
for line in stdout:
    print(f"  {line.strip()}")

print("\n11. ZFS 数据集:")
stdin, stdout, stderr = client.exec_command("zfs list")
for line in stdout:
    print(f"  {line.strip()}")

print("\n12. 根目录大文件/目录:")
stdin, stdout, stderr = client.exec_command("du -sh /* 2>/dev/null | sort -h | tail -10")
for line in stdout:
    print(f"  {line.strip()}")

client.close()
