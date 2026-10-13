"""Codes-barres des boîtes de médicaments (décision produit du 2026-10-13 : un code par médicament,
celui de la boîte).

Deux formes lues par le scanner (caméra ou douchette) :
- code linéaire EAN-13 / EAN-8 / UPC-A, ou GTIN-14 ;
- DataMatrix GS1 : GTIN (01), et souvent date de péremption (17), numéro de lot (10) et numéro de
  série (21), séparés par le caractère GS (\\x1d) ou écrits « (01)…(17)… » par certaines douchettes.

Le GTIN est ramené à une forme canonique pour que la même boîte donne toujours le même code,
quelle que soit sa lecture : UPC-A (12 chiffres) et GTIN-14 d'indicateur 0 deviennent l'EAN-13
imprimé sous les barres ; EAN-8 et GTIN-14 d'indicateur 1 à 9 (cartons) restent tels quels.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

GS = "\x1d"
# Identifiants d'application GS1 à longueur fixe (chiffres de données, sans l'identifiant).
_FIXED_LENGTH_AIS = {"01": 14, "11": 6, "15": 6, "17": 6}
# Identifiants à longueur variable, terminés par GS ou par la fin du code.
_VARIABLE_AIS = {"10": 20, "21": 20}
# Préfixes de symbologie ajoutés par certains lecteurs (« ]d2 » = DataMatrix GS1, « ]C1 » = GS1-128…).
_SYMBOLOGY_PREFIX = re.compile(r"^\][A-Za-z][0-9]")


@dataclass(frozen=True)
class ScannedCode:
    gtin: str
    batch_number: str = ""
    expiry_date: date | None = None


def _check_digit_ok(digits):
    """Clé de contrôle GS1 (modulo 10, poids 3 et 1 en partant de la droite)."""
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_gtin(value):
    """Code saisi ou scanné -> GTIN canonique. Lève ValidationError si le code est invalide."""
    digits = re.sub(r"\s", "", value or "")
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        raise ValidationError(_("Code-barres invalide : 8, 12, 13 ou 14 chiffres attendus."))
    if not _check_digit_ok(digits):
        raise ValidationError(_("Code-barres invalide : la clé de contrôle ne correspond pas."))
    if len(digits) == 12:
        return "0" + digits
    if len(digits) == 14 and digits[0] == "0":
        return digits[1:]
    return digits


def _parse_expiry(yymmdd):
    """AAMMJJ ; un jour « 00 » désigne le dernier jour du mois (règle GS1)."""
    year, month, day = 2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    if not 1 <= month <= 12:
        return None
    last_day = calendar.monthrange(year, month)[1]
    if day == 0:
        day = last_day
    return date(year, month, day) if day <= last_day else None


def _parse_gs1(text):
    """Éléments GS1 d'un DataMatrix (ou d'un GS1-128) -> {identifiant: valeur}."""
    text = text.replace("(", GS).replace(")", "")  # forme lisible « (01)…(17)… »
    elements, i = {}, 0
    while i < len(text):
        if text[i] == GS:
            i += 1
            continue
        ai = text[i:i + 2]
        if ai in _FIXED_LENGTH_AIS:
            length = _FIXED_LENGTH_AIS[ai]
            elements[ai] = text[i + 2:i + 2 + length]
            i += 2 + length
        elif ai in _VARIABLE_AIS:
            end = text.find(GS, i + 2)
            end = len(text) if end == -1 else end
            elements[ai] = text[i + 2:end][:_VARIABLE_AIS[ai]]
            i = end
        else:
            break  # identifiant non pris en charge : on garde ce qui a déjà été lu
    return elements


def parse_scanned_code(raw):
    """Texte lu par le scanner -> ScannedCode (GTIN canonique, lot et péremption s'ils sont présents)."""
    text = _SYMBOLOGY_PREFIX.sub("", (raw or "").strip())
    if text.startswith(GS):
        text = text[1:]
    compact = re.sub(r"\s", "", text)
    if compact.isdigit() and len(compact) in (8, 12, 13, 14):
        return ScannedCode(gtin=normalize_gtin(compact))
    elements = _parse_gs1(text)
    if "01" not in elements:
        raise ValidationError(_("Code-barres non reconnu : aucun code de médicament (GTIN) n'y figure."))
    expiry = elements.get("17")
    return ScannedCode(
        gtin=normalize_gtin(elements["01"]),
        batch_number=elements.get("10", "").strip(),
        expiry_date=_parse_expiry(expiry) if expiry and expiry.isdigit() else None,
    )
