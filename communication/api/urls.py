from rest_framework.routers import DefaultRouter

from .views import InAppNotificationViewSet

router = DefaultRouter()
router.register("", InAppNotificationViewSet, basename="notification")

urlpatterns = router.urls
