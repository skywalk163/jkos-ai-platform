"""SSH 到 FreeBSD 192.168.0.82 执行命令（凭据从 G:\\dswork\\AI\\.env 读取，不落日志）

账号策略: 默认先试 workbuddy（用户指定），认证失败自动回退 ai（已验证可用）。
        可用 --user workbuddy|ai 强制指定。

用法:
    python scripts/ssh82.py "uname -a" "freebsd-version"      # 多条命令依次执行
    python scripts/ssh82.py - < local_script.sh                # 从 stdin 读脚本整体执行
"""
import pathlib
import sys

import paramiko

ROOT = pathlib.Path(__file__).resolve().parents[2]  # G:\dswork\AI
ENV_FILE = ROOT / ".env"

ACCOUNTS = [("workbuddy", "SSH_USER_WORKBUDDY", "SSH_PASS_WORKBUDDY"),
            ("ai", "SSH_USER_AI", "SSH_PASS_AI")]


def load_env(path: pathlib.Path) -> dict:
    env = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def connect(env: dict, host: str, only_user: str | None):
    errors = []
    for name, user_key, pass_key in ACCOUNTS:
        if only_user and name != only_user:
            continue
        user, password = env.get(user_key), env.get(pass_key)
        if not (user and password):
            errors.append(f"{name}: .env 缺少 {user_key}/{pass_key}")
            continue
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, username=user, password=password, timeout=20,
                           banner_timeout=20, auth_timeout=20,
                           allow_agent=False, look_for_keys=False)
            print(f"[登录成功] {user}@{host}", file=sys.stderr)
            return client, user
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}")
            try:
                client.close()
            except Exception:
                pass
    detail = "; ".join(errors) or "无可用账号"
    raise SystemExit(f"[错误] SSH 登录失败 -> {detail}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = sys.argv[1:]
    only_user = None
    if "--user" in args:
        i = args.index("--user")
        only_user = args[i + 1]
        del args[i:i + 2]

    env = load_env(ENV_FILE)
    host = env.get("82SSH_HOST")
    if not host:
        raise SystemExit(f"[错误] .env 缺少 82SSH_HOST")

    client, user = connect(env, host, only_user)
    exit_code = 0
    try:
        if args and args[0] == "-":
            script = sys.stdin.read().replace("\r\n", "\n").replace("\r", "\n")
            commands = [("stdin-script", script)]
        else:
            commands = [(c, c) for c in args]
        if not commands:
            print('用法: python scripts/ssh82.py "cmd" ... | python scripts/ssh82.py - < script.sh '
                  '[--user workbuddy|ai]')
            return 2

        for title, cmd in commands:
            print(f"\n$ {title}")
            print("-" * 60)
            stdin, stdout, stderr = client.exec_command(cmd, timeout=1800)
            out = stdout.read().decode("utf-8", errors="replace").rstrip()
            err = stderr.read().decode("utf-8", errors="replace").rstrip()
            code = stdout.channel.recv_exit_status()
            if out:
                print(out)
            if err:
                print(f"[stderr]\n{err}", file=sys.stderr)
            print(f"[exit={code}]")
            exit_code = exit_code or code
    finally:
        client.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
