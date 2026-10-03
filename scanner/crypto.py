from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def cipher():
    key = getattr(settings, "TELEGRAM_SESSION_KEY", None)
    if not key:
        raise ImproperlyConfigured("Задайте TELEGRAM_SESSION_KEY в .env")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("TELEGRAM_SESSION_KEY должен быть ключом Fernet") from exc


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    return cipher().decrypt(value.encode()).decode()
