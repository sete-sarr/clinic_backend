"""Catalogue de traductions du backend (docs/i18n.md §4), sans dépendre des outils GNU gettext
(absents des postes Windows et du build Render) :

- extract_msgids() liste les messages traduisibles du code (appels gettext / _ / gettext_lazy) ;
- read_po() lit un fichier .po ; build_mo() produit le .mo binaire que Django charge.

Les messages source (msgid) sont en français, langue par défaut (LANGUAGE_CODE) : seul le
catalogue anglais existe, locale/en/LC_MESSAGES/django.po. Après toute modification du .po :
`python manage.py compile_translations` (le .mo compilé est commité)."""

import ast
import struct
from pathlib import Path

GETTEXT_FUNCTIONS = {"_", "gettext", "gettext_lazy"}
_SKIP_DIRS = {"venv", "tests", "migrations", "__pycache__", "locale", "staticfiles", "media"}


def extract_msgids(root: Path) -> dict[str, list[str]]:
    """msgid -> emplacements ("app/fichier.py:ligne") des appels de traduction à argument littéral."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if set(rel.parts) & _SKIP_DIRS or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in GETTEXT_FUNCTIONS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.setdefault(node.args[0].value, []).append(f"{rel.as_posix()}:{node.lineno}")
    return found


def _unquote(token: str) -> str:
    body = token.strip()
    if not (body.startswith('"') and body.endswith('"')):
        raise ValueError(f"Chaîne .po invalide : {token!r}")
    body = body[1:-1]
    out, i = [], 0
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            out.append(escapes.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def read_po(path: Path) -> dict[str, str]:
    """msgid -> msgstr (en-tête "" compris). Format .po simple : msgid/msgstr, lignes de suite,
    commentaires ; pas de pluriels ni de contextes (non utilisés dans ce projet)."""
    entries: dict[str, str] = {}
    current: dict[str, list[str]] = {}
    field = None

    def flush():
        if "msgid" in current and "msgstr" in current:
            entries["".join(current["msgid"])] = "".join(current["msgstr"])

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if line == "" and current:
                flush()
                current, field = {}, None
            continue
        if line.startswith("msgid "):
            if "msgstr" in current:
                flush()
                current = {}
            field = "msgid"
            current[field] = [_unquote(line[len("msgid "):])]
        elif line.startswith("msgstr "):
            field = "msgstr"
            current[field] = [_unquote(line[len("msgstr "):])]
        elif line.startswith('"') and field:
            current[field].append(_unquote(line))
        else:
            raise ValueError(f"{path}: ligne non reconnue : {raw!r}")
    flush()
    return entries


def build_mo(entries: dict[str, str]) -> bytes:
    """Fichier .mo GNU (little-endian, sans table de hachage) — format lu par gettext/Django.
    Les entrées sans traduction sont omises (gettext renvoie alors le msgid)."""
    items = sorted((k, v) for k, v in entries.items() if v or k == "")
    ids = [k.encode("utf-8") for k, _ in items]
    strs = [v.encode("utf-8") for _, v in items]
    count = len(items)
    header_size = 7 * 4
    ids_table = header_size
    strs_table = ids_table + count * 8
    data_start = strs_table + count * 8

    offsets, data = [], b""
    for blob in ids + strs:
        offsets.append((len(blob), data_start + len(data)))
        data += blob + b"\x00"
    id_offsets, str_offsets = offsets[:count], offsets[count:]

    output = struct.pack("<7I", 0x950412DE, 0, count, ids_table, strs_table, 0, 0)
    for length, offset in id_offsets + str_offsets:
        output += struct.pack("<2I", length, offset)
    return output + data
