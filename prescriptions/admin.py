from django.contrib import admin

from .models import Prescription, PrescriptionItem


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 1


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("patient", "doctor", "status", "clinic", "created_at")
    list_filter = ("clinic", "status")
    inlines = [PrescriptionItemInline]
