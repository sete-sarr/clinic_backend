"""One-off demo data seed for screenshot purposes. Uses the project's own service layer
(register_clinic, create_doctor, create_appointment, create_invoice, create_consultation) so every
record respects real business rules (numbering, VAT calc, status machine) rather than being built
by hand. Run via: manage.py shell < seed_demo.py"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

# No local Redis broker and no real email/SMS provider configured in this dev environment — the
# app's own notification dispatch (business/communication-policy.md: async, centralized) is real
# and out of scope for this screenshot-data seed script. Patch every module's already-imported
# reference to a no-op just for this run, rather than wiring a fake broker/provider that would
# only paper over the same gap.
import appointments.notifications

appointments.notifications.send_notification = lambda **kwargs: None

from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.utils import timezone

from accounts.models import User
from accounts.services import register_clinic, create_staff_member
from appointments.models import Appointment
from appointments.services import create_appointment
from billing.models import Invoice
from billing.services import create_invoice, issue_invoice
from clinics.models import Clinic
from consultations.models import Consultation
from consultations.services import create_consultation
from departments.models import Department
from doctors.models import Doctor
from doctors.services import create_doctor
from medical_records.models import MedicalRecord
from patients.models import Patient
from payments.models import Payment
from payments.services import create_payment

CLINIC_NAME = "Clinique Belle Santé"

# Business models use on_delete=PROTECT everywhere (no physical deletion, by design). Unwind any
# previous partial run of this exact script, child-tables-first, so unique constraints
# (username, patient_number, invoice number...) don't collide with this fresh run.
for stale_clinic in Clinic.objects.filter(name=CLINIC_NAME):
    Payment.objects.filter(clinic=stale_clinic).delete()
    Invoice.objects.filter(clinic=stale_clinic).delete()
    Consultation.objects.filter(clinic=stale_clinic).delete()
    Appointment.objects.filter(clinic=stale_clinic).delete()
    MedicalRecord.objects.filter(clinic=stale_clinic).delete()
    Patient.objects.filter(clinic=stale_clinic).delete()
    Doctor.objects.filter(clinic=stale_clinic).delete()
    Department.objects.filter(clinic=stale_clinic).delete()
    User.objects.filter(clinic=stale_clinic).delete()
    stale_clinic.delete()

print("Creating clinic + admin...")
admin_user = register_clinic(
    clinic_name=CLINIC_NAME,
    clinic_email="contact@bellesante.example",
    clinic_phone="0522334455",
    username="admin.demo",
    password="Demo1234!",
    email="admin@bellesante.example",
    first_name="Nadia",
    last_name="El Amrani",
)
clinic = admin_user.clinic
print(f"  clinic id={clinic.id}, admin={admin_user.username}")

print("Creating departments...")
dept_general = Department.objects.create(
    clinic=clinic, name="Médecine générale", code="GEN", department_type=Department.DepartmentType.MEDICAL
)
dept_pedia = Department.objects.create(
    clinic=clinic, name="Pédiatrie", code="PED", department_type=Department.DepartmentType.MEDICAL
)
dept_cardio = Department.objects.create(
    clinic=clinic, name="Cardiologie", code="CAR", department_type=Department.DepartmentType.MEDICAL
)

print("Creating doctors...")
doctor1 = create_doctor(
    clinic=clinic,
    professional_number="MED-2026-001",
    specialty="Médecine générale",
    department=dept_general,
    phone="0611223344",
    user_data={
        "username": "dr.benali",
        "email": "s.benali@bellesante.example",
        "first_name": "Sofia",
        "last_name": "Benali",
        "password": "Demo1234!",
    },
)
doctor2 = create_doctor(
    clinic=clinic,
    professional_number="MED-2026-002",
    specialty="Pédiatrie",
    department=dept_pedia,
    phone="0611223355",
    user_data={
        "username": "dr.tahiri",
        "email": "y.tahiri@bellesante.example",
        "first_name": "Youssef",
        "last_name": "Tahiri",
        "password": "Demo1234!",
    },
)
doctor3 = create_doctor(
    clinic=clinic,
    professional_number="MED-2026-003",
    specialty="Cardiologie",
    department=dept_cardio,
    phone="0611223366",
    user_data={
        "username": "dr.mansouri",
        "email": "l.mansouri@bellesante.example",
        "first_name": "Leïla",
        "last_name": "Mansouri",
        "password": "Demo1234!",
    },
)
print(f"  {doctor1}, {doctor2}, {doctor3}")

print("Creating staff...")
secretary = create_staff_member(
    clinic=clinic,
    role="secretary",
    user_data={
        "username": "secretariat",
        "email": "accueil@bellesante.example",
        "first_name": "Amina",
        "last_name": "Rachidi",
        "password": "Demo1234!",
    },
    actor=admin_user,
)
accountant = create_staff_member(
    clinic=clinic,
    role="accountant",
    user_data={
        "username": "comptabilite",
        "email": "compta@bellesante.example",
        "first_name": "Karim",
        "last_name": "Ouazzani",
        "password": "Demo1234!",
    },
    actor=admin_user,
)
print(f"  {secretary}, {accountant}")

print("Creating patients...")
patients_data = [
    ("Fatima", "Zahra", date(1985, 3, 12), Patient.Gender.FEMALE, "0661112233"),
    ("Ahmed", "Bensaid", date(1990, 7, 22), Patient.Gender.MALE, "0661112244"),
    ("Salma", "Idrissi", date(2015, 1, 5), Patient.Gender.FEMALE, "0661112255"),
    ("Omar", "Fassi", date(1978, 11, 30), Patient.Gender.MALE, "0661112266"),
    ("Nour", "Chraibi", date(1995, 5, 18), Patient.Gender.FEMALE, "0661112277"),
    ("Youssef", "Kadiri", date(1962, 9, 9), Patient.Gender.MALE, "0661112288"),
]
patients = []
for i, (first, last, dob, gender, phone) in enumerate(patients_data, start=1):
    p = Patient.objects.create(
        clinic=clinic,
        patient_number=f"PAT-2026-{i:05d}",
        first_name=first,
        last_name=last,
        date_of_birth=dob,
        gender=gender,
        phone=phone,
        email=f"{first.lower()}.{last.lower()}@example.com",
        blood_type=Patient.BloodType.O_POS,
    )
    patients.append(p)
    MedicalRecord.objects.get_or_create(
        patient=p, clinic=clinic, defaults={"allergies": "Aucune connue", "medical_history": "RAS"}
    )
print(f"  {len(patients)} patients created")

print("Creating appointments (today + this week)...")
today = timezone.localdate()
appt_slots = [
    (patients[0], doctor1, today + timedelta(days=1), time(9, 0), "Consultation de suivi"),
    (patients[1], doctor1, today + timedelta(days=1), time(9, 30), "Douleur abdominale"),
    (patients[2], doctor2, today + timedelta(days=1), time(10, 0), "Contrôle vaccinal"),
    (patients[3], doctor3, today + timedelta(days=1), time(11, 0), "Bilan cardiaque"),
    (patients[4], doctor1, today + timedelta(days=2), time(9, 0), "Première consultation"),
    (patients[5], doctor3, today + timedelta(days=2), time(14, 0), "Suivi hypertension"),
    (patients[0], doctor1, today + timedelta(days=3), time(9, 0), "Renouvellement ordonnance"),
]
appointments = []
for patient, doctor, d, t, reason in appt_slots:
    appt = create_appointment(clinic=clinic, doctor=doctor, patient=patient, date=d, time=t, reason=reason)
    appointments.append(appt)
print(f"  {len(appointments)} appointments created")

print("Creating a couple of completed consultations...")
consult1 = create_consultation(
    clinic=clinic,
    patient=patients[1],
    doctor=doctor1,
    date=timezone.now() - timedelta(days=3),
    chief_complaint="Douleur abdominale depuis 2 jours",
    diagnosis="Gastrite légère",
    treatment_plan="Repos, alimentation légère, traitement antiacide 7 jours",
    status=Consultation.Status.COMPLETED,
)
consult2 = create_consultation(
    clinic=clinic,
    patient=patients[3],
    doctor=doctor3,
    date=timezone.now() - timedelta(days=5),
    chief_complaint="Bilan cardiaque de routine",
    diagnosis="Tension artérielle légèrement élevée",
    treatment_plan="Suivi tensionnel mensuel, réduction du sel, contrôle dans 1 mois",
    status=Consultation.Status.COMPLETED,
)
print(f"  {consult1}, {consult2}")

print("Creating invoices (draft, issued, paid)...")
inv1 = create_invoice(
    clinic=clinic,
    patient=patients[1],
    doctor=doctor1,
    issue_date=today - timedelta(days=3),
    lines=[{"description": "Consultation générale", "quantity": 1, "unit_price": Decimal("300.00")}],
)
issue_invoice(invoice=inv1)
create_payment(
    clinic=clinic, invoice=inv1, amount=inv1.total_amount, method="cash",
    date=today - timedelta(days=3), created_by=accountant,
)

inv2 = create_invoice(
    clinic=clinic,
    patient=patients[3],
    doctor=doctor3,
    issue_date=today - timedelta(days=5),
    lines=[
        {"description": "Consultation cardiologie", "quantity": 1, "unit_price": Decimal("450.00")},
        {"description": "Électrocardiogramme", "quantity": 1, "unit_price": Decimal("200.00")},
    ],
)
issue_invoice(invoice=inv2)

inv3 = create_invoice(
    clinic=clinic,
    patient=patients[4],
    issue_date=today,
    lines=[{"description": "Consultation pédiatrie", "quantity": 1, "unit_price": Decimal("250.00")}],
)
# left as draft

print(f"  {inv1.number} (paid), {inv2.number} (issued), draft invoice for {patients[4]}")

print("\nDone. Demo login credentials (password: Demo1234! for all):")
print(f"  Administrateur : admin.demo")
print(f"  Médecin        : dr.benali")
print(f"  Secrétaire     : secretariat")
print(f"  Comptable      : comptabilite")
