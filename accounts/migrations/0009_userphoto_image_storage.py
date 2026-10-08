import common.models
from django.db import migrations, models

from common.photo_migration import copy_photo_contents_to_storage


def move_to_storage(apps, schema_editor):
    copy_photo_contents_to_storage(apps.get_model("accounts", "UserPhoto"), owner_field="user", kind="u")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_userphoto"),
    ]

    operations = [
        migrations.AddField(
            model_name="userphoto",
            name="image",
            field=models.FileField(
                default="", max_length=255, storage=common.models.photo_storage, upload_to=common.models.photo_upload_to
            ),
            preserve_default=False,
        ),
        migrations.RunPython(move_to_storage, migrations.RunPython.noop),
        migrations.RemoveField(model_name="userphoto", name="content"),
    ]
