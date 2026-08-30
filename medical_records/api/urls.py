from rest_framework.routers import DefaultRouter

from .views import MedicalRecordViewSet

router = DefaultRouter()
router.register("", MedicalRecordViewSet, basename="medical-record")

urlpatterns = router.urls
