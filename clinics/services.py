"""Logos de clinique (design-system/branding.md) : stockage en base et diffusion.

- Écriture : set_clinic_logo / remove_clinic_logo, appelés par ClinicSerializer.update() après
  validation (PNG/JPEG, 2 Mo max).
- Écran : URL signée (django.core.signing) servie par ClinicLogoView, pour qu'une balise <img> la
  charge sans en-tête d'authentification, sans pour autant rendre un logo accessible à partir du
  seul identifiant de la clinique. La version (date de mise à jour) fait partie de la signature :
  un nouveau logo change l'URL, ce qui permet une mise en cache longue.
- PDF : data URI (logo d'impression, sinon logo principal), lisible par WeasyPrint sans accès
  réseau ni fichier."""

import base64

from django.core import signing
from django.db import transaction
from django.urls import reverse

from clinics.models import ClinicLogo

# Champ de l'API (ClinicSerializer) -> type de logo.
LOGO_FIELDS = {
    "logo_light": ClinicLogo.Kind.LIGHT,
    "logo_dark": ClinicLogo.Kind.DARK,
    "logo_print": ClinicLogo.Kind.PRINT,
    "favicon": ClinicLogo.Kind.FAVICON,
}
CONTENT_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg"}
# Documents imprimés : logo d'impression s'il existe, sinon le logo principal.
PRINT_LOGO_PREFERENCE = (ClinicLogo.Kind.PRINT, ClinicLogo.Kind.LIGHT)

# Signature sans horodatage : l'URL d'un logo reste stable tant qu'il n'est pas remplacé (sa
# version fait partie du jeton), ce qui permet au navigateur de la garder en cache.
_SIGNER = signing.Signer(salt="clinics.logo")


def set_clinic_logo(*, clinic, kind, uploaded_file):
    """Enregistre (ou remplace) le logo `kind`. `uploaded_file` vient d'un ImageField validé :
    Pillow a déjà identifié son format (uploaded_file.image.format)."""
    image_format = uploaded_file.image.format
    uploaded_file.seek(0)
    ClinicLogo.objects.update_or_create(
        clinic=clinic,
        kind=kind,
        defaults={"content": uploaded_file.read(), "content_type": CONTENT_TYPES[image_format]},
    )


def remove_clinic_logo(*, clinic, kind):
    ClinicLogo.objects.filter(clinic=clinic, kind=kind).delete()


@transaction.atomic
def update_clinic_logos(*, clinic, changes):
    """`changes` : {champ de l'API: fichier validé, ou None pour retirer le logo}."""
    for field, uploaded_file in changes.items():
        kind = LOGO_FIELDS[field]
        if uploaded_file is None:
            remove_clinic_logo(clinic=clinic, kind=kind)
        else:
            set_clinic_logo(clinic=clinic, kind=kind, uploaded_file=uploaded_file)


def logos_without_content(clinic):
    """{type: ClinicLogo} sans charger les octets (assez pour construire les URL)."""
    return {logo.kind: logo for logo in ClinicLogo.objects.filter(clinic=clinic).defer("content")}


def _version(logo):
    return int(logo.updated_at.timestamp() * 1000)


def logo_url(*, logo, request=None):
    token = _SIGNER.sign_object({"c": logo.clinic_id, "k": logo.kind, "v": _version(logo)})
    path = reverse("clinic-logo", args=[token])
    return request.build_absolute_uri(path) if request is not None else path


def logo_from_token(token):
    """Le logo désigné par une URL signée, ou None (signature invalide, logo retiré ou remplacé)."""
    try:
        data = _SIGNER.unsign_object(token)
        clinic_id, kind, version = data["c"], data["k"], data["v"]
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
    logo = ClinicLogo.objects.filter(clinic_id=clinic_id, kind=kind).first()
    if logo is None or _version(logo) != version:
        return None
    return logo


def print_logo_data_uri(clinic):
    """Data URI du logo à imprimer sur les documents PDF de la clinique, ou "" s'il n'y en a pas."""
    logos = {logo.kind: logo for logo in ClinicLogo.objects.filter(clinic=clinic, kind__in=PRINT_LOGO_PREFERENCE)}
    for kind in PRINT_LOGO_PREFERENCE:
        logo = logos.get(kind)
        if logo is not None:
            encoded = base64.b64encode(bytes(logo.content)).decode("ascii")
            return f"data:{logo.content_type};base64,{encoded}"
    return ""
