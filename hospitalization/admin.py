from django.contrib import admin

from .models import Admission, Bed, Room, RoomType


@admin.register(RoomType)
class RoomTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "clinic", "nightly_rate", "is_active"]
    list_filter = ["clinic"]


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ["number", "clinic", "department", "room_type", "is_active"]
    list_filter = ["clinic"]


@admin.register(Bed)
class BedAdmin(admin.ModelAdmin):
    list_display = ["__str__", "clinic", "status", "is_active"]
    list_filter = ["clinic", "status"]


@admin.register(Admission)
class AdmissionAdmin(admin.ModelAdmin):
    list_display = ["number", "clinic", "patient", "doctor", "status", "admitted_at", "discharged_at"]
    list_filter = ["clinic", "status"]
    search_fields = ["number"]
