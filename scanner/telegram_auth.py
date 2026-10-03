from datetime import timedelta
import logging
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from .crypto import decrypt, encrypt

logger = logging.getLogger(__name__)


def new_client(session=""):
    if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        raise ValueError("Укажите TELEGRAM_API_ID и TELEGRAM_API_HASH в .env")
    return TelegramClient(StringSession(session), settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)


async def request_code(account, phone):
    client = new_client()
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        account.phone = phone
        account.pending_session = encrypt(client.session.save())
        account.pending_phone_hash = sent.phone_code_hash
        account.pending_at = timezone.now()
        account.pending_requires_password = False
        clear_qr(account)
        await sync_to_async(account.save)()
    finally:
        await client.disconnect()


async def verify_code(account, code="", password=""):
    if not account.pending_session or not account.pending_at or timezone.now() - account.pending_at > timedelta(minutes=15):
        raise ValueError("Код устарел. Запросите новый.")
    client = new_client(decrypt(account.pending_session))
    await client.connect()
    try:
        if password:
            await client.sign_in(password=password)
        else:
            try:
                await client.sign_in(phone=account.phone, code=code, phone_code_hash=account.pending_phone_hash)
            except SessionPasswordNeededError:
                account.pending_requires_password = True
                await sync_to_async(account.save)(update_fields=["pending_requires_password"])
                return False
        await complete_login(client, account)
        return True
    finally:
        await client.disconnect()


def clear_qr(account):
    account.pending_qr_id = ""
    account.pending_qr_url = ""
    account.pending_qr_expires_at = None
    account.pending_qr_error = ""


def cancel_qr(account):
    clear_qr(account)
    account.pending_at = None
    account.save()


def request_qr(account):
    if account.is_connected:
        raise ValueError("Сначала отключите текущий аккаунт.")
    account.pending_qr_id = str(uuid.uuid4())
    account.pending_qr_url = ""
    account.pending_qr_expires_at = None
    account.pending_qr_error = ""
    account.pending_session = ""
    account.pending_phone_hash = ""
    account.pending_requires_password = False
    account.pending_at = timezone.now()
    account.phone = ""
    account.save()


async def complete_login(client, account):
    me = await client.get_me()
    if me.bot:
        raise ValueError("Поддерживается только личный Telegram-аккаунт.")
    account.telegram_id = me.id
    account.name = " ".join(filter(None, [me.first_name, me.last_name])) or me.username or account.phone
    account.phone = account.phone or getattr(me, "phone", "") or ""
    account.encrypted_session = encrypt(client.session.save())
    account.connected_at = timezone.now()
    account.pending_session = ""
    account.pending_phone_hash = ""
    account.pending_at = None
    account.pending_requires_password = False
    clear_qr(account)
    await sync_to_async(account.save)()


async def revoke_account(account):
    if account.encrypted_session:
        try:
            client = new_client(decrypt(account.encrypted_session))
            await client.connect()
            try:
                await client.log_out()
            finally:
                await client.disconnect()
        except Exception:
            logger.exception("Не удалось завершить Telegram-сессию удалённо; локальные данные будут удалены")
    account.encrypted_session = ""
    account.pending_session = ""
    account.pending_phone_hash = ""
    account.pending_at = None
    account.pending_requires_password = False
    clear_qr(account)
    account.connected_at = None
    account.telegram_id = None
    account.name = ""
    account.phone = ""
    await sync_to_async(account.save)()
