"""清理 FreeBSD 磁盘空间"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)

print("磁盘空间分析:")
stdin, stdout, stderr = client.exec_command("df -h /")
for line in stdout:
    print(f"  {line.strip()}")

print("\nZFS 数据集:")
stdin, stdout, stderr = client.exec_command("zfs list")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理 /tmp:")
stdin, stdout, stderr = client.exec_command("rm -rf /tmp/* 2>/dev/null; echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理 /var/tmp:")
stdin, stdout, stderr = client.exec_command("rm -rf /var/tmp/* 2>/dev/null; echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理 pkg 缓存:")
stdin, stdout, stderr = client.exec_command("pkg clean -y 2>/dev/null; echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理 /var/cache:")
stdin, stdout, stderr = client.exec_command("rm -rf /var/cache/* 2>/dev/null; echo 'done'")
for line in stdout:
    print(f"  {line.strip()}")

print("\n清理后磁盘空间:")
stdin, stdout, stderr = client.exec_command("df -h /")
for line in stdout:
    print(f"  {line.strip()}")

print("\nZFS 快照:")
stdin, stdout, stderr = client.exec_command("zfs list -t snapshot | head -10")
for line in stdout:
    print(f"  {line.strip()}")

client.close()
