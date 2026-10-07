from rest_framework.routers import DefaultRouter

from .views import AdmissionViewSet, BedViewSet, RoomTypeViewSet, RoomViewSet

router = DefaultRouter()
router.register("room-types", RoomTypeViewSet, basename="room-type")
router.register("rooms", RoomViewSet, basename="room")
router.register("beds", BedViewSet, basename="bed")
router.register("admissions", AdmissionViewSet, basename="admission")

urlpatterns = router.urls
