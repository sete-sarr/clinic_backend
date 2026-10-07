from rest_framework.routers import DefaultRouter

from .views import VisitLogViewSet

router = DefaultRouter()
router.register("", VisitLogViewSet, basename="visit")

urlpatterns = router.urls
