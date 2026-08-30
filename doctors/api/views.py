from django_filters.rest_framework import DjangoFilterBackend

from common.viewsets import TenantScopedModelViewSet
from doctors.models import Doctor
from doctors.permissions import CanManageDoctors

from .serializers import DoctorSerializer


class DoctorViewSet(TenantScopedModelViewSet):
    serializer_class = DoctorSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageDoctors]
    queryset = Doctor.objects.select_related("user", "clinic", "department").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["department", "is_active"]
