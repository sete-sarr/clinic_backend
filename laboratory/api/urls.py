from rest_framework.routers import DefaultRouter

from .views import LabOrderViewSet, LabTestViewSet

router = DefaultRouter()
router.register("tests", LabTestViewSet, basename="lab-test")
router.register("orders", LabOrderViewSet, basename="lab-order")

urlpatterns = router.urls
