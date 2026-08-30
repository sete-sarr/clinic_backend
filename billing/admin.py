from django.contrib import admin

from .models import Invoice, InvoiceLine


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 1


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "patient", "clinic", "total_amount", "status", "issue_date")
    list_filter = ("clinic", "status")
    inlines = [InvoiceLineInline]
