"""Extract a source's description, retaining Telegram UTF-16 formatting offsets."""

from copy import copy
from dataclasses import dataclass
import re

from telethon.tl.types import MessageEntitySpoiler

from .presentation import utf16_length


DEFAULT_EXCLUDED_LINES = "Скрыт\nРазместить объявление\nНаписать продавцу\nДобавить в избранное\nПрофиль"
PARSING_FIELDS = (
    "parse_start_line", "parse_end_line", "parse_skip_last_lines",
    "parse_start_after", "parse_end_before", "parse_exclude_lines",
    "parse_exclude_contains", "parse_remove_spoilers",
)


@dataclass
class ParsedDescription:
    text: str
    entities: list


def normalized_line(value):
    # Ignore decorative emoji/punctuation, but keep words and numbers distinct.
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), re.UNICODE))


def phrases(value):
    return [line.strip().casefold() for line in value.splitlines() if line.strip()]


def parse_description(text, source, entities=()):
    """Line limits refer to the original text, including empty lines (1-based).

    Markers match literal, case-insensitive substrings of entire lines. A missing
    start marker returns no description instead of accidentally sending a header.
    Removed lines never contribute to matching or to the outgoing description.
    """
    text = text or ""
    entities = entities or ()
    lines = text.splitlines(keepends=True)
    first = max(source.parse_start_line - 1, 0)
    last = len(lines) - source.parse_skip_last_lines
    if source.parse_end_line is not None:
        last = min(last, source.parse_end_line)
    after = phrases(source.parse_start_after)
    before = phrases(source.parse_end_before)
    excluded = {normalized_line(line) for line in phrases(source.parse_exclude_lines)} - {""}
    contains = phrases(source.parse_exclude_contains)
    spoilers = [entity for entity in entities if isinstance(entity, MessageEntitySpoiler)]
    started = not after
    spans = []
    position = 0
    utf16_position = 0
    for index, line in enumerate(lines):
        start, end = position, position + len(line)
        utf16_start, utf16_end = utf16_position, utf16_position + utf16_length(line)
        position, utf16_position = end, utf16_end
        if index < first or index >= last:
            continue
        folded = line.casefold()
        if not started:
            if any(marker in folded for marker in after):
                started = True
            continue
        if any(marker in folded for marker in before):
            break
        if normalized_line(line) in excluded or any(marker in folded for marker in contains):
            continue
        if source.parse_remove_spoilers and any(
            entity.offset < utf16_end and entity.offset + entity.length > utf16_start
            for entity in spoilers
        ):
            continue
        spans.append((start, end))

    # Trim only the outer whitespace, preserving the content and inner blank lines.
    while spans:
        start, end = spans[0]
        start += len(text[start:end]) - len(text[start:end].lstrip())
        if start < end:
            spans[0] = (start, end)
            break
        spans.pop(0)
    while spans:
        start, end = spans[-1]
        end = start + len(text[start:end].rstrip())
        if start < end:
            spans[-1] = (start, end)
            break
        spans.pop()

    # Join adjacent spans before clipping entities so multi-line formatting survives.
    merged = []
    for start, end in spans:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    result_entities = []
    output_offset = 0
    for start, end in merged:
        original_offset = utf16_length(text[:start])
        length = utf16_length(text[start:end])
        for entity in entities:
            left = max(original_offset, entity.offset)
            right = min(original_offset + length, entity.offset + entity.length)
            if left < right:
                shifted = copy(entity)
                shifted.offset = output_offset + left - original_offset
                shifted.length = right - left
                result_entities.append(shifted)
        output_offset += length
    result_entities.sort(key=lambda entity: (entity.offset, -entity.length))
    return ParsedDescription("".join(text[start:end] for start, end in merged), result_entities)
