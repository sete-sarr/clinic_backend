from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from common.translation_catalog import build_mo, read_po


class Command(BaseCommand):
    """Compile chaque locale/<langue>/LC_MESSAGES/django.po en .mo, sans GNU gettext
    (common/translation_catalog.py). À lancer après toute modification d'un .po."""

    help = "Compile les catalogues de traduction .po du projet en .mo (sans GNU gettext)."

    def handle(self, *args, **options):
        for locale_dir in settings.LOCALE_PATHS:
            for po_path in sorted(Path(locale_dir).glob("*/LC_MESSAGES/django.po")):
                entries = read_po(po_path)
                po_path.with_suffix(".mo").write_bytes(build_mo(entries))
                translated = sum(1 for msgid, msgstr in entries.items() if msgid and msgstr)
                self.stdout.write(f"{po_path.parent.parent.name}: {translated} message(s) compilé(s)")
