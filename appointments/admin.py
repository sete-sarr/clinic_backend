from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("patient", "doctor", "date", "time", "status", "clinic")
    list_filter = ("clinic", "status", "date")
    search_fields = ("patient__first_name", "patient__last_name", "doctor__user__last_name")
