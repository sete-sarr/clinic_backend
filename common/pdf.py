"""Rendu commun des documents PDF (WeasyPrint) — docs/reporting-guidelines.md, docs/i18n.md §2.

Un document est toujours généré dans la langue de sa CLINIQUE (Paramètres → Langue de la clinique),
jamais dans celle de l'utilisateur qui l'imprime : une ordonnance ou une facture remise au patient
doit être identique quel que soit le poste qui la produit. Les contrôles d'accès et l'audit restent
dans le service propre à chaque document."""

from django.template.loader import render_to_string
from django.utils import translation
from weasyprint import HTML


def render_pdf(template_name, context, *, clinic) -> bytes:
    from communication.services import language_for_clinic

    with translation.override(language_for_clinic(clinic)):
        html = render_to_string(template_name, context)
    return HTML(string=html).write_pdf()
