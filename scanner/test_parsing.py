from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from telethon.tl.types import Channel, MessageEntityBold, MessageEntitySpoiler, MessageEntityTextUrl, User

from .management.commands.run_scanner import scanner_session, send_formatted_message
from .matching import first_match
from .models import Keyword, MessageFormat, SourceChat
from .parsing import DEFAULT_EXCLUDED_LINES, parse_description
from .presentation import utf16_length


class DescriptionParsingTests(SimpleTestCase):
    def test_screenshot_description_has_no_header_price_or_footer(self):
        original = "#techno\nПродавец | 8933239431\n💰 Продажа\n\n228\nЧихол\nАфон 14 пуро\n📲 Скрыт\n📣 Разместить объявление\n\n💵 Цена: 100 KGS\n✉ Написать продавцу\n⭐ Добавить в избранное"
        source = SourceChat(parse_start_line=5, parse_end_before="Цена:")
        self.assertEqual(parse_description(original, source).text, "228\nЧихол\nАфон 14 пуро")

    def test_numeric_bounds_include_original_blank_lines_and_trim_tail(self):
        source = SourceChat(parse_start_line=3, parse_end_line=6, parse_skip_last_lines=2)
        self.assertEqual(parse_description("header\n\nfirst\n\nlast\nfooter\nend", source).text, "first\n\nlast")
        self.assertEqual(parse_description("short", source).text, "")

    def test_markers_are_case_insensitive_literal_alternatives(self):
        source = SourceChat(parse_start_after="ОПИСАНИЕ:\nПодробности [товара]", parse_end_before="Цена:\nКонтакты:")
        self.assertEqual(parse_description("heading\nПодробности [товара]\nтовар\nКОНТАКТЫ: продавец\nfooter", source).text, "товар")
        self.assertEqual(parse_description("heading\nтовар\nЦена: 10", source).text, "")
        self.assertEqual(parse_description("Описание:\nтовар без подвала", source).text, "товар без подвала")

    def test_exact_service_lines_do_not_remove_words_inside_description(self):
        text = "📲 СКРЫТ\n📣 Разместить объявление\nМогу разместить объявление за вас\nскрытый дефект\n📌 Профиль\nТовар"
        self.assertEqual(parse_description(text, SourceChat()).text, "Могу разместить объявление за вас\nскрытый дефект\nТовар")
        self.assertEqual(parse_description("Скрыт", SourceChat(parse_exclude_lines="")).text, "Скрыт")

    def test_excluded_contains_and_channel_settings_are_independent(self):
        text = "Товар\nАртикул: 123\nописание"
        self.assertEqual(parse_description(text, SourceChat(parse_exclude_contains="АРТИКУЛ:")).text, "Товар\nописание")
        self.assertEqual(parse_description(text, SourceChat()).text, text)

    def test_hidden_phone_and_its_entities_are_removed(self):
        text = "📦 Товар\n📲 123456\nОписание"
        spoiler = MessageEntitySpoiler(offset=utf16_length("📦 Товар\n📲 "), length=6)
        result = parse_description(text, SourceChat(), [spoiler])
        self.assertEqual(result.text, "📦 Товар\nОписание")
        self.assertEqual(result.entities, [])
        self.assertEqual(parse_description(text, SourceChat(parse_remove_spoilers=False), [spoiler]).text, text)

    def test_links_and_formatting_keep_correct_utf16_offsets(self):
        text = "Заголовок\n📦 Товар\n📲 Скрыт\nПодробнее\n📣 Разместить объявление"
        bold = MessageEntityBold(offset=utf16_length("Заголовок\n"), length=utf16_length("📦 Товар\n📲 Скрыт\nПодробнее"))
        link = MessageEntityTextUrl(offset=utf16_length("Заголовок\n📦 Товар\n📲 Скрыт\n"), length=9, url="https://example.org/item")
        footer = MessageEntityTextUrl(offset=utf16_length("Заголовок\n📦 Товар\n📲 Скрыт\nПодробнее\n📣 "), length=20, url="https://example.org/publish")
        result = parse_description(text, SourceChat(parse_start_line=2), [bold, link, footer])
        self.assertEqual(result.text, "📦 Товар\nПодробнее")
        links = [entity for entity in result.entities if isinstance(entity, MessageEntityTextUrl)]
        self.assertEqual(len(links), 1)
        self.assertEqual((links[0].offset, links[0].length), (utf16_length("📦 Товар\n"), 9))
        self.assertEqual(links[0].url, "https://example.org/item")
        self.assertEqual(bold.offset, utf16_length("Заголовок\n"))
        self.assertTrue(all(entity.offset + entity.length <= utf16_length(result.text) for entity in result.entities))

    def test_service_phrases_do_not_trigger_matching_or_exclusions(self):
        result = parse_description("Ищу швею\n📣 Разместить объявление", SourceChat())
        self.assertIsNone(first_match(result.text, [Keyword(phrase="разместить объявление")]))
        rule = Keyword(phrase="ищу швею", exclusions="разместить объявление")
        self.assertIs(first_match(result.text, [rule]), rule)

    def test_invalid_range_is_rejected(self):
        with self.assertRaises(ValidationError):
            SourceChat(parse_start_line=5, parse_end_line=3).clean()

    def test_empty_and_service_only_posts_are_empty(self):
        for text in ("", "\n\n", "📲 Скрыт\n📣 Разместить объявление"):
            self.assertEqual(parse_description(text, SourceChat()).text, "")

    def test_media_caption_uses_only_description(self):
        client = SimpleNamespace(send_file=AsyncMock())
        media = object()
        event = SimpleNamespace(
            id=1, raw_text="#techno\nОписание товара\n📲 Скрыт\n📣 Разместить объявление\n100 KGS",
            message=SimpleNamespace(photo=object(), document=None, media=media, entities=[]),
            get_chat=AsyncMock(return_value=Channel(id=42, title="Market", photo=None, date=None, username="market")),
            get_sender=AsyncMock(return_value=User(id=1, username="seller")),
        )
        async_to_sync(send_formatted_message)(
            client, "target", event, MessageFormat(),
            SourceChat(parse_start_line=2, parse_end_before="KGS"), Keyword(phrase="товар"),
        )
        self.assertEqual(client.send_file.call_args.kwargs["caption"], "Описание товара\n\nНаписать в личку")
        self.assertIs(client.send_file.call_args.args[1], media)

    def test_scanner_matches_logs_and_sends_the_same_description(self):
        source = SourceChat(pk=1, locator="source", chat_id=123, parse_start_line=2)
        target = SimpleNamespace(locator="target", chat_id=456, last_error="")
        rule = Keyword(phrase="товар")

        class Client:
            def on(self, event_type):
                def register(handler):
                    self.handler = handler
                    return handler
                return register

            connect = AsyncMock()
            disconnect = AsyncMock()
            is_user_authorized = AsyncMock(return_value=True)
            get_dialogs = AsyncMock(return_value=[])

            def is_connected(self):
                return False

            async def run_until_disconnected(self):
                for text in ("товар в заголовке\nСкрыт", "заголовок\nТовар\nРазместить объявление"):
                    await self.handler(SimpleNamespace(id=7, chat_id=123, raw_text=text, message=SimpleNamespace(entities=[])))

        module = "scanner.management.commands.run_scanner"
        with patch(f"{module}.decrypt", return_value="session"), patch(f"{module}.new_client", return_value=Client()), patch(
            f"{module}.db_config", return_value=([source], target, [rule], MessageFormat())
        ), patch(f"{module}.resolve_chat", new=AsyncMock(side_effect=[(object(), 123), (object(), 456)])), patch(
            f"{module}.db_claim", return_value=99
        ) as claim, patch(f"{module}.db_finish") as finish, patch(f"{module}.send_formatted_message", new=AsyncMock()) as send:
            async_to_sync(scanner_session)("encrypted")
        claim.assert_called_once()
        self.assertEqual(claim.call_args.args[-1], "Товар")
        send.assert_awaited_once()
        self.assertEqual(send.call_args.args[-1].text, "Товар")
        finish.assert_called_once_with(99)


