from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer as BaseTokenObtainPairSerializer

from accounts.models import User
from accounts.services import STAFF_ROLES_ASSIGNABLE
from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    doctor_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "clinic", "roles", "doctor_id"]

    def get_roles(self, obj):
        return list(obj.groups.values_list("name", flat=True))

    def get_doctor_id(self, obj):
        # Permet au frontend de pré-remplir/verrouiller le champ médecin sur les formulaires de
        # consultation et d'ordonnance, au lieu de demander au médecin de choisir son propre nom
        # dans une liste.
        doctor_profile = getattr(obj, "doctor_profile", None)
        return doctor_profile.id if doctor_profile else None


class TokenObtainPairSerializer(BaseTokenObtainPairSerializer):
    """Intègre clinic_id et roles dans le JWT afin que le frontend n'ait jamais à les deviner."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["clinic_id"] = user.clinic_id
        token["roles"] = list(user.groups.values_list("name", flat=True))
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # business/access-policy.md "AUDIT POLICY" : l'authentification est un événement journalisé.
        record_audit(user=self.user, action=AuditLog.Action.LOGIN, obj=self.user)
        data["user"] = UserSerializer(self.user).data
        return data


class ClinicRegistrationSerializer(serializers.Serializer):
    clinic_name = serializers.CharField(max_length=255)
    clinic_email = serializers.EmailField(required=False, allow_blank=True, default="")
    clinic_phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, validators=[validate_password])

    # Pas de validate_username ici : ceci crée toujours une toute nouvelle clinique, dont l'espace
    # de noms d'utilisateur (unique par clinique, voir User.Meta.constraints) est garanti vide.

    def validate_clinic_name(self, value):
        # Unicité insensible à la casse et aux espaces superflus (Clinic.Meta.constraints) — le
        # sélecteur public de clinique sur le portail patient n'affiche que le nom
        # (ClinicPublicSerializer), un nom en doublon serait donc indiscernable pour un patient
        # essayant de choisir sa propre clinique.
        value = value.strip()
        if Clinic.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError("A clinic with that name already exists.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value


class PatientActivationRequestSerializer(serializers.Serializer):
    clinic = serializers.IntegerField()
    patient_number = serializers.CharField(max_length=30)
    phone = serializers.CharField(max_length=32)
    date_of_birth = serializers.DateField()


class StaffListSerializer(serializers.ModelSerializer):
    """Représentation en lecture pour l'écran de gestion du personnel. Contrairement à
    UserSerializer.roles (pluriel, utilisé par /me/), role est une valeur unique — un membre du
    personnel géré via cet écran possède exactement un rôle parmi STAFF_ROLES_ASSIGNABLE (ou
    "doctor", exposé en lecture seule pour la complétude de l'annuaire)."""

    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "role", "is_active", "date_joined"]

    def get_role(self, obj):
        return obj.groups.values_list("name", flat=True).first()


class StaffCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, validators=[validate_password])
    role = serializers.ChoiceField(choices=STAFF_ROLES_ASSIGNABLE)

    def validate_username(self, value):
        # Scope par clinique (User.Meta.constraints) — le même username peut déjà être pris par
        # une clinique sans rapport, ce n'est pas un problème, seule une collision au sein de
        # cette clinique compte.
        clinic = self.context["request"].user.clinic
        if User.objects.filter(username=value, clinic=clinic).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate_email(self, value):
        # email est l'identifiant de connexion global (User.USERNAME_FIELD) — unique sur toutes les cliniques.
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value


class StaffUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]


class StaffRoleChangeSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=STAFF_ROLES_ASSIGNABLE)


class PatientActivationVerifySerializer(serializers.Serializer):
    clinic = serializers.IntegerField()
    patient_number = serializers.CharField(max_length=30)
    phone = serializers.CharField(max_length=32)
    date_of_birth = serializers.DateField()
    code = serializers.CharField(max_length=6)
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_username(self, value):
        # Scope par clinique (User.Meta.constraints). self.initial_data (payload brut) est utilisé
        # plutôt qu'attrs, car les validateurs de champ s'exécutent avant que les attrs
        # inter-champs n'existent.
        clinic_id = self.initial_data.get("clinic")
        if User.objects.filter(username=value, clinic_id=clinic_id).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value
