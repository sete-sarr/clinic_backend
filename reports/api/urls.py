from django.urls import path

from .views import ClinicActivityReportView, DashboardStatsView

urlpatterns = [
    path("activity/", ClinicActivityReportView.as_view(), name="clinic-activity-report"),
    path("dashboard/", DashboardStatsView.as_view(), name="dashboard-stats"),
]
