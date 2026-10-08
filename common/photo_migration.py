"""Migration des photos de profil de la base vers le bucket (accounts 0009, patients 0003) : les
photos enregistrées pendant la courte période où leur contenu était en base sont déposées dans le
stockage des photos, puis la colonne binaire est supprimée."""

import uuid

from django.core.files.base import ContentFile


def copy_photo_contents_to_storage(model, *, owner_field, kind):
    from common.models import photo_storage

    storage = photo_storage()
    for photo in model.objects.select_related(owner_field).exclude(content=b""):
        clinic_id = getattr(photo, owner_field).clinic_id
        name = storage.save(f"clinics/{clinic_id}/{kind}/{uuid.uuid4().hex}.jpg", ContentFile(bytes(photo.content)))
        model.objects.filter(pk=photo.pk).update(image=name)
