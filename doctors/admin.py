from django.contrib import admin

from .models import Doctor


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("__str__", "clinic", "department", "specialty", "is_active")
    list_filter = ("clinic", "department", "is_active")
    search_fields = ("user__first_name", "user__last_name", "professional_number")
