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
        """Fait passer les changements de statut par subscriptions.services afin que record_audit
        se déclenche toujours — un simple save() de champ admin ne doit jamais contourner
        silencieusement la journalisation d'audit (business/subscription-billing-policy.md :
        "Chaque changement de statut d'abonnement est journalisé")."""
        from subscriptions.models import SubscriptionEvent
        from subscriptions.services import change_subscription_status

        if change and "subscription_status" in form.changed_data:
            old = Clinic.objects.get(pk=obj.pk)
            new_status = obj.subscription_status
            obj.subscription_status = old.subscription_status  # laisser le service appliquer la transition
            super().save_model(request, obj, form, change)  # persister d'abord les modifications des AUTRES champs
            try:
                change_subscription_status(
                    clinic=obj, status=new_status, changed_by=request.user,
                    source=SubscriptionEvent.Source.ADMIN_MANUAL, metadata={"clinic_id": obj.pk},
                )
            except ValueError as exc:
                raise ValidationError(str(exc))  # s'affiche comme une erreur de formulaire admin normale, pas une 500 brute
        else:
            super().save_model(request, obj, form, change)
