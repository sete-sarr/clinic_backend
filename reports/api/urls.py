from django.urls import path

from .views import ClinicActivityReportView

urlpatterns = [
    path("activity/", ClinicActivityReportView.as_view(), name="clinic-activity-report"),
]
