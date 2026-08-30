from rest_framework.routers import DefaultRouter

from .views import DoctorViewSet

router = DefaultRouter()
router.register("", DoctorViewSet, basename="doctor")

urlpatterns = router.urls
