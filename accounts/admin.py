from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "clinic", "is_staff", "is_active")
    list_filter = DjangoUserAdmin.list_filter + ("clinic",)
    fieldsets = DjangoUserAdmin.fieldsets + (("Clinic", {"fields": ("clinic",)}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (("Clinic", {"fields": ("clinic",)}),)
