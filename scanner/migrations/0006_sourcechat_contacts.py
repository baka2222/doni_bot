from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("scanner", "0005_sourcechat_parsing"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcechat",
            name="contact_label",
            field=models.CharField(blank=True, help_text="Например, «Написать продавцу». Пусто — использовать подпись из раздела «Формат сообщений».", max_length=80, verbose_name="Подпись контакта"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="contact_link_labels",
            field=models.TextField(blank=True, default="Написать продавцу\nСвязаться с продавцом\nНаписать в личку\nПродавец\nКонтакт", help_text="Подписи ссылок или кнопок, по одной на строке. Также подходит строка «Продавец: @username». Сравнение без учёта эмодзи, знаков препинания и регистра. Ссылка ищется в полном исходном посте до обрезки описания.", verbose_name="Как подписана ссылка продавца в исходном посте"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="contact_mode",
            field=models.CharField(choices=[("auto", "Ссылка продавца из поста, иначе автор сообщения"), ("message", "Только ссылка продавца из поста"), ("sender", "Только автор сообщения"), ("none", "Без ссылки (только подпись)")], default="auto", help_text="Ссылка ведёт только в личный аккаунт. Если подходящего контакта нет, подпись остаётся некликабельной. Каналы и боты не используются.", max_length=16, verbose_name="Откуда брать контакт"),
        ),
    ]
