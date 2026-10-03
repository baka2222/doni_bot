from django.core.exceptions import ValidationError
from django.db import models
from string import Formatter


class SingletonModel(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class TelegramAccount(SingletonModel):
    phone = models.CharField("Телефон", max_length=32, blank=True)
    telegram_id = models.BigIntegerField("Telegram ID", null=True, blank=True)
    name = models.CharField("Имя", max_length=255, blank=True)
    encrypted_session = models.TextField("Зашифрованная сессия", blank=True, editable=False)
    pending_session = models.TextField("Ожидающая сессия", blank=True, editable=False)
    pending_phone_hash = models.CharField("Хеш кода", max_length=255, blank=True, editable=False)
    pending_at = models.DateTimeField("Код запрошен", null=True, blank=True, editable=False)
    pending_requires_password = models.BooleanField("Нужен пароль 2FA", default=False, editable=False)
    pending_qr_id = models.CharField("Запрос QR", max_length=36, blank=True, editable=False)
    pending_qr_url = models.TextField("QR-ссылка", blank=True, editable=False)
    pending_qr_expires_at = models.DateTimeField("QR действителен до", null=True, blank=True, editable=False)
    pending_qr_error = models.TextField("Ошибка QR", blank=True, editable=False)
    connected_at = models.DateTimeField("Подключен", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Telegram-аккаунт"
        verbose_name_plural = "Telegram-аккаунт"

    def __str__(self):
        return self.name or self.phone or "Аккаунт не подключен"

    @property
    def is_connected(self):
        return bool(self.encrypted_session)


class SourceChat(models.Model):
    title = models.CharField("Название", max_length=255)
    locator = models.CharField("ID, @username или ссылка", max_length=500, unique=True)
    chat_id = models.BigIntegerField("Telegram ID", null=True, blank=True, editable=False)
    enabled = models.BooleanField("Сканировать", default=True)
    last_error = models.TextField("Ошибка подключения", blank=True, editable=False)
    resolved_at = models.DateTimeField("Проверено", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Группа или канал-источник"
        verbose_name_plural = "Группы и каналы-источники"
        ordering = ["title"]

    def __str__(self):
        return self.title

    def clean(self):
        if self.locator and not self.locator.strip():
            raise ValidationError({"locator": "Укажите ID или ссылку на группу или канал."})

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values_list("locator", flat=True).first()
            if old != self.locator:
                self.chat_id = None
                self.resolved_at = None
                self.last_error = ""
        super().save(*args, **kwargs)


class TargetChat(SingletonModel):
    title = models.CharField("Название", max_length=255, default="Моя группа")
    locator = models.CharField("ID, @username или ссылка", max_length=500)
    chat_id = models.BigIntegerField("Telegram ID", null=True, blank=True, editable=False)
    last_error = models.TextField("Ошибка подключения", blank=True, editable=False)
    resolved_at = models.DateTimeField("Проверено", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Целевая группа"
        verbose_name_plural = "Целевая группа"

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        old = type(self).objects.filter(pk=1).values_list("locator", flat=True).first()
        if old != self.locator:
            self.chat_id = None
            self.resolved_at = None
            self.last_error = ""
        super().save(*args, **kwargs)


class MessageFormat(SingletonModel):
    show_origin = models.BooleanField(
        "Показывать источник сообщения",
        default=False,
        help_text="Добавляет строку с названием группы или канала в то же сообщение.",
    )
    template = models.TextField(
        "Шаблон сообщения",
        default="{text}\n\n{contact}",
        help_text="Поля: {text}, {contact}, {source}, {keyword}. {text} и {contact} нужны по одному разу.",
    )
    contact_label = models.CharField("Текст ссылки на автора", max_length=80, default="Написать в личку")
    channel_label = models.CharField("Текст ссылки на канал", max_length=80, default="Открыть публикацию")

    class Meta:
        verbose_name = "Формат сообщения"
        verbose_name_plural = "Формат сообщений"

    def __str__(self):
        return "Формат отправляемых сообщений"

    def clean(self):
        super().clean()
        counts = {"text": 0, "contact": 0}
        try:
            parts = list(Formatter().parse(self.template))
        except ValueError as exc:
            raise ValidationError({"template": f"Неверные фигурные скобки: {exc}"}) from exc
        for _, field, spec, conversion in parts:
            if field is None:
                continue
            if field not in {"text", "contact", "source", "keyword"} or spec or conversion:
                raise ValidationError({"template": "Допустимы только поля {text}, {contact}, {source}, {keyword} без дополнительных параметров."})
            if field in counts:
                counts[field] += 1
        if counts != {"text": 1, "contact": 1}:
            raise ValidationError({"template": "Добавьте по одному полю {text} и {contact}."})


class Keyword(models.Model):
    phrase = models.CharField("Фраза", max_length=255, unique=True)
    aliases = models.TextField("Алиасы (по одному на строке)", blank=True)
    exclusions = models.TextField("Исключающие фразы (по одной на строке)", blank=True)
    fuzzy = models.BooleanField("Учитывать опечатки", default=True)
    enabled = models.BooleanField("Активно", default=True)

    class Meta:
        verbose_name = "Ключевое слово / фраза"
        verbose_name_plural = "Ключевые слова / фразы"
        ordering = ["phrase"]

    def __str__(self):
        return self.phrase


class Delivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        SENT = "sent", "Переслано"
        FAILED = "failed", "Ошибка"

    source = models.ForeignKey(SourceChat, on_delete=models.PROTECT, verbose_name="Источник")
    source_chat_id = models.BigIntegerField("ID исходного чата")
    message_id = models.BigIntegerField("ID сообщения")
    target_chat_id = models.BigIntegerField("ID целевой группы")
    keyword = models.ForeignKey(Keyword, on_delete=models.SET_NULL, null=True, verbose_name="Правило")
    preview = models.TextField("Текст", blank=True)
    status = models.CharField("Статус", max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField("Попытки", default=0)
    last_error = models.TextField("Ошибка", blank=True)
    created_at = models.DateTimeField("Получено", auto_now_add=True)
    sent_at = models.DateTimeField("Переслано", null=True, blank=True)

    class Meta:
        verbose_name = "Пересылка"
        verbose_name_plural = "Журнал пересылок"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["source_chat_id", "message_id", "target_chat_id"], name="unique_delivery")]

    def __str__(self):
        return f"{self.source_chat_id}/{self.message_id} → {self.target_chat_id}"
