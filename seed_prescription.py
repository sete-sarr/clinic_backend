import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

from clinics.models import Clinic
from consultations.models import Consultation
from prescriptions.services import create_prescription

clinic = Clinic.objects.get(name="Clinique Belle Santé")
consult = Consultation.objects.get(clinic=clinic, patient__patient_number="PAT-2026-00002")

if not hasattr(consult, "prescription"):
    presc = create_prescription(
        clinic=clinic,
        consultation=consult,
        patient=consult.patient,
        doctor=consult.doctor,
        items=[
            {
                "medication_name": "Oméprazole",
                "dosage": "20 mg",
                "frequency": "1 fois/jour",
                "duration": "7 jours",
                "quantity": 7,
                "instructions": "À prendre le matin, à jeun",
            },
            {
                "medication_name": "Paracétamol",
                "dosage": "500 mg",
                "frequency": "3 fois/jour si besoin",
                "duration": "5 jours",
                "quantity": 15,
                "instructions": "En cas de douleur",
            },
        ],
    )
    print(f"Created prescription {presc.id}")
else:
    print("Prescription already exists")
