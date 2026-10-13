"""Clinique de démonstration complète et cohérente : « Clinique Teranga Santé » (Dakar, F CFA).

    python manage.py seed_demo_clinic [--password MOT_DE_PASSE]

Tout passe par les services métier (numérotation, TVA, stock FEFO, machines à états, journal
d'audit) : les données se comportent exactement comme celles saisies dans l'application. Seul
l'historique (rendez-vous passés) est daté dans le passé directement, les services refusant à juste
titre un rendez-vous dans le passé.

Sécurité :
- aucun e-mail ni SMS n'est envoyé (adresses en .example, envoi désactivé le temps de la commande) ;
  les notifications dans l'application, elles, sont créées normalement ;
- tout est créé dans une seule transaction : une erreur ne laisse aucune donnée partielle ;
- la commande refuse de s'exécuter si la clinique ou l'un de ses comptes existe déjà (jamais de
  suppression : business/validation-rules.md interdit la suppression physique).
"""

import math
import random
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

CLINIC_NAME = "Clinique Teranga Santé"
DOMAIN = "teranga-sante.example"
DEFAULT_PASSWORD = "Teranga2026!"
HISTORY_DAYS = 28


def gtin13(n):
    """Code EAN-13 de « diffusion restreinte » (préfixe 2) : jamais celui d'un vrai produit."""
    body = f"20{n:010d}"
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


def email(local):
    return f"{local}@{DOMAIN}"


# --- Personnel ---------------------------------------------------------------------------------------

DEPARTMENTS = [
    ("mg", "Médecine générale", "MG"),
    ("ped", "Pédiatrie", "PED"),
    ("car", "Cardiologie", "CAR"),
    ("gyn", "Gynécologie-obstétrique", "GYN"),
]

# clé, prénom, nom, département, spécialité, n° d'ordre, téléphone, tarif de consultation
DOCTORS = [
    ("diop", "Aminata", "Diop", "mg", "Médecine générale", "CNOM-SN-1187", "77 514 22 18", 10000),
    ("fall", "Cheikh Tidiane", "Fall", "mg", "Médecine générale", "CNOM-SN-1402", "78 236 41 90", 10000),
    ("sarr", "Fatou Bintou", "Sarr", "ped", "Pédiatrie", "CNOM-SN-0968", "77 645 03 72", 10000),
    ("ba", "Mamadou Lamine", "Ba", "car", "Cardiologie", "CNOM-SN-0741", "76 310 58 44", 20000),
    ("ndoye", "Awa", "Ndoye", "gyn", "Gynécologie-obstétrique", "CNOM-SN-1253", "70 822 19 63", 15000),
]

# rôle, nom d'utilisateur, prénom, nom, fonction
STAFF = [
    ("secretary", "m.sow", "Mariama", "Sow", "Accueil et secrétariat"),
    ("accountant", "i.gueye", "Ibrahima", "Gueye", "Caisse et comptabilité"),
    ("pharmacist", "k.faye", "Khady", "Faye", "Pharmacienne"),
    ("lab_technician", "b.mbaye", "Babacar", "Mbaye", "Technicien de laboratoire"),
    ("nurse", "n.seck", "Ndèye", "Seck", "Infirmière (pédiatrie)"),
    ("nurse", "p.diallo", "Pape Malick", "Diallo", "Infirmier (médecine)"),
]

# --- Pharmacie ---------------------------------------------------------------------------------------

# clé, nom, unité, prix unité, conditionnement, unités/cond., prix cond., vente au détail, seuil min (unités)
MEDICATIONS = [
    ("para", "Paracétamol 500 mg", "comprimé", 50, "boîte", 16, 750, True, 160),
    ("amox", "Amoxicilline 500 mg", "gélule", 100, "boîte", 12, 1100, True, 120),
    ("al", "Artéméther-Luméfantrine 20/120 mg", "comprimé", 0, "boîte", 24, 2500, False, 96),
    ("amlo", "Amlodipine 5 mg", "comprimé", 100, "boîte", 30, 2900, True, 90),
    ("metf", "Metformine 850 mg", "comprimé", 80, "boîte", 30, 2200, True, 90),
    ("salb", "Salbutamol 100 µg aérosol doseur", "flacon", 3500, "", 1, 0, True, 5),
    ("sro", "Sels de réhydratation orale (SRO)", "sachet", 150, "", 1, 0, True, 50),
    ("fer", "Fer + acide folique", "comprimé", 60, "boîte", 30, 1500, True, 90),
    ("ibu", "Ibuprofène 400 mg", "comprimé", 60, "boîte", 20, 1000, True, 60),
    ("omep", "Oméprazole 20 mg", "gélule", 140, "boîte", 14, 1800, True, 42),
    ("furo", "Furosémide 40 mg", "comprimé", 70, "boîte", 20, 1200, True, 40),
    ("cef", "Ceftriaxone 1 g injectable", "flacon", 2500, "", 1, 0, True, 10),
    ("glu", "Sérum glucosé 5 % 500 ml", "poche", 1200, "", 1, 0, True, 40),
]

