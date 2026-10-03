import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Создать первого администратора из переменных окружения"

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if not username or not password:
            raise ValueError("Задайте DJANGO_SUPERUSER_USERNAME и DJANGO_SUPERUSER_PASSWORD")
        user_model = get_user_model()
        if not user_model.objects.filter(username=username).exists():
            user_model.objects.create_superuser(username=username, password=password)
            self.stdout.write(self.style.SUCCESS("Администратор создан"))
