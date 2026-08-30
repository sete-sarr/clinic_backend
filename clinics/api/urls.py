from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ClinicPublicListView, ClinicViewSet

router = DefaultRouter()
router.register("", ClinicViewSet, basename="clinic")

urlpatterns = [
    path("public/", ClinicPublicListView.as_view(), name="clinic-public-list"),
] + router.urls