# clé -> [(lot, péremption en mois, quantité reçue (conditionnements), coût par conditionnement, fournisseur)]
SUPPLIERS = ("Grossiste MédiStock SARL", "Dépôt pharmaceutique du Cap-Vert")
BATCHES = {
    "para": [("PA24117", 7, 40, 520), ("PA25032", 22, 60, 520)],
    "amox": [("AX24209", 5, 30, 800), ("AX25061", 19, 40, 800)],
    "al": [("AL25014", 14, 40, 1900)],
    "amlo": [("AM24188", 9, 25, 2100), ("AM25047", 25, 30, 2100)],
    "metf": [("MF25009", 18, 40, 1600)],
    "salb": [("SB24233", 11, 25, 2700)],
    "sro": [("SR25020", 20, 400, 100)],
    "fer": [("FE25038", 16, 30, 1000)],
    "ibu": [("IB24301", 8, 30, 650)],
    "omep": [("OM25011", 21, 25, 1250)],
    "furo": [("FU25026", 17, 20, 850)],
    "cef": [("CF25008", 13, 60, 1800)],
    "glu": [("GL25019", 15, 30, 900)],  # sous le seuil (40) : alerte de stock bas
}

# --- Laboratoire -------------------------------------------------------------------------------------

LAB_TESTS = [
    ("NFS", "Numération formule sanguine (NFS)", 7500, "", None, None),
    ("TDR", "Test de diagnostic rapide du paludisme (TDR)", 3000, "", None, None),
    ("GLY", "Glycémie à jeun", 2500, "g/L", "0.70", "1.10"),
    ("HBA1C", "Hémoglobine glyquée (HbA1c)", 9000, "%", None, "6.5"),
    ("CREA", "Créatininémie", 4000, "mg/L", "6", "12"),
    ("CRP", "Protéine C réactive (CRP)", 6000, "mg/L", None, "6"),
    ("GSRH", "Groupe sanguin et rhésus", 5000, "", None, None),
    ("CHOL", "Cholestérol total", 5000, "g/L", None, "2.00"),
    ("ECBU", "Examen cytobactériologique des urines (ECBU)", 8000, "", None, None),
]


@dataclass
class Case:
    """Motif de consultation récurrent d'un patient, avec son traitement : chaque visite de
    l'historique en reprend les éléments, pour un dossier qui se lit comme un vrai suivi."""

    doctor: str
    reason: str
    complaint: str
    diagnosis: str
    plan: str
    items: list = field(default_factory=list)  # (médicament, posologie, fréquence, durée, quantité, consigne)
    labs: dict = field(default_factory=dict)  # code -> résultat
    lab_note: str = ""


