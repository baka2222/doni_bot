"""Find personal contacts in the original post before its footer is removed."""

import asyncio
import logging
import re
from urllib.parse import parse_qs, urlsplit

from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import MessageEntityMention, MessageEntityMentionName, MessageEntityTextUrl, MessageEntityUrl

from .parsing import normalized_line
from .presentation import contact_action


logger = logging.getLogger(__name__)
DEFAULT_CONTACT_LINK_LABELS = "Написать продавцу\nСвязаться с продавцом\nНаписать в личку\nПродавец\nКонтакт"
CONTACT_FIELDS = ("contact_mode", "contact_label", "contact_link_labels")
USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")
PLAIN_LINK = re.compile(r"https?://[^\s<>]+|tg://[^\s<>]+|(?<!\w)@[A-Za-z][A-Za-z0-9_]{0,31}\b", re.IGNORECASE)


def personal_reference(url):
    """Accept only Telegram profile links, not posts, invites or bot commands."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if url.startswith("@"):
        return url if USERNAME.fullmatch(url[1:]) else None
    if url.startswith(("t.me/", "telegram.me/")):
        url = "https://" + url
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.username or parsed.password or parsed.fragment:
        return None
    if parsed.scheme in {"https", "http"}:
        if parsed.netloc.lower() not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"} or parsed.query:
            return None
        username = parsed.path.strip("/")
        return "@" + username if USERNAME.fullmatch(username) else None
    if parsed.scheme == "tg" and not parsed.path:
        query = parse_qs(parsed.query)
        if parsed.netloc == "user" and set(query) == {"id"} and len(query["id"]) == 1:
            value = query["id"][0]
            return int(value) if value.isdecimal() and int(value) > 0 else None
        if parsed.netloc == "resolve" and set(query) == {"domain"} and len(query["domain"]) == 1:
            username = query["domain"][0]
            return "@" + username if USERNAME.fullmatch(username) else None
    return None


def contact_candidates(text, entities, reply_markup, labels):
    """Match a link/button label or a labelled line such as 'Продавец: @name'."""
    labels = {normalized_line(label) for label in labels.splitlines()} - {""}
    candidates = []
    encoded = (text or "").encode("utf-16-le")
    for entity in entities or ():
        if not isinstance(entity, (MessageEntityTextUrl, MessageEntityUrl, MessageEntityMention, MessageEntityMentionName)):
            continue
        left, right = entity.offset * 2, (entity.offset + entity.length) * 2
        if left < 0 or right > len(encoded):
            continue
        visible = encoded[left:right].decode("utf-16-le", errors="replace")
        prefix = encoded[:left].decode("utf-16-le", errors="replace").split("\n")[-1]
        if normalized_line(visible) not in labels and normalized_line(prefix) not in labels:
            continue
        if isinstance(entity, MessageEntityMentionName):
            candidates.append(f"tg://user?id={entity.user_id}")
        elif isinstance(entity, MessageEntityTextUrl):
            candidates.append(entity.url)
        else:
            candidates.append(visible)
    for row in getattr(reply_markup, "rows", ()) or ():
        for button in row.buttons:
            if normalized_line(button.text) not in labels:
                continue
            # Support both the current typed buttons and older flat TL buttons.
            action = getattr(button, "type", button)
            if getattr(action, "url", None):
                candidates.append(action.url)
            elif getattr(action, "user_id", None):
                candidates.append(f"tg://user?id={action.user_id}")
    for line in (text or "").splitlines():
        for match in PLAIN_LINK.finditer(line):
            if normalized_line(line[:match.start()]) in labels:
                candidates.append(match.group().rstrip(".,;!?)]}"))
    return list(dict.fromkeys(candidates))


async def resolve_contact(client, source, message_format, event, sender):
    label = source.contact_label or message_format.contact_label
    if source.contact_mode == "none":
        return label, ""
    if source.contact_mode == "sender":
        return contact_action(sender, label)
    candidates = contact_candidates(
        event.raw_text, getattr(event.message, "entities", None),
        getattr(event.message, "reply_markup", None), source.contact_link_labels,
    )
    references = set()
    for candidate in candidates[:10]:
        reference = personal_reference(candidate)
        if reference is None or reference in references:
            continue
        references.add(reference)
        try:
            person = await asyncio.wait_for(client.get_entity(reference), timeout=10)
        except FloodWaitError:
            logger.warning("Telegram ограничил проверку контакта продавца; ссылка не добавлена")
            break
        except (RPCError, ValueError, TypeError, OSError, asyncio.TimeoutError):
            continue
        _, url = contact_action(person, label)
        if url:
            return label, url
    # An explicit but unusable seller contact must not be replaced by the poster.
    if source.contact_mode == "auto" and not candidates:
        return contact_action(sender, label)
    return label, ""
