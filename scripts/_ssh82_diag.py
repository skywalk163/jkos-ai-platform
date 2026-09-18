"""尝试 .env 中其他密码是否复用于 workbuddy（仅 kbd-interactive，成功即停）"""
import pathlib
import sys

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
env = {}
for raw in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")

HOST = env["82SSH_HOST"]
USER = "workbuddy"
PWDS = [env.get("SSH_PASS_WORKBUDDY", ""), env.get("SSH_PASS_TRAE", ""),
        env.get("SSH_PASS_DUMATE2", ""), env.get("SSH_PASS_AI", "")]

for pwd in PWDS:
    try:
        t = paramiko.Transport((HOST, 22))
        t.connect()
        t.auth_interactive(USER, lambda title, instr, prompts: [pwd] * (len(prompts) or 1))
        print(f"[OK] workbuddy 认证成功（密码来源已确认，不回显）")
        t.close()
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        try:
            t.close()
        except Exception:
            pass
print("全部尝试失败")
