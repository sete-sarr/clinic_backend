from django.contrib import admin

from .models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("patient_number", "first_name", "last_name", "clinic", "phone", "is_active")
    list_filter = ("clinic", "is_active")
    search_fields = ("patient_number", "first_name", "last_name", "phone", "national_id")
