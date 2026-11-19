from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        # 库中哈希格式异常/空值属于数据问题，认证语义应为「不匹配」而不是 500。
        return False