class ParsingAdminTests(TestCase):
    url = "/admin/scanner/sourcechat/parse-preview/"

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", password="test-password")
        self.client.force_login(self.user)

    def data(self, **changes):
        data = {
            "parse_start_line": 3, "parse_end_line": "", "parse_skip_last_lines": 0,
            "parse_start_after": "", "parse_end_before": "", "parse_exclude_lines": DEFAULT_EXCLUDED_LINES,
            "parse_exclude_contains": "", "parse_remove_spoilers": "on",
            "contact_mode": "auto", "contact_label": "", "contact_link_labels": "Написать продавцу",
            "parsing_sample": "\nheader\nТовар\n📣 Разместить объявление",
        }
        data.update(changes)
        return data

    def test_preview_uses_unsaved_settings_and_original_line_numbers(self):
        source = SourceChat.objects.create(title="Market", locator="@market")
        response = self.client.post(self.url, self.data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "Товар")
        self.assertTrue(response.json()["numbered_original"].startswith("1: \n2: header\n3: Товар"))
        source.refresh_from_db()
        self.assertEqual(source.parse_start_line, 1)
        self.assertEqual(SourceChat.objects.count(), 1)

    def test_invalid_range_is_reported_and_no_source_is_created(self):
        response = self.client.post(self.url, self.data(parse_end_line=1))
        self.assertEqual(response.status_code, 400)
        self.assertIn("parse_end_line", response.json()["errors"])
        self.assertFalse(SourceChat.objects.exists())

    def test_preview_requires_source_edit_permission(self):
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 403)

    def test_admin_exposes_preview_and_saves_per_source_options(self):
        source = SourceChat.objects.create(title="Market", locator="@market")
        url = f"/admin/scanner/sourcechat/{source.pk}/change/"
        response = self.client.get(url)
        self.assertContains(response, "Проверить разбор")
        self.assertContains(response, "scanner/source_parsing.js")
        response = self.client.post(url, self.data(title="Market", locator="@market", enabled="on", _save="Сохранить"))
        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual(source.parse_start_line, 3)
        self.assertFalse(hasattr(source, "parsing_sample"))
