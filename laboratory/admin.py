from django.contrib import admin

from .models import LabOrder, LabTest


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "clinic", "price", "is_active"]
    list_filter = ["clinic", "is_active"]
    search_fields = ["code", "name"]


@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = ["number", "clinic", "patient", "doctor", "status", "created_at"]
    list_filter = ["clinic", "status"]
    search_fields = ["number"]
