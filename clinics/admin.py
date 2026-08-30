from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import Clinic


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = (
        "name", "email", "phone", "is_active", "subscription_status", "plan_tier", "created_at",
    )
    list_filter = ("subscription_status", "plan_tier", "billing_cycle", "is_active")
    search_fields = ("name", "email")
    readonly_fields = ("stripe_customer_id", "stripe_subscription_id", "current_period_end")

    def save_model(self, request, obj, form, change):
        """Route status changes through subscriptions.services so record_audit still fires — a
        raw admin field save() must never silently bypass audit logging
        (business/subscription-billing-policy.md: "Every subscription status change is logged")."""
        from subscriptions.models import SubscriptionEvent
        from subscriptions.services import change_subscription_status

        if change and "subscription_status" in form.changed_data:
            old = Clinic.objects.get(pk=obj.pk)
            new_status = obj.subscription_status
            obj.subscription_status = old.subscription_status  # let the service apply the transition
            super().save_model(request, obj, form, change)  # persist any OTHER field edits first
            try:
                change_subscription_status(
                    clinic=obj, status=new_status, changed_by=request.user,
                    source=SubscriptionEvent.Source.ADMIN_MANUAL, metadata={"clinic_id": obj.pk},
                )
            except ValueError as exc:
                raise ValidationError(str(exc))  # renders as a normal admin form error, not a raw 500
        else:
            super().save_model(request, obj, form, change)
