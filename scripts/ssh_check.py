"""SSH 连接 FreeBSD 并执行部署"""
import paramiko
import time
import sys

HOST = "192.168.0.82"
USER = "ai"
PASS = "ai2026"

def run_ssh_command(client, cmd, get_pty=False):
    """执行命令并返回输出"""
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=get_pty)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    return exit_status, out, err

def main():
    print(f"[1] 连接 {HOST} ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(HOST, username=USER, password=PASS, timeout=15)
        print("[1] 连接成功 ✓")
    except Exception as e:
        print(f"[1] 连接失败: {e}")
        return 1

    # 检查系统信息
    print("\n[2] 系统信息:")
    for cmd in ["uname -a", "freebsd-version", "whoami", "id"]:
        status, out, err = run_ssh_command(client, cmd)
        print(f"  $ {cmd}")
        if out:
            for line in out.split('\n'):
                print(f"    {line}")
        if err:
            print(f"    [err] {err}")

    # 检查网络
    print("\n[3] 网络检查:")
    status, out, err = run_ssh_command(client, "ifconfig | grep -A2 'inet ' | grep -v 127.0.0.1")
    if out:
        for line in out.split('\n'):
            print(f"    {line}")

    # 检查磁盘
    print("\n[4] 磁盘空间:")
    status, out, err = run_ssh_command(client, "df -h / /var /tmp 2>/dev/null || df -h /")
    if out:
        for line in out.split('\n'):
            print(f"    {line}")

    # 检查 ZFS
    print("\n[5] ZFS 状态:")
    status, out, err = run_ssh_command(client, "zfs list 2>/dev/null | head -10")
    if out:
        for line in out.split('\n'):
            print(f"    {line}")
    else:
        print("    (无 ZFS 数据集或 zfs 命令不可用)")

    # 检查已安装包
    print("\n[6] 已安装关键包:")
    for pkg in ["python3.11", "python3.12", "ffmpeg", "git", "sudo"]:
        status, out, err = run_ssh_command(client, f"pkg info -e {pkg} 2>/dev/null && echo '{pkg} installed' || echo '{pkg} NOT found'")
        if out:
            for line in out.split('\n'):
                if line.strip():
                    print(f"    {line}")

    # 检查 sudo
    print("\n[7] sudo 权限:")
    status, out, err = run_ssh_command(client, "sudo -n whoami 2>&1")
    if status == 0:
        print(f"    sudo 无需密码: {out.strip()}")
    else:
        print(f"    sudo 需要密码: {err.strip()}")

    # 检查当前目录
    print("\n[8] 当前目录:")
    status, out, err = run_ssh_command(client, "pwd; ls -la ~")
    if out:
        for line in out.split('\n'):
            print(f"    {line}")

    client.close()
    print("\n[完成] 系统检查完毕")
    return 0

if __name__ == "__main__":
    sys.exit(main())
