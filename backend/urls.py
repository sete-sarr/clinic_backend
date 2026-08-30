"""
URL configuration for backend project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.api.views import LogoutView, TokenObtainPairView
from common.api.search import GlobalSearchView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/auth/logout/", LogoutView.as_view(), name="logout"),
    path("api/v1/search/", GlobalSearchView.as_view(), name="global-search"),
    path("api/v1/audit-log/", include("common.api.urls")),
    path("api/v1/accounts/", include("accounts.api.urls")),
    path("api/v1/clinics/", include("clinics.api.urls")),
    path("api/v1/departments/", include("departments.api.urls")),
    path("api/v1/doctors/", include("doctors.api.urls")),
    path("api/v1/patients/", include("patients.api.urls")),
    path("api/v1/appointments/", include("appointments.api.urls")),
    path("api/v1/consultations/", include("consultations.api.urls")),
    path("api/v1/medical-records/", include("medical_records.api.urls")),
    path("api/v1/prescriptions/", include("prescriptions.api.urls")),
    path("api/v1/billing/", include("billing.api.urls")),
    path("api/v1/payments/", include("payments.api.urls")),
    path("api/v1/reports/", include("reports.api.urls")),
    path("api/v1/subscriptions/", include("subscriptions.api.urls")),
]

# Dev-only media serving (uploaded clinic logos/favicons). Production media serving is an infra
# concern (nginx/S3/CDN) outside this app's scope.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
