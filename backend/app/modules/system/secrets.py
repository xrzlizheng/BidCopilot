import base64
import os
from datetime import UTC, datetime
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings
from app.core.errors import ServiceUnavailableError, ValidationAppError
from app.modules.system.models import ModelSecret


def _master_key() -> bytes:
    settings = get_settings()
    raw = settings.MODEL_SECRET_MASTER_KEY
    if not raw:
        raise ServiceUnavailableError("模型密钥服务未配置")
    try:
        key = base64.b64decode(raw.encode())
    except Exception as exc:
        raise ServiceUnavailableError("模型主密钥格式无效") from exc
    if len(key) != 32:
        raise ServiceUnavailableError("模型主密钥长度必须为 32 字节")
    return key


def _aad(tenant_id: UUID, scope: str, owner_id: UUID | None) -> bytes:
    return f"{tenant_id}:{scope}:{owner_id or ''}".encode()


def encrypt_secret(
    *,
    tenant_id: UUID,
    scope: str,
    owner_id: UUID | None,
    provider: str,
    plaintext: str,
    created_by: UUID | None,
) -> ModelSecret:
    value = plaintext.strip()
    if not value:
        raise ValidationAppError("API Key 不能为空")
    master = _master_key()
    dek = os.urandom(32)
    nonce = os.urandom(12)
    dek_nonce = os.urandom(12)
    aad = _aad(tenant_id, scope, owner_id)
    ciphertext = AESGCM(dek).encrypt(nonce, value.encode(), aad)
    wrapped_dek = AESGCM(master).encrypt(dek_nonce, dek, aad)
    return ModelSecret(
        tenant_id=tenant_id,
        scope=scope,
        owner_id=owner_id,
        provider=provider,
        ciphertext=ciphertext,
        nonce=nonce,
        wrapped_dek=wrapped_dek,
        dek_nonce=dek_nonce,
        key_version=get_settings().MODEL_SECRET_KEY_VERSION,
        last4=value[-4:],
        created_by=created_by,
    )


def decrypt_secret(secret: ModelSecret | None) -> str | None:
    if secret is None or secret.revoked_at is not None:
        return None
    master = _master_key()
    aad = _aad(secret.tenant_id, secret.scope, secret.owner_id)
    try:
        dek = AESGCM(master).decrypt(secret.dek_nonce, secret.wrapped_dek, aad)
        plaintext = AESGCM(dek).decrypt(secret.nonce, secret.ciphertext, aad)
    except Exception as exc:
        raise ServiceUnavailableError("模型密钥解密失败") from exc
    return plaintext.decode()


def revoke_secret(secret: ModelSecret) -> None:
    secret.revoked_at = datetime.now(UTC)
