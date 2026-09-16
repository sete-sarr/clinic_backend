from rest_framework.routers import DefaultRouter

from .views import MedicationViewSet, StockBatchViewSet, StockMovementViewSet

router = DefaultRouter()
router.register("medications", MedicationViewSet, basename="medication")
router.register("batches", StockBatchViewSet, basename="stock-batch")
router.register("movements", StockMovementViewSet, basename="stock-movement")

urlpatterns = router.urls
