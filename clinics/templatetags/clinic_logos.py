"""Filtre de gabarit pour les PDF : {{ clinic|clinic_print_logo }} -> data URI du logo (ou "")."""

from django import template

from clinics.services import print_logo_data_uri

register = template.Library()


@register.filter
def clinic_print_logo(clinic):
    return print_logo_data_uri(clinic) if clinic else ""
