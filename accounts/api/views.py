from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import translation
from django.utils.translation import gettext as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView as BaseTokenObtainPairView

from accounts.models import User, UserPhoto
from accounts.services import (
    STAFF_ROLES_ASSIGNABLE,
    activate_patient_account,
    create_staff_member,
    deactivate_staff_member,
    reactivate_staff_member,
    register_clinic,
    request_patient_activation,
    update_staff_role,
)
from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog
from common.permissions import IsClinicAdmin, IsSameClinic, SubscriptionActivePermission, in_role
from common.photos import PhotoUploadSerializer, owner_photo_url, remove_photo, set_photo
from common.viewsets import TenantScopedMixin

from .serializers import (
    ClinicRegistrationSerializer,
    MePreferencesSerializer,
    PatientActivationRequestSerializer,
    PatientActivationVerifySerializer,
    StaffCreateSerializer,
    StaffListSerializer,
    StaffRoleChangeSerializer,
    StaffUpdateSerializer,
    TokenObtainPairSerializer,
    UserSerializer,
)


class TokenObtainPairView(BaseTokenObtainPairView):
    """Per-IP throttled (security audit, 2026-09-02) — login was previously unrestricted, making
    password brute-forcing against a known email trivial."""

    serializer_class = TokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


# Rôles du personnel (médecin compris) autorisés à changer leur propre photo.
STAFF_PHOTO_ROLES = ("doctor", *STAFF_ROLES_ASSIGNABLE)


def user_photo_response(request, user):
    """POST : ajoute ou remplace la photo de `user` ; DELETE : la retire (common/photos.py)."""
    if request.method == "DELETE":
        remove_photo(model=UserPhoto, owner=user, actor=request.user)
    else:
        serializer = PhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_photo(model=UserPhoto, owner=user, uploaded_file=serializer.validated_data["photo"], actor=request.user)
    return Response({"photo": owner_photo_url(user, request)})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user, context={"request": request}).data)

    def patch(self, request):
        """Préférence de langue de l'utilisateur connecté (docs/i18n.md §2)."""
        serializer = MePreferencesSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user, context={"request": request}).data)


class MePhotoView(APIView):
    """Photo de profil de l'utilisateur connecté (décision produit du 2026-10-08 : chaque membre du
    personnel, médecin compris, peut changer la sienne). Un patient n'a pas de photo de compte :
    la photo d'un patient est celle de son dossier, enregistrée avec son consentement par
    l'accueil (patients/api/views.py)."""

    permission_classes = [IsAuthenticated]

    def _check_staff(self, request):
        if not request.user.clinic_id or not in_role(request.user, *STAFF_PHOTO_ROLES):
            self.permission_denied(request)

    def post(self, request):
        self._check_staff(request)
        return user_photo_response(request, request.user)

    def delete(self, request):
        self._check_staff(request)
        return user_photo_response(request, request.user)


