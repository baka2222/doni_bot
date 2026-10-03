from copy import copy
from string import Formatter

from telethon.tl.types import Channel, MessageEntityTextUrl, User


def contact_action(sender, source, message_id, contact_label="Написать в личку", channel_label="Открыть публикацию"):
    """Choose a destination that can actually be opened from the target group."""
    if isinstance(sender, User) and not sender.bot:
        url = f"https://t.me/{sender.username}" if sender.username else f"tg://user?id={sender.id}"
        return contact_label, url
    if isinstance(source, Channel):
        if source.username:
            return channel_label, f"https://t.me/{source.username}/{message_id}"
        return channel_label, f"https://t.me/c/{source.id}/{message_id}"
    return "Контакт не указан", ""


def utf16_length(value):
    return len(value.encode("utf-16-le")) // 2


def origin_prefix(template, source_title, show_origin):
    if not show_origin:
        return ""
    if any(field == "source" for _, field, _, _ in Formatter().parse(template)):
        return ""
    return f"↪ Источник: {source_title}\n\n"


def render_message(template, original_text, contact_label, contact_url, source_title="", keyword="", original_entities=(), origin_prefix=""):
    """Expand a safe template and preserve Telegram's UTF-16 entity offsets."""
    values = {
        "text": original_text or "",
        "contact": contact_label,
        "source": source_title,
        "keyword": keyword,
    }
    chunks = [origin_prefix]
    entities = []
    offset = utf16_length(origin_prefix)
    for literal, field, spec, conversion in Formatter().parse(template):
        chunks.append(literal)
        offset += utf16_length(literal)
        if field is None:
            continue
        if field not in values or spec or conversion:
            raise ValueError("Недопустимое поле в шаблоне сообщения")
        value = values[field]
        if field == "text":
            for original in original_entities or ():
                shifted = copy(original)
                shifted.offset += offset
                entities.append(shifted)
        elif field == "contact" and contact_url:
            entities.append(MessageEntityTextUrl(offset=offset, length=utf16_length(value), url=contact_url))
        chunks.append(value)
        offset += utf16_length(value)
    return "".join(chunks), entities