CASES = {
    "hta_diab": Case(
        "diop", "Suivi HTA et diabète", "Contrôle tensionnel et glycémique, céphalées occasionnelles le matin.",
        "HTA essentielle équilibrée sous traitement ; diabète de type 2 insuffisamment contrôlé.",
        "Poursuite de l'amlodipine et de la metformine. Régime pauvre en sel et en sucres rapides, marche 30 min/jour. HbA1c dans 3 mois.",
        [("amlo", "5 mg", "1 comprimé le matin", "30 jours", 30, ""),
         ("metf", "850 mg", "1 comprimé matin et soir", "30 jours", 60, "Au milieu des repas.")],
        {"GLY": "1.38", "HBA1C": "7.4"}, "Patient à jeun depuis 12 h. Suivi diabète.",
    ),
    "grossesse": Case(
        "ndoye", "Consultation prénatale", "Grossesse de 24 SA, mouvements actifs perçus, pas de plainte.",
        "Grossesse évolutive de 24 SA, examen obstétrical normal ; anémie légère.",
        "Fer + acide folique quotidien. Échographie morphologique. Prochaine consultation prénatale dans 4 semaines.",
        [("fer", "1 comprimé", "1 fois par jour", "30 jours", 30, "À prendre à distance du thé et du café.")],
        {"GSRH": "B positif", "NFS": "Hb 10,8 g/dL ; GB 8 100/mm³ ; plaquettes 231 000/mm³"},
        "Bilan prénatal du 2e trimestre.",
    ),
    "asthme": Case(
        "sarr", "Toux nocturne et sifflements", "Toux sèche la nuit depuis une semaine, sifflements après l'effort.",
        "Asthme de l'enfant, crise légère.",
        "Salbutamol avec chambre d'inhalation en cas de gêne. Éviter la fumée et la poussière. Revoir dans 15 jours.",
        [("salb", "2 bouffées", "en cas de gêne, jusqu'à 4 fois par jour", "1 mois", 1, "Avec la chambre d'inhalation.")],
    ),
    "drepano": Case(
        "diop", "Douleurs osseuses diffuses", "Douleurs des membres et du dos depuis 24 h, fatigue.",
        "Drépanocytose SS — crise vaso-occlusive modérée.",
        "Hydratation abondante (2 à 3 L/jour), antalgiques, repos. Hospitalisation si la douleur persiste.",
        [("para", "1 g", "3 fois par jour", "5 jours", 30, "Espacer les prises d'au moins 6 heures.")],
        {"NFS": "Hb 7,9 g/dL ; GB 14 200/mm³ ; plaquettes 412 000/mm³", "CRP": "38"},
        "Drépanocytose SS connue, crise douloureuse.",
    ),
    "ic": Case(
        "ba", "Essoufflement à l'effort", "Essoufflement en montant les escaliers, chevilles gonflées le soir.",
        "Insuffisance cardiaque à fraction d'éjection préservée, décompensation légère.",
        "Furosémide le matin. Restriction en sel et en liquides, pesée quotidienne. Échographie cardiaque de contrôle.",
        [("furo", "40 mg", "1 comprimé le matin", "30 jours", 30, "Se peser chaque matin.")],
        {"CREA": "13.5"}, "Sous diurétique, contrôle de la fonction rénale.",
    ),
    "palu_enfant": Case(
        "sarr", "Fièvre et vomissements", "Fièvre depuis 48 h, deux vomissements, enfant fatigué mais réactif.",
        "Paludisme simple à P. falciparum (TDR positif).",
        "Artéméther-luméfantrine selon le poids, paracétamol si fièvre. Contrôle à J3, moustiquaire imprégnée.",
        [("al", "1 comprimé", "2 fois par jour", "3 jours", 6, "Avec un aliment gras (lait)."),
         ("para", "250 mg", "toutes les 6 heures si fièvre", "3 jours", 6, "Demi-comprimé.")],
        {"TDR": "Positif (P. falciparum)"}, "Fièvre à 39,2 °C.",
    ),
    "lombalgie": Case(
        "fall", "Douleurs lombaires", "Douleurs du bas du dos depuis 3 jours après le port d'une charge lourde.",
        "Lombalgie commune aiguë, sans signe neurologique.",
        "Ibuprofène au cours des repas pendant 5 jours, repos relatif, chaleur locale. Reprise progressive de l'activité.",
        [("ibu", "400 mg", "3 fois par jour", "5 jours", 15, "Pendant les repas.")],
    ),
    "angine": Case(
        "fall", "Mal de gorge et fièvre", "Douleur à la déglutition et fièvre à 38,8 °C depuis 2 jours.",
        "Angine érythémato-pultacée.",
        "Amoxicilline pendant 6 jours, paracétamol si fièvre. Reconsulter si gêne respiratoire.",
        [("amox", "1 g", "2 fois par jour", "6 jours", 24, "Terminer le traitement même en cas d'amélioration."),
         ("para", "1 g", "jusqu'à 3 fois par jour", "3 jours", 9, "")],
    ),
    "gastro": Case(
        "diop", "Diarrhée et douleurs abdominales", "Selles liquides depuis la veille (6 par jour), crampes abdominales.",
        "Gastro-entérite aiguë sans déshydratation.",
        "SRO après chaque selle liquide, alimentation adaptée. Reconsulter en cas de fièvre ou de sang dans les selles.",
        [("sro", "1 sachet", "après chaque selle liquide", "3 jours", 10, "Dans 1 litre d'eau potable.")],
    ),
    "hta": Case(
        "ba", "Contrôle tensionnel", "Tension à 152/95 mmHg à la pharmacie, céphalées.",
        "HTA de grade 1.",
        "Amlodipine 5 mg. Réduction du sel, activité physique. Bilan lipidique et fonction rénale.",
        [("amlo", "5 mg", "1 comprimé le matin", "30 jours", 30, "")],
        {"CHOL": "2.45", "CREA": "9.1"}, "Bilan initial d'HTA.",
    ),
    "otite": Case(
        "sarr", "Douleur de l'oreille", "Otalgie droite et fièvre depuis la veille, enfant qui pleure la nuit.",
        "Otite moyenne aiguë droite.",
        "Amoxicilline pendant 8 jours, paracétamol. Contrôle si persistance de la fièvre après 48 h.",
        [("amox", "500 mg", "3 fois par jour", "8 jours", 24, "")],
    ),
    "grossesse_terme": Case(
        "ndoye", "Consultation prénatale (3e trimestre)", "Grossesse de 37 SA, contractions irrégulières.",
        "Grossesse de 37 SA, présentation du siège.",
        "Césarienne programmée à 39 SA. Bilan préopératoire, consultation d'anesthésie.",
        [("fer", "1 comprimé", "1 fois par jour", "30 jours", 30, "")],
        {"NFS": "Hb 11,6 g/dL ; GB 9 300/mm³ ; plaquettes 254 000/mm³", "GSRH": "A positif"},
        "Bilan préopératoire, césarienne programmée.",
    ),
    "diabete": Case(
        "diop", "Suivi diabète", "Soif importante, baisse de la vision de loin depuis un mois.",
        "Diabète de type 2 déséquilibré.",
        "Augmentation de la metformine. Avis ophtalmologique (fond d'œil). Éducation diététique.",
        [("metf", "850 mg", "1 comprimé matin, midi et soir", "30 jours", 90, "Au milieu des repas.")],
        {"GLY": "1.92", "HBA1C": "9.1"}, "Diabète déséquilibré.",
    ),
    "cystite": Case(
        "fall", "Brûlures mictionnelles", "Brûlures en urinant et envies fréquentes depuis 2 jours, sans fièvre.",
        "Cystite aiguë simple.",
        "ECBU avant traitement, boire abondamment. Antibiothérapie adaptée à l'antibiogramme.",
        labs={"ECBU": "Leucocyturie significative ; E. coli > 10⁵ UFC/mL, sensible à la ciprofloxacine."},
        lab_note="Brûlures mictionnelles, pas de fièvre.",
    ),
    "gastrite": Case(
        "fall", "Brûlures d'estomac", "Brûlures épigastriques après les repas depuis 3 semaines.",
        "Gastrite, probablement favorisée par les anti-inflammatoires.",
        "Oméprazole pendant 4 semaines, arrêt de l'automédication par anti-inflammatoires.",
        [("omep", "20 mg", "1 gélule le matin à jeun", "28 jours", 28, "")],
    ),
}