class LogoutView(APIView):
    """Blacklists the refresh token (rest_framework_simplejwt.token_blacklist, already installed)
    so logout is a real server-side revocation, not just the frontend forgetting the token —
    and records the audit event business/access-policy.md requires ("AUDIT POLICY": Logout)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except TokenError:
                pass  # déjà invalide/expiré/blacklisté — la déconnexion se poursuit quand même
        record_audit(user=request.user, action=AuditLog.Action.LOGOUT, obj=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClinicRegistrationView(APIView):
    """Public tenant self-registration: creates the clinic, starts its trial (subscriptions.services
    .start_trial), and creates the first user as that clinic's clinic_admin. Returns the same
    access/refresh/user shape as TokenObtainPairView so the frontend can log the user in immediately
    without a second round-trip."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "clinic_registration"

    def post(self, request):
        serializer = ClinicRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            # Langue de la clinique = langue de l'interface utilisée pour s'inscrire (en-tête
            # Accept-Language, activé par LocaleMiddleware) ; modifiable ensuite dans Paramètres.
            language = (translation.get_language() or "")[:2]
            locale = language if language in Clinic.Locale.values else Clinic.Locale.FRENCH
            user = register_clinic(**serializer.validated_data, locale=locale)
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)

        token = TokenObtainPairSerializer.get_token(user)
        return Response(
            {
                "access": str(token.access_token),
                "refresh": str(token),
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


class PatientActivationRequestView(APIView):
    """Step 1 of patient portal activation: request an OTP. Always returns 200 with a generic
    message regardless of whether the identity matched — enumeration protection."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "patient_activation"

    def post(self, request):
        serializer = PatientActivationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_patient_activation(
            clinic_id=serializer.validated_data["clinic"],
            patient_number=serializer.validated_data["patient_number"],
            phone=serializer.validated_data["phone"],
            date_of_birth=serializer.validated_data["date_of_birth"],
        )
        return Response({"message": _("Si les informations correspondent, un code de vérification vous a été envoyé.")})


class PatientActivationVerifyView(APIView):
    """Step 2: verify the OTP and set up portal credentials."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "patient_activation"

    def post(self, request):
        serializer = PatientActivationVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            activate_patient_account(
                clinic_id=serializer.validated_data["clinic"],
                patient_number=serializer.validated_data["patient_number"],
                phone=serializer.validated_data["phone"],
                date_of_birth=serializer.validated_data["date_of_birth"],
                code=serializer.validated_data["code"],
                username=serializer.validated_data["username"],
                password=serializer.validated_data["password"],
            )
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)
        return Response({"message": _("Compte activé. Vous pouvez maintenant vous connecter.")})


class StaffViewSet(
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Clinic staff management (business/permissions-matrix.md UTILISATEURS): the clinic_admin
    creates/edits/deactivates secretary, accountant, and clinic_admin users, and reassigns roles
    among them. Deliberately excludes "doctor" (own profile model, own screen/endpoint under
    doctors/api/) and "patient" (own creation flow under patients + OTP activation).

    No DELETE verb is exposed: permissions-matrix.md distinguishes "Désactiver" (allowed) from
    "Supprimer" (forbidden) for this resource specifically, unlike patients/doctors where DELETE-as
    -soft-delete is the established convention. A dedicated action makes the sanctioned operation
    unambiguous — see docs/known-issues.md #3 for the ambiguity this avoids repeating.
    """

    permission_classes = [IsAuthenticated, IsSameClinic, IsClinicAdmin, SubscriptionActivePermission]
    queryset = (
        User.objects.exclude(groups__name="patient")
        .select_related("photo")
        .prefetch_related("groups")
        .order_by("last_name", "first_name", "id")
    )
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_active"]

    def get_serializer_class(self):
        return {
            "create": StaffCreateSerializer,
            "update": StaffUpdateSerializer,
            "partial_update": StaffUpdateSerializer,
        }.get(self.action, StaffListSerializer)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        role = data.pop("role")
        try:
            user = create_staff_member(clinic=request.user.clinic, role=role, user_data=data, actor=request.user)
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)
        return Response(StaffListSerializer(user, context={"request": request}).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        user = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=user)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        try:
            deactivate_staff_member(user=user, actor=request.user)
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)
        return Response(StaffListSerializer(user, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        user = self.get_object()
        reactivate_staff_member(user=user, actor=request.user)
        return Response(StaffListSerializer(user, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="role", url_name="role")
    def change_role(self, request, pk=None):
        user = self.get_object()
        serializer = StaffRoleChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            update_staff_role(user=user, new_role=serializer.validated_data["role"], actor=request.user)
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)
        return Response(StaffListSerializer(user, context={"request": request}).data)

    @action(detail=True, methods=["post", "delete"])
    def photo(self, request, pk=None):
        """Photo d'un membre du personnel ou d'un médecin, gérée par l'administrateur."""
        return user_photo_response(request, self.get_object())
