"""DSH 认证 - JWT (HS256) 纯标准库实现

不引入 PyJWT，规避 FreeBSD 平台的编译依赖（开发计划 M0 技术决策）。
claims 约定：sub=用户, tid=租户id, tenant=租户code, roles=[角色列表],
           type=access|refresh, jti=唯一标识（用于刷新令牌黑名单）。
安全基线：HMAC-SHA256 + hmac.compare_digest 常量时间比较 + 强制密钥长度 >= 16。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

SECRET_MIN_LEN = 16

# ─── 刷新令牌黑名单（进程内，生产环境应替换为 Redis）───
_refresh_blacklist: Dict[str, float] = {}


def _gc_blacklist() -> None:
    """惰性清理已过期黑名单条目"""
    now = time.time()
    expired = [jti for jti, exp in _refresh_blacklist.items() if exp < now]
    for jti in expired:
        del _refresh_blacklist[jti]


def blacklist_refresh_token(jti: str, expires_at: float) -> None:
    """将刷新令牌加入黑名单（登出时调用）"""
    _gc_blacklist()
    _refresh_blacklist[jti] = expires_at


def is_refresh_token_blacklisted(jti: str) -> bool:
    """检查刷新令牌是否已被撤销"""
    _gc_blacklist()
    return jti in _refresh_blacklist


class JWTError(Exception):
    """JWT 基础异常"""


class TokenInvalid(JWTError):
    """Token 无效（格式/签名/算法错误）"""


class TokenExpired(JWTError):
    """Token 已过期"""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def encode(payload: Dict[str, Any], secret: str, expires_in: int = 86400) -> str:
    """签发 JWT；expires_in 可为负数（用于测试过期场景）"""
    if not secret or len(secret) < SECRET_MIN_LEN:
        raise JWTError("JWT 密钥缺失或过短（需 >= 16 字符）")
    now = int(time.time())
    body = {"iat": now, "exp": now + int(expires_in)}
    body.update(payload)
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(body, separators=(',', ':')).encode())}"
    )
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def issue_access_token(
    user_id: str, tenant_id: str, tenant_code: str, roles: list,
    secret: str, expires_in: int = 900, jti: Optional[str] = None,
) -> str:
    """签发短期访问令牌（默认 15 分钟）

    Args:
        user_id: 用户标识
        tenant_id: 租户 ID
        tenant_code: 租户代码
        roles: 角色列表
        secret: JWT 密钥
        expires_in: 过期秒数（默认 900 = 15 分钟）
        jti: 可选唯一标识（用于撤销）
    """
    payload = {
        "sub": user_id,
        "tid": tenant_id,
        "tenant": tenant_code,
        "roles": list(roles),
        "type": "access",
    }
    if jti:
        payload["jti"] = jti
    return encode(payload, secret, expires_in)


def issue_refresh_token(
    user_id: str, tenant_id: str, tenant_code: str, roles: list,
    secret: str, expires_in: int = 604800,
) -> str:
    """签发长期刷新令牌（默认 7 天），含唯一 jti 用于黑名单撤销"""
    payload = {
        "sub": user_id,
        "tid": tenant_id,
        "tenant": tenant_code,
        "roles": list(roles),
        "type": "refresh",
        "jti": secrets.token_urlsafe(16),
    }
    return encode(payload, secret, expires_in)


def refresh_access_token(refresh_token: str, secret: str) -> str:
    """用刷新令牌换取新的访问令牌

    Raises:
        TokenInvalid: 令牌格式/签名错误
        TokenExpired: 令牌已过期
        JWTError: 令牌类型错误或已被撤销
    """
    claims = decode(refresh_token, secret)
    if claims.get("type") != "refresh":
        raise JWTError("令牌类型错误：需要 refresh token")
    jti = claims.get("jti")
    if jti and is_refresh_token_blacklisted(jti):
        raise JWTError("刷新令牌已被撤销")
    return issue_access_token(
        user_id=claims["sub"],
        tenant_id=claims["tid"],
        tenant_code=claims["tenant"],
        roles=claims.get("roles", []),
        secret=secret,
        expires_in=900,
        jti=jti,
    )


def issue_access_token(
    user_id: str, tenant_id: str, tenant_code: str, roles: list,
    secret: str, expires_in: int = 900, jti: Optional[str] = None,
) -> str:
    """签发短期访问令牌（默认 15 分钟）

    Args:
        user_id: 用户标识
        tenant_id: 租户 ID
        tenant_code: 租户代码
        roles: 角色列表
        secret: JWT 密钥
        expires_in: 过期秒数（默认 900 = 15 分钟）
        jti: 可选唯一标识（用于撤销）
    """
    payload = {
        "sub": user_id,
        "tid": tenant_id,
        "tenant": tenant_code,
        "roles": list(roles),
        "type": "access",
    }
    if jti:
        payload["jti"] = jti
    return encode(payload, secret, expires_in)


def issue_refresh_token(
    user_id: str, tenant_id: str, tenant_code: str, roles: list,
    secret: str, expires_in: int = 604800,
) -> str:
    """签发长期刷新令牌（默认 7 天），含唯一 jti 用于黑名单撤销"""
    payload = {
        "sub": user_id,
        "tid": tenant_id,
        "tenant": tenant_code,
        "roles": list(roles),
        "type": "refresh",
        "jti": secrets.token_urlsafe(16),
    }
    return encode(payload, secret, expires_in)


def refresh_access_token(refresh_token: str, secret: str) -> str:
    """用刷新令牌换取新的访问令牌

    Raises:
        TokenInvalid: 令牌格式/签名错误
        TokenExpired: 令牌已过期
        JWTError: 令牌类型错误或已被撤销
    """
    claims = decode(refresh_token, secret)
    if claims.get("type") != "refresh":
        raise JWTError("令牌类型错误：需要 refresh token")
    jti = claims.get("jti")
    if jti and is_refresh_token_blacklisted(jti):
        raise JWTError("刷新令牌已被撤销")
    return issue_access_token(
        user_id=claims["sub"],
        tenant_id=claims["tid"],
        tenant_code=claims["tenant"],
        roles=claims.get("roles", []),
        secret=secret,
        expires_in=900,
        jti=jti,
    )


def decode(token: str, secret: str) -> Dict[str, Any]:
    """校验并解析 JWT，返回 claims；失败抛 TokenInvalid / TokenExpired

    对 access token 额外检查 jti 黑名单（登出撤销）。
    """
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        signing_input = f"{head_b64}.{body_b64}"
        expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            raise TokenInvalid("签名校验失败")
        header = json.loads(_b64url_decode(head_b64))
        if header.get("alg") != "HS256":
            raise TokenInvalid(f"不支持的算法: {header.get('alg')}")
        claims = json.loads(_b64url_decode(body_b64))
    except JWTError:
        raise
    except Exception as e:
        raise TokenInvalid(f"token 解析失败: {e}") from e
    if int(time.time()) >= int(claims.get("exp", 0)):
        raise TokenExpired(f"token 已于 {claims.get('exp')} 过期")
    # access token 黑名单检查
    if claims.get("type") == "access":
        jti = claims.get("jti")
        if jti and is_refresh_token_blacklisted(jti):
            raise JWTError("访问令牌已被撤销")
    return claims
