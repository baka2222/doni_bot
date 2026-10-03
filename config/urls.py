from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path

urlpatterns = [
    path("", lambda request: HttpResponseRedirect("/admin/")),
    path("admin/", admin.site.urls),
]
