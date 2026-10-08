"""Validation des images téléversées (logos de clinique, photos de profil).

ImageField rejette déjà tout ce qui n'est pas une véritable image décodable par Pillow (audit de
sécurité, 2026-09-02) ; ces validateurs limitent en plus la taille et les formats à ceux autorisés
par design-system/ (PNG, JPG/JPEG)."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

MAX_IMAGE_SIZE_BYTES = 2 * 1024 * 1024  # 2 Mo
ALLOWED_IMAGE_FORMATS = {"PNG", "JPEG"}


def validate_image_size(value):
    if value.size > MAX_IMAGE_SIZE_BYTES:
        raise ValidationError(_("L'image ne doit pas dépasser 2 Mo."))


def validate_image_format(value):
    image_format = getattr(getattr(value, "image", None), "format", None)
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError(_("Seuls les formats PNG et JPEG sont acceptés."))
