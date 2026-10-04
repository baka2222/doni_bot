from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from telethon.tl.types import (
    Channel, InlineButtonTypeCallback, InlineButtonTypeUrl, InlineButtonTypeUserProfile,
    KeyboardInlineButton, KeyboardInlineButtonRow, MessageEntityMentionName,
    MessageEntityTextUrl, ReplyInlineMarkup, User,
)

from .contacts import DEFAULT_CONTACT_LINK_LABELS, contact_candidates, personal_reference, resolve_contact
from .management.commands.run_scanner import send_formatted_message
from .models import Keyword, MessageFormat, SourceChat
from .presentation import utf16_length


class SellerContactTests(SimpleTestCase):
    def setUp(self):
        self.channel = Channel(id=42, title="Cinele", photo=None, date=None, username="cinele", broadcast=True)
        self.seller = User(id=99, username="real_seller")
        self.source = SourceChat(contact_label="Написать продавцу")
        self.client = SimpleNamespace(get_entity=AsyncMock(return_value=self.seller), send_message=AsyncMock())

    def event(self, url="https://t.me/real_seller", label="Написать продавцу"):
        text = f"📦 Товар\n📲 Скрыт\n📣 Разместить объявление\n✉ {label}"
        entity = MessageEntityTextUrl(offset=utf16_length(text[:text.index(label)]), length=utf16_length(label), url=url)
        return SimpleNamespace(
            id=7, raw_text=text, message=SimpleNamespace(entities=[entity]),
            get_chat=AsyncMock(return_value=self.channel), get_sender=AsyncMock(return_value=self.channel),
        )

    def resolve(self, event, sender=None):
        return async_to_sync(resolve_contact)(self.client, self.source, MessageFormat(), event, sender or self.channel)

    def test_seller_link_survives_footer_removal_and_opens_private_chat(self):
        event = self.event()
        async_to_sync(send_formatted_message)(self.client, "target", event, MessageFormat(), self.source, Keyword(phrase="товар"))
        args, kwargs = self.client.send_message.call_args
        self.assertEqual(args[1], "📦 Товар\n\nНаписать продавцу")
        self.assertEqual(len(kwargs["formatting_entities"]), 1)
        link = kwargs["formatting_entities"][0]
        self.assertEqual((link.offset, link.url), (utf16_length("📦 Товар\n\n"), "https://t.me/real_seller"))
        self.client.get_entity.assert_awaited_once_with("@real_seller")

    def test_no_link_in_channel_leaves_same_label_unclickable(self):
        event = self.event()
        event.message.entities = []
        async_to_sync(send_formatted_message)(self.client, "target", event, MessageFormat(), self.source, Keyword(phrase="товар"))
        self.assertEqual(self.client.send_message.call_args.args[1], "📦 Товар\n\nНаписать продавцу")
        self.assertEqual(self.client.send_message.call_args.kwargs["formatting_entities"], [])
        self.client.get_entity.assert_not_awaited()

    def test_channel_bot_or_deleted_user_is_not_a_seller_contact(self):
        for entity in (self.channel, User(id=1, username="sellerbot", bot=True), User(id=1, deleted=True)):
            with self.subTest(entity=type(entity).__name__):
                self.client.get_entity.return_value = entity
                self.assertEqual(self.resolve(self.event()), ("Написать продавцу", ""))

    def test_post_invite_external_link_and_bot_deep_link_are_not_private_contacts(self):
        for url in ("https://t.me/cinele/123", "https://t.me/c/42/7", "https://t.me/+invite", "https://t.me/sellerbot?start=123", "https://example.org/seller", "javascript:alert(1)", "https://t.me.evil.example/seller"):
            with self.subTest(url=url):
                self.assertEqual(self.resolve(self.event(url)), ("Написать продавцу", ""))
        self.client.get_entity.assert_not_awaited()

    def test_resolution_failure_still_sends_post_with_unclickable_contact(self):
        self.client.get_entity.side_effect = ValueError("No such username")
        event = self.event()
        async_to_sync(send_formatted_message)(self.client, "target", event, MessageFormat(), self.source, Keyword(phrase="товар"))
        self.assertEqual(self.client.send_message.call_args.kwargs["formatting_entities"], [])

    def test_per_source_label_and_modes(self):
        event = self.event()
        author = User(id=101, username="poster")
        self.assertEqual(self.resolve(event, author)[1], "https://t.me/real_seller")
        self.source.contact_mode = "sender"
        self.assertEqual(self.resolve(event, author)[1], "https://t.me/poster")
        self.source.contact_mode = "none"
        self.assertEqual(self.resolve(event, author), ("Написать продавцу", ""))
        self.source.contact_mode = "message"
        event.message.entities = []
        self.assertEqual(self.resolve(event, author)[1], "")
        self.source.contact_mode = "auto"
        self.source.contact_label = ""
        self.assertEqual(self.resolve(event, author), ("Написать в личку", "https://t.me/poster"))

    def test_invalid_explicit_seller_is_not_replaced_by_post_author(self):
        self.client.get_entity.return_value = self.channel
        self.assertEqual(self.resolve(self.event(), User(id=101, username="poster"))[1], "")

    def test_contact_label_can_be_configured_without_taking_advertising_link(self):
        self.source.contact_link_labels = "ПРОДАВЕЦ ТОВАРА"
        self.assertEqual(self.resolve(self.event(label="Продавец товара"))[1], "https://t.me/real_seller")
        event = self.event(label="Разместить объявление")
        self.assertEqual(self.resolve(event)[1], "")

    def test_inline_url_and_profile_buttons_and_callback_without_url(self):
        event = self.event()
        event.message.entities = []
        for action, expected in (
            (InlineButtonTypeUrl(url="https://t.me/real_seller"), "https://t.me/real_seller"),
            (InlineButtonTypeUserProfile(user_id=99), "https://t.me/real_seller"),
            (InlineButtonTypeCallback(data=b"seller"), ""),
        ):
            event.message.reply_markup = ReplyInlineMarkup(rows=[KeyboardInlineButtonRow(buttons=[
                KeyboardInlineButton(text="✉ Написать продавцу", type=action),
            ])])
            self.assertEqual(self.resolve(event)[1], expected)

    def test_numeric_mention_contact_and_user_without_username(self):
        event = self.event()
        link = event.message.entities[0]
        event.message.entities = [MessageEntityMentionName(offset=link.offset, length=link.length, user_id=99)]
        self.client.get_entity.return_value = User(id=99)
        self.assertEqual(self.resolve(event)[1], "tg://user?id=99")
        self.client.get_entity.assert_awaited_once_with(99)

    def test_plain_labelled_contact_line_and_unlabelled_urls(self):
        candidates = contact_candidates(
            "https://t.me/channel\n📧 Продавец: @real_seller\nРазместить объявление: https://t.me/adverts",
            [], None, DEFAULT_CONTACT_LINK_LABELS,
        )
        self.assertEqual(candidates, ["@real_seller"])

    def test_supported_profile_link_forms(self):
        for url, expected in (
            ("@seller", "@seller"), ("t.me/seller", "@seller"),
            ("https://telegram.me/seller/", "@seller"),
            ("tg://resolve?domain=seller", "@seller"), ("tg://user?id=123", 123),
            ("tg://user?id=-1", None), ("tg://user?id=1&id=2", None),
        ):
            with self.subTest(url=url):
                self.assertEqual(personal_reference(url), expected)


class SellerContactAdminTests(TestCase):
    def test_source_contact_settings_are_editable_and_saved(self):
        admin = get_user_model().objects.create_superuser("admin", password="test-password")
        self.client.force_login(admin)
        source = SourceChat.objects.create(title="Market", locator="@market")
        path = f"/admin/scanner/sourcechat/{source.pk}/change/"
        response = self.client.get(path)
        self.assertContains(response, "Контакт продавца / автора")
        response = self.client.post(path, {
            "title": "Market", "locator": "@market", "enabled": "on",
            "parse_start_line": 1, "parse_skip_last_lines": 0,
            "contact_mode": "message", "contact_label": "Написать продавцу", "contact_link_labels": "Продавец товара",
            "_save": "Сохранить",
        })
        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual((source.contact_mode, source.contact_label, source.contact_link_labels), ("message", "Написать продавцу", "Продавец товара"))
