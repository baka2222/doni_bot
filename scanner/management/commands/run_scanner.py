import asyncio
import logging
import re
import time

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from telethon import events, utils
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import Channel, Chat, ChatInviteAlready, MessageMediaWebPage

from scanner.crypto import decrypt, encrypt
from scanner.matching import first_match
from scanner.models import Delivery, Keyword, MessageFormat, SourceChat, TargetChat, TelegramAccount
from scanner.presentation import contact_action, origin_prefix, render_message, utf16_length
from scanner.telegram_auth import complete_login, new_client

logger = logging.getLogger(__name__)


def db_snapshot():
    close_old_connections()
    account = TelegramAccount.objects.filter(pk=1).first()
    return account.encrypted_session if account else ""


def db_qr_request():
    close_old_connections()
    account = TelegramAccount.objects.filter(pk=1).first()
    return account.pending_qr_id if account and not account.is_connected else ""


def db_qr_publish(request_id, url, expires_at):
    close_old_connections()
    return TelegramAccount.objects.filter(pk=1, pending_qr_id=request_id, encrypted_session="").update(
        pending_qr_url=encrypt(url), pending_qr_expires_at=expires_at, pending_qr_error=""
    ) > 0


def db_qr_password(request_id, session):
    close_old_connections()
    TelegramAccount.objects.filter(pk=1, pending_qr_id=request_id, encrypted_session="").update(
        pending_session=encrypt(session), pending_at=timezone.now(), pending_requires_password=True,
        pending_qr_id="", pending_qr_url="", pending_qr_expires_at=None,
    )


def db_qr_error(request_id, error):
    close_old_connections()
    TelegramAccount.objects.filter(pk=1, pending_qr_id=request_id, encrypted_session="").update(
        pending_qr_id="", pending_qr_url="", pending_qr_expires_at=None,
        pending_qr_error=str(error)[:1000],
    )


async def wait_for_qr_cancel(request_id):
    while await sync_to_async(db_qr_request)() == request_id:
        await asyncio.sleep(2)


