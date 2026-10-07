from django.contrib import admin

from .models import VisitLog


@admin.register(VisitLog)
class VisitLogAdmin(admin.ModelAdmin):
    list_display = ["visitor_name", "clinic", "visitor_type", "checked_in_at", "checked_out_at", "auto_closed"]
    list_filter = ["clinic", "visitor_type", "auto_closed"]
    search_fields = ["visitor_name"]
