from django.contrib.auth.models import AbstractUser, UnicodeUsernameValidator
from django.db import models


class User(AbstractUser):
    """
    clinic is nullable only for platform-level superusers; every doctor,
    secretary, accountant, clinic_admin, or patient user must have one
    (docs/architecture.md: "an authenticated user is attached to one clinic").

    A user account belongs to exactly one clinic — there is no cross-clinic identity. The same
    physical person employed by two different clinics (e.g. a doctor working at both) needs two
    separate accounts, one per clinic, each with its own email (see USERNAME_FIELD note below).
    This is a pre-existing, deliberate limitation of the one-clinic-per-account model, not
    something introduced by the email/username uniqueness split.

    email is the unique login identifier (USERNAME_FIELD) across the whole platform — two
    different clinics' staff/doctors cannot share an email, but CAN share a username (usernames
    are only unique per clinic, see Meta.constraints). This lets an unrelated "jdupont" exist in
    both Clinic A and Clinic B without collision, while login-by-email stays unambiguous.
    """

    username = models.CharField(
        "username",
        max_length=150,
        help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
        validators=[UnicodeUsernameValidator()],
    )
    email = models.EmailField("email address", unique=True)
    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.PROTECT, null=True, blank=True, related_name="users"
    )
    # Préférence de langue de l'utilisateur (docs/i18n.md §2) : "" = suit la langue de sa clinique.
    # Mêmes codes que settings.LANGUAGES et Clinic.Locale.
    language = models.CharField(
        max_length=2, blank=True, default="", choices=[("fr", "Français"), ("en", "English")]
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["clinic", "username"], name="unique_username_per_clinic"),
        ]

    def __str__(self):
        return self.get_username()

    @property
    def effective_language(self):
        """Langue à utiliser pour cet utilisateur (écrans, e-mails) : sa préférence, sinon celle
        de sa clinique, sinon la langue par défaut de la plateforme."""
        from django.conf import settings

        return self.language or getattr(self.clinic, "locale", "") or settings.LANGUAGE_CODE
