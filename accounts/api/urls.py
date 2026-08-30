from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ClinicRegistrationView,
    MeView,
    PatientActivationRequestView,
    PatientActivationVerifyView,
    StaffViewSet,
)

router = DefaultRouter()
router.register("staff", StaffViewSet, basename="staff")

urlpatterns = [
    path("me/", MeView.as_view(), name="me"),
    path("clinics/register/", ClinicRegistrationView.as_view(), name="clinic-registration"),
    path("patient/activation/request/", PatientActivationRequestView.as_view(), name="patient-activation-request"),
    path("patient/activation/verify/", PatientActivationVerifyView.as_view(), name="patient-activation-verify"),
] + router.urls
