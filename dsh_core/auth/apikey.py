"""DSH 认证 - API 密钥管理

M8 安全加固任务 8.1：API 密钥的加密存储、哈希存储与轮换支持。

安全基线：
- 密钥明文仅返回一次（创建时），后续仅存储哈希值
- 存储加密使用 AES-256-GCM（需要 DSH_ENCRYPTION_KEY 环境变量）
- 哈希存储使用 bcrypt（如不可用则回退到 SHA-256 + salt）
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ─── 加密模块（可选依赖）───
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# ─── 哈希模块（可选依赖）───
try:
    import bcrypt

    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False


# ─── 异常定义 ───
class APIKeyError(Exception):
    """API 密钥基础异常"""


class EncryptionDisabled(APIKeyError):
    """加密功能未启用（缺少 cryptography 库或密钥）"""


class APIKeyInvalid(APIKeyError):
    """API 密钥无效"""


# ─── 数据模型 ───
@dataclass
class APIKey:
    """API 密钥记录"""

    key_id: str
    name: str
    encrypted_secret: Optional[str] = None  # 加密后的密钥（AES-256-GCM）
    hashed_secret: Optional[str] = None  # 哈希后的密钥（bcrypt）
    tenant_id: str = ""
    user_id: str = ""
    permissions: List[str] = field(default_factory=list)
    created_at: str = ""
    expires_at: str = ""
    last_used_at: str = ""
    revoked: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key_id": self.key_id,
            "name": self.name,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "permissions": list(self.permissions),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked,
            "meta": dict(self.meta),
        }


# ─── 加密工具 ───
class _Crypto:
    """内部加密工具类（延迟初始化）"""

    _aead: Optional[AESGCM] = None
    _initialized: bool = False

    @classmethod
    def initialize(cls, encryption_key: Optional[str] = None) -> None:
        """初始化加密器

        Args:
            encryption_key: 加密密钥（从 DSH_ENCRYPTION_KEY 环境变量读取）
        """
        if cls._initialized:
            return

        key = encryption_key or os.getenv("DSH_ENCRYPTION_KEY", "")
        if not key:
            cls._initialized = True
            return

        if not HAS_CRYPTO:
            raise EncryptionDisabled(
                "加密功能需要 cryptography 库：pip install cryptography"
            )

        # 使用 PBKDF2 派生 32 字节密钥
        salt = b"dsh-api-key-v1"  # 固定盐值（同一密钥派生相同结果）
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        derived_key = kdf.derive(key.encode())
        cls._aead = AESGCM(derived_key)
        cls._initialized = True

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        """加密字符串"""
        if cls._aead is None:
            raise EncryptionDisabled("加密功能未初始化")
        nonce = secrets.token_bytes(12)  # 96-bit nonce
        ciphertext = cls._aead.encrypt(nonce, plaintext.encode(), None)
        return f"{nonce.hex()}:{ciphertext.hex()}"

    @classmethod
    def decrypt(cls, encrypted: str) -> str:
        """解密字符串"""
        if cls._aead is None:
            raise EncryptionDisabled("加密功能未初始化")
        nonce_hex, ciphertext_hex = encrypted.split(":")
        nonce = bytes.fromhex(nonce_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)
        return cls._aead.decrypt(nonce, ciphertext, None).decode()


# ─── 哈希工具 ───
def _hash_secret(secret: str) -> str:
    """对密钥进行哈希存储

    优先使用 bcrypt，回退到 SHA-256 + salt
    """
    if HAS_BCRYPT:
        return bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode()
    else:
        salt = secrets.token_hex(8)
        return f"sha256:{salt}:{hashlib.sha256(f'{salt}{secret}'.encode()).hexdigest()}"


def _verify_secret(secret: str, hashed: str) -> bool:
    """验证密钥是否匹配哈希值"""
    if HAS_BCRYPT:
        try:
            return bcrypt.checkpw(secret.encode(), hashed.encode())
        except Exception:
            return False
    else:
        # 回退到 SHA-256
        if not hashed.startswith("sha256:"):
            return False
        _, salt, expected = hashed.split(":")
        actual = hashlib.sha256(f"{salt}{secret}".encode()).hexdigest()
        return actual == expected


# ─── API 密钥管理器 ───
class APIKeyManager:
    """API 密钥管理器

    提供密钥的创建、验证、撤销、轮换功能。
    密钥明文仅返回一次，后续仅存储哈希值。
    """

    def __init__(self, encryption_key: Optional[str] = None):
        """初始化密钥管理器

        Args:
            encryption_key: 加密密钥（从 DSH_ENCRYPTION_KEY 环境变量读取）
        """
        _Crypto.initialize(encryption_key)
        self._keys: Dict[str, APIKey] = {}  # key_id -> APIKey

    def create_key(
        self,
        name: str,
        tenant_id: str = "",
        user_id: str = "",
        permissions: Optional[List[str]] = None,
        expires_days: int = 365,
        meta: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, APIKey]:
        """创建新的 API 密钥

        Args:
            name: 密钥名称（用于识别）
            tenant_id: 租户 ID
            user_id: 用户 ID
            permissions: 权限列表
            expires_days: 过期天数（默认 365 天）
            meta: 元数据

        Returns:
            (明文密钥, 密钥记录) — 明文密钥仅返回一次，请妥善保存
        """
        key_id = secrets.token_urlsafe(16)
        secret = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        expires = (
            datetime.now(timezone.utc).timestamp() + expires_days * 86400
        )

        # 尝试加密存储
        try:
            encrypted = _Crypto.encrypt(secret)
            record = APIKey(
                key_id=key_id,
                name=name,
                encrypted_secret=encrypted,
                tenant_id=tenant_id,
                user_id=user_id,
                permissions=permissions or [],
                created_at=now,
                expires_at=datetime.fromtimestamp(
                    expires, timezone.utc
                ).isoformat(timespec="seconds"),
                meta=meta or {},
            )
        except EncryptionDisabled:
            # 回退到哈希存储
            record = APIKey(
                key_id=key_id,
                name=name,
                hashed_secret=_hash_secret(secret),
                tenant_id=tenant_id,
                user_id=user_id,
                permissions=permissions or [],
                created_at=now,
                expires_at=datetime.fromtimestamp(
                    expires, timezone.utc
                ).isoformat(timespec="seconds"),
                meta=meta or {},
            )

        self._keys[key_id] = record
        return secret, record

    def verify_key(self, key_id: str, secret: str) -> bool:
        """验证 API 密钥

        Args:
            key_id: 密钥 ID
            secret: 密钥明文

        Returns:
            是否有效
        """
        record = self._keys.get(key_id)
        if not record:
            return False
        if record.revoked:
            return False
        if record.expires_at:
            exp = datetime.fromisoformat(record.expires_at).timestamp()
            if time.time() > exp:
                return False

        # 验证密钥
        if record.encrypted_secret:
            try:
                decrypted = _Crypto.decrypt(record.encrypted_secret)
                return decrypted == secret
            except Exception:
                return False
        elif record.hashed_secret:
            return _verify_secret(secret, record.hashed_secret)
        return False

    def revoke_key(self, key_id: str) -> bool:
        """撤销 API 密钥

        Args:
            key_id: 密钥 ID

        Returns:
            是否成功撤销
        """
        record = self._keys.get(key_id)
        if not record:
            return False
        record.revoked = True
        return True

    def rotate_key(
        self, key_id: str, new_expires_days: Optional[int] = None
    ) -> Optional[str]:
        """轮换 API 密钥（生成新密钥，保留记录）

        Args:
            key_id: 密钥 ID
            new_expires_days: 新的过期天数（None 则保持原过期时间）

        Returns:
            新密钥明文，失败返回 None
        """
        record = self._keys.get(key_id)
        if not record or record.revoked:
            return None

        new_secret = secrets.token_urlsafe(32)
        now = time.time()

        if record.encrypted_secret:
            try:
                record.encrypted_secret = _Crypto.encrypt(new_secret)
            except Exception:
                return None
        elif record.hashed_secret:
            record.hashed_secret = _hash_secret(new_secret)

        if new_expires_days is not None:
            record.expires_at = datetime.fromtimestamp(
                now + new_expires_days * 86400, timezone.utc
            ).isoformat(timespec="seconds")

        return new_secret

    def get_key(self, key_id: str) -> Optional[APIKey]:
        """获取密钥记录（不含密钥值）"""
        return self._keys.get(key_id)

    def list_keys(
        self, tenant_id: Optional[str] = None, user_id: Optional[str] = None
    ) -> List[APIKey]:
        """列出密钥记录"""
        keys = list(self._keys.values())
        if tenant_id:
            keys = [k for k in keys if k.tenant_id == tenant_id]
        if user_id:
            keys = [k for k in keys if k.user_id == user_id]
        return keys

    def record_usage(self, key_id: str) -> None:
        """记录密钥使用时间"""
        record = self._keys.get(key_id)
        if record:
            record.last_used_at = datetime.now(
                timezone.utc
            ).isoformat(timespec="seconds")


# ─── 全局实例 ───
_api_key_manager: Optional[APIKeyManager] = None


def get_api_key_manager() -> APIKeyManager:
    """获取全局 API 密钥管理器"""
    global _api_key_manager
    if _api_key_manager is None:
        _api_key_manager = APIKeyManager()
    return _api_key_manager


def configure_api_key_manager(encryption_key: Optional[str] = None) -> None:
    """配置 API 密钥管理器"""
    global _api_key_manager
    _api_key_manager = APIKeyManager(encryption_key)
