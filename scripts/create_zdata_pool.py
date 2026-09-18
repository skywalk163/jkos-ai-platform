"""在 ada1 上创建新 ZFS 池并部署 DSH"""
import paramiko

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASS, timeout=15)

print("=" * 60)
print("第一步：在 ada1 上创建分区")
print("=" * 60)

# 创建 GPT 分区表
print("  创建 GPT 分区表...")
stdin, stdout, stderr = client.exec_command("sudo gpart create -s gpt ada1 2>&1")
for line in stdout:
    print(f"  {line.strip()}")
for line in stderr:
    print(f"  [err] {line.strip()}")

# 创建整个磁盘的分区
print("  创建分区...")
stdin, stdout, stderr = client.exec_command("sudo gpart add -t freebsd-zfs -l dsh_data ada1 2>&1")
for line in stdout:
    print(f"  {line.strip()}")
for line in stderr:
    print(f"  [err] {line.strip()}")

print("\n" + "=" * 60)
print("第二步：创建 ZFS 池")
print("=" * 60)

# 创建 zdata 池
print("  创建 zdata 池...")
stdin, stdout, stderr = client.exec_command("sudo zpool create -o altroot=/data zdata dastrada1 2>&1 || sudo zpool create zdata /dev/gpt/dsh_data 2>&1")
for line in stdout:
    print(f"  {line.strip()}")
for line in stderr:
    print(f"  [err] {line.strip()}")

# 尝试不同的设备路径
print("  尝试 /dev/ada1...")
stdin, stdout, stderr = client.exec_command("sudo zpool create zdata /dev/ada1 2>&1")
for line in stdout:
    print(f"  {line.strip()}")
for line in stderr:
    print(f"  [err] {line.strip()}")

print("\n" + "=" * 60)
print("第三步：检查 ZFS 池状态")
print("=" * 60)

stdin, stdout, stderr = client.exec_command("zpool list")
for line in stdout:
    print(f"  {line.strip()}")

stdin, stdout, stderr = client.exec_command("zfs list")
for line in stdout:
    print(f"  {line.strip()}")

print("\n" + "=" * 60)
print("第四步：在 zdata 上创建 DSH 数据集")
print("=" * 60)

stdin, stdout, stderr = client.exec_command("sudo zfs create -o mountpoint=/data/dsh -o compression=lz4 -o atime=off zdata/dsh 2>&1")
for line in stdout:
    print(f"  {line.strip()}")
for line in stderr:
    print(f"  [err] {line.strip()}")

stdin, stdout, stderr = client.exec_command("zfs list | grep dsh")
for line in stdout:
    print(f"  {line.strip()}")

print("\n" + "=" * 60)
print("第五步：设置权限")
print("=" * 60)

stdin, stdout, stderr = client.exec_command("sudo chown -R ai:wheel /data/dsh 2>&1")
for line in stdout:
    print(f"  {line.strip()}")

stdin, stdout, stderr = client.exec_command("ls -la /data/dsh 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

print("\n" + "=" * 60)
print("第六步：检查磁盘空间")
print("=" * 60)

stdin, stdout, stderr = client.exec_command("df -h / /data 2>/dev/null")
for line in stdout:
    print(f"  {line.strip()}")

stdin, stdout, stderr = client.exec_command("zpool list")
for line in stdout:
    print(f"  {line.strip()}")

client.close()
