import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("scanner", "0004_messageformat_show_origin"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcechat",
            name="parse_end_before",
            field=models.TextField(blank=True, help_text="Маркеры по одному на строке, например «KGS» или «Телефон:». Первый найденный маркер и всё после него отбрасываются. Регистр не важен.", verbose_name="Заканчивать перед строкой с текстом"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="parse_end_line",
            field=models.PositiveIntegerField(blank=True, help_text="Включительно. Пусто — до конца поста.", null=True, validators=[django.core.validators.MinValueValidator(1)], verbose_name="Заканчивать на строке"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="parse_exclude_contains",
            field=models.TextField(blank=True, help_text="По одной фразе на строке. Удаляет отдельные строки с любым из этих фрагментов, без учёта регистра.", verbose_name="Исключать строки, содержащие текст"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="parse_exclude_lines",
            field=models.TextField(blank=True, default="Скрыт\nРазместить объявление\nНаписать продавцу\nДобавить в избранное\nПрофиль", help_text="По одной фразе на строке. Совпадение со всей строкой; эмодзи, знаки препинания и регистр не учитываются.", verbose_name="Исключать служебные строки"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="parse_remove_spoilers",
            field=models.BooleanField(default=True, help_text="Убирает целиком строки со спойлерами Telegram, например скрытый номер телефона. При проверке вставленного текста форматирование спойлеров недоступно.", verbose_name="Исключать строки со скрытым текстом"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="parse_skip_last_lines",
            field=models.PositiveIntegerField(default=0, help_text="Количество последних строк исходного поста, которые не входят в описание.", verbose_name="Убрать строк с конца"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="parse_start_after",
            field=models.TextField(blank=True, help_text="Маркеры по одному на строке. Начало после первого совпадения с любым маркером в выбранном диапазоне. Если совпадения нет, пост пропускается.", verbose_name="Начинать после строки с текстом"),
        ),
        migrations.AddField(
            model_name="sourcechat",
            name="parse_start_line",
            field=models.PositiveIntegerField(default=1, help_text="Номер строки исходного поста, начиная с 1. Пустые строки тоже считаются.", validators=[django.core.validators.MinValueValidator(1)], verbose_name="Начинать со строки"),
        ),
    ]
