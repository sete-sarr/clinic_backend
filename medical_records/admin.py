from django.contrib import admin

from .models import MedicalRecord


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = ("patient", "clinic", "updated_at")
    list_filter = ("clinic",)
    search_fields = ("patient__first_name", "patient__last_name", "patient__patient_number")
