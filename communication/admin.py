from django.contrib import admin

from .models import NotificationLog, OtpCode


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ["id", "channel", "notification_type", "recipient_address", "status", "clinic", "created_at"]
    list_filter = ["channel", "notification_type", "status"]
    search_fields = ["recipient_address"]
    readonly_fields = [field.name for field in NotificationLog._meta.fields]


@admin.register(OtpCode)
class OtpCodeAdmin(admin.ModelAdmin):
    list_display = ["id", "purpose", "user", "patient", "clinic", "attempts", "expires_at", "consumed_at"]
    list_filter = ["purpose"]
    readonly_fields = [field.name for field in OtpCode._meta.fields]
