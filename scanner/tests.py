from django.test import SimpleTestCase, TestCase
from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model
from django.test import override_settings
from cryptography.fernet import Fernet
from asgiref.sync import async_to_sync
from types import SimpleNamespace
from unittest.mock import patch
from datetime import timedelta
from django.utils import timezone
from django.core.exceptions import ValidationError
from telethon.tl.types import Channel, MessageEntityBold, MessageEntityTextUrl, User
from telethon.errors import SessionPasswordNeededError

from .matching import match_rule
from .models import Keyword, MessageFormat, TargetChat, TelegramAccount
from .telegram_auth import request_code, verify_code
from .telegram_auth import request_qr
from .crypto import encrypt
from .presentation import contact_action, origin_prefix, render_message
from .management.commands.run_scanner import qr_login_session, resolve_chat, send_formatted_message


class MatcherTests(SimpleTestCase):
    def rule(self, **changes):
        data = {"phrase": "ищу швею", "aliases": "нужна швея\nтребуются швеи", "exclusions": "обучение швей", "fuzzy": True}
        data.update(changes)
        return Keyword(**data)

    def test_word_forms_and_aliases(self):
        rule = self.rule()
        self.assertTrue(match_rule("Ищем ШВЕЙ на постоянную работу", rule))
        self.assertTrue(match_rule("Нужны швеи в цех", rule))
        self.assertFalse(match_rule("Пошив швейных изделий", rule))

    def test_exclusion_wins(self):
        self.assertFalse(match_rule("Ищу швею для обучения швей", self.rule()))

    def test_short_words_do_not_fuzzy_match(self):
        self.assertFalse(match_rule("Ищу швеы", self.rule()))

    def test_one_typo_in_long_word(self):
        self.assertTrue(match_rule("Нужно производсво сумок", self.rule(phrase="производство", aliases="", exclusions="")))


class SingletonTests(TestCase):
    def test_only_one_account_and_target(self):
        TelegramAccount.objects.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            TelegramAccount.objects.create(phone="+996123")
        TargetChat.objects.create(locator="-100123", title="Первая")
        with self.assertRaises(IntegrityError), transaction.atomic():
            TargetChat.objects.create(locator="-100456", title="Вторая")
        self.assertEqual(TelegramAccount.objects.count(), 1)
        self.assertEqual(TargetChat.objects.count(), 1)
        self.assertEqual(TargetChat.objects.get().locator, "-100123")


