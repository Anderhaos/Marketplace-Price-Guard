import os
from cryptography.fernet import Fernet, InvalidToken
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEY is required to save or use seller tokens")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as error:
        raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEY must be a Fernet key") from error


def encrypt_token(token):
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext):
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise ValueError("Cannot decrypt seller token; verify TOKEN_ENCRYPTION_KEY") from error
