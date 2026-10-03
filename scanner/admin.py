import base64
from io import BytesIO

from asgiref.sync import async_to_sync
import qrcode
from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import Delivery, Keyword, MessageFormat, SourceChat, TargetChat, TelegramAccount
from .presentation import origin_prefix, render_message
from .crypto import decrypt
from .telegram_auth import cancel_qr, request_code, request_qr, revoke_account, verify_code


class PhoneForm(forms.Form):
    phone = forms.CharField(label="Номер телефона", max_length=32, widget=forms.TextInput(attrs={"placeholder": "+996...", "autocomplete": "tel"}))


class CodeForm(forms.Form):
    code = forms.CharField(label="Код Telegram", max_length=20, widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}))


class PasswordForm(forms.Form):
    password = forms.CharField(label="Пароль двухэтапной проверки", widget=forms.PasswordInput)


@admin.register(TelegramAccount)
class TelegramAccountAdmin(admin.ModelAdmin):
    list_display = ("__str__", "telegram_id", "connected_at", "connection_link")
    readonly_fields = ("phone", "telegram_id", "name", "connected_at", "connection_link")
    fields = ("phone", "telegram_id", "name", "connected_at", "connection_link")

    def has_add_permission(self, request):
        return request.user.is_superuser and not TelegramAccount.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Действие")
    def connection_link(self, obj):
        return format_html('<a class="button" href="{}">{}</a>', reverse("admin:telegram-connect"), "Управление подключением")

    def get_urls(self):
        return [path("connect/", self.admin_site.admin_view(self.connect_view), name="telegram-connect")] + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        if not TelegramAccount.objects.exists():
            return HttpResponseRedirect(reverse("admin:telegram-connect"))
        return super().changelist_view(request, extra_context)

    def connect_view(self, request):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Только суперпользователь может управлять Telegram-аккаунтом.")
        account, _ = TelegramAccount.objects.get_or_create(pk=1)
        if request.method == "POST":
            action = request.POST.get("action")
            try:
                if action == "qr" and not account.is_connected:
                    request_qr(account)
                    messages.info(request, "Откройте Telegram на телефоне → Настройки → Устройства → Подключить устройство и отсканируйте QR-код.")
                    return HttpResponseRedirect(request.path)
                elif action == "cancel_qr" and account.pending_qr_id and not account.is_connected:
                    cancel_qr(account)
                    return HttpResponseRedirect(request.path)
                elif action == "phone" and not account.is_connected:
                    form = PhoneForm(request.POST)
                    if form.is_valid():
                        async_to_sync(request_code)(account, form.cleaned_data["phone"].strip())
                        messages.success(request, "Код отправлен через Telegram. Введите его ниже.")
                        return HttpResponseRedirect(request.path)
                elif action == "code" and account.pending_session and not account.is_connected:
                    form = CodeForm(request.POST)
                    if form.is_valid():
                        if async_to_sync(verify_code)(account, code=form.cleaned_data["code"]):
                            messages.success(request, "Telegram-аккаунт подключён.")
                        else:
                            messages.info(request, "Введите пароль двухэтапной проверки.")
                        return HttpResponseRedirect(request.path)
                elif action == "password" and account.pending_requires_password and not account.is_connected:
                    form = PasswordForm(request.POST)
                    if form.is_valid():
                        async_to_sync(verify_code)(account, password=form.cleaned_data["password"])
                        messages.success(request, "Telegram-аккаунт подключён.")
                        return HttpResponseRedirect(request.path)
                elif action == "disconnect" and account.is_connected:
                    async_to_sync(revoke_account)(account)
                    messages.success(request, "Аккаунт отключён.")
                    return HttpResponseRedirect(request.path)
            except Exception as exc:
                messages.error(request, f"Telegram: {exc}")
        qr_image = ""
        if account.pending_qr_id and account.pending_qr_url:
            try:
                image = qrcode.make(decrypt(account.pending_qr_url))
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                qr_image = base64.b64encode(buffer.getvalue()).decode("ascii")
            except Exception:
                messages.error(request, "Не удалось показать QR-код. Обновите его.")
        return render(request, "admin/scanner/connect.html", {
            **self.admin_site.each_context(request),
            "title": "Подключение Telegram-аккаунта",
            "account": account,
            "phone_form": PhoneForm(),
            "code_form": CodeForm(),
            "password_form": PasswordForm(),
            "qr_image": qr_image,
        })


@admin.register(SourceChat)
class SourceChatAdmin(admin.ModelAdmin):
    list_display = ("title", "locator", "chat_id", "enabled", "resolved_at", "last_error")
    list_filter = ("enabled",)
    search_fields = ("title", "locator")
    readonly_fields = ("chat_id", "last_error", "resolved_at")


@admin.register(TargetChat)
class TargetChatAdmin(admin.ModelAdmin):
    list_display = ("title", "locator", "chat_id", "resolved_at", "last_error")
    readonly_fields = ("chat_id", "last_error", "resolved_at")

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not TargetChat.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Keyword)
class KeywordAdmin(admin.ModelAdmin):
    list_display = ("phrase", "enabled", "fuzzy")
    list_filter = ("enabled", "fuzzy")
    search_fields = ("phrase", "aliases")


@admin.register(MessageFormat)
class MessageFormatAdmin(admin.ModelAdmin):
    fields = ("show_origin", "template", "contact_label", "channel_label", "preview")
    readonly_fields = ("preview",)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not MessageFormat.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if MessageFormat.objects.filter(pk=1).exists():
            return HttpResponseRedirect(reverse("admin:scanner_messageformat_change", args=[1]))
        return super().changelist_view(request, extra_context)

    @admin.display(description="Пример с текущими настройками")
    def preview(self, obj):
        if not obj:
            return "Сохраните настройки, чтобы увидеть пример."
        try:
            origin = origin_prefix(obj.template, "Швейные заказы", obj.show_origin)
            rendered, _ = render_message(
                obj.template, "Ищу швею для заказа", obj.contact_label, "https://t.me/example",
                "Швейные заказы" if obj.show_origin else "", "ищу швею", origin_prefix=origin,
            )
        except ValueError:
            return "Исправьте шаблон, чтобы увидеть пример."
        return format_html('<div style="white-space:pre-wrap;border:1px solid #ddd;padding:12px;border-radius:8px">{}</div>', rendered)


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "source", "message_id", "keyword", "status", "attempts", "last_error")
    list_filter = ("status", "source", "keyword")
    search_fields = ("preview", "last_error")
    readonly_fields = ("source", "source_chat_id", "message_id", "target_chat_id", "keyword", "preview", "status", "attempts", "last_error", "created_at", "sent_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