async def qr_login_session(request_id):
    client = new_client()
    await client.connect()
    try:
        qr = await client.qr_login()
        while await sync_to_async(db_qr_request)() == request_id:
            if not await sync_to_async(db_qr_publish)(request_id, qr.url, qr.expires):
                return
            login_task = asyncio.create_task(qr.wait())
            cancel_task = asyncio.create_task(wait_for_qr_cancel(request_id))
            try:
                done, pending = await asyncio.wait({login_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
                if cancel_task in done:
                    return
                await login_task
            except asyncio.TimeoutError:
                await qr.recreate()
                continue
            except SessionPasswordNeededError:
                await sync_to_async(db_qr_password)(request_id, client.session.save())
                return
            finally:
                for task in (login_task, cancel_task):
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
            account = await sync_to_async(lambda: TelegramAccount.objects.get(pk=1))()
            if account.pending_qr_id == request_id and not account.is_connected:
                await complete_login(client, account)
            return
    except Exception as exc:
        logger.exception("Ошибка входа по QR")
        await sync_to_async(db_qr_error)(request_id, exc)
    finally:
        await client.disconnect()


def db_config():
    close_old_connections()
    return (
        list(SourceChat.objects.filter(enabled=True)),
        TargetChat.objects.filter(pk=1).first(),
        list(Keyword.objects.filter(enabled=True)),
        MessageFormat.objects.filter(pk=1).first() or MessageFormat(),
    )


def db_resolved(model, pk, chat_id, error=""):
    close_old_connections()
    model.objects.filter(pk=pk).update(chat_id=chat_id, last_error=error, resolved_at=timezone.now())


def db_claim(source, chat_id, message_id, target_id, keyword, preview):
    close_old_connections()
    delivery, created = Delivery.objects.get_or_create(
        source_chat_id=chat_id,
        message_id=message_id,
        target_chat_id=target_id,
        defaults={"source": source, "keyword": keyword, "preview": preview[:1000]},
    )
    return delivery.pk if created else None


def db_finish(delivery_id, error=""):
    close_old_connections()
    Delivery.objects.filter(pk=delivery_id).update(
        status=Delivery.Status.FAILED if error else Delivery.Status.SENT,
        attempts=1,
        last_error=error[:2000],
        sent_at=None if error else timezone.now(),
    )


async def resolve_chat(client, locator, dialog_by_id, group_only=False):
    """Resolve only chats that the connected account has already joined."""
    locator = locator.strip()
    if locator.lstrip("-").isdigit():
        wanted = int(locator)
        dialog = dialog_by_id.get(wanted)
        if dialog is None and wanted > 0:
            dialog = next((d for d in dialog_by_id.values() if abs(d.id) == wanted or abs(d.entity.id) == wanted), None)
        if dialog is None:
            raise ValueError("Чат с таким ID отсутствует в диалогах аккаунта")
        entity = dialog.entity
        chat_id = dialog.id
    else:
        invite = re.search(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/|\+)([A-Za-z0-9_-]+)", locator)
        if invite:
            invite_info = await client(CheckChatInviteRequest(invite.group(1)))
            if not isinstance(invite_info, ChatInviteAlready):
                raise ValueError("Сначала вступите в приватный чат через Telegram")
            entity = invite_info.chat
        else:
            entity = await client.get_entity(locator)
        chat_id = utils.get_peer_id(entity)

    if not isinstance(entity, (Chat, Channel)):
        raise ValueError("Указана не группа или канал")
    if group_only and isinstance(entity, Channel) and not entity.megagroup:
        raise ValueError("Целью должна быть группа, а не канал")
    if chat_id not in dialog_by_id:
        raise ValueError("Аккаунт ещё не состоит в этом чате")
    return dialog_by_id[chat_id].entity, chat_id


async def send_formatted_message(client, target, event, message_format, source, rule):
    original_chat = await event.get_chat()
    if getattr(event.message, "noforwards", False) or getattr(original_chat, "noforwards", False):
        raise ValueError("В источнике запрещено копирование сообщений")
    sender = await event.get_sender()
    label, url = contact_action(
        sender, original_chat, event.id, message_format.contact_label, message_format.channel_label
    )
    body, entities = render_message(
        message_format.template, event.raw_text or "", label, url,
        source.title if message_format.show_origin else "", rule.phrase,
        getattr(event.message, "entities", None),
        origin_prefix(message_format.template, source.title, message_format.show_origin),
    )
    media = getattr(event.message, "photo", None) or getattr(event.message, "document", None)
    web_preview = isinstance(getattr(event.message, "media", None), MessageMediaWebPage)
    if getattr(event.message, "media", None) and not media and not web_preview:
        raise ValueError("Этот тип вложения нельзя отправить одним сообщением с текстом")
    limit = 1024 if media else 4096
    if utf16_length(body) > limit:
        raise ValueError(f"Текст с шаблоном длиннее лимита Telegram ({limit} символов)")
    if media:
        return await client.send_file(
            target, event.message.media, caption=body, formatting_entities=entities,
        )
    return await client.send_message(
        target, body, formatting_entities=entities,
        link_preview=web_preview,
    )


async def scanner_session(encrypted_session):
    client = new_client(decrypt(encrypted_session))
    state = {"sources": {}, "target_id": None, "target_entity": None, "rules": [], "format": None, "signature": None, "last_resolved": 0}

    @client.on(events.NewMessage)
    async def on_message(event):
        source = state["sources"].get(event.chat_id)
        target_id = state["target_id"]
        target_entity = state["target_entity"]
        if not source or not target_id or not target_entity or event.chat_id == target_id:
            return
        rule = first_match(event.raw_text or "", state["rules"])
        if not rule:
            return
        try:
            delivery_id = await sync_to_async(db_claim)(source, event.chat_id, event.id, target_id, rule, event.raw_text or "")
            if not delivery_id:
                return
            try:
                await send_formatted_message(client, target_entity, event, state["format"], source, rule)
            except Exception as exc:
                logger.exception("Не удалось отправить %s/%s", event.chat_id, event.id)
                await sync_to_async(db_finish)(delivery_id, str(exc))
            else:
                await sync_to_async(db_finish)(delivery_id)
                logger.info("Отправлено %s/%s по правилу %s", event.chat_id, event.id, rule.phrase)
        except Exception:
            logger.exception("Ошибка обработки %s/%s", event.chat_id, event.id)

    async def refresh():
        sources, target, rules, message_format = await sync_to_async(db_config)()
        signature = (tuple((s.pk, s.locator) for s in sources), target.locator if target else None)
        if signature == state["signature"] and time.monotonic() - state["last_resolved"] < 300:
            state["rules"] = rules
            state["format"] = message_format
            return
        dialogs = await client.get_dialogs(limit=None)
        dialog_by_id = {dialog.id: dialog for dialog in dialogs}
        source_map = {}
        for source in sources:
            try:
                entity, chat_id = await resolve_chat(client, source.locator, dialog_by_id)
                source_map[chat_id] = source
                if source.chat_id != chat_id or source.last_error:
                    await sync_to_async(db_resolved)(SourceChat, source.pk, chat_id)
            except Exception as exc:
                logger.warning("Источник %s: %s", source.locator, exc)
                await sync_to_async(db_resolved)(SourceChat, source.pk, None, str(exc))
        target_id = None
        target_entity = None
        if target:
            try:
                target_entity, target_id = await resolve_chat(client, target.locator, dialog_by_id, group_only=True)
                if target.chat_id != target_id or target.last_error:
                    await sync_to_async(db_resolved)(TargetChat, target.pk, target_id)
            except Exception as exc:
                logger.warning("Целевая группа %s: %s", target.locator, exc)
                await sync_to_async(db_resolved)(TargetChat, target.pk, None, str(exc))
        state.update(sources=source_map, target_id=target_id, target_entity=target_entity, rules=rules, format=message_format, signature=signature, last_resolved=time.monotonic())
        logger.info("Активны %s источников, %s правил, цель %s", len(source_map), len(rules), target_id)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error("Telegram-сессия недействительна. Подключите аккаунт заново.")
            return
        await refresh()

        async def maintenance():
            while client.is_connected():
                await asyncio.sleep(20)
                if await sync_to_async(db_snapshot)() != encrypted_session:
                    logger.info("Telegram-аккаунт изменён; переподключение")
                    await client.disconnect()
                    break
                try:
                    await refresh()
                except Exception:
                    logger.exception("Не удалось обновить настройки чатов")

        task = asyncio.create_task(maintenance())
        try:
            await client.run_until_disconnected()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        await client.disconnect()


class Command(BaseCommand):
    help = "Слушать выбранные Telegram-чаты и пересылать подходящие сообщения"

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        asyncio.run(self.run())

    async def run(self):
        while True:
            try:
                session = await sync_to_async(db_snapshot)()
                if session:
                    await scanner_session(session)
                    await asyncio.sleep(10)
                else:
                    qr_request = await sync_to_async(db_qr_request)()
                    if qr_request:
                        await qr_login_session(qr_request)
                    else:
                        await asyncio.sleep(2)
            except Exception:
                logger.exception("Сканер перезапустится через 10 секунд")
                await asyncio.sleep(10)
