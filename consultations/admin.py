from django.contrib import admin

from .models import Consultation


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = ("patient", "doctor", "date", "status", "clinic")
    list_filter = ("clinic", "status")
    search_fields = ("patient__first_name", "patient__last_name")