# prénom, nom, naissance, genre, groupe sanguin, téléphone, CNI, cas, antécédents, allergies, compte portail
PATIENTS = [
    ("Moussa", "Diagne", date(1968, 3, 14), "male", "O+", "77 412 36 58", "1751196800123", "hta_diab",
     "HTA depuis 2015, diabète de type 2 depuis 2019.", "", False),
    ("Astou", "Mbengue", date(1992, 7, 2), "female", "B+", "78 221 45 07", "2751199200456", "grossesse",
     "G2P1, accouchement par voie basse en 2021.", "", True),
    ("Ibrahima", "Thiam", date(2019, 1, 25), "male", "A+", "77 905 11 63", "", "asthme",
     "Asthme diagnostiqué en 2023. Vaccins à jour.", "", False),
    ("Aïssatou", "Cissé", date(1985, 11, 9), "female", "O+", "76 334 82 19", "2751198500789", "drepano",
     "Drépanocytose SS, 2 à 3 crises par an. Cholécystectomie en 2018.", "", False),
    ("Modou", "Faye", date(1955, 5, 30), "male", "A-", "77 650 27 94", "1751195501012", "ic",
     "Insuffisance cardiaque, HTA ancienne, ancien fumeur.", "Aspirine (urticaire).", False),
    ("Coumba", "Ndiaye", date(2021, 9, 18), "female", "unknown", "78 448 90 35", "", "palu_enfant",
     "Née à terme, bon développement.", "", False),
    ("Serigne", "Mbaye", date(1979, 2, 3), "male", "B+", "77 117 64 28", "1751197901345", "lombalgie",
     "Chauffeur de taxi. Pas d'antécédent notable.", "", False),
    ("Rokhaya", "Gaye", date(1999, 4, 21), "female", "O-", "70 563 18 47", "2751199901678", "angine",
     "Angines à répétition dans l'enfance.", "", False),
    ("Alioune Badara", "Sy", date(1988, 8, 12), "male", "AB+", "77 802 33 51", "1751198801901", "gastro",
     "Aucun antécédent.", "", False),
    ("Mame Diarra", "Kébé", date(1972, 12, 6), "female", "A+", "76 249 70 12", "2751197202234", "hta",
     "Ménopausée. Mère hypertendue.", "", False),
    ("Lamine", "Coly", date(2016, 6, 27), "male", "O+", "77 381 52 66", "", "otite",
     "Otites à répétition.", "Pénicilline ? (éruption en 2019, à confirmer).", False),
    ("Binta", "Barry", date(1995, 10, 15), "female", "A+", "78 670 14 29", "2751199502567", "grossesse_terme",
     "G1P0.", "", False),
    ("El Hadji", "Seck", date(1960, 1, 19), "male", "B-", "77 236 98 03", "1751196002890", "diabete",
     "Diabète de type 2 depuis 2010, rétinopathie à surveiller.", "", False),
    ("Fatoumata", "Baldé", date(2015, 3, 8), "female", "O+", "76 902 45 81", "", "palu_enfant",
     "Paludisme simple en 2024.", "", False),
    ("Oumar", "Sall", date(1983, 9, 29), "male", "O+", "77 559 21 74", "1751198303123", None,
     "Fumeur (10 paquets-années), père décédé d'un infarctus.", "", False),
    ("Ndèye Fatou", "Wade", date(1990, 2, 11), "female", "B+", "78 115 39 60", "2751199003456", "cystite",
     "Cystites récidivantes.", "", False),
    ("Babacar", "Niang", date(1975, 7, 4), "male", "unknown", "77 640 88 15", "1751197503789", None,
     "Suivi dans une autre structure.", "", False),
    ("Yacine", "Touré", date(2000, 5, 17), "female", "A+", "77 708 26 49", "2751200004012", "gastrite",
     "Étudiante. Automédication fréquente par ibuprofène.", "", True),
]

FUTURE_REASONS = {
    "hta_diab": "Contrôle trimestriel HTA et diabète",
    "grossesse": "Consultation prénatale (28 SA)",
    "asthme": "Contrôle de l'asthme",
    "ic": "Contrôle cardiologique et échographie",
    "hta": "Résultats du bilan et contrôle tensionnel",
    "diabete": "Contrôle de la glycémie",
    "drepano": "Suivi de la drépanocytose",
    "gastrite": "Contrôle après traitement",
}


