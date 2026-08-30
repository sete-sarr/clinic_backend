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
    Cross-entity search for the top-bar search box (design-system/components.md has no spec for
    this — placement/behavior decided ad hoc, see frontend shell). Every branch is tenant-scoped
    and role-gated per business/access-policy.md: a role only searches the entities it is allowed
    to see (e.g. secretary never gets consultations/medical-record results — not implemented here
    since neither is searched at all yet; accountant never gets appointment results).
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
        # business/access-policy.md: CAISSIER (accountant) has no "Rendez-vous" entry.
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
        # No patient detail route exists yet (frontend/src/app/app.routes.ts) — patients are only
        # edited via a dialog opened from the list, so results link back to the filtered list.
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
        # doctor-list has no text-search filter (frontend/features/doctors/doctor-list) and no
        # detail route — link to the plain list, the requester locates the row visually.
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
        # appointment-list filters by patient_number, not free text, and has no detail route — link
        # back to the filtered list (frontend/features/appointments/appointment-list).
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
