"""Devises de facturation des patients (docs/i18n.md §8) — choisies par la clinique (Paramètres),
mémorisées sur chaque facture à sa création. Aucune conversion : un montant est toujours affiché
dans la devise de son enregistrement.

Table reproduite À L'IDENTIQUE dans frontend/src/app/core/utils/money.ts (même rendu à l'écran et
dans les PDF) — toute modification se reporte des deux côtés, tests compris.

L'abonnement à la plateforme n'est pas concerné : il reste tarifé en FCFA (subscriptions/catalog.py).
"""

from decimal import ROUND_HALF_UP, Decimal

from django.utils import translation

DEFAULT_CURRENCY = "XOF"

# code ISO 4217 -> (symbole, décimales, position en anglais). En français, le symbole suit toujours
# le montant (« 1 250,50 € ») ; en anglais : "prefix" (« €1,250.50 »), "prefix_space"
# (« CHF 1,250.50 ») ou "suffix" (« 10,000 F CFA »).
CURRENCIES = {
    "XOF": ("F CFA", 0, "suffix"),
    "XAF": ("FCFA", 0, "suffix"),
    "GNF": ("FG", 0, "suffix"),
    "CDF": ("FC", 2, "suffix"),
    "MAD": ("DH", 2, "suffix"),
    "DZD": ("DA", 2, "suffix"),
    "NGN": ("₦", 2, "prefix"),
    "GHS": ("GH₵", 2, "prefix"),
    "KES": ("KSh", 2, "prefix_space"),
    "EUR": ("€", 2, "prefix"),
    "USD": ("$", 2, "prefix"),
    "GBP": ("£", 2, "prefix"),
    "CAD": ("CA$", 2, "prefix"),
    "CHF": ("CHF", 2, "prefix_space"),
}
CURRENCY_CHOICES = [(code, code) for code in CURRENCIES]

_NARROW_NBSP = " "  # séparateur de milliers français
_NBSP = " "  # entre le montant et le symbole


def format_money(amount, currency, language=None) -> str:
    """« 10 000 F CFA » / « 10,000 F CFA », « 1 250,50 € » / « €1,250.50 ».

    Une devise sans centimes (FCFA…) s'affiche sans décimales, sauf si le montant en comporte
    (TVA calculée, par exemple 180,90) : on ne masque jamais une partie d'un montant enregistré."""
    if amount is None or amount == "":
        return ""
    symbol, decimals, english_position = CURRENCIES.get(currency, (currency, 2, "suffix"))
    value = Decimal(str(amount))
    if decimals == 0 and value != value.to_integral_value():
        decimals = 2
    value = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    number = f"{value:,.{decimals}f}"

    if (language or translation.get_language() or "fr").startswith("en"):
        if english_position == "prefix":
            return f"{symbol}{number}"
        if english_position == "prefix_space":
            return f"{symbol}{_NBSP}{number}"
        return f"{number}{_NBSP}{symbol}"
    number = number.replace(",", _NARROW_NBSP).replace(".", ",")
    return f"{number}{_NBSP}{symbol}"
