from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ClinicPublicListView, ClinicViewSet, UserGuideDownloadView

router = DefaultRouter()
router.register("", ClinicViewSet, basename="clinic")

urlpatterns = [
    path("public/", ClinicPublicListView.as_view(), name="clinic-public-list"),
    # Déclaré avant router.urls : sinon "user-guide" serait capturé comme pk par la route détail.
    path("user-guide/", UserGuideDownloadView.as_view(), name="clinic-user-guide"),
] + router.urls
