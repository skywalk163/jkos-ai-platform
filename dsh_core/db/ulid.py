"""DSH 数据层 - ULID 主键生成（纯标准库，无第三方依赖）

ULID = 48bit 毫秒时间戳 + 80bit 随机数，Crockford Base32 编码，26 字符。
按设计文档 §2.6 主键规范：字典序即时间序，适合作为 SQLite 聚簇主键。
"""
from __future__ import annotations

import os
import re
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def new_ulid() -> str:
    """生成单调可排序的 ULID 字符串"""
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts << 80) | rand
    return "".join(ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))


def is_ulid(value: str) -> bool:
    """校验是否为合法 ULID（排除易混淆字符 I/L/O/U）"""
    return bool(value) and bool(_PATTERN.match(value))