class AdminTests(TestCase):
    def test_message_format_can_be_edited_in_admin(self):
        user = get_user_model().objects.create_superuser("admin", password="a-long-test-password")
        self.client.force_login(user)
        self.assertEqual(MessageFormat.objects.filter(pk=1).count(), 1)
        response = self.client.get("/admin/scanner/messageformat/")
        self.assertEqual(response.status_code, 302)
        response = self.client.get("/admin/scanner/messageformat/1/change/")
        self.assertContains(response, "Шаблон сообщения")
        response = self.client.post("/admin/scanner/messageformat/1/change/", {
            "show_origin": "on",
            "template": "{source}: {text}\n\n{contact}",
            "contact_label": "Связаться",
            "channel_label": "Читать пост",
            "_save": "Сохранить",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MessageFormat.objects.get(pk=1).contact_label, "Связаться")
        self.assertTrue(MessageFormat.objects.get(pk=1).show_origin)

    def test_connection_page_is_available_to_admin(self):
        user = get_user_model().objects.create_superuser("admin", password="a-long-test-password")
        self.client.force_login(user)
        response = self.client.get("/admin/scanner/telegramaccount/connect/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Подключение")
        self.assertEqual(TelegramAccount.objects.count(), 1)

    def test_qr_request_from_admin_page(self):
        user = get_user_model().objects.create_superuser("admin", password="a-long-test-password")
        self.client.force_login(user)
        response = self.client.post("/admin/scanner/telegramaccount/connect/", {"action": "qr"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TelegramAccount.objects.get(pk=1).pending_qr_id)
        response = self.client.get("/admin/scanner/telegramaccount/connect/")
        self.assertContains(response, "Готовим QR-код")

    def test_qr_is_rendered_only_on_admin_page(self):
        user = get_user_model().objects.create_superuser("admin", password="a-long-test-password")
        self.client.force_login(user)
        with override_settings(TELEGRAM_SESSION_KEY=Fernet.generate_key().decode()):
            account = TelegramAccount.objects.create()
            request_qr(account)
            account.pending_qr_url = encrypt("tg://login?token=test-token")
            account.save()
            response = self.client.get("/admin/scanner/telegramaccount/connect/")
        self.assertContains(response, "data:image/png;base64,")

    def test_qr_can_be_cancelled(self):
        user = get_user_model().objects.create_superuser("admin", password="a-long-test-password")
        self.client.force_login(user)
        account = TelegramAccount.objects.create()
        request_qr(account)
        response = self.client.post("/admin/scanner/telegramaccount/connect/", {"action": "cancel_qr"})
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertFalse(account.pending_qr_id)


class TelegramConnectionTests(TestCase):
    def test_code_flow_saves_only_encrypted_session(self):
        class FakeClient:
            session = SimpleNamespace(save=lambda: "secret-session")

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def send_code_request(self, phone):
                return SimpleNamespace(phone_code_hash="hash")

            async def sign_in(self, **kwargs):
                self.test_case.assertEqual(kwargs["phone_code_hash"], "hash")

            async def get_me(self):
                return SimpleNamespace(id=123, bot=False, first_name="Иван", last_name="", username=None)

        client = FakeClient()
        client.test_case = self
        with override_settings(TELEGRAM_SESSION_KEY=Fernet.generate_key().decode()), patch("scanner.telegram_auth.new_client", return_value=client):
            account = TelegramAccount.objects.create()
            async_to_sync(request_code)(account, "+996555000000")
            self.assertNotIn("secret-session", account.pending_session)
            self.assertTrue(async_to_sync(verify_code)(account, code="12345"))
            account.refresh_from_db()
            self.assertEqual(account.telegram_id, 123)
            self.assertNotIn("secret-session", account.encrypted_session)
            self.assertFalse(account.pending_session)

    def test_qr_login_completes_and_clears_pending_request(self):
        class FakeQR:
            url = "tg://login?token=test-token"
            expires = timezone.now() + timedelta(seconds=30)

            async def wait(self):
                pass

        class FakeClient:
            session = SimpleNamespace(save=lambda: "qr-secret-session")

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def qr_login(self):
                return FakeQR()

            async def get_me(self):
                return SimpleNamespace(id=321, bot=False, first_name="Иван", last_name="", username="ivan", phone="123")

        account = TelegramAccount.objects.create()
        request_qr(account)
        with override_settings(TELEGRAM_SESSION_KEY=Fernet.generate_key().decode()), patch(
            "scanner.management.commands.run_scanner.new_client", return_value=FakeClient()
        ):
            async_to_sync(qr_login_session)(account.pending_qr_id)
        account.refresh_from_db()
        self.assertEqual(account.telegram_id, 321)
        self.assertEqual(account.phone, "123")
        self.assertFalse(account.pending_qr_id)
        self.assertNotIn("qr-secret-session", account.encrypted_session)

    def test_qr_login_can_finish_with_two_factor_password(self):
        class FakeQR:
            url = "tg://login?token=test-token"
            expires = timezone.now() + timedelta(seconds=30)

            async def wait(self):
                raise SessionPasswordNeededError(request=None)

        class FakeClient:
            session = SimpleNamespace(save=lambda: "qr-two-factor-session")

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def qr_login(self):
                return FakeQR()

            async def sign_in(self, **kwargs):
                self.test_case.assertEqual(kwargs, {"password": "password"})

            async def get_me(self):
                return SimpleNamespace(id=321, bot=False, first_name="Иван", last_name="", username=None, phone="123")

        client = FakeClient()
        client.test_case = self
        account = TelegramAccount.objects.create()
        request_qr(account)
        with override_settings(TELEGRAM_SESSION_KEY=Fernet.generate_key().decode()), patch(
            "scanner.management.commands.run_scanner.new_client", return_value=client
        ), patch("scanner.telegram_auth.new_client", return_value=client):
            async_to_sync(qr_login_session)(account.pending_qr_id)
            account.refresh_from_db()
            self.assertTrue(account.pending_requires_password)
            self.assertFalse(account.pending_qr_id)
            self.assertTrue(async_to_sync(verify_code)(account, password="password"))
        account.refresh_from_db()
        self.assertEqual(account.telegram_id, 321)


class ChannelAndPresentationTests(SimpleTestCase):
    def test_broadcast_channel_is_valid_source(self):
        channel = Channel(id=123, title="Швейные заказы", photo=None, date=None, broadcast=True, megagroup=False, username="sewing_jobs")
        dialog = SimpleNamespace(id=-1000000000123, entity=channel)
        entity, chat_id = async_to_sync(resolve_chat)(None, str(dialog.id), {dialog.id: dialog})
        self.assertIs(entity, channel)
        self.assertEqual(chat_id, dialog.id)
        with self.assertRaises(ValueError):
            async_to_sync(resolve_chat)(None, str(dialog.id), {dialog.id: dialog}, group_only=True)

    def test_contact_links_for_person_and_channel(self):
        user = User(id=123, username="tailor")
        channel = Channel(id=456, title="Jobs", photo=None, date=None, broadcast=True, username="sewing_jobs")
        self.assertEqual(contact_action(user, channel, 42), ("Написать в личку", "https://t.me/tailor"))
        self.assertEqual(contact_action(channel, channel, 42), ("Открыть публикацию", "https://t.me/sewing_jobs/42"))

    def test_template_keeps_original_and_clickable_contact_in_one_text(self):
        original = MessageEntityBold(offset=3, length=4)
        text, entities = render_message(
            "{source}\n{text}\n\n{contact}", "🧵 Тест", "Написать в личку", "https://t.me/tailor",
            "Заказы", original_entities=[original],
        )
        self.assertEqual(text, "Заказы\n🧵 Тест\n\nНаписать в личку")
        self.assertEqual(original.offset, 3)
        self.assertEqual(entities[0].offset, 10)
        self.assertIsInstance(entities[1], MessageEntityTextUrl)
        self.assertEqual(entities[1].url, "https://t.me/tailor")

    def test_origin_toggle_adds_source_once_and_shifts_links(self):
        prefix = origin_prefix("{text}\n\n{contact}", "Швейные заказы", True)
        text, entities = render_message(
            "{text}\n\n{contact}", "Ищу швею", "Написать в личку", "https://t.me/tailor",
            origin_prefix=prefix,
        )
        self.assertEqual(text, "↪ Источник: Швейные заказы\n\nИщу швею\n\nНаписать в личку")
        self.assertEqual(entities[0].offset, len(text[:text.index("Написать в личку")].encode("utf-16-le")) // 2)
        self.assertEqual(origin_prefix("{source}\n{text}\n\n{contact}", "Швейные заказы", True), "")
        self.assertEqual(origin_prefix("{text}\n\n{contact}", "Швейные заказы", False), "")

    def test_template_rejects_duplicate_or_unsafe_fields(self):
        for template in ("{text}", "{text}{text}{contact}", "{text.__class__}{contact}"):
            with self.subTest(template=template), self.assertRaises(ValidationError):
                MessageFormat(template=template).clean()

    def test_text_is_sent_once_with_contact_link(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            async def send_message(self, *args, **kwargs):
                self.calls.append(("message", args, kwargs))

            async def send_file(self, *args, **kwargs):
                self.calls.append(("file", args, kwargs))

        client = FakeClient()
        user = User(id=123, username="tailor")
        chat = Channel(id=456, title="Jobs", photo=None, date=None, broadcast=True, username="sewing_jobs")
        event = SimpleNamespace(id=42, raw_text="Ищу швею", message=SimpleNamespace(photo=None, document=None, entities=[], noforwards=False))

        async def get_sender():
            return user

        async def get_chat():
            return chat

        event.get_sender = get_sender
        event.get_chat = get_chat
        async_to_sync(send_formatted_message)(client, "target", event, MessageFormat(), SimpleNamespace(title="Jobs"), SimpleNamespace(phrase="ищу швею"))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], "message")
        self.assertEqual(client.calls[0][1][1], "Ищу швею\n\nНаписать в личку")

    def test_media_caption_is_sent_as_one_message(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            async def send_file(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        client = FakeClient()
        user = User(id=123, username="tailor")
        chat = Channel(id=456, title="Jobs", photo=None, date=None, broadcast=True, username="sewing_jobs")
        media = object()
        event = SimpleNamespace(id=42, raw_text="Ищу швею", message=SimpleNamespace(photo=object(), document=None, media=media, entities=[], noforwards=False))

        async def get_sender():
            return user

        async def get_chat():
            return chat

        event.get_sender = get_sender
        event.get_chat = get_chat
        async_to_sync(send_formatted_message)(client, "target", event, MessageFormat(), SimpleNamespace(title="Jobs"), SimpleNamespace(phrase="ищу швею"))
        self.assertEqual(len(client.calls), 1)
        self.assertIs(client.calls[0][0][1], media)
        self.assertEqual(client.calls[0][1]["caption"], "Ищу швею\n\nНаписать в личку")
