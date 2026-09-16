from django.contrib import admin

from .models import Medication, StockBatch, StockMovement


@admin.register(Medication)
class MedicationAdmin(admin.ModelAdmin):
    list_display = ("name", "clinic", "current_stock", "min_threshold", "max_threshold", "is_active")
    list_filter = ("clinic", "is_active")
    search_fields = ("name",)


@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    list_display = ("medication", "batch_number", "expiry_date", "quantity_remaining", "clinic")
    list_filter = ("clinic",)
    search_fields = ("batch_number", "medication__name")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("medication", "movement_type", "quantity_delta", "invoice", "created_at", "clinic")
    list_filter = ("clinic", "movement_type")
    search_fields = ("medication__name",)
