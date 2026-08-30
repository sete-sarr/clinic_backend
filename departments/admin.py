from django.contrib import admin

from .models import Department


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "clinic", "department_type", "status")
    list_filter = ("clinic", "department_type", "status")
    search_fields = ("name", "code")
