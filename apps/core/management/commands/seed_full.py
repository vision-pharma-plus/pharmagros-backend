"""
Seed a complete, self-consistent dataset covering the whole business cycle.

Everything here is created by calling the same service functions the API
calls. Nothing is inserted directly into a transactional table, because the
point of the exercise is a database whose stock ledger, VAT and receivables
actually reconcile — and those invariants live in the services, not in the
models. Writing rows straight in would produce data that looks right in a list
view and is wrong the moment a report sums it.

The one exception is master data (categories, medicines, partners), which has
no workflow to speak of: those are plain creates.

Run after `seed_rbac` and `seed_sequences`, which this command depends on for
role assignment and document numbering.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.catalog.models import (
    Category,
    DosageForm,
    Manufacturer,
    Medicine,
    StorageCondition,
    UnitOfMeasure,
)
from apps.inventory.models import StockBatch, Warehouse
from apps.invoicing import services as invoicing_services
from apps.invoicing.models import Invoice, InvoiceStatus, PaymentMethod
from apps.partners.models import (
    Customer,
    CustomerType,
    PartnerStatus,
    PaymentTerms,
    Supplier,
)
from apps.purchasing import services as purchasing_services
from apps.sales import services as sales_services
from apps.sales.models import SalesReceipt, SaleType, TenderMethod

User = get_user_model()

D = Decimal


class Command(BaseCommand):
    help = "Seed a complete demonstration dataset spanning procurement to payment."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Seed even though sales documents already exist. This ADDS a "
                "second set; it does not replace the first."
            ),
        )

    # The command is one transaction: a partially-seeded database is worse
    # than an empty one, because the totals reconcile against nothing.
    @transaction.atomic
    def handle(self, *args, **options):
        if not Role.objects.filter(code="system-administrator").exists():
            raise CommandError(
                "RBAC is not seeded. Run `manage.py seed_rbac` first."
            )

        # Master data below is created with get_or_create and so is safe to
        # re-run, but the sales and purchasing workflows are not: they post a
        # new document every time. Re-running unguarded would silently double
        # the revenue and issue stock twice, which is exactly the kind of
        # damage that is hard to unpick on a live database.
        existing = Invoice.objects.count() + SalesReceipt.objects.count()
        if existing and not options["force"]:
            raise CommandError(
                f"{existing} sales document(s) already exist. Seeding again "
                "would duplicate them. Use --force only if you genuinely want "
                "a second set, or start from an empty database."
            )

        self.now = timezone.now()
        self.today = self.now.date()

        admin = self._admin_actor()

        self.stdout.write("Seeding master data...")
        warehouse = self._warehouse()
        units = self._units()
        categories = self._categories()
        manufacturers = self._manufacturers()
        medicines = self._medicines(categories, manufacturers, units)

        self.stdout.write("Seeding partners...")
        suppliers = self._suppliers()
        customers = self._customers()

        self.stdout.write("Seeding users...")
        users = self._users()

        # Procurement first: sales cannot issue stock that was never received,
        # so the received purchase order is what makes everything downstream
        # possible. Ordering here mirrors the real sequence of events.
        self.stdout.write("Seeding purchasing...")
        received_po = self._received_order(
            suppliers[0], warehouse, medicines, users["inventory"], admin
        )
        pending_po = self._pending_order(
            suppliers[1], warehouse, medicines, users["inventory"], admin
        )

        self.stdout.write("Seeding cash sales (receipts)...")
        receipts = self._cash_sales(customers[0], warehouse, medicines, users["cashier"])

        self.stdout.write("Seeding credit sales (invoices)...")
        invoices = self._credit_sales(
            customers[1], warehouse, medicines, users["cashier"]
        )

        self.stdout.write("Recording payments...")
        payments = self._payments(customers[1], invoices, admin)

        self._report(
            warehouse=warehouse,
            medicines=medicines,
            suppliers=suppliers,
            customers=customers,
            users=users,
            received_po=received_po,
            pending_po=pending_po,
            receipts=receipts,
            invoices=invoices,
            payments=payments,
        )

    # ------------------------------------------------------------------
    # Actors
    # ------------------------------------------------------------------
    def _admin_actor(self):
        """
        Every service call is attributed to a real user because the audit trail
        rejects anonymous mutations on several models.
        """
        admin = User.objects.filter(is_superuser=True).order_by("created_at").first()
        if admin is None:
            admin = User.objects.create_user(
                email="admin@pharmagros.bi",
                password="ChangeMe!2026",
                first_name="Vision",
                last_name="Administrateur",
                is_staff=True,
                is_superuser=True,
                is_active=True,
                phone="+257 68 606 080",
                employee_code="EMP-000",
                job_title="Administrateur systeme",
                language="fr",
            )
            admin.roles.add(Role.objects.get(code="system-administrator"))
        return admin

    def _users(self):
        """Two operational users, each with a role that matches their job."""
        specs = [
            {
                "key": "cashier",
                "email": "vendeur@pharmagros.bi",
                "first_name": "Alice",
                "last_name": "Niyonzima",
                "phone": "+257 79 112 233",
                "employee_code": "EMP-001",
                "job_title": "Pharmacienne comptoir",
                "role": "pharmacist",
            },
            {
                "key": "inventory",
                "email": "magasin@pharmagros.bi",
                "first_name": "Jean-Claude",
                "last_name": "Bizimana",
                "phone": "+257 79 445 566",
                "employee_code": "EMP-002",
                "job_title": "Responsable de stock",
                "role": "inventory-officer",
            },
        ]

        users = {}
        for spec in specs:
            user, created = User.objects.get_or_create(
                email=spec["email"],
                defaults={
                    "first_name": spec["first_name"],
                    "last_name": spec["last_name"],
                    "phone": spec["phone"],
                    "employee_code": spec["employee_code"],
                    "job_title": spec["job_title"],
                    "language": "fr",
                    "is_active": True,
                    "is_staff": False,
                },
            )
            if created:
                user.set_password("Pharma!2026")
                user.save()
            user.roles.add(Role.objects.get(code=spec["role"]))
            users[spec["key"]] = user
        return users

    # ------------------------------------------------------------------
    # Master data
    # ------------------------------------------------------------------
    def _warehouse(self):
        warehouse, _created = Warehouse.objects.get_or_create(
            code="DEP-CENTRAL",
            defaults={
                "name_fr": "Depot central Bujumbura",
                "name_en": "Bujumbura central warehouse",
                "address": "12 Avenue du Commerce, Mukaza",
                "city": "Bujumbura",
                "is_default": True,
                "is_active": True,
                "is_cold_chain": True,
            },
        )
        return warehouse

    def _units(self):
        specs = [
            ("BTE", "Boite", "Box"),
            ("FL", "Flacon", "Bottle"),
            ("PLQ", "Plaquette", "Blister pack"),
            ("TUB", "Tube", "Tube"),
        ]
        units = {}
        for code, name_fr, name_en in specs:
            unit, _created = UnitOfMeasure.objects.get_or_create(
                code=code,
                defaults={"name_fr": name_fr, "name_en": name_en, "is_active": True},
            )
            units[code] = unit
        return units

    def _categories(self):
        specs = [
            ("ANALG", "Analgesiques", "Analgesics", "Antidouleurs et antipyretiques."),
            ("ATB", "Antibiotiques", "Antibiotics", "Anti-infectieux systemiques."),
            ("ANTIPAL", "Antipaludiques", "Antimalarials", "Traitement du paludisme."),
            ("CARDIO", "Cardiovasculaire", "Cardiovascular", "Hypertension et cardiologie."),
            ("VITA", "Vitamines", "Vitamins", "Supplements et carences."),
            ("DERM", "Dermatologie", "Dermatology", "Traitements cutanes."),
        ]
        categories = {}
        for code, name_fr, name_en, description in specs:
            category, _created = Category.objects.get_or_create(
                code=code,
                defaults={
                    "name_fr": name_fr,
                    "name_en": name_en,
                    "description_fr": description,
                    "description_en": description,
                    "is_active": True,
                },
            )
            categories[code] = category
        return categories

    def _manufacturers(self):
        specs = [
            ("SIPHAR", "Siphar Laboratoires", "Tunisie", "contact@siphar.tn",
             "+216 71 234 567", "https://www.siphar.tn"),
            ("CIPLA", "Cipla Ltd", "Inde", "export@cipla.com",
             "+91 22 2482 1000", "https://www.cipla.com"),
            ("DENK", "Denk Pharma", "Allemagne", "info@denkpharma.de",
             "+49 89 208 039 0", "https://www.denkpharma.de"),
        ]
        manufacturers = {}
        for code, name, country, email, phone, website in specs:
            manufacturer, _created = Manufacturer.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "country": country,
                    "contact_email": email,
                    "contact_phone": phone,
                    "website": website,
                    "is_active": True,
                },
            )
            manufacturers[code] = manufacturer
        return manufacturers

    def _medicines(self, categories, manufacturers, units):
        """
        Ten medicines with every field populated.

        Prices are VAT-inclusive, matching the field semantics: `selling_price`
        and `wholesale_price` are what the customer pays, while `unit_cost` is
        the supplier's pre-tax price. Seeding a VAT-exclusive figure into the
        selling price would overstate every margin in the reporting module by
        the VAT rate.
        """
        specs = [
            {
                "product_code": "MED-0001",
                "name_fr": "Paracetamol 500 mg", "name_en": "Paracetamol 500 mg",
                "generic_name": "Paracetamol",
                "brand_name_fr": "Doliprane", "brand_name_en": "Doliprane",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 100 comprimes",
                "category": "ANALG", "manufacturer": "SIPHAR", "unit": "BTE",
                "unit_cost": D("4500"), "selling_price": D("7080"),
                "wholesale_price": D("6490"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("50"), "safety_stock": D("20"),
                "max_stock_level": D("600"),
                "atc_code": "N02BE01", "registration_number": "BDI-2023-0451",
                "barcode": "6001234500017",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de premier recours, forte rotation.",
                "notes_en": "First-line analgesic, high turnover.",
            },
            {
                "product_code": "MED-0002",
                "name_fr": "Amoxicilline 500 mg", "name_en": "Amoxicillin 500 mg",
                "generic_name": "Amoxicilline",
                "brand_name_fr": "Clamoxyl", "brand_name_en": "Clamoxyl",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 16 gelules",
                "category": "ATB", "manufacturer": "CIPLA", "unit": "BTE",
                "unit_cost": D("7200"), "selling_price": D("11800"),
                "wholesale_price": D("10620"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("40"), "safety_stock": D("15"),
                "max_stock_level": D("400"),
                "atc_code": "J01CA04", "registration_number": "BDI-2023-0782",
                "barcode": "6001234500024",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0003",
                "name_fr": "Artemether/Lumefantrine 20/120 mg",
                "name_en": "Artemether/Lumefantrine 20/120 mg",
                "generic_name": "Artemether + Lumefantrine",
                "brand_name_fr": "Coartem", "brand_name_en": "Coartem",
                "strength_fr": "20 mg/120 mg", "strength_en": "20 mg/120 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 24 comprimes",
                "category": "ANTIPAL", "manufacturer": "CIPLA", "unit": "BTE",
                "unit_cost": D("9800"), "selling_price": D("13000"),
                "wholesale_price": D("12000"), "vat_rate": D("0"),
                # Antimalarials are on the Burundian exempt list; the flag
                # overrides the rate so a later rate edit cannot start taxing
                # an exempt line by accident.
                "is_vat_exempt": True,
                "reorder_level": D("60"), "safety_stock": D("25"),
                "max_stock_level": D("500"),
                "atc_code": "P01BF01", "registration_number": "BDI-2022-0119",
                "barcode": "6001234500031",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Proteger de la lumiere.",
                "notes_fr": "Programme national antipaludique, exonere de TVA.",
                "notes_en": "National antimalarial programme, VAT exempt.",
            },
            {
                "product_code": "MED-0004",
                "name_fr": "Amlodipine 5 mg", "name_en": "Amlodipine 5 mg",
                "generic_name": "Amlodipine",
                "brand_name_fr": "Amlor", "brand_name_en": "Amlor",
                "strength_fr": "5 mg", "strength_en": "5 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 30 comprimes",
                "category": "CARDIO", "manufacturer": "DENK", "unit": "BTE",
                "unit_cost": D("6400"), "selling_price": D("10620"),
                "wholesale_price": D("9440"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("30"), "safety_stock": D("12"),
                "max_stock_level": D("300"),
                "atc_code": "C08CA01", "registration_number": "BDI-2023-0333",
                "barcode": "6001234500048",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver dans l'emballage d'origine.",
                "notes_fr": "Traitement chronique de l'hypertension.",
                "notes_en": "Chronic hypertension treatment.",
            },
            {
                "product_code": "MED-0005",
                "name_fr": "Metformine 850 mg", "name_en": "Metformin 850 mg",
                "generic_name": "Metformine",
                "brand_name_fr": "Glucophage", "brand_name_en": "Glucophage",
                "strength_fr": "850 mg", "strength_en": "850 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 60 comprimes",
                "category": "CARDIO", "manufacturer": "DENK", "unit": "BTE",
                "unit_cost": D("5900"), "selling_price": D("9440"),
                "wholesale_price": D("8260"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("35"), "safety_stock": D("15"),
                "max_stock_level": D("350"),
                "atc_code": "A10BA02", "registration_number": "BDI-2023-0512",
                "barcode": "6001234500055",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 30 C.",
                "notes_fr": "Antidiabetique oral de premiere intention.",
                "notes_en": "First-line oral antidiabetic.",
            },
            {
                "product_code": "MED-0006",
                "name_fr": "Ceftriaxone 1 g injectable",
                "name_en": "Ceftriaxone 1 g injection",
                "generic_name": "Ceftriaxone",
                "brand_name_fr": "Rocephine", "brand_name_en": "Rocephin",
                "strength_fr": "1 g", "strength_en": "1 g",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Flacon unitaire",
                "category": "ATB", "manufacturer": "CIPLA", "unit": "FL",
                "unit_cost": D("11500"), "selling_price": D("17700"),
                "wholesale_price": D("16520"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("25"), "safety_stock": D("10"),
                "max_stock_level": D("250"),
                "atc_code": "J01DD04", "registration_number": "BDI-2022-0904",
                "barcode": "6001234500062",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Antibiotique hospitalier, chaine du froid.",
                "notes_en": "Hospital antibiotic, cold chain required.",
            },
            {
                "product_code": "MED-0007",
                "name_fr": "Vitamine C 1000 mg", "name_en": "Vitamin C 1000 mg",
                "generic_name": "Acide ascorbique",
                "brand_name_fr": "Laroscorbine", "brand_name_en": "Laroscorbine",
                "strength_fr": "1000 mg", "strength_en": "1000 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Tube de 20 comprimes",
                "category": "VITA", "manufacturer": "SIPHAR", "unit": "TUB",
                "unit_cost": D("3200"), "selling_price": D("5310"),
                "wholesale_price": D("4720"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("45"), "safety_stock": D("20"),
                "max_stock_level": D("450"),
                "atc_code": "A11GA01", "registration_number": "BDI-2024-0087",
                "barcode": "6001234500079",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Tube bien ferme, a l'abri de l'humidite.",
                "notes_fr": "Complement effervescent, vente libre.",
                "notes_en": "Effervescent supplement, over the counter.",
            },
            {
                "product_code": "MED-0008",
                "name_fr": "Ibuprofene 400 mg", "name_en": "Ibuprofen 400 mg",
                "generic_name": "Ibuprofene",
                "brand_name_fr": "Advil", "brand_name_en": "Advil",
                "strength_fr": "400 mg", "strength_en": "400 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Plaquette de 20 comprimes",
                "category": "ANALG", "manufacturer": "SIPHAR", "unit": "PLQ",
                "unit_cost": D("3800"), "selling_price": D("6490"),
                "wholesale_price": D("5900"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("50"), "safety_stock": D("20"),
                "max_stock_level": D("500"),
                "atc_code": "M01AE01", "registration_number": "BDI-2023-0298",
                "barcode": "6001234500086",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C.",
                "notes_fr": "Anti-inflammatoire non steroidien.",
                "notes_en": "Non-steroidal anti-inflammatory.",
            },
            {
                "product_code": "MED-0009",
                "name_fr": "Sirop antitussif 125 ml",
                "name_en": "Cough syrup 125 ml",
                "generic_name": "Dextromethorphane",
                "brand_name_fr": "Toplexil", "brand_name_en": "Toplexil",
                "strength_fr": "15 mg/5 ml", "strength_en": "15 mg/5 ml",
                "dosage_form": DosageForm.SYRUP, "pack_size": "Flacon de 125 ml",
                "category": "ANALG", "manufacturer": "SIPHAR", "unit": "FL",
                "unit_cost": D("5100"), "selling_price": D("8260"),
                "wholesale_price": D("7080"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("30"), "safety_stock": D("12"),
                "max_stock_level": D("300"),
                "atc_code": "R05DA09", "registration_number": "BDI-2023-0641",
                "barcode": "6001234500093",
                "requires_prescription": False, "is_controlled": True,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Flacon bien ferme, hors de portee des enfants.",
                "notes_fr": "Substance surveillee: tracabilite renforcee.",
                "notes_en": "Controlled substance: reinforced traceability.",
            },
            {
                "product_code": "MED-0010",
                "name_fr": "Pommade antifongique 30 g",
                "name_en": "Antifungal ointment 30 g",
                "generic_name": "Clotrimazole",
                "brand_name_fr": "Canesten", "brand_name_en": "Canesten",
                "strength_fr": "1 %", "strength_en": "1 %",
                "dosage_form": DosageForm.OINTMENT, "pack_size": "Tube de 30 g",
                "category": "DERM", "manufacturer": "DENK", "unit": "TUB",
                "unit_cost": D("4100"), "selling_price": D("7080"),
                "wholesale_price": D("6490"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("25"), "safety_stock": D("10"),
                "max_stock_level": D("250"),
                "atc_code": "D01AC01", "registration_number": "BDI-2024-0155",
                "barcode": "6001234500109",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C.",
                "notes_fr": "Usage cutane, vente libre.",
                "notes_en": "Topical use, over the counter.",
            },
        ]

        medicines = []
        for spec in specs:
            spec = dict(spec)
            category = categories[spec.pop("category")]
            manufacturer = manufacturers[spec.pop("manufacturer")]
            unit = units[spec.pop("unit")]
            code = spec.pop("product_code")

            medicine, _created = Medicine.objects.get_or_create(
                product_code=code,
                defaults={
                    **spec,
                    "category": category,
                    "manufacturer": manufacturer,
                    "unit_of_measure": unit,
                    "expiry_alert_days": 180,
                },
            )
            medicines.append(medicine)
        return medicines

    # ------------------------------------------------------------------
    # Partners
    # ------------------------------------------------------------------
    def _suppliers(self):
        specs = [
            {
                "supplier_code": "FRN-001",
                "name": "Pharmakina Distribution",
                "nif": "4000123456",
                "contact_person": "Emmanuel Ndikumana",
                "email": "commandes@pharmakina.bi",
                "phone": "+257 22 224 466",
                "address": "45 Boulevard de l'Uprona",
                "city": "Bujumbura",
                "country": "Burundi",
                "payment_terms": PaymentTerms.NET_30,
                "currency": "BIF",
                "lead_time_days": 14,
                "bank_name": "Banque de Credit de Bujumbura",
                "bank_account": "BI43 1010 0000 1234 5678",
                "swift_code": "BCBUBIBI",
                "is_approved": True,
                "approval_notes": "Agrement ministeriel verifie le 12/01/2026.",
                "notes": "Fournisseur principal, delai de 14 jours.",
            },
            {
                "supplier_code": "FRN-002",
                "name": "East Africa Medical Supplies",
                "nif": "P051234567X",
                "contact_person": "Grace Mukamana",
                "email": "sales@eams.co.ke",
                "phone": "+254 20 555 7788",
                "address": "Enterprise Road, Industrial Area",
                "city": "Nairobi",
                "country": "Kenya",
                "payment_terms": PaymentTerms.NET_60,
                "currency": "BIF",
                "lead_time_days": 30,
                "bank_name": "Kenya Commercial Bank",
                "bank_account": "KE12 3400 0000 9988 7766",
                "swift_code": "KCBLKENX",
                "is_approved": True,
                "approval_notes": "Dossier qualite regional valide.",
                "notes": "Importations regionales, delai de 30 jours.",
            },
        ]
        suppliers = []
        for spec in specs:
            code = spec.pop("supplier_code")
            supplier, _created = Supplier.objects.get_or_create(
                supplier_code=code,
                defaults={**spec, "status": PartnerStatus.ACTIVE},
            )
            suppliers.append(supplier)
        return suppliers

    def _customers(self):
        specs = [
            {
                "customer_code": "CLI-001",
                "business_name": "Pharmacie du Lac",
                "trading_name": "Pharmacie du Lac",
                "customer_type": CustomerType.PHARMACY,
                "nif": "4000987654",
                "rc_number": "RC/BJM/2019/4471",
                "pharmacy_licence": "LIC-PH-2019-0332",
                "licence_expiry": self.today + timedelta(days=400),
                "contact_person": "Didier Nkurunziza",
                "email": "contact@pharmacie-du-lac.bi",
                "phone": "+257 22 118 899",
                "alternate_phone": "+257 79 118 900",
                "address": "8 Avenue de la Plage",
                "city": "Bujumbura",
                "province": "Bujumbura Mairie",
                "country": "Burundi",
                "payment_terms": PaymentTerms.CASH,
                "credit_limit": D("0"),
                "discount_percent": D("0"),
                "notes": "Client comptoir, reglement immediat en especes.",
            },
            {
                "customer_code": "CLI-002",
                "business_name": "Hopital Prince Regent Charles",
                "trading_name": "HPRC",
                "customer_type": CustomerType.HOSPITAL,
                "nif": "4000555111",
                "rc_number": "RC/BJM/2005/0118",
                "pharmacy_licence": "LIC-HOP-2005-0007",
                "licence_expiry": self.today + timedelta(days=620),
                "contact_person": "Dr Chantal Irakoze",
                "email": "pharmacie@hprc.bi",
                "phone": "+257 22 223 344",
                "alternate_phone": "+257 79 223 345",
                "address": "Avenue de l'Hopital, Rohero",
                "city": "Bujumbura",
                "province": "Bujumbura Mairie",
                "country": "Burundi",
                "payment_terms": PaymentTerms.NET_30,
                # Headroom for the credit sales below: confirm_sale refuses to
                # post an invoice that would breach the limit, so this has to
                # comfortably exceed the seeded order value.
                "credit_limit": D("15000000"),
                "discount_percent": D("5"),
                "notes": "Marche institutionnel, facturation a 30 jours.",
            },
        ]
        customers = []
        for spec in specs:
            code = spec.pop("customer_code")
            customer, _created = Customer.objects.get_or_create(
                customer_code=code,
                defaults={**spec, "status": PartnerStatus.ACTIVE},
            )
            customers.append(customer)
        return customers

    # ------------------------------------------------------------------
    # Purchasing
    # ------------------------------------------------------------------
    def _received_order(self, supplier, warehouse, medicines, actor, approver):
        """
        A purchase order taken all the way to stock: draft, approved, sent,
        then received. `receive_goods` is what creates the StockBatch rows, so
        this is the only reason any of the sales below can be confirmed.

        The approver is deliberately a different user from the creator: the
        service enforces segregation of duties and refuses a self-approval.
        """
        order = purchasing_services.create_order(
            supplier=supplier,
            warehouse=warehouse,
            lines=[
                {
                    "product": medicines[0], "quantity_ordered": D("120"),
                    "unit_cost": D("4500"),
                    "expected_expiry_date": self.today + timedelta(days=540),
                    "notes": "Palette complete, rotation rapide.",
                },
                {
                    "product": medicines[1], "quantity_ordered": D("80"),
                    "unit_cost": D("7200"),
                    "expected_expiry_date": self.today + timedelta(days=585),
                    "notes": "Antibiotique sur ordonnance.",
                },
                {
                    "product": medicines[2], "quantity_ordered": D("100"),
                    "unit_cost": D("9800"),
                    "expected_expiry_date": self.today + timedelta(days=630),
                    "notes": "Programme antipaludique, exonere.",
                },
                {
                    "product": medicines[7], "quantity_ordered": D("90"),
                    "unit_cost": D("3800"),
                    "expected_expiry_date": self.today + timedelta(days=675),
                    "notes": "Anti-inflammatoire, vente libre.",
                },
            ],
            expected_delivery_date=self.today - timedelta(days=3),
            freight_cost=D("150000"),
            customs_duty=D("90000"),
            other_charges=D("25000"),
            supplier_reference="PK-2026-0455",
            notes="Reapprovisionnement trimestriel.",
            actor=actor,
        )

        purchasing_services.submit_for_approval(order, actor=actor)
        purchasing_services.approve_order(
            order, actor=approver, notes="Budget trimestriel valide."
        )
        purchasing_services.mark_sent(order, actor=actor)

        order.refresh_from_db()
        receipt_lines = []
        for index, line in enumerate(order.lines.all().order_by("line_number")):
            receipt_lines.append(
                {
                    "purchase_order_line": line,
                    "batch_number": f"LOT-2026-{index + 1:03d}",
                    "expiry_date": self.today + timedelta(days=540 + index * 45),
                    "manufacturing_date": self.today - timedelta(days=120),
                    "quantity_received": line.quantity_ordered,
                    "unit_cost": line.unit_cost,
                }
            )

        purchasing_services.receive_goods(
            order,
            lines=receipt_lines,
            delivery_note_number="BL-2026-0788",
            receipt_date=self.now - timedelta(days=2),
            quality_checked=True,
            quality_notes="Chaine du froid respectee, emballages conformes.",
            actor=actor,
        )
        order.refresh_from_db()
        return order

    def _pending_order(self, supplier, warehouse, medicines, actor, approver):
        """An approved order still awaiting delivery: no stock impact yet."""
        order = purchasing_services.create_order(
            supplier=supplier,
            warehouse=warehouse,
            lines=[
                {
                    "product": medicines[5], "quantity_ordered": D("60"),
                    "unit_cost": D("11500"),
                    "expected_expiry_date": self.today + timedelta(days=480),
                    "notes": "Chaine du froid a maintenir a la livraison.",
                },
                {
                    "product": medicines[3], "quantity_ordered": D("70"),
                    "unit_cost": D("6400"),
                    "expected_expiry_date": self.today + timedelta(days=720),
                    "notes": "Traitement chronique, stock de securite.",
                },
            ],
            expected_delivery_date=self.today + timedelta(days=21),
            freight_cost=D("200000"),
            customs_duty=D("120000"),
            other_charges=D("30000"),
            supplier_reference="EAMS-2026-1120",
            notes="Commande regionale en attente de livraison.",
            actor=actor,
        )
        purchasing_services.submit_for_approval(order, actor=actor)
        purchasing_services.approve_order(
            order, actor=approver, notes="Approuve, en attente d'expedition."
        )
        purchasing_services.mark_sent(order, actor=actor)
        order.refresh_from_db()
        return order

    # ------------------------------------------------------------------
    # Sales
    # ------------------------------------------------------------------
    def _cash_sales(self, customer, warehouse, medicines, actor):
        """
        Two cash sales. confirm_sale issues a SalesReceipt rather than an
        invoice for these, which is the documented behaviour for money taken
        at the counter — there is no receivable to raise.
        """
        receipts = []
        specs = [
            {
                "lines": [
                    {"product": medicines[0], "quantity": D("4")},
                    {"product": medicines[7], "quantity": D("2")},
                ],
                "tendered": D("50000"),
                "reference": "CAISSE-001",
            },
            {
                "lines": [
                    {"product": medicines[2], "quantity": D("3")},
                    {"product": medicines[1], "quantity": D("1")},
                ],
                "tendered": D("60000"),
                "reference": "CAISSE-002",
            },
        ]

        for spec in specs:
            sale = sales_services.create_sale(
                customer=customer,
                warehouse=warehouse,
                lines=spec["lines"],
                sale_type=SaleType.CASH,
                salesperson=actor,
                notes="Vente comptoir reglee en especes.",
                actor=actor,
            )
            sales_services.confirm_sale(
                sale,
                actor=actor,
                payment_method=TenderMethod.CASH,
                payment_reference=spec["reference"],
                amount_tendered=spec["tendered"],
            )
            sale.refresh_from_db()
            receipt = SalesReceipt.objects.filter(sale=sale).first()
            if receipt is not None:
                receipts.append(receipt)
        return receipts

    def _credit_sales(self, customer, warehouse, medicines, actor):
        """
        Two credit sales, which post invoices instead of receipts. One is left
        open so the receivables ageing has something in it; the other is paid
        in full below.
        """
        invoices = []
        # Every line here draws on a medicine the received purchase order
        # actually brought into stock. Selling something with no batch behind
        # it would fail the stock check in confirm_sale, which is the correct
        # behaviour — the fix is to order it, not to skip the line.
        specs = [
            [
                {"product": medicines[0], "quantity": D("15")},
                {"product": medicines[7], "quantity": D("10")},
            ],
            [
                {"product": medicines[1], "quantity": D("12")},
                {"product": medicines[2], "quantity": D("20")},
            ],
        ]

        for lines in specs:
            sale = sales_services.create_sale(
                customer=customer,
                warehouse=warehouse,
                lines=lines,
                sale_type=SaleType.CREDIT,
                salesperson=actor,
                notes="Marche institutionnel, facturation a 30 jours.",
                actor=actor,
            )
            sales_services.confirm_sale(sale, actor=actor)
            sale.refresh_from_db()
            invoice = Invoice.objects.filter(sale=sale).first()
            if invoice is not None:
                invoices.append(invoice)
        return invoices

    def _payments(self, customer, invoices, actor):
        """
        Settle the first invoice completely and part-pay the second, so the
        dataset carries all three receivable states: paid, partial, open.
        """
        payments = []
        if not invoices:
            return payments

        first = invoices[0]
        first.refresh_from_db()
        if first.status == InvoiceStatus.POSTED and first.balance_due > 0:
            payments.append(
                invoicing_services.record_payment(
                    customer=customer,
                    amount=first.balance_due,
                    method=PaymentMethod.BANK_TRANSFER,
                    bank_reference="VIR-2026-00418",
                    notes="Reglement integral de la facture.",
                    invoice_ids=[first.id],
                    actor=actor,
                )
            )

        if len(invoices) > 1:
            second = invoices[1]
            second.refresh_from_db()
            if second.status == InvoiceStatus.POSTED and second.balance_due > 0:
                half = (second.balance_due / 2).quantize(D("0.01"))
                payments.append(
                    invoicing_services.record_payment(
                        customer=customer,
                        amount=half,
                        method=PaymentMethod.MOBILE_MONEY,
                        bank_reference="LUMICASH-88213",
                        notes="Acompte de 50 pour cent.",
                        invoice_ids=[second.id],
                        actor=actor,
                    )
                )
        return payments

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def _report(self, **ctx):
        w = self.stdout.write
        w("")
        w(self.style.SUCCESS("Seed complete."))
        w(f"  Warehouse:      {ctx['warehouse'].code}")
        w(f"  Medicines:      {len(ctx['medicines'])}")
        w(f"  Suppliers:      {len(ctx['suppliers'])}")
        w(f"  Customers:      {len(ctx['customers'])}")
        w(f"  Users:          {len(ctx['users'])} operational + 1 administrator")
        w(f"  Purchase orders: {ctx['received_po'].order_number} (received), "
          f"{ctx['pending_po'].order_number} (awaiting delivery)")
        w(f"  Sales receipts: {len(ctx['receipts'])}")
        w(f"  Invoices:       {len(ctx['invoices'])}")
        w(f"  Payments:       {len(ctx['payments'])}")

        batches = StockBatch.objects.filter(warehouse=ctx["warehouse"])
        w(f"  Stock batches:  {batches.count()}")
        w("")
        w("Verify with: manage.py verify_seed")
