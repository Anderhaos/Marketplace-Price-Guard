import base64
import hashlib
import hmac

from config import ENCRYPTION_KEY


def _key():
    return hashlib.sha256(ENCRYPTION_KEY.encode("utf-8")).digest()


def encrypt_text(text):
    data = text.encode("utf-8")
    key = _key()
    encrypted = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    signature = hmac.new(key, encrypted, hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(signature + encrypted).decode("ascii")


def decrypt_text(token):
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    signature, encrypted = raw[:12], raw[12:]
    key = _key()
    expected = hmac.new(key, encrypted, hashlib.sha256).digest()[:12]
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Не удалось расшифровать сохраненный токен: ключ шифрования изменился.")
    data = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(encrypted))
    return data.decode("utf-8")
