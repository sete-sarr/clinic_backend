from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from appointments.models import Appointment
from common.permissions import in_role
from doctors.models import Doctor
from patients.models import Patient

MIN_QUERY_LENGTH = 2
RESULTS_PER_TYPE = 5


class GlobalSearchView(APIView):
    """
    Recherche inter-entités pour la barre de recherche du top-bar (design-system/components.md ne
    définit pas de spécification pour ceci — placement/comportement décidés de façon ad hoc, voir
    le shell frontend). Chaque branche est scopée par tenant et filtrée par rôle selon
    business/access-policy.md : un rôle ne recherche que les entités qu'il est autorisé à voir
    (ex. secretary n'obtient jamais de résultats consultations/dossier médical — non implémenté ici
    car aucun des deux n'est encore recherché ; accountant n'obtient jamais de résultats de
    rendez-vous).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        user = request.user
        clinic_id = getattr(user, "clinic_id", None)
        if not user.is_superuser and not clinic_id:
            return Response({"patients": [], "doctors": [], "appointments": []})

        if len(query) < MIN_QUERY_LENGTH:
            return Response({"patients": [], "doctors": [], "appointments": []})

        results = {
            "patients": self._search_patients(query, user, clinic_id) if self._can_search_patients(user) else [],
            "doctors": self._search_doctors(query, user, clinic_id) if self._can_search_doctors(user) else [],
            "appointments": (
                self._search_appointments(query, user, clinic_id) if self._can_search_appointments(user) else []
            ),
        }
        return Response(results)

    @staticmethod
    def _can_search_patients(user):
        return in_role(user, "doctor", "secretary", "accountant", "clinic_admin")

    @staticmethod
    def _can_search_doctors(user):
        return in_role(user, "doctor", "secretary", "accountant", "clinic_admin")

    @staticmethod
    def _can_search_appointments(user):
        # business/access-policy.md : CAISSIER (accountant) n'a pas d'entrée "Rendez-vous".
        return in_role(user, "doctor", "secretary", "clinic_admin")

    def _search_patients(self, query, user, clinic_id):
        qs = Patient.objects.filter(is_active=True)
        qs = qs.all() if user.is_superuser else qs.filter(clinic_id=clinic_id)
        qs = qs.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(patient_number__icontains=query)
            | Q(phone__icontains=query)
        )
        # Aucune route de détail patient n'existe encore (frontend/src/app/app.routes.ts) — les
        # patients ne sont modifiés que via une boîte de dialogue ouverte depuis la liste, donc les
        # résultats renvoient vers la liste filtrée.
        return [
            {
                "id": patient.id,
                "title": f"{patient.first_name} {patient.last_name}",
                "subtitle": patient.patient_number,
                "route": "/patients",
                "query_params": {"search": patient.patient_number},
            }
            for patient in qs[:RESULTS_PER_TYPE]
        ]

    def _search_doctors(self, query, user, clinic_id):
        qs = Doctor.objects.select_related("user").filter(is_active=True)
        qs = qs.all() if user.is_superuser else qs.filter(clinic_id=clinic_id)
        qs = qs.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(specialty__icontains=query)
            | Q(professional_number__icontains=query)
        )
        # doctor-list n'a pas de filtre de recherche texte (frontend/features/doctors/doctor-list)
        # ni de route de détail — lien vers la liste simple, le demandeur localise la ligne
        # visuellement.
        return [
            {
                "id": doctor.id,
                "title": doctor.user.get_full_name() or doctor.user.get_username(),
                "subtitle": doctor.specialty,
                "route": "/doctors",
                "query_params": {},
            }
            for doctor in qs[:RESULTS_PER_TYPE]
        ]

    def _search_appointments(self, query, user, clinic_id):
        qs = Appointment.objects.select_related("patient", "doctor__user")
        qs = qs.all() if user.is_superuser else qs.filter(clinic_id=clinic_id)
        if in_role(user, "doctor") and not in_role(user, "clinic_admin", "secretary"):
            doctor_profile = getattr(user, "doctor_profile", None)
            qs = qs.filter(doctor=doctor_profile) if doctor_profile else qs.none()
        qs = qs.filter(
            Q(patient__first_name__icontains=query)
            | Q(patient__last_name__icontains=query)
            | Q(patient__patient_number__icontains=query)
            | Q(ticket_number__icontains=query)
        )
        # appointment-list filtre par patient_number, pas par texte libre, et n'a pas de route de
        # détail — lien vers la liste filtrée (frontend/features/appointments/appointment-list).
        return [
            {
                "id": appointment.id,
                "title": f"{appointment.patient.first_name} {appointment.patient.last_name}",
                "subtitle": f"{appointment.date} — {appointment.doctor.user.get_full_name()}",
                "route": "/appointments",
                "query_params": {"patient_number": appointment.patient.patient_number},
            }
            for appointment in qs.order_by("-date", "-time")[:RESULTS_PER_TYPE]
        ]