class Command(BaseCommand):
    help = "Crée la clinique de démonstration « Clinique Teranga Santé » (Dakar) avec des données cohérentes."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Mot de passe commun à tous les comptes.")

    def handle(self, *args, password, **options):
        from accounts.models import User
        from clinics.models import Clinic

        all_emails = [email("direction")] + [email(f"dr.{d[0]}") for d in DOCTORS] + [email(s[1]) for s in STAFF]
        if Clinic.objects.filter(name=CLINIC_NAME).exists() or User.objects.filter(email__in=all_emails).exists():
            raise CommandError(f"« {CLINIC_NAME} » ou l'un de ses comptes existe déjà : rien n'a été créé.")

        self.rng = random.Random(2026)
        self.today = timezone.localdate()
        self.password = password
        with _outgoing_messages_muted(), transaction.atomic():
            self._seed()
        self._report()

    # --- Construction ---------------------------------------------------------------------------------

    def _seed(self):
        self._clinic_and_staff()
        self._patients()
        self._pharmacy()
        self._laboratory_catalog()
        self._history()
        self._today()
        self._future()
        self._hospitalization()
        self._visitors()
        self._emergency_access()

    def _clinic_and_staff(self):
        from accounts.services import create_staff_member, register_clinic
        from departments.models import Department
        from doctors.services import create_doctor

        self.admin = register_clinic(
            clinic_name=CLINIC_NAME, clinic_email=email("contact"), clinic_phone="33 860 45 12",
            username="o.ndiaye", password=self.password, email=email("direction"),
            first_name="Ousmane", last_name="Ndiaye",
        )
        clinic = self.clinic = self.admin.clinic
        clinic.address = "Sacré-Cœur 3, VDN, Dakar"
        clinic.currency = "XOF"
        clinic.inpatient_billing_mode = clinic.InpatientBillingMode.PER_ROOM_TYPE
        clinic.is_demo = True  # jamais suspendue à la fin de l'essai
        clinic.save(update_fields=["address", "currency", "inpatient_billing_mode", "is_demo"])

        self.departments = {
            key: Department.objects.create(clinic=clinic, name=name, code=code,
                                           department_type=Department.DepartmentType.MEDICAL)
            for key, name, code in DEPARTMENTS
        }
        self.doctors, self.fees = {}, {}
        for key, first, last, dept, specialty, number, phone, fee in DOCTORS:
            self.doctors[key] = create_doctor(
                clinic=clinic, professional_number=number, specialty=specialty, department=self.departments[dept],
                phone=phone, user_data={"username": f"dr.{key}", "email": email(f"dr.{key}"),
                                        "first_name": first, "last_name": last, "password": self.password},
            )
            self.fees[key] = fee
        self.staff = {}
        for role, username, first, last, _title in STAFF:
            self.staff[username] = create_staff_member(
                clinic=clinic, role=role, actor=self.admin,
                user_data={"username": username, "email": email(username), "first_name": first,
                           "last_name": last, "password": self.password},
            )
        self.secretary = self.staff["m.sow"]
        self.cashier = self.staff["i.gueye"]
        self.pharmacist = self.staff["k.faye"]
        self.lab_tech = self.staff["b.mbaye"]
        self.nurses = [self.staff["n.seck"], self.staff["p.diallo"]]

    def _patients(self):
        from accounts.models import User
        from patients.services import create_patient

        self.patients, self.case_of, self.portal_accounts = {}, {}, []
        patient_group, _ = Group.objects.get_or_create(name="patient")
        for first, last, born, gender, blood, phone, cni, case, history, allergies, portal in PATIENTS:
            local = f"{first.split()[0]}.{last}".lower().replace("ï", "i").replace("é", "e").replace("è", "e")
            patient = create_patient(
                clinic=self.clinic, first_name=first, last_name=last, date_of_birth=born, gender=gender,
                blood_type=blood, phone=phone, national_id=cni, email=email(local) if portal else "",
            )
            record = patient.medical_record
            record.medical_history = history
            record.allergies = allergies
            record.save(update_fields=["medical_history", "allergies", "updated_at"])
            if portal:
                user = User.objects.create_user(
                    username=local, email=patient.email, password=self.password, first_name=first,
                    last_name=last, clinic=self.clinic,
                )
                user.groups.add(patient_group)
                patient.user = user
                patient.save(update_fields=["user"])
                self.portal_accounts.append((f"{first} {last}", patient.email))
            self.patients[last] = patient
            self.case_of[last] = case

    def _pharmacy(self):
        from pharmacy.services import create_medication, receive_stock_batch

        self.medications = {}
        for n, (key, name, unit, unit_price, pack, per_pack, pack_price, retail, minimum) in enumerate(MEDICATIONS, 1):
            self.medications[key] = create_medication(
                clinic=self.clinic, actor=self.pharmacist, name=name, barcode=gtin13(n), unit=unit,
                unit_price=Decimal(unit_price), pack_unit=pack, units_per_pack=per_pack,
                pack_price=Decimal(pack_price), allow_unit_sale=retail, min_threshold=minimum,
            )
        for key, batches in BATCHES.items():
            medication = self.medications[key]
            for i, (lot, months, quantity, cost) in enumerate(batches):
                receive_stock_batch(
                    medication=medication, actor=self.pharmacist, batch_number=lot,
                    expiry_date=self.today + timedelta(days=30 * months),
                    received_date=self.today - timedelta(days=60 - 25 * i), quantity=quantity,
                    received_in="pack" if medication.is_packaged else "unit", unit_cost=Decimal(cost),
                    supplier=SUPPLIERS[i % 2],
                )

    def _laboratory_catalog(self):
        from laboratory.services import create_lab_test

        self.lab_tests = {
            code: create_lab_test(
                clinic=self.clinic, actor=self.admin, code=code, name=name, price=Decimal(price), unit=unit,
                reference_min=Decimal(low) if low else None, reference_max=Decimal(high) if high else None,
            )
            for code, name, price, unit, low, high in LAB_TESTS
        }

    # --- Historique : 4 semaines de consultations -------------------------------------------------------

    def _history(self):
        from appointments.models import Appointment

        followed = [name for name, case in self.case_of.items() if case]
        self.labs_done = set()
        for days_ago in range(HISTORY_DAYS, 0, -1):
            day = self.today - timedelta(days=days_ago)
            if day.weekday() == 6:  # dimanche : clinique fermée
                continue
            slots = {}
            for name in self.rng.sample(followed, 3 if day.weekday() == 5 else 4):
                case = CASES[self.case_of[name]]
                doctor = self.doctors[case.doctor]
                slot = slots.get(case.doctor, 0)
                slots[case.doctor] = slot + 1
                roll = self.rng.random()
                status = (Appointment.Status.NO_SHOW if roll < 0.08 else
                          Appointment.Status.CANCELLED if roll < 0.14 else Appointment.Status.COMPLETED)
                appointment = Appointment.objects.create(
                    clinic=self.clinic, patient=self.patients[name], doctor=doctor, department=doctor.department,
                    date=day, time=time(8 + slot, 30), status=status, reason=case.reason,
                )
                if status == Appointment.Status.COMPLETED:
                    self._visit(appointment, case, days_ago)

    def _visit(self, appointment, case, days_ago):
        from consultations.models import Consultation
        from consultations.services import create_consultation
        from prescriptions.models import Prescription
        from prescriptions.services import create_prescription

        patient, doctor, day = appointment.patient, appointment.doctor, appointment.date
        consultation = create_consultation(
            clinic=self.clinic, patient=patient, doctor=doctor, appointment=appointment,
            date=timezone.make_aware(datetime.combine(day, appointment.time)) + timedelta(minutes=10),
            is_follow_up=Consultation.objects.filter(patient=patient).exists(),
            chief_complaint=case.complaint, diagnosis=case.diagnosis, treatment_plan=case.plan,
            status=Consultation.Status.VALIDATED,
        )
        if case.items:
            create_prescription(
                clinic=self.clinic, consultation=consultation, patient=patient, doctor=doctor,
                status=Prescription.Status.VALIDATED, notes="",
                items=[{"medication_name": self.medications[key].name, "dosage": dosage, "frequency": frequency,
                        "duration": duration, "quantity": quantity, "instructions": instructions}
                       for key, dosage, frequency, duration, quantity, instructions in case.items],
            )
        fee_label = ("Consultation de médecine générale" if doctor.specialty == "Médecine générale"
                     else f"Consultation spécialisée — {doctor.specialty}")
        self._invoice(patient, doctor, day, [{"description": fee_label, "quantity": 1,
                                              "unit_price": Decimal(self.fees[self._key(doctor)])}],
                      paid=days_ago > 3 or self.rng.random() < 0.5)
        # Ordonnance délivrée à la pharmacie de la clinique (une fois sur deux).
        if case.items and self.rng.random() < 0.55:
            self._invoice(patient, doctor, day, self._medication_lines(case), paid=True, actor=self.pharmacist)
        if case.labs and patient.pk not in self.labs_done:
            self.labs_done.add(patient.pk)
            self._lab_order(patient, doctor, consultation, case, validated=True)

    def _key(self, doctor):
        return next(key for key, value in self.doctors.items() if value.pk == doctor.pk)

    def _medication_lines(self, case):
        lines = []
        for key, *_rest, quantity, _instructions in case.items:
            medication = self.medications[key]
            if medication.is_packaged:
                packs = max(1, math.ceil(quantity / medication.units_per_pack))
                lines.append({"description": f"{medication.name} — {medication.pack_unit}", "quantity": packs,
                              "unit_price": medication.pack_price, "medication": medication, "sale_unit": "pack"})
            else:
                lines.append({"description": f"{medication.name} ({medication.unit})", "quantity": quantity,
                              "unit_price": medication.unit_price, "medication": medication, "sale_unit": "unit"})
        return lines

    def _invoice(self, patient, doctor, day, lines, *, paid, actor=None, partial=False):
        from billing.services import create_invoice, issue_invoice
        from payments.services import create_payment

        actor = actor or self.cashier
        invoice = create_invoice(clinic=self.clinic, patient=patient, doctor=doctor, lines=lines, issue_date=day)
        issue_invoice(invoice=invoice, actor=actor)
        if paid or partial:
            amount = invoice.total_amount if paid else (invoice.total_amount / 2).quantize(Decimal("1"))
            method = self.rng.choices(["cash", "mobile_money", "card"], weights=[5, 4, 1])[0]
            create_payment(clinic=self.clinic, invoice=invoice, amount=amount, created_by=actor, method=method, date=day)
        return invoice

    def _lab_order(self, patient, doctor, consultation, case, *, validated):
        from laboratory import services

        order = services.create_lab_order(
            clinic=self.clinic, doctor=doctor, patient=patient, consultation=consultation, actor=doctor.user,
            tests=[self.lab_tests[code] for code in case.labs], clinical_note=case.lab_note,
        )
        services.collect_sample(order=order, actor=self.lab_tech)
        if not validated:
            return order
        services.start_processing(order=order, actor=self.lab_tech)
        items = {item.test_code: item.id for item in order.items.all()}
        services.record_results(order=order, actor=self.lab_tech, entries=[
            {"item": items[code], "value": value, "comment": ""} for code, value in case.labs.items()
        ])
        services.complete_order(order=order, actor=self.lab_tech)
        services.validate_order(order=order, actor=doctor.user)
        order.refresh_from_db()
        from billing.services import issue_invoice
        from payments.services import create_payment

        issue_invoice(invoice=order.invoice, actor=self.cashier)
        create_payment(clinic=self.clinic, invoice=order.invoice, amount=order.invoice.total_amount,
                       created_by=self.cashier, method="mobile_money", date=self.today)
        return order

    # --- Aujourd'hui ----------------------------------------------------------------------------------

    def _today(self):
        from appointments.models import Appointment
        from appointments.services import check_in_appointment
        from consultations.models import Consultation
        from consultations.services import create_consultation

        plan = [  # patient, cas, heure, arrivé ?
            ("Diagne", "hta_diab", time(8, 30), True),
            ("Thiam", "asthme", time(9, 0), True),
            ("Gaye", "angine", time(9, 30), True),
            ("Mbengue", "grossesse", time(10, 0), False),
            ("Kébé", "hta", time(11, 0), False),
            ("Mbaye", "lombalgie", time(15, 0), False),
        ]
        for name, case_key, at, arrived in plan:
            case = CASES[case_key]
            doctor = self.doctors[case.doctor]
            appointment = Appointment.objects.create(
                clinic=self.clinic, patient=self.patients[name], doctor=doctor, department=doctor.department,
                date=self.today, time=at, status=Appointment.Status.CONFIRMED, reason=case.reason,
            )
            if arrived:
                check_in_appointment(appointment=appointment)
        # Consultation en cours (brouillon) pour le premier patient arrivé.
        first = Appointment.objects.get(clinic=self.clinic, date=self.today, patient=self.patients["Diagne"])
        create_consultation(
            clinic=self.clinic, patient=first.patient, doctor=first.doctor, appointment=first,
            date=timezone.now(), is_follow_up=True, status=Consultation.Status.DRAFT,
            chief_complaint="Contrôle tensionnel : 138/86 mmHg. Glycémie capillaire à 1,45 g/L ce matin.",
        )
        # Demande d'analyses du jour : prélevée, en attente de résultats au laboratoire.
        self._lab_order(self.patients["Wade"], self.doctors["fall"], None, CASES["cystite"], validated=False)
        # Facture brouillon en cours de saisie à l'accueil.
        from billing.services import create_invoice

        create_invoice(clinic=self.clinic, patient=self.patients["Gaye"], doctor=self.doctors["fall"],
                       lines=[{"description": "Consultation de médecine générale", "quantity": 1,
                               "unit_price": Decimal("10000")}])

    # --- À venir --------------------------------------------------------------------------------------

    def _future(self):
        from appointments.models import Appointment
        from appointments.services import create_appointment

        day_offset = 1
        for name, reason in [("Diagne", None), ("Mbengue", None), ("Thiam", None), ("Faye", None),
                             ("Kébé", None), ("Seck", None), ("Cissé", None), ("Touré", None)]:
            case_key = self.case_of[name]
            doctor = self.doctors[CASES[case_key].doctor]
            day = self.today + timedelta(days=day_offset)
            if day.weekday() == 6:
                day += timedelta(days=1)
            appointment = create_appointment(
                clinic=self.clinic, patient=self.patients[name], doctor=doctor, date=day,
                time=time(9 + day_offset % 6, 0), reason=reason or FUTURE_REASONS[case_key],
            )
            if day_offset % 2:
                Appointment.objects.filter(pk=appointment.pk).update(status=Appointment.Status.CONFIRMED)
            day_offset += 1
        # Rendez-vous de département : le médecin sera celui de garde (rotation).
        for name, dept, offset, at, reason in [
            ("Sall", "car", 2, time(10, 30), "Douleur thoracique à l'effort — avis cardiologique"),
            ("Mbaye", "mg", 1, time(16, 0), "Fièvre et courbatures depuis 3 jours"),
            ("Sy", "mg", 3, time(15, 30), "Certificat médical d'aptitude au sport"),
        ]:
            day = self.today + timedelta(days=offset)
            if day.weekday() == 6:
                day += timedelta(days=1)
            create_appointment(clinic=self.clinic, patient=self.patients[name], department=self.departments[dept],
                               date=day, time=at, reason=reason)

    # --- Hospitalisation ------------------------------------------------------------------------------

    def _hospitalization(self):
        from hospitalization import services
        from hospitalization.models import Admission, Bed, BedTransfer, Room, RoomType

        single = RoomType.objects.create(clinic=self.clinic, name="Chambre individuelle", nightly_rate=Decimal("35000"))
        double = RoomType.objects.create(clinic=self.clinic, name="Chambre double", nightly_rate=Decimal("20000"))
        beds = {}
        for dept, number, room_type, labels in [
            ("mg", "101", single, ["A"]), ("mg", "102", double, ["A", "B"]), ("mg", "103", double, ["A", "B"]),
            ("ped", "201", single, ["A"]), ("ped", "202", double, ["A", "B"]),
            ("gyn", "301", single, ["A"]), ("gyn", "302", double, ["A", "B"]),
        ]:
            room = Room.objects.create(clinic=self.clinic, department=self.departments[dept], number=number,
                                       room_type=room_type)
            for label in labels:
                beds[f"{number}-{label}"] = Bed.objects.create(clinic=self.clinic, room=room, label=label)

        def stay(name, doctor, dept, bed, reason, days_ago):
            doctor = self.doctors[doctor]
            admission = services.create_admission(
                clinic=self.clinic, doctor=doctor, patient=self.patients[name], department=self.departments[dept],
                reason=reason, actor=doctor.user, bed=beds[bed],
            )
            when = timezone.now() - timedelta(days=days_ago, hours=4)
            Admission.objects.filter(pk=admission.pk).update(admitted_at=when)
            BedTransfer.objects.filter(admission=admission).update(transferred_at=when)
            admission.refresh_from_db()
            return admission

        def vitals(admission, nurse, series):
            for hours_ago, temperature, pulse, systolic, diastolic, spo2 in series:
                services.record_vital_signs(
                    admission=admission, actor=nurse, recorded_at=timezone.now() - timedelta(hours=hours_ago),
                    temperature=Decimal(temperature), pulse=pulse, systolic=systolic, diastolic=diastolic,
                    oxygen_saturation=spo2,
                )

        child = stay("Baldé", "sarr", "ped", "201-A",
                     "Paludisme grave de l'enfant : fièvre à 40 °C, prostration, TDR positif. Artésunate IV.", 2)
        vitals(child, self.nurses[0], [(44, "40.1", 138, 95, 60, 95), (30, "39.2", 126, 98, 62, 97),
                                       (18, "38.1", 112, 100, 64, 98), (6, "37.4", 102, 102, 66, 98)])
        services.add_nursing_note(admission=child, actor=self.nurses[0],
                                  note="Apyrétique depuis ce matin, s'alimente à nouveau. Mère présente.")
        crisis = stay("Cissé", "diop", "mg", "102-A",
                      "Crise vaso-occlusive drépanocytaire hyperalgique : hydratation IV et antalgiques.", 1)
        vitals(crisis, self.nurses[1], [(20, "37.9", 104, 118, 74, 96), (8, "37.3", 92, 120, 76, 98)])
        services.add_nursing_note(admission=crisis, actor=self.nurses[1],
                                  note="Douleur cotée à 5/10 contre 9/10 à l'entrée. Boit bien, perfusion en place.")
        heart = stay("Faye", "ba", "mg", "101-A",
                     "Décompensation cardiaque : dyspnée de repos, œdèmes des membres inférieurs.", 5)
        vitals(heart, self.nurses[1], [(110, "36.9", 96, 162, 94, 91), (60, "36.8", 84, 144, 88, 95),
                                       (12, "36.7", 76, 132, 82, 97)])
        services.add_nursing_note(admission=heart, actor=self.nurses[1],
                                  note="Perte de 3 kg depuis l'entrée, œdèmes en nette régression.")
        services.discharge(admission=heart, actor=self.doctors["ba"].user,
                           summary="Évolution favorable sous diurétique. Sortie avec furosémide 40 mg/j, "
                                   "contrôle en cardiologie dans 15 jours, régime pauvre en sel.")
        heart.refresh_from_db()
        from billing.services import issue_invoice
        from payments.services import create_payment

        issue_invoice(invoice=heart.invoice, actor=self.cashier)
        create_payment(clinic=self.clinic, invoice=heart.invoice, created_by=self.cashier, method="cash",
                       amount=Decimal("50000"), date=self.today)  # acompte : solde à régler
        services.create_admission(
            clinic=self.clinic, doctor=self.doctors["ndoye"], patient=self.patients["Barry"],
            department=self.departments["gyn"], actor=self.doctors["ndoye"].user,
            reason="Césarienne programmée à 39 SA (présentation du siège).",
            planned_for=self.today + timedelta(days=5),
        )
        services.set_bed_out_of_service(bed=beds["103-B"], actor=self.admin)

    # --- Visiteurs ------------------------------------------------------------------------------------

    def _visitors(self):
        from hospitalization.models import Admission
        from visitors import services
        from visitors.models import VisitLog

        child = Admission.objects.get(clinic=self.clinic, patient=self.patients["Baldé"])
        crisis = Admission.objects.get(clinic=self.clinic, patient=self.patients["Cissé"])
        services.check_in(clinic=self.clinic, actor=self.secretary, visitor_name="Mariama Baldé",
                          visitor_phone="76 902 45 81", visitor_type="patient_visit", purpose="Mère de la patiente",
                          visited_admission=child)
        services.check_in(clinic=self.clinic, actor=self.secretary, visitor_name="Moustapha Cissé",
                          visitor_phone="77 290 13 56", visitor_type="patient_visit", purpose="Frère de la patiente",
                          visited_admission=crisis)
        services.check_in(clinic=self.clinic, actor=self.secretary, visitor_name="Grossiste MédiStock SARL",
                          visitor_type="supplier", purpose="Livraison de médicaments", visited_free_text="Pharmacie")
        maintenance = services.check_in(clinic=self.clinic, actor=self.secretary, visitor_name="Technicien climatisation (prestataire)",
                                        visitor_type="contractor", purpose="Maintenance de la climatisation",
                                        visited_free_text="Bloc administratif")
        services.check_out(visit=maintenance, actor=self.secretary)
        for days_ago, name, visitor_type, purpose in [
            (1, "Seynabou Faye", "patient_visit", "Épouse du patient (chambre 101)"),
            (2, "Abdou Karim Ndiaye", "companion", "Accompagne sa fille en pédiatrie"),
            (3, "Électricien (prestataire)", "contractor", "Contrôle du tableau électrique"),
        ]:
            arrived = timezone.now() - timedelta(days=days_ago, hours=5)
            VisitLog.objects.create(clinic=self.clinic, visitor_name=name, visitor_type=visitor_type, purpose=purpose,
                                    checked_in_at=arrived, checked_in_by=self.secretary,
                                    checked_out_at=arrived + timedelta(minutes=50), checked_out_by=self.secretary)

    # --- Accès d'urgence ------------------------------------------------------------------------------

    def _emergency_access(self):
        from patients.access import grant_emergency_access

        grant_emergency_access(
            user=self.doctors["fall"].user, patient_number=self.patients["Niang"].patient_number,
            reason="Patient amené inconscient aux urgences par les sapeurs-pompiers : besoin de ses antécédents "
                   "et allergies avant traitement.",
        )

    # --- Compte rendu ---------------------------------------------------------------------------------

    def _report(self):
        out = self.stdout.write
        out(self.style.SUCCESS(f"\n{CLINIC_NAME} créée (Dakar, F CFA). Mot de passe de tous les comptes : {self.password}\n"))
        out("Administrateur de la clinique (compte d'inscription)")
        out(f"  Ousmane Ndiaye ............ {email('direction')}")
        out("Médecins")
        for key, first, last, dept, specialty, *_ in DOCTORS:
            out(f"  Dr {first} {last} ({specialty}) ... {email(f'dr.{key}')}")
        out("Personnel")
        for _role, username, first, last, title in STAFF:
            out(f"  {first} {last} — {title} ... {email(username)}")
        out("Patients avec espace patient")
        for name, address in self.portal_accounts:
            out(f"  {name} ... {address}")


@contextmanager
def _outgoing_messages_muted():
    """Aucun e-mail ni SMS pendant la génération : l'envoi est remplacé par un no-op dans chaque
    module qui l'a importé, puis rétabli (les notifications dans l'application restent créées)."""
    import appointments.notifications
    import communication.services
    import laboratory.notifications
    import pharmacy.notifications

    modules = (communication.services, appointments.notifications, laboratory.notifications,
               pharmacy.notifications)
    originals = [module.send_notification for module in modules]
    for module in modules:
        module.send_notification = lambda **_kwargs: None
    try:
        yield
    finally:
        for module, original in zip(modules, originals):
            module.send_notification = original
