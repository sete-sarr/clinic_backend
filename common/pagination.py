from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """20 résultats par page par défaut ; `page_size` permet aux sélecteurs du frontend (médecins,
    services, examens…) de charger une liste complète, dans la limite de 200 lignes par requête."""

    page_size_query_param = "page_size"
    max_page_size = 200
