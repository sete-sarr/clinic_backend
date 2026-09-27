# Formats français de Django, sauf le séparateur décimal : les PDF (factures, reçus, relevés)
# doivent afficher les montants exactement comme l'écran, qui reprend la valeur brute de l'API
# ("118.00") — voir CLAUDE.md « réutiliser les mêmes valeurs persistées affichées à l'écran ».
from django.conf.locale.fr.formats import *  # noqa: F401,F403

DECIMAL_SEPARATOR = "."
