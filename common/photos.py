"""Photos de profil (personnel, médecins, patients) : enregistrement et diffusion.

- Enregistrement : l'image validée (PNG/JPEG, 2 Mo max — common/images.py) est recadrée en carré,
  réduite à PHOTO_SIZE px et ré-encodée en JPEG, ce qui retire aussi les métadonnées EXIF
  (position GPS, appareil…), puis déposée comme objet privé dans le stockage des photos (bucket
  Cloudflare R2, settings.STORAGES["photos"]). L'ancien objet n'est supprimé qu'une fois la
  transaction validée. Chaque ajout ou retrait est journalisé.
- Écran : URL signée servie par PhotoView (qui lit l'objet dans le bucket), pour qu'une balise <img>
  la charge sans le JWT et sans jamais rendre le bucket public. Donnée
  personnelle : contrairement aux logos, l'URL expire. Elle reste identique pendant une fenêtre de
  PHOTO_URL_WINDOW secondes (cache navigateur possible), puis reste valable une fenêtre de plus.
"""

import io
import time
from functools import cache

from django.apps import apps
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from PIL import Image, ImageOps
from rest_framework import serializers

from common.audit import record_audit
from common.images import validate_image_format, validate_image_size
from common.models import AuditLog, PhotoBase

PHOTO_SIZE = 512
PHOTO_URL_WINDOW = 6 * 3600
PHOTO_CONTENT_TYPE = "image/jpeg"

_SIGNER = signing.Signer(salt="common.photo")


class PhotoUploadSerializer(serializers.Serializer):
    photo = serializers.ImageField(validators=[validate_image_size, validate_image_format])


class PhotoUrlMixin(serializers.Serializer):
    """Champ `photo` : URL signée de la photo de profil (common/photos.py), ou null. Absolue dès que
    la requête est dans le contexte — le frontend n'est pas servi par le même domaine que l'API."""

    photo = serializers.SerializerMethodField()

    def get_photo(self, obj):
        return owner_photo_url(obj, self.context.get("request"))


def normalize_photo(uploaded_file):
    """Octets JPEG carrés de PHOTO_SIZE px, orientation EXIF appliquée, transparence sur fond blanc."""
    uploaded_file.seek(0)
    with Image.open(uploaded_file) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    background = Image.new("RGB", image.size, "white")
    background.paste(image, mask=image.getchannel("A"))
    square = ImageOps.fit(background, (PHOTO_SIZE, PHOTO_SIZE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    square.save(buffer, "JPEG", quality=85, optimize=True)
    return buffer.getvalue()


def _delete_after_commit(image_field):
    """Supprime l'objet du bucket une fois la transaction validée (jamais une photo encore référencée)."""
    storage, name = image_field.storage, image_field.name
    if name:
        transaction.on_commit(lambda: storage.delete(name))


def set_photo(*, model, owner, uploaded_file, actor, **extra):
    """Ajoute ou remplace la photo de `owner`. `extra` : champs propres au modèle (consentement…)."""
    content = ContentFile(normalize_photo(uploaded_file))
    with transaction.atomic():
        photo = model.objects.select_for_update().filter(**{model.owner_field: owner}).first()
        if photo is not None:
            _delete_after_commit(photo.image)
        else:
            photo = model(**{model.owner_field: owner})
        for field, value in extra.items():
            setattr(photo, field, value)
        photo.image.save("photo.jpg", content, save=False)
        try:
            photo.save()
        except Exception:
            photo.image.storage.delete(photo.image.name)  # pas d'objet orphelin dans le bucket
            raise
        record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=owner, metadata={"photo": "set"})
    owner.photo = photo  # remplace une éventuelle photo mise en cache par select_related


def remove_photo(*, model, owner, actor):
    """Retrait définitif de la ligne et de l'objet (minimisation des données) ; sans effet s'il n'y
    a pas de photo."""
    with transaction.atomic():
        photo = model.objects.select_for_update().filter(**{model.owner_field: owner}).first()
        if photo is not None:
            _delete_after_commit(photo.image)
            photo.delete()
            record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=owner, metadata={"photo": "removed"})
    descriptor = type(owner).photo
    if descriptor.is_cached(owner):
        descriptor.related.delete_cached_value(owner)


def read_photo(photo):
    """Octets JPEG de la photo, ou None si l'objet est introuvable dans le bucket."""
    try:
        with photo.image.open("rb") as image_file:
            return image_file.read()
    except Exception:  # objet absent, bucket injoignable : la page affiche l'image par défaut
        return None


@cache
def _photo_models():
    return {model.photo_kind: model for model in apps.get_models() if issubclass(model, PhotoBase)}


def _version(photo):
    return int(photo.updated_at.timestamp() * 1000)


def photo_url(photo, request=None):
    expires = (int(time.time()) // PHOTO_URL_WINDOW + 2) * PHOTO_URL_WINDOW
    token = _SIGNER.sign_object({"k": photo.photo_kind, "o": photo.pk, "v": _version(photo), "e": expires})
    path = reverse("photo", args=[token])
    return request.build_absolute_uri(path) if request is not None else path


def owner_photo_url(owner, request=None):
    """URL de la photo de `owner`, ou None (sans select_related("photo") préalable : une requête)."""
    descriptor = type(owner).photo
    if descriptor.is_cached(owner):
        try:
            photo = owner.photo
        except ObjectDoesNotExist:
            return None
    else:
        photo = descriptor.related.related_model.objects.filter(pk=owner.pk).first()
    return photo_url(photo, request) if photo is not None else None


def photo_from_token(token):
    """(photo, secondes avant expiration), ou (None, 0) : signature invalide, URL expirée, photo
    retirée ou remplacée."""
    try:
        data = _SIGNER.unsign_object(token)
        model = _photo_models()[data["k"]]
        owner_id, version, expires = data["o"], data["v"], int(data["e"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None, 0
    remaining = expires - int(time.time())
    if remaining <= 0:
        return None, 0
    photo = model.objects.filter(pk=owner_id).first()
    if photo is None or _version(photo) != version:
        return None, 0
    return photo, remaining
