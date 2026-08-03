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
        self._slow_moving_order(
            suppliers[0], warehouse, medicines, users["inventory"], admin
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
            ("AMP", "Ampoule", "Ampoule"),
            ("PCE", "Piece", "Piece"),
            ("SCH", "Sachet", "Sachet"),
            ("PAI", "Paire", "Pair"),
            ("ROL", "Rouleau", "Roll"),
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
            ("ANTIPAR", "Antiparasitaires", "Antiparasitics", "Vermifuges et antiscabieux."),
            ("ANTIFONG", "Antifongiques", "Antifungals", "Mycoses cutanees et systemiques."),
            ("ANTIHIST", "Antihistaminiques", "Antihistamines", "Allergies et antitussifs."),
            ("GASTRO", "Gastro-enterologie", "Gastroenterology", "Antiacides, antispasmodiques, rehydratation."),
            ("RESP", "Respiratoire", "Respiratory", "Bronchodilatateurs et antiasthmatiques."),
            ("CORTICO", "Corticoides", "Corticosteroids", "Anti-inflammatoires steroidiens."),
            ("SNC", "Systeme nerveux central", "Central nervous system", "Psychotropes et neurologie."),
            ("UROGEN", "Uro-genital", "Urogenital", "Sphere urologique et gynecologique."),
            ("OPHT", "Ophtalmologie", "Ophthalmology", "Collyres et pommades oculaires."),
            ("SOLINJ", "Solutes et injectables", "Solutions and injectables", "Perfusions et injections."),
            ("CONSOM", "Consommables medicaux", "Medical consumables", "Dispositifs et consommables a usage unique."),
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
        # One manufacturer per supply line, so the catalogue's origin data
        # matches the supplier each product is actually bought from.
        specs = [
            ("BIODEAL", "Biodeal Laboratories Ltd", "Kenya", "info@biodeal.co.ke",
             "+254 20 209 1234", "https://www.biodeal.co.ke"),
            ("LABALLIED", "Laboratory & Allied Ltd", "Kenya", "export@laballied.com",
             "+254 20 650 3300", "https://www.laballied.com"),
            ("HARLEYS", "Harley's Limited", "Kenya", "info@harleysltd.com",
             "+254 20 445 5000", "https://www.harleysltd.com"),
            ("INFINITE", "Infinite Health Ltd", "Kenya", "sales@infinitehealth.co.ke",
             "+254 20 380 7711", "https://www.infinitehealth.co.ke"),
            ("DAWA", "Dawa Limited", "Kenya", "export@dawalimited.com",
             "+254 20 802 1000", "https://www.dawalimited.com"),
            ("UNIVERSAL", "Universal Corporation Ltd", "Kenya", "info@universalcorporation.com",
             "+254 20 202 0800", "https://www.universalcorporation.com"),
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
        The full trading catalogue: 85 products with every field populated.

        Prices are VAT-inclusive, matching the field semantics: `selling_price`
        and `wholesale_price` are what the customer pays, while `unit_cost` is
        the supplier's pre-tax price. Seeding a VAT-exclusive figure into the
        selling price would overstate every margin in the reporting module by
        the VAT rate.

        Costs originate from a supplier price list quoted in USD and are
        carried here already converted at 2 950 BIF/USD, because the ledger is
        single-currency: storing the dollar figure would leave every stock
        valuation and margin report a factor of ~3 000 out. Retail is set at a
        52 percent gross margin on cost with VAT applied on top, wholesale ten
        percent under retail — the spread a wholesaler actually works on.

        VAT exemption follows the Burundian essential-medicines list:
        antimalarials, anthelmintics, oral rehydration salts, basic vitamins
        and single-use consumables are exempt, and the flag is set alongside a
        zero rate so a later rate edit cannot start taxing an exempt line.
        """
        specs = [
            {
                "product_code": "MED-0001",
                "name_fr": "WOMBIT SUSP 200MG/5ML", "name_en": "WOMBIT SUSP 200MG/5ML",
                "generic_name": "Albendazole",
                "brand_name_fr": "WOMBIT SUSP 200MG/5ML", "brand_name_en": "WOMBIT SUSP 200MG/5ML",
                "strength_fr": "200 mg/5 ml", "strength_en": "200 mg/5 ml",
                "dosage_form": DosageForm.SUSPENSION, "pack_size": "Flacon de 10 ml",
                "category": "ANTIPAR", "manufacturer": "BIODEAL", "unit": "FL",
                "unit_cost": D("350"), "selling_price": D("530"),
                "wholesale_price": D("480"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("175"), "safety_stock": D("70"),
                "max_stock_level": D("1050"),
                "atc_code": "P02CA03", "registration_number": "BDI-2022-1000",
                "barcode": "6001234500001",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Vermifuge, programme de dewormage scolaire.",
                "notes_en": "Anthelmintic, school deworming programme.",
            },
            {
                "product_code": "MED-0002",
                "name_fr": "CANAZOLE 100MG", "name_en": "CANAZOLE 100MG",
                "generic_name": "Clotrimazole",
                "brand_name_fr": "CANAZOLE 100MG", "brand_name_en": "CANAZOLE 100MG",
                "strength_fr": "100 mg", "strength_en": "100 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 6 comprimes",
                "category": "ANTIFONG", "manufacturer": "BIODEAL", "unit": "BTE",
                "unit_cost": D("590"), "selling_price": D("1060"),
                "wholesale_price": D("950"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("42"), "safety_stock": D("17"),
                "max_stock_level": D("252"),
                "atc_code": "G01AF02", "registration_number": "BDI-2023-1007",
                "barcode": "6001234500002",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antifongique, forte demande saisonniere.",
                "notes_en": "Antifungal, strong seasonal demand.",
            },
            {
                "product_code": "MED-0003",
                "name_fr": "CLOMZOL E CREAM 1 %", "name_en": "CLOMZOL E CREAM 1 %",
                "generic_name": "Clotrimazole",
                "brand_name_fr": "CLOMZOL E CREAM", "brand_name_en": "CLOMZOL E CREAM",
                "strength_fr": "1 %", "strength_en": "1 %",
                "dosage_form": DosageForm.CREAM, "pack_size": "Tube de 20 g",
                "category": "ANTIFONG", "manufacturer": "BIODEAL", "unit": "TUB",
                "unit_cost": D("590"), "selling_price": D("1060"),
                "wholesale_price": D("950"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("49"), "safety_stock": D("20"),
                "max_stock_level": D("294"),
                "atc_code": "D01AC01", "registration_number": "BDI-2024-1014",
                "barcode": "6001234500003",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antifongique, forte demande saisonniere.",
                "notes_en": "Antifungal, strong seasonal demand.",
            },
            {
                "product_code": "MED-0004",
                "name_fr": "IBUFLAM SUSPENSION 100 mg/5 ml", "name_en": "IBUFLAM SUSPENSION 100 mg/5 ml",
                "generic_name": "Ibuprofene",
                "brand_name_fr": "IBUFLAM SUSPENSION", "brand_name_en": "IBUFLAM SUSPENSION",
                "strength_fr": "100 mg/5 ml", "strength_en": "100 mg/5 ml",
                "dosage_form": DosageForm.SUSPENSION, "pack_size": "Flacon de 100 ml",
                "category": "ANALG", "manufacturer": "BIODEAL", "unit": "FL",
                "unit_cost": D("590"), "selling_price": D("1060"),
                "wholesale_price": D("950"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("42"), "safety_stock": D("17"),
                "max_stock_level": D("252"),
                "atc_code": "M01AE01", "registration_number": "BDI-2025-1021",
                "barcode": "6001234500004",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0005",
                "name_fr": "IBUFLAM 400MG", "name_en": "IBUFLAM 400MG",
                "generic_name": "Ibuprofene",
                "brand_name_fr": "IBUFLAM 400MG", "brand_name_en": "IBUFLAM 400MG",
                "strength_fr": "400 mg", "strength_en": "400 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "ANALG", "manufacturer": "BIODEAL", "unit": "BTE",
                "unit_cost": D("2660"), "selling_price": D("4770"),
                "wholesale_price": D("4290"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("70"), "safety_stock": D("28"),
                "max_stock_level": D("420"),
                "atc_code": "M01AE01", "registration_number": "BDI-2022-1028",
                "barcode": "6001234500005",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0006",
                "name_fr": "HISTARGAN 25MG", "name_en": "HISTARGAN 25MG",
                "generic_name": "Promethazine",
                "brand_name_fr": "HISTARGAN 25MG", "brand_name_en": "HISTARGAN 25MG",
                "strength_fr": "25 mg", "strength_en": "25 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "ANTIHIST", "manufacturer": "BIODEAL", "unit": "BTE",
                "unit_cost": D("1770"), "selling_price": D("3170"),
                "wholesale_price": D("2850"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "D04AA10", "registration_number": "BDI-2023-1035",
                "barcode": "6001234500006",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.PROTECT_LIGHT,
                "storage_notes": "Conserver a l'abri de la lumiere, emballage d'origine.",
                "notes_fr": "Antihistaminique, vente de comptoir.",
                "notes_en": "Antihistamine, counter sales.",
            },
            {
                "product_code": "MED-0007",
                "name_fr": "VITAMIN B COMPLEX Complexe B", "name_en": "VITAMIN B COMPLEX Complexe B",
                "generic_name": "Vitamines B1, B2, B6",
                "brand_name_fr": "VITAMIN B COMPLEX", "brand_name_en": "VITAMIN B COMPLEX",
                "strength_fr": "Complexe B", "strength_en": "Complexe B",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "VITA", "manufacturer": "BIODEAL", "unit": "BTE",
                "unit_cost": D("1770"), "selling_price": D("2690"),
                "wholesale_price": D("2420"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("33"), "safety_stock": D("13"),
                "max_stock_level": D("198"),
                "atc_code": "A11EA00", "registration_number": "BDI-2024-1042",
                "barcode": "6001234500007",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Complement nutritionnel, exonere de TVA.",
                "notes_en": "Nutritional supplement, VAT exempt.",
            },
            {
                "product_code": "MED-0008",
                "name_fr": "CIPRODEAL 500MG", "name_en": "CIPRODEAL 500MG",
                "generic_name": "Ciprofloxacine",
                "brand_name_fr": "CIPRODEAL 500MG", "brand_name_en": "CIPRODEAL 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "ATB", "manufacturer": "BIODEAL", "unit": "BTE",
                "unit_cost": D("6490"), "selling_price": D("11640"),
                "wholesale_price": D("10480"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "J01MA02", "registration_number": "BDI-2025-1049",
                "barcode": "6001234500008",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0009",
                "name_fr": "LEVAWORM 40MG", "name_en": "LEVAWORM 40MG",
                "generic_name": "Levamisole",
                "brand_name_fr": "LEVAWORM 40MG", "brand_name_en": "LEVAWORM 40MG",
                "strength_fr": "40 mg", "strength_en": "40 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 25 x 3 comprimes",
                "category": "ANTIPAR", "manufacturer": "BIODEAL", "unit": "BTE",
                "unit_cost": D("2950"), "selling_price": D("4480"),
                "wholesale_price": D("4030"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "P02CE01", "registration_number": "BDI-2022-1056",
                "barcode": "6001234500009",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Vermifuge, programme de dewormage scolaire.",
                "notes_en": "Anthelmintic, school deworming programme.",
            },
            {
                "product_code": "MED-0010",
                "name_fr": "BIOSCAB LOTION 25%", "name_en": "BIOSCAB LOTION 25%",
                "generic_name": "Benzoate de benzyle",
                "brand_name_fr": "BIOSCAB LOTION 25%", "brand_name_en": "BIOSCAB LOTION 25%",
                "strength_fr": "25 %", "strength_en": "25 %",
                "dosage_form": DosageForm.SOLUTION, "pack_size": "Flacon de 100 ml",
                "category": "DERM", "manufacturer": "BIODEAL", "unit": "FL",
                "unit_cost": D("1330"), "selling_price": D("2390"),
                "wholesale_price": D("2150"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "P03AX01", "registration_number": "BDI-2023-1063",
                "barcode": "6001234500010",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.PROTECT_LIGHT,
                "storage_notes": "Conserver a l'abri de la lumiere, emballage d'origine.",
                "notes_fr": "Usage cutane externe.",
                "notes_en": "External topical use.",
            },
            {
                "product_code": "MED-0011",
                "name_fr": "MINOLINE 100MG", "name_en": "MINOLINE 100MG",
                "generic_name": "Aminophylline",
                "brand_name_fr": "MINOLINE 100MG", "brand_name_en": "MINOLINE 100MG",
                "strength_fr": "100 mg", "strength_en": "100 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "RESP", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("1830"), "selling_price": D("3280"),
                "wholesale_price": D("2950"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "R03DA05", "registration_number": "BDI-2024-1070",
                "barcode": "6001234500011",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Traitement respiratoire, suivi medical requis.",
                "notes_en": "Respiratory treatment, medical follow-up required.",
            },
            {
                "product_code": "MED-0012",
                "name_fr": "KEMOXYL DRY SUSPENSION 125 mg/5 ml", "name_en": "KEMOXYL DRY SUSPENSION 125 mg/5 ml",
                "generic_name": "Amoxicilline",
                "brand_name_fr": "KEMOXYL DRY SUSPENSION", "brand_name_en": "KEMOXYL DRY SUSPENSION",
                "strength_fr": "125 mg/5 ml", "strength_en": "125 mg/5 ml",
                "dosage_form": DosageForm.POWDER, "pack_size": "Flacon de 100 ml",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "FL",
                "unit_cost": D("860"), "selling_price": D("1540"),
                "wholesale_price": D("1390"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "J01CA04", "registration_number": "BDI-2025-1077",
                "barcode": "6001234500012",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0013",
                "name_fr": "KEMOXYL 250MG", "name_en": "KEMOXYL 250MG",
                "generic_name": "Amoxicilline",
                "brand_name_fr": "KEMOXYL 250MG", "brand_name_en": "KEMOXYL 250MG",
                "strength_fr": "250 mg", "strength_en": "250 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 10 x 10 gelules",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("3540"), "selling_price": D("6350"),
                "wholesale_price": D("5720"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "J01CA04", "registration_number": "BDI-2022-1084",
                "barcode": "6001234500013",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0014",
                "name_fr": "LABCLAV 625MG 500 mg/125 mg", "name_en": "LABCLAV 625MG 500 mg/125 mg",
                "generic_name": "Amoxicilline 500 mg + Acide clavulanique 125 mg",
                "brand_name_fr": "LABCLAV 625MG", "brand_name_en": "LABCLAV 625MG",
                "strength_fr": "500 mg/125 mg", "strength_en": "500 mg/125 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 comprimes",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("3100"), "selling_price": D("5560"),
                "wholesale_price": D("5000"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "J01CR02", "registration_number": "BDI-2023-1091",
                "barcode": "6001234500014",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0015",
                "name_fr": "AXYLIN DRY SUSPENSION 125 mg/125 mg par 5 ml", "name_en": "AXYLIN DRY SUSPENSION 125 mg/125 mg par 5 ml",
                "generic_name": "Ampicilline 125 mg + Cloxacilline 125 mg",
                "brand_name_fr": "AXYLIN DRY SUSPENSION", "brand_name_en": "AXYLIN DRY SUSPENSION",
                "strength_fr": "125 mg/125 mg par 5 ml", "strength_en": "125 mg/125 mg par 5 ml",
                "dosage_form": DosageForm.POWDER, "pack_size": "Flacon de 100 ml",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "FL",
                "unit_cost": D("1420"), "selling_price": D("2550"),
                "wholesale_price": D("2300"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "J01CR50", "registration_number": "BDI-2024-1098",
                "barcode": "6001234500015",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0016",
                "name_fr": "O.R.S POWDER WHO FORMULA Formule OMS 1 l", "name_en": "O.R.S POWDER WHO FORMULA Formule OMS 1 l",
                "generic_name": "Sels de rehydratation orale",
                "brand_name_fr": "O.R.S POWDER WHO FORMULA", "brand_name_en": "O.R.S POWDER WHO FORMULA",
                "strength_fr": "Formule OMS 1 l", "strength_en": "Formule OMS 1 l",
                "dosage_form": DosageForm.POWDER, "pack_size": "Boite de 50 sachets",
                "category": "GASTRO", "manufacturer": "LABALLIED", "unit": "SCH",
                "unit_cost": D("8850"), "selling_price": D("13450"),
                "wholesale_price": D("12110"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "A07CA00", "registration_number": "BDI-2025-1105",
                "barcode": "6001234500016",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Sphere digestive, rotation reguliere.",
                "notes_en": "Digestive range, steady turnover.",
            },
            {
                "product_code": "MED-0017",
                "name_fr": "ZEROCIN DRY POWDER SUSPENSION 200 mg/5 ml", "name_en": "ZEROCIN DRY POWDER SUSPENSION 200 mg/5 ml",
                "generic_name": "Azithromycine",
                "brand_name_fr": "ZEROCIN DRY POWDER SUSPENSION", "brand_name_en": "ZEROCIN DRY POWDER SUSPENSION",
                "strength_fr": "200 mg/5 ml", "strength_en": "200 mg/5 ml",
                "dosage_form": DosageForm.POWDER, "pack_size": "Flacon de 15 ml",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "FL",
                "unit_cost": D("1270"), "selling_price": D("2280"),
                "wholesale_price": D("2050"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "J01FA10", "registration_number": "BDI-2022-1112",
                "barcode": "6001234500017",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0018",
                "name_fr": "LAEOVATE CREAM 0,1 % p/p", "name_en": "LAEOVATE CREAM 0,1 % p/p",
                "generic_name": "Betamethasone",
                "brand_name_fr": "LAEOVATE CREAM", "brand_name_en": "LAEOVATE CREAM",
                "strength_fr": "0,1 % p/p", "strength_en": "0,1 % p/p",
                "dosage_form": DosageForm.CREAM, "pack_size": "Tube de 15 g",
                "category": "CORTICO", "manufacturer": "LABALLIED", "unit": "TUB",
                "unit_cost": D("530"), "selling_price": D("950"),
                "wholesale_price": D("860"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "D07AC01", "registration_number": "BDI-2023-1119",
                "barcode": "6001234500018",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Corticoide, delivrance encadree.",
                "notes_en": "Corticosteroid, supervised dispensing.",
            },
            {
                "product_code": "MED-0019",
                "name_fr": "EVERCEF DRY POWDER SUSPENSION 50 mg/5 ml", "name_en": "EVERCEF DRY POWDER SUSPENSION 50 mg/5 ml",
                "generic_name": "Cefixime",
                "brand_name_fr": "EVERCEF DRY POWDER SUSPENSION", "brand_name_en": "EVERCEF DRY POWDER SUSPENSION",
                "strength_fr": "50 mg/5 ml", "strength_en": "50 mg/5 ml",
                "dosage_form": DosageForm.POWDER, "pack_size": "Flacon de 100 ml",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "FL",
                "unit_cost": D("2950"), "selling_price": D("5290"),
                "wholesale_price": D("4760"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "J01DD08", "registration_number": "BDI-2024-1126",
                "barcode": "6001234500019",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0020",
                "name_fr": "EVERCEF 200MG", "name_en": "EVERCEF 200MG",
                "generic_name": "Cefixime",
                "brand_name_fr": "EVERCEF 200MG", "brand_name_en": "EVERCEF 200MG",
                "strength_fr": "200 mg", "strength_en": "200 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 comprimes",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("2800"), "selling_price": D("5020"),
                "wholesale_price": D("4520"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "J01DD08", "registration_number": "BDI-2025-1133",
                "barcode": "6001234500020",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0021",
                "name_fr": "CETRIZ SYRUP 5 mg/5 ml", "name_en": "CETRIZ SYRUP 5 mg/5 ml",
                "generic_name": "Cetirizine (chlorhydrate)",
                "brand_name_fr": "CETRIZ SYRUP", "brand_name_en": "CETRIZ SYRUP",
                "strength_fr": "5 mg/5 ml", "strength_en": "5 mg/5 ml",
                "dosage_form": DosageForm.SYRUP, "pack_size": "Flacon de 60 ml",
                "category": "ANTIHIST", "manufacturer": "LABALLIED", "unit": "FL",
                "unit_cost": D("560"), "selling_price": D("1000"),
                "wholesale_price": D("900"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("21"), "safety_stock": D("8"),
                "max_stock_level": D("126"),
                "atc_code": "R06AE07", "registration_number": "BDI-2022-1140",
                "barcode": "6001234500021",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antihistaminique, vente de comptoir.",
                "notes_en": "Antihistamine, counter sales.",
            },
            {
                "product_code": "MED-0022",
                "name_fr": "RHEUMAC GEL 1 %", "name_en": "RHEUMAC GEL 1 %",
                "generic_name": "Diclofenac sodique",
                "brand_name_fr": "RHEUMAC GEL", "brand_name_en": "RHEUMAC GEL",
                "strength_fr": "1 %", "strength_en": "1 %",
                "dosage_form": DosageForm.GEL, "pack_size": "Tube de 20 g",
                "category": "ANALG", "manufacturer": "LABALLIED", "unit": "TUB",
                "unit_cost": D("530"), "selling_price": D("950"),
                "wholesale_price": D("860"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("104"), "safety_stock": D("42"),
                "max_stock_level": D("624"),
                "atc_code": "M02AA15", "registration_number": "BDI-2023-1147",
                "barcode": "6001234500022",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0023",
                "name_fr": "DOXY 100MG", "name_en": "DOXY 100MG",
                "generic_name": "Doxycycline",
                "brand_name_fr": "DOXY 100MG", "brand_name_en": "DOXY 100MG",
                "strength_fr": "100 mg", "strength_en": "100 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 10 x 10 gelules",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("4280"), "selling_price": D("7680"),
                "wholesale_price": D("6910"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("21"), "safety_stock": D("8"),
                "max_stock_level": D("126"),
                "atc_code": "J01AA02", "registration_number": "BDI-2024-1154",
                "barcode": "6001234500023",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.PROTECT_LIGHT,
                "storage_notes": "Conserver a l'abri de la lumiere, emballage d'origine.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0024",
                "name_fr": "EROCIN DRY MIXTURE 125 mg/5 ml", "name_en": "EROCIN DRY MIXTURE 125 mg/5 ml",
                "generic_name": "Erythromycine (stearate)",
                "brand_name_fr": "EROCIN DRY MIXTURE", "brand_name_en": "EROCIN DRY MIXTURE",
                "strength_fr": "125 mg/5 ml", "strength_en": "125 mg/5 ml",
                "dosage_form": DosageForm.POWDER, "pack_size": "Flacon de 100 ml",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "FL",
                "unit_cost": D("1420"), "selling_price": D("2550"),
                "wholesale_price": D("2300"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "J01FA01", "registration_number": "BDI-2025-1161",
                "barcode": "6001234500024",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0025",
                "name_fr": "NOSPASUM 10 mg", "name_en": "NOSPASUM 10 mg",
                "generic_name": "Butylbromure d'hyoscine",
                "brand_name_fr": "NOSPASUM", "brand_name_en": "NOSPASUM",
                "strength_fr": "10 mg", "strength_en": "10 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "GASTRO", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("5900"), "selling_price": D("10580"),
                "wholesale_price": D("9520"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "A03BB01", "registration_number": "BDI-2022-1168",
                "barcode": "6001234500025",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Sphere digestive, rotation reguliere.",
                "notes_en": "Digestive range, steady turnover.",
            },
            {
                "product_code": "MED-0026",
                "name_fr": "NATOA 100MG", "name_en": "NATOA 100MG",
                "generic_name": "Mebendazole",
                "brand_name_fr": "NATOA 100MG", "brand_name_en": "NATOA 100MG",
                "strength_fr": "100 mg", "strength_en": "100 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "ANTIPAR", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("1920"), "selling_price": D("2920"),
                "wholesale_price": D("2630"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("28"), "safety_stock": D("11"),
                "max_stock_level": D("168"),
                "atc_code": "P02CA01", "registration_number": "BDI-2023-1175",
                "barcode": "6001234500026",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Vermifuge, programme de dewormage scolaire.",
                "notes_en": "Anthelmintic, school deworming programme.",
            },
            {
                "product_code": "MED-0027",
                "name_fr": "NATOA SUSPENSION 100 mg/5 ml", "name_en": "NATOA SUSPENSION 100 mg/5 ml",
                "generic_name": "Mebendazole",
                "brand_name_fr": "NATOA SUSPENSION", "brand_name_en": "NATOA SUSPENSION",
                "strength_fr": "100 mg/5 ml", "strength_en": "100 mg/5 ml",
                "dosage_form": DosageForm.SUSPENSION, "pack_size": "Flacon de 30 ml",
                "category": "ANTIPAR", "manufacturer": "LABALLIED", "unit": "FL",
                "unit_cost": D("620"), "selling_price": D("940"),
                "wholesale_price": D("850"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("49"), "safety_stock": D("20"),
                "max_stock_level": D("294"),
                "atc_code": "P02CA01", "registration_number": "BDI-2024-1182",
                "barcode": "6001234500027",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Vermifuge, programme de dewormage scolaire.",
                "notes_en": "Anthelmintic, school deworming programme.",
            },
            {
                "product_code": "MED-0028",
                "name_fr": "LABSTATIN VAGINAL 100 000 UI", "name_en": "LABSTATIN VAGINAL 100 000 UI",
                "generic_name": "Nystatine",
                "brand_name_fr": "LABSTATIN VAGINAL", "brand_name_en": "LABSTATIN VAGINAL",
                "strength_fr": "100 000 UI", "strength_en": "100 000 UI",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 3 x 5 comprimes avec applicateur",
                "category": "UROGEN", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("940"), "selling_price": D("1690"),
                "wholesale_price": D("1520"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("42"), "safety_stock": D("17"),
                "max_stock_level": D("252"),
                "atc_code": "G01AA01", "registration_number": "BDI-2025-1189",
                "barcode": "6001234500028",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Sphere uro-genitale, sur ordonnance.",
                "notes_en": "Urogenital range, prescription only.",
            },
            {
                "product_code": "MED-0029",
                "name_fr": "PARATAL 500MG", "name_en": "PARATAL 500MG",
                "generic_name": "Paracetamol",
                "brand_name_fr": "PARATAL 500MG", "brand_name_en": "PARATAL 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "ANALG", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("1120"), "selling_price": D("2010"),
                "wholesale_price": D("1810"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("63"), "safety_stock": D("25"),
                "max_stock_level": D("378"),
                "atc_code": "N02BE01", "registration_number": "BDI-2022-1196",
                "barcode": "6001234500029",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0030",
                "name_fr": "LASTMOL SYRUP 2 mg/5 ml", "name_en": "LASTMOL SYRUP 2 mg/5 ml",
                "generic_name": "Salbutamol",
                "brand_name_fr": "LASTMOL SYRUP", "brand_name_en": "LASTMOL SYRUP",
                "strength_fr": "2 mg/5 ml", "strength_en": "2 mg/5 ml",
                "dosage_form": DosageForm.SYRUP, "pack_size": "Flacon de 100 ml",
                "category": "RESP", "manufacturer": "LABALLIED", "unit": "FL",
                "unit_cost": D("830"), "selling_price": D("1490"),
                "wholesale_price": D("1340"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("21"), "safety_stock": D("8"),
                "max_stock_level": D("126"),
                "atc_code": "R03CC02", "registration_number": "BDI-2023-1203",
                "barcode": "6001234500030",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Traitement respiratoire, suivi medical requis.",
                "notes_en": "Respiratory treatment, medical follow-up required.",
            },
            {
                "product_code": "MED-0031",
                "name_fr": "EVOKE 50MG", "name_en": "EVOKE 50MG",
                "generic_name": "Sildenafil",
                "brand_name_fr": "EVOKE 50MG", "brand_name_en": "EVOKE 50MG",
                "strength_fr": "50 mg", "strength_en": "50 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 4 comprimes",
                "category": "UROGEN", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("350"), "selling_price": D("630"),
                "wholesale_price": D("570"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("42"), "safety_stock": D("17"),
                "max_stock_level": D("252"),
                "atc_code": "G04BE03", "registration_number": "BDI-2024-1210",
                "barcode": "6001234500031",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Sphere uro-genitale, sur ordonnance.",
                "notes_en": "Urogenital range, prescription only.",
            },
            {
                "product_code": "MED-0032",
                "name_fr": "RACYCLINE SKIN OINTMENT 3 % p/v", "name_en": "RACYCLINE SKIN OINTMENT 3 % p/v",
                "generic_name": "Tetracycline (chlorhydrate)",
                "brand_name_fr": "RACYCLINE SKIN OINTMENT", "brand_name_en": "RACYCLINE SKIN OINTMENT",
                "strength_fr": "3 % p/v", "strength_en": "3 % p/v",
                "dosage_form": DosageForm.OINTMENT, "pack_size": "Tube de 15 g",
                "category": "DERM", "manufacturer": "LABALLIED", "unit": "TUB",
                "unit_cost": D("530"), "selling_price": D("950"),
                "wholesale_price": D("860"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("42"), "safety_stock": D("17"),
                "max_stock_level": D("252"),
                "atc_code": "D06AA04", "registration_number": "BDI-2025-1217",
                "barcode": "6001234500032",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Usage cutane externe.",
                "notes_en": "External topical use.",
            },
            {
                "product_code": "MED-0033",
                "name_fr": "TYNAZOLE 500MG", "name_en": "TYNAZOLE 500MG",
                "generic_name": "Tinidazole",
                "brand_name_fr": "TYNAZOLE 500MG", "brand_name_en": "TYNAZOLE 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 4 comprimes",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("210"), "selling_price": D("380"),
                "wholesale_price": D("340"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("108"), "safety_stock": D("43"),
                "max_stock_level": D("648"),
                "atc_code": "J01XD02", "registration_number": "BDI-2022-1224",
                "barcode": "6001234500033",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0034",
                "name_fr": "LABGENTIL 500MG", "name_en": "LABGENTIL 500MG",
                "generic_name": "Secnidazole",
                "brand_name_fr": "LABGENTIL 500MG", "brand_name_en": "LABGENTIL 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 4 comprimes",
                "category": "ATB", "manufacturer": "LABALLIED", "unit": "BTE",
                "unit_cost": D("860"), "selling_price": D("1540"),
                "wholesale_price": D("1390"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("104"), "safety_stock": D("42"),
                "max_stock_level": D("624"),
                "atc_code": "P01AB07", "registration_number": "BDI-2023-1231",
                "barcode": "6001234500034",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0035",
                "name_fr": "HYCORUM CREAM 1 % p/p", "name_en": "HYCORUM CREAM 1 % p/p",
                "generic_name": "Hydrocortisone",
                "brand_name_fr": "HYCORUM CREAM", "brand_name_en": "HYCORUM CREAM",
                "strength_fr": "1 % p/p", "strength_en": "1 % p/p",
                "dosage_form": DosageForm.CREAM, "pack_size": "Tube de 15 g",
                "category": "CORTICO", "manufacturer": "LABALLIED", "unit": "TUB",
                "unit_cost": D("710"), "selling_price": D("1270"),
                "wholesale_price": D("1140"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("313"), "safety_stock": D("125"),
                "max_stock_level": D("1878"),
                "atc_code": "D07AA02", "registration_number": "BDI-2024-1238",
                "barcode": "6001234500035",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Corticoide, delivrance encadree.",
                "notes_en": "Corticosteroid, supervised dispensing.",
            },
            {
                "product_code": "MED-0036",
                "name_fr": "AMOXICILLINE 500MG", "name_en": "AMOXICILLINE 500MG",
                "generic_name": "Amoxicilline",
                "brand_name_fr": "AMOXICILLINE 500MG", "brand_name_en": "AMOXICILLINE 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 100 gelules",
                "category": "ATB", "manufacturer": "HARLEYS", "unit": "BTE",
                "unit_cost": D("6580"), "selling_price": D("11800"),
                "wholesale_price": D("10620"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("208"), "safety_stock": D("83"),
                "max_stock_level": D("1248"),
                "atc_code": "J01CA04", "registration_number": "BDI-2025-1245",
                "barcode": "6001234500036",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0037",
                "name_fr": "MOXACIL 500MG", "name_en": "MOXACIL 500MG",
                "generic_name": "Amoxicilline",
                "brand_name_fr": "MOXACIL 500MG", "brand_name_en": "MOXACIL 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 100 gelules",
                "category": "ATB", "manufacturer": "HARLEYS", "unit": "BTE",
                "unit_cost": D("8140"), "selling_price": D("14600"),
                "wholesale_price": D("13140"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("69"), "safety_stock": D("28"),
                "max_stock_level": D("414"),
                "atc_code": "J01CA04", "registration_number": "BDI-2022-1252",
                "barcode": "6001234500037",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0038",
                "name_fr": "PHARMADEXNICOL EYE DROPS 0,5 %", "name_en": "PHARMADEXNICOL EYE DROPS 0,5 %",
                "generic_name": "Chloramphenicol",
                "brand_name_fr": "PHARMADEXNICOL EYE DROPS", "brand_name_en": "PHARMADEXNICOL EYE DROPS",
                "strength_fr": "0,5 %", "strength_en": "0,5 %",
                "dosage_form": DosageForm.DROPS, "pack_size": "Flacon de 5 ml",
                "category": "OPHT", "manufacturer": "HARLEYS", "unit": "FL",
                "unit_cost": D("2680"), "selling_price": D("4810"),
                "wholesale_price": D("4330"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "S01AA01", "registration_number": "BDI-2023-1259",
                "barcode": "6001234500038",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Collyre, chaine du froid a respecter.",
                "notes_en": "Eye drops, cold chain required.",
            },
            {
                "product_code": "MED-0039",
                "name_fr": "IBUPAR SUSPENSION 100 mg/5 ml", "name_en": "IBUPAR SUSPENSION 100 mg/5 ml",
                "generic_name": "Ibuprofene",
                "brand_name_fr": "IBUPAR SUSPENSION", "brand_name_en": "IBUPAR SUSPENSION",
                "strength_fr": "100 mg/5 ml", "strength_en": "100 mg/5 ml",
                "dosage_form": DosageForm.SUSPENSION, "pack_size": "Flacon de 100 ml",
                "category": "ANALG", "manufacturer": "HARLEYS", "unit": "FL",
                "unit_cost": D("240"), "selling_price": D("430"),
                "wholesale_price": D("390"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("104"), "safety_stock": D("42"),
                "max_stock_level": D("624"),
                "atc_code": "M01AE01", "registration_number": "BDI-2024-1266",
                "barcode": "6001234500039",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0040",
                "name_fr": "AMPICILLINE 500MG INJ", "name_en": "AMPICILLINE 500MG INJ",
                "generic_name": "Ampicilline",
                "brand_name_fr": "AMPICILLINE 500MG INJ", "brand_name_en": "AMPICILLINE 500MG INJ",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Flacon injectable",
                "category": "SOLINJ", "manufacturer": "HARLEYS", "unit": "FL",
                "unit_cost": D("560"), "selling_price": D("1000"),
                "wholesale_price": D("900"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "J01CA01", "registration_number": "BDI-2025-1273",
                "barcode": "6001234500040",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0041",
                "name_fr": "ABGENTA EYE DROPS 0,3 %", "name_en": "ABGENTA EYE DROPS 0,3 %",
                "generic_name": "Gentamicine",
                "brand_name_fr": "ABGENTA EYE DROPS", "brand_name_en": "ABGENTA EYE DROPS",
                "strength_fr": "0,3 %", "strength_en": "0,3 %",
                "dosage_form": DosageForm.DROPS, "pack_size": "Flacon de 10 ml",
                "category": "OPHT", "manufacturer": "HARLEYS", "unit": "FL",
                "unit_cost": D("560"), "selling_price": D("1000"),
                "wholesale_price": D("900"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "S01AA11", "registration_number": "BDI-2022-1280",
                "barcode": "6001234500041",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Collyre, chaine du froid a respecter.",
                "notes_en": "Eye drops, cold chain required.",
            },
            {
                "product_code": "MED-0042",
                "name_fr": "PIROXICAM 20MG", "name_en": "PIROXICAM 20MG",
                "generic_name": "Piroxicam",
                "brand_name_fr": "PIROXICAM 20MG", "brand_name_en": "PIROXICAM 20MG",
                "strength_fr": "20 mg", "strength_en": "20 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 100 gelules",
                "category": "ANALG", "manufacturer": "HARLEYS", "unit": "BTE",
                "unit_cost": D("2180"), "selling_price": D("3910"),
                "wholesale_price": D("3520"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "M01AC01", "registration_number": "BDI-2023-1287",
                "barcode": "6001234500042",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0043",
                "name_fr": "ZINC SULPHATE 20MG", "name_en": "ZINC SULPHATE 20MG",
                "generic_name": "Sulfate de zinc",
                "brand_name_fr": "ZINC SULPHATE 20MG", "brand_name_en": "ZINC SULPHATE 20MG",
                "strength_fr": "20 mg", "strength_en": "20 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 100 comprimes",
                "category": "VITA", "manufacturer": "HARLEYS", "unit": "BTE",
                "unit_cost": D("2920"), "selling_price": D("4440"),
                "wholesale_price": D("4000"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "A12CB01", "registration_number": "BDI-2024-1294",
                "barcode": "6001234500043",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Complement nutritionnel, exonere de TVA.",
                "notes_en": "Nutritional supplement, VAT exempt.",
            },
            {
                "product_code": "MED-0044",
                "name_fr": "ARTH + LUM FORTE 80 mg/480 mg", "name_en": "ARTH + LUM FORTE 80 mg/480 mg",
                "generic_name": "Artemether 80 mg + Lumefantrine 480 mg",
                "brand_name_fr": "ARTH + LUM FORTE", "brand_name_en": "ARTH + LUM FORTE",
                "strength_fr": "80 mg/480 mg", "strength_en": "80 mg/480 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 6 comprimes",
                "category": "ANTIPAL", "manufacturer": "HARLEYS", "unit": "BTE",
                "unit_cost": D("3980"), "selling_price": D("6050"),
                "wholesale_price": D("5450"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "P01BF01", "registration_number": "BDI-2025-1301",
                "barcode": "6001234500044",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.PROTECT_LIGHT,
                "storage_notes": "Conserver a l'abri de la lumiere, emballage d'origine.",
                "notes_fr": "Programme national antipaludique, exonere de TVA.",
                "notes_en": "National antimalarial programme, VAT exempt.",
            },
            {
                "product_code": "MED-0045",
                "name_fr": "MARAMOJA 500 mg/65 mg", "name_en": "MARAMOJA 500 mg/65 mg",
                "generic_name": "Paracetamol 500 mg + Cafeine 65 mg",
                "brand_name_fr": "MARAMOJA", "brand_name_en": "MARAMOJA",
                "strength_fr": "500 mg/65 mg", "strength_en": "500 mg/65 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 100 comprimes",
                "category": "ANALG", "manufacturer": "HARLEYS", "unit": "BTE",
                "unit_cost": D("12180"), "selling_price": D("21850"),
                "wholesale_price": D("19670"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "N02BE51", "registration_number": "BDI-2022-1308",
                "barcode": "6001234500045",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0046",
                "name_fr": "ACTION 500 mg/2 mg", "name_en": "ACTION 500 mg/2 mg",
                "generic_name": "Paracetamol 500 mg + Chlorphenamine 2 mg",
                "brand_name_fr": "ACTION", "brand_name_en": "ACTION",
                "strength_fr": "500 mg/2 mg", "strength_en": "500 mg/2 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 100 comprimes",
                "category": "ANALG", "manufacturer": "HARLEYS", "unit": "BTE",
                "unit_cost": D("11590"), "selling_price": D("20790"),
                "wholesale_price": D("18710"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "N02BE51", "registration_number": "BDI-2023-1315",
                "barcode": "6001234500046",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0047",
                "name_fr": "DAWA CPM 4MG", "name_en": "DAWA CPM 4MG",
                "generic_name": "Chlorphenamine (maleate)",
                "brand_name_fr": "DAWA CPM 4MG", "brand_name_en": "DAWA CPM 4MG",
                "strength_fr": "4 mg", "strength_en": "4 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 100 comprimes",
                "category": "ANTIHIST", "manufacturer": "HARLEYS", "unit": "BTE",
                "unit_cost": D("2120"), "selling_price": D("3800"),
                "wholesale_price": D("3420"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "R06AB04", "registration_number": "BDI-2024-1322",
                "barcode": "6001234500047",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antihistaminique, vente de comptoir.",
                "notes_en": "Antihistamine, counter sales.",
            },
            {
                "product_code": "MED-0048",
                "name_fr": "GANTS CHIRURGICAUX STERILES 7.5 Taille 7,5", "name_en": "GANTS CHIRURGICAUX STERILES 7.5 Taille 7,5",
                "generic_name": "Gants chirurgicaux steriles",
                "brand_name_fr": "GANTS CHIRURGICAUX STERILES 7.5", "brand_name_en": "GANTS CHIRURGICAUX STERILES 7.5",
                "strength_fr": "Taille 7,5", "strength_en": "Taille 7,5",
                "dosage_form": DosageForm.CONSUMABLE, "pack_size": "Paire sterile",
                "category": "CONSOM", "manufacturer": "INFINITE", "unit": "PAI",
                "unit_cost": D("490"), "selling_price": D("740"),
                "wholesale_price": D("670"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "V07AY00", "registration_number": "BDI-2025-1329",
                "barcode": "6001234500048",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Consommable a usage unique, exonere de TVA.",
                "notes_en": "Single-use consumable, VAT exempt.",
            },
            {
                "product_code": "MED-0049",
                "name_fr": "GANTS D'EXAMEN EN LATEX Non steriles", "name_en": "GANTS D'EXAMEN EN LATEX Non steriles",
                "generic_name": "Gants d'examen en latex",
                "brand_name_fr": "GANTS D'EXAMEN EN LATEX", "brand_name_en": "GANTS D'EXAMEN EN LATEX",
                "strength_fr": "Non steriles", "strength_en": "Non steriles",
                "dosage_form": DosageForm.CONSUMABLE, "pack_size": "Boite de 100 gants",
                "category": "CONSOM", "manufacturer": "INFINITE", "unit": "BTE",
                "unit_cost": D("7970"), "selling_price": D("12110"),
                "wholesale_price": D("10900"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "V07AY00", "registration_number": "BDI-2022-1336",
                "barcode": "6001234500049",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Consommable a usage unique, exonere de TVA.",
                "notes_en": "Single-use consumable, VAT exempt.",
            },
            {
                "product_code": "MED-0050",
                "name_fr": "SERINGUE 5ML AVEC AIGUILLE", "name_en": "SERINGUE 5ML AVEC AIGUILLE",
                "generic_name": "Seringue a usage unique",
                "brand_name_fr": "SERINGUE 5ML AVEC AIGUILLE", "brand_name_en": "SERINGUE 5ML AVEC AIGUILLE",
                "strength_fr": "5 ml", "strength_en": "5 ml",
                "dosage_form": DosageForm.DEVICE, "pack_size": "Piece sous blister",
                "category": "CONSOM", "manufacturer": "INFINITE", "unit": "PCE",
                "unit_cost": D("90"), "selling_price": D("140"),
                "wholesale_price": D("130"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("217"), "safety_stock": D("87"),
                "max_stock_level": D("1302"),
                "atc_code": "V07AY00", "registration_number": "BDI-2023-1343",
                "barcode": "6001234500050",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Consommable a usage unique, exonere de TVA.",
                "notes_en": "Single-use consumable, VAT exempt.",
            },
            {
                "product_code": "MED-0051",
                "name_fr": "SERINGUE 10ML AVEC AIGUILLE", "name_en": "SERINGUE 10ML AVEC AIGUILLE",
                "generic_name": "Seringue a usage unique",
                "brand_name_fr": "SERINGUE 10ML AVEC AIGUILLE", "brand_name_en": "SERINGUE 10ML AVEC AIGUILLE",
                "strength_fr": "10 ml", "strength_en": "10 ml",
                "dosage_form": DosageForm.DEVICE, "pack_size": "Piece sous blister",
                "category": "CONSOM", "manufacturer": "INFINITE", "unit": "PCE",
                "unit_cost": D("120"), "selling_price": D("180"),
                "wholesale_price": D("160"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("83"), "safety_stock": D("33"),
                "max_stock_level": D("498"),
                "atc_code": "V07AY00", "registration_number": "BDI-2024-1350",
                "barcode": "6001234500051",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Consommable a usage unique, exonere de TVA.",
                "notes_en": "Single-use consumable, VAT exempt.",
            },
            {
                "product_code": "MED-0052",
                "name_fr": "SERINGUE 2ML AVEC AIGUILLE", "name_en": "SERINGUE 2ML AVEC AIGUILLE",
                "generic_name": "Seringue a usage unique",
                "brand_name_fr": "SERINGUE 2ML AVEC AIGUILLE", "brand_name_en": "SERINGUE 2ML AVEC AIGUILLE",
                "strength_fr": "2 ml", "strength_en": "2 ml",
                "dosage_form": DosageForm.DEVICE, "pack_size": "Piece sous blister",
                "category": "CONSOM", "manufacturer": "INFINITE", "unit": "PCE",
                "unit_cost": D("80"), "selling_price": D("120"),
                "wholesale_price": D("110"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("146"), "safety_stock": D("58"),
                "max_stock_level": D("876"),
                "atc_code": "V07AY00", "registration_number": "BDI-2025-1357",
                "barcode": "6001234500052",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Consommable a usage unique, exonere de TVA.",
                "notes_en": "Single-use consumable, VAT exempt.",
            },
            {
                "product_code": "MED-0053",
                "name_fr": "COMPRESSE GAZE EN ROULEAU Rouleau de 1,5 kg", "name_en": "COMPRESSE GAZE EN ROULEAU Rouleau de 1,5 kg",
                "generic_name": "Gaze hydrophile",
                "brand_name_fr": "COMPRESSE GAZE EN ROULEAU", "brand_name_en": "COMPRESSE GAZE EN ROULEAU",
                "strength_fr": "Rouleau de 1,5 kg", "strength_en": "Rouleau de 1,5 kg",
                "dosage_form": DosageForm.CONSUMABLE, "pack_size": "Rouleau de 1,5 kg",
                "category": "CONSOM", "manufacturer": "INFINITE", "unit": "ROL",
                "unit_cost": D("21680"), "selling_price": D("32950"),
                "wholesale_price": D("29660"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "V07AY00", "registration_number": "BDI-2022-1364",
                "barcode": "6001234500053",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Consommable a usage unique, exonere de TVA.",
                "notes_en": "Single-use consumable, VAT exempt.",
            },
            {
                "product_code": "MED-0054",
                "name_fr": "OMEPRAZOLE 40MG INJECTION", "name_en": "OMEPRAZOLE 40MG INJECTION",
                "generic_name": "Omeprazole",
                "brand_name_fr": "OMEPRAZOLE 40MG INJECTION", "brand_name_en": "OMEPRAZOLE 40MG INJECTION",
                "strength_fr": "40 mg", "strength_en": "40 mg",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Ampoule injectable",
                "category": "SOLINJ", "manufacturer": "INFINITE", "unit": "AMP",
                "unit_cost": D("1120"), "selling_price": D("2010"),
                "wholesale_price": D("1810"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("17"), "safety_stock": D("7"),
                "max_stock_level": D("102"),
                "atc_code": "A02BC01", "registration_number": "BDI-2023-1371",
                "barcode": "6001234500054",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0055",
                "name_fr": "HYDROCORTISONE INJECTION 100 mg", "name_en": "HYDROCORTISONE INJECTION 100 mg",
                "generic_name": "Hydrocortisone (succinate sodique)",
                "brand_name_fr": "HYDROCORTISONE INJECTION", "brand_name_en": "HYDROCORTISONE INJECTION",
                "strength_fr": "100 mg", "strength_en": "100 mg",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Ampoule injectable",
                "category": "SOLINJ", "manufacturer": "INFINITE", "unit": "AMP",
                "unit_cost": D("590"), "selling_price": D("1060"),
                "wholesale_price": D("950"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("38"), "safety_stock": D("15"),
                "max_stock_level": D("228"),
                "atc_code": "H02AB09", "registration_number": "BDI-2024-1378",
                "barcode": "6001234500055",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0056",
                "name_fr": "PARACETAMOL INJECTION 1 g/100 ml", "name_en": "PARACETAMOL INJECTION 1 g/100 ml",
                "generic_name": "Paracetamol",
                "brand_name_fr": "PARACETAMOL INJECTION", "brand_name_en": "PARACETAMOL INJECTION",
                "strength_fr": "1 g/100 ml", "strength_en": "1 g/100 ml",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Ampoule injectable",
                "category": "SOLINJ", "manufacturer": "INFINITE", "unit": "AMP",
                "unit_cost": D("1330"), "selling_price": D("2390"),
                "wholesale_price": D("2150"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("69"), "safety_stock": D("28"),
                "max_stock_level": D("414"),
                "atc_code": "N02BE01", "registration_number": "BDI-2025-1385",
                "barcode": "6001234500056",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0057",
                "name_fr": "LIGNOCAINE 2% INJECTION", "name_en": "LIGNOCAINE 2% INJECTION",
                "generic_name": "Lidocaine (chlorhydrate)",
                "brand_name_fr": "LIGNOCAINE 2% INJECTION", "brand_name_en": "LIGNOCAINE 2% INJECTION",
                "strength_fr": "2 %", "strength_en": "2 %",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Ampoule injectable",
                "category": "SOLINJ", "manufacturer": "INFINITE", "unit": "AMP",
                "unit_cost": D("650"), "selling_price": D("1170"),
                "wholesale_price": D("1050"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "N01BB02", "registration_number": "BDI-2022-1392",
                "barcode": "6001234500057",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0058",
                "name_fr": "QUININE INJECTION 600 mg/2 ml", "name_en": "QUININE INJECTION 600 mg/2 ml",
                "generic_name": "Quinine (dichlorhydrate)",
                "brand_name_fr": "QUININE INJECTION", "brand_name_en": "QUININE INJECTION",
                "strength_fr": "600 mg/2 ml", "strength_en": "600 mg/2 ml",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Flacon injectable",
                "category": "ANTIPAL", "manufacturer": "INFINITE", "unit": "FL",
                "unit_cost": D("620"), "selling_price": D("940"),
                "wholesale_price": D("850"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("69"), "safety_stock": D("28"),
                "max_stock_level": D("414"),
                "atc_code": "P01BC01", "registration_number": "BDI-2023-1399",
                "barcode": "6001234500058",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.PROTECT_LIGHT,
                "storage_notes": "Conserver a l'abri de la lumiere, emballage d'origine.",
                "notes_fr": "Programme national antipaludique, exonere de TVA.",
                "notes_en": "National antimalarial programme, VAT exempt.",
            },
            {
                "product_code": "MED-0059",
                "name_fr": "CEFTRIAXONE 1G INJECTION", "name_en": "CEFTRIAXONE 1G INJECTION",
                "generic_name": "Ceftriaxone",
                "brand_name_fr": "CEFTRIAXONE 1G INJECTION", "brand_name_en": "CEFTRIAXONE 1G INJECTION",
                "strength_fr": "1 g", "strength_en": "1 g",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Flacon avec ampoule d'eau PPI",
                "category": "SOLINJ", "manufacturer": "INFINITE", "unit": "FL",
                "unit_cost": D("800"), "selling_price": D("1430"),
                "wholesale_price": D("1290"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("35"), "safety_stock": D("14"),
                "max_stock_level": D("210"),
                "atc_code": "J01DD04", "registration_number": "BDI-2024-1406",
                "barcode": "6001234500059",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0060",
                "name_fr": "BENZATHINE BENZYL 2.4 MEGA 2,4 MUI", "name_en": "BENZATHINE BENZYL 2.4 MEGA 2,4 MUI",
                "generic_name": "Benzathine benzylpenicilline",
                "brand_name_fr": "BENZATHINE BENZYL 2.4 MEGA", "brand_name_en": "BENZATHINE BENZYL 2.4 MEGA",
                "strength_fr": "2,4 MUI", "strength_en": "2,4 MUI",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Ampoule injectable",
                "category": "SOLINJ", "manufacturer": "INFINITE", "unit": "AMP",
                "unit_cost": D("650"), "selling_price": D("1170"),
                "wholesale_price": D("1050"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("21"), "safety_stock": D("8"),
                "max_stock_level": D("126"),
                "atc_code": "J01CE08", "registration_number": "BDI-2025-1413",
                "barcode": "6001234500060",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0061",
                "name_fr": "GLUCOSE 5% INFUSION", "name_en": "GLUCOSE 5% INFUSION",
                "generic_name": "Glucose",
                "brand_name_fr": "GLUCOSE 5% INFUSION", "brand_name_en": "GLUCOSE 5% INFUSION",
                "strength_fr": "5 %", "strength_en": "5 %",
                "dosage_form": DosageForm.INFUSION, "pack_size": "Poche de 500 ml",
                "category": "SOLINJ", "manufacturer": "INFINITE", "unit": "FL",
                "unit_cost": D("1550"), "selling_price": D("2360"),
                "wholesale_price": D("2120"), "vat_rate": D("0"),
                "is_vat_exempt": True,
                "reorder_level": D("69"), "safety_stock": D("28"),
                "max_stock_level": D("414"),
                "atc_code": "B05BA03", "registration_number": "BDI-2022-1420",
                "barcode": "6001234500061",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0062",
                "name_fr": "AMLODAWA 10MG", "name_en": "AMLODAWA 10MG",
                "generic_name": "Amlodipine",
                "brand_name_fr": "AMLODAWA 10MG", "brand_name_en": "AMLODAWA 10MG",
                "strength_fr": "10 mg", "strength_en": "10 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 3 x 10 comprimes",
                "category": "CARDIO", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("940"), "selling_price": D("1690"),
                "wholesale_price": D("1520"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("21"), "safety_stock": D("8"),
                "max_stock_level": D("126"),
                "atc_code": "C08CA01", "registration_number": "BDI-2023-1427",
                "barcode": "6001234500062",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Traitement chronique, dispensation mensuelle.",
                "notes_en": "Chronic treatment, monthly dispensing.",
            },
            {
                "product_code": "MED-0063",
                "name_fr": "AMILINE 25MG", "name_en": "AMILINE 25MG",
                "generic_name": "Amitriptyline",
                "brand_name_fr": "AMILINE 25MG", "brand_name_en": "AMILINE 25MG",
                "strength_fr": "25 mg", "strength_en": "25 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "SNC", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("1150"), "selling_price": D("2060"),
                "wholesale_price": D("1850"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("15"), "safety_stock": D("6"),
                "max_stock_level": D("90"),
                "atc_code": "N06AA09", "registration_number": "BDI-2024-1434",
                "barcode": "6001234500063",
                "requires_prescription": True, "is_controlled": True,
                "storage_condition": StorageCondition.PROTECT_LIGHT,
                "storage_notes": "Conserver a l'abri de la lumiere, emballage d'origine.",
                "notes_fr": "Psychotrope: registre des substances surveillees.",
                "notes_en": "Psychotropic: controlled substances register.",
            },
            {
                "product_code": "MED-0064",
                "name_fr": "AZIDAWA 200 SUSP 200 mg/5 ml", "name_en": "AZIDAWA 200 SUSP 200 mg/5 ml",
                "generic_name": "Azithromycine",
                "brand_name_fr": "AZIDAWA 200 SUSP", "brand_name_en": "AZIDAWA 200 SUSP",
                "strength_fr": "200 mg/5 ml", "strength_en": "200 mg/5 ml",
                "dosage_form": DosageForm.SUSPENSION, "pack_size": "Flacon de 15 ml",
                "category": "ATB", "manufacturer": "DAWA", "unit": "FL",
                "unit_cost": D("1030"), "selling_price": D("1850"),
                "wholesale_price": D("1670"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("22"), "safety_stock": D("9"),
                "max_stock_level": D("132"),
                "atc_code": "J01FA10", "registration_number": "BDI-2025-1441",
                "barcode": "6001234500064",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0065",
                "name_fr": "AZIDAWA 500MG", "name_en": "AZIDAWA 500MG",
                "generic_name": "Azithromycine",
                "brand_name_fr": "AZIDAWA 500MG", "brand_name_en": "AZIDAWA 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 3 comprimes",
                "category": "ATB", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("1030"), "selling_price": D("1850"),
                "wholesale_price": D("1670"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("105"), "safety_stock": D("42"),
                "max_stock_level": D("630"),
                "atc_code": "J01FA10", "registration_number": "BDI-2022-1448",
                "barcode": "6001234500065",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0066",
                "name_fr": "CINDOMET 25MG", "name_en": "CINDOMET 25MG",
                "generic_name": "Indometacine",
                "brand_name_fr": "CINDOMET 25MG", "brand_name_en": "CINDOMET 25MG",
                "strength_fr": "25 mg", "strength_en": "25 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 10 x 10 gelules",
                "category": "ANALG", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("1770"), "selling_price": D("3170"),
                "wholesale_price": D("2850"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "M01AB01", "registration_number": "BDI-2023-1455",
                "barcode": "6001234500066",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0067",
                "name_fr": "CURAMOL SUSP 120 mg/5 ml", "name_en": "CURAMOL SUSP 120 mg/5 ml",
                "generic_name": "Paracetamol",
                "brand_name_fr": "CURAMOL SUSP", "brand_name_en": "CURAMOL SUSP",
                "strength_fr": "120 mg/5 ml", "strength_en": "120 mg/5 ml",
                "dosage_form": DosageForm.SUSPENSION, "pack_size": "Flacon de 100 ml",
                "category": "ANALG", "manufacturer": "DAWA", "unit": "FL",
                "unit_cost": D("650"), "selling_price": D("1170"),
                "wholesale_price": D("1050"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("104"), "safety_stock": D("42"),
                "max_stock_level": D("624"),
                "atc_code": "N02BE01", "registration_number": "BDI-2024-1462",
                "barcode": "6001234500067",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0068",
                "name_fr": "CURAMOL 500MG", "name_en": "CURAMOL 500MG",
                "generic_name": "Paracetamol",
                "brand_name_fr": "CURAMOL 500MG", "brand_name_en": "CURAMOL 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 1000 comprimes",
                "category": "ANALG", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("10180"), "selling_price": D("18260"),
                "wholesale_price": D("16430"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("70"), "safety_stock": D("28"),
                "max_stock_level": D("420"),
                "atc_code": "N02BE01", "registration_number": "BDI-2025-1469",
                "barcode": "6001234500068",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0069",
                "name_fr": "DACINAZOLE CREAM 2 % p/p", "name_en": "DACINAZOLE CREAM 2 % p/p",
                "generic_name": "Miconazole (nitrate)",
                "brand_name_fr": "DACINAZOLE CREAM", "brand_name_en": "DACINAZOLE CREAM",
                "strength_fr": "2 % p/p", "strength_en": "2 % p/p",
                "dosage_form": DosageForm.CREAM, "pack_size": "Tube de 15 g",
                "category": "ANTIFONG", "manufacturer": "DAWA", "unit": "TUB",
                "unit_cost": D("530"), "selling_price": D("950"),
                "wholesale_price": D("860"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("50"), "safety_stock": D("20"),
                "max_stock_level": D("300"),
                "atc_code": "D01AC02", "registration_number": "BDI-2022-1476",
                "barcode": "6001234500069",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antifongique, forte demande saisonniere.",
                "notes_en": "Antifungal, strong seasonal demand.",
            },
            {
                "product_code": "MED-0070",
                "name_fr": "DAWA CPM SYRUP 2 mg/5 ml", "name_en": "DAWA CPM SYRUP 2 mg/5 ml",
                "generic_name": "Chlorphenamine (maleate)",
                "brand_name_fr": "DAWA CPM SYRUP", "brand_name_en": "DAWA CPM SYRUP",
                "strength_fr": "2 mg/5 ml", "strength_en": "2 mg/5 ml",
                "dosage_form": DosageForm.SYRUP, "pack_size": "Flacon de 100 ml",
                "category": "ANTIHIST", "manufacturer": "DAWA", "unit": "FL",
                "unit_cost": D("740"), "selling_price": D("1330"),
                "wholesale_price": D("1200"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("49"), "safety_stock": D("20"),
                "max_stock_level": D("294"),
                "atc_code": "R06AB04", "registration_number": "BDI-2023-1483",
                "barcode": "6001234500070",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antihistaminique, vente de comptoir.",
                "notes_en": "Antihistamine, counter sales.",
            },
            {
                "product_code": "MED-0071",
                "name_fr": "DAWACLOX 250MG", "name_en": "DAWACLOX 250MG",
                "generic_name": "Cloxacilline",
                "brand_name_fr": "DAWACLOX 250MG", "brand_name_en": "DAWACLOX 250MG",
                "strength_fr": "250 mg", "strength_en": "250 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 10 x 10 gelules",
                "category": "ATB", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("4720"), "selling_price": D("8470"),
                "wholesale_price": D("7620"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "J01CF02", "registration_number": "BDI-2024-1490",
                "barcode": "6001234500071",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0072",
                "name_fr": "DAWACLOX 500MG", "name_en": "DAWACLOX 500MG",
                "generic_name": "Cloxacilline",
                "brand_name_fr": "DAWACLOX 500MG", "brand_name_en": "DAWACLOX 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 10 x 10 gelules",
                "category": "ATB", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("9590"), "selling_price": D("17200"),
                "wholesale_price": D("15480"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("28"), "safety_stock": D("11"),
                "max_stock_level": D("168"),
                "atc_code": "J01CF02", "registration_number": "BDI-2025-1497",
                "barcode": "6001234500072",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0073",
                "name_fr": "DAWADINE 10% SOLUTION 10 % p/v", "name_en": "DAWADINE 10% SOLUTION 10 % p/v",
                "generic_name": "Povidone iodee",
                "brand_name_fr": "DAWADINE 10% SOLUTION", "brand_name_en": "DAWADINE 10% SOLUTION",
                "strength_fr": "10 % p/v", "strength_en": "10 % p/v",
                "dosage_form": DosageForm.SOLUTION, "pack_size": "Flacon de 100 ml",
                "category": "DERM", "manufacturer": "DAWA", "unit": "FL",
                "unit_cost": D("1480"), "selling_price": D("2650"),
                "wholesale_price": D("2390"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "D08AG02", "registration_number": "BDI-2022-1504",
                "barcode": "6001234500073",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.PROTECT_LIGHT,
                "storage_notes": "Conserver a l'abri de la lumiere, emballage d'origine.",
                "notes_fr": "Usage cutane externe.",
                "notes_en": "External topical use.",
            },
            {
                "product_code": "MED-0074",
                "name_fr": "DAWAGENTA INJ 80 mg/2 ml", "name_en": "DAWAGENTA INJ 80 mg/2 ml",
                "generic_name": "Gentamicine (sulfate)",
                "brand_name_fr": "DAWAGENTA INJ", "brand_name_en": "DAWAGENTA INJ",
                "strength_fr": "80 mg/2 ml", "strength_en": "80 mg/2 ml",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Ampoule injectable",
                "category": "SOLINJ", "manufacturer": "DAWA", "unit": "AMP",
                "unit_cost": D("120"), "selling_price": D("220"),
                "wholesale_price": D("200"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("50"), "safety_stock": D("20"),
                "max_stock_level": D("300"),
                "atc_code": "J01GB03", "registration_number": "BDI-2023-1511",
                "barcode": "6001234500074",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0075",
                "name_fr": "DAWAPRAZ 20MG", "name_en": "DAWAPRAZ 20MG",
                "generic_name": "Omeprazole",
                "brand_name_fr": "DAWAPRAZ 20MG", "brand_name_en": "DAWAPRAZ 20MG",
                "strength_fr": "20 mg", "strength_en": "20 mg",
                "dosage_form": DosageForm.CAPSULE, "pack_size": "Boite de 10 x 10 gelules",
                "category": "GASTRO", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("2240"), "selling_price": D("4020"),
                "wholesale_price": D("3620"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("104"), "safety_stock": D("42"),
                "max_stock_level": D("624"),
                "atc_code": "A02BC01", "registration_number": "BDI-2024-1518",
                "barcode": "6001234500075",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.DRY,
                "storage_notes": "Stocker au sec, sur palette, a l'abri de l'humidite.",
                "notes_fr": "Sphere digestive, rotation reguliere.",
                "notes_en": "Digestive range, steady turnover.",
            },
            {
                "product_code": "MED-0076",
                "name_fr": "DAWASOLONE 5MG", "name_en": "DAWASOLONE 5MG",
                "generic_name": "Prednisolone",
                "brand_name_fr": "DAWASOLONE 5MG", "brand_name_en": "DAWASOLONE 5MG",
                "strength_fr": "5 mg", "strength_en": "5 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "CORTICO", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("1480"), "selling_price": D("2650"),
                "wholesale_price": D("2390"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("49"), "safety_stock": D("20"),
                "max_stock_level": D("294"),
                "atc_code": "H02AB06", "registration_number": "BDI-2025-1525",
                "barcode": "6001234500076",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Corticoide, delivrance encadree.",
                "notes_en": "Corticosteroid, supervised dispensing.",
            },
            {
                "product_code": "MED-0077",
                "name_fr": "DAWASTIN SUS 100 000 UI/ml", "name_en": "DAWASTIN SUS 100 000 UI/ml",
                "generic_name": "Nystatine",
                "brand_name_fr": "DAWASTIN SUS", "brand_name_en": "DAWASTIN SUS",
                "strength_fr": "100 000 UI/ml", "strength_en": "100 000 UI/ml",
                "dosage_form": DosageForm.SUSPENSION, "pack_size": "Flacon de 30 ml",
                "category": "ANTIFONG", "manufacturer": "DAWA", "unit": "FL",
                "unit_cost": D("1090"), "selling_price": D("1960"),
                "wholesale_price": D("1760"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("49"), "safety_stock": D("20"),
                "max_stock_level": D("294"),
                "atc_code": "A07AA02", "registration_number": "BDI-2022-1532",
                "barcode": "6001234500077",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.REFRIGERATED,
                "storage_notes": "Chaine du froid 2-8 C obligatoire.",
                "notes_fr": "Antifongique, forte demande saisonniere.",
                "notes_en": "Antifungal, strong seasonal demand.",
            },
            {
                "product_code": "MED-0078",
                "name_fr": "DEXAMETHASONE INJECTION 4 mg", "name_en": "DEXAMETHASONE INJECTION 4 mg",
                "generic_name": "Dexamethasone",
                "brand_name_fr": "DEXAMETHASONE INJECTION", "brand_name_en": "DEXAMETHASONE INJECTION",
                "strength_fr": "4 mg", "strength_en": "4 mg",
                "dosage_form": DosageForm.INJECTION, "pack_size": "Ampoule injectable",
                "category": "SOLINJ", "manufacturer": "DAWA", "unit": "AMP",
                "unit_cost": D("240"), "selling_price": D("430"),
                "wholesale_price": D("390"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("117"), "safety_stock": D("47"),
                "max_stock_level": D("702"),
                "atc_code": "H02AB02", "registration_number": "BDI-2023-1539",
                "barcode": "6001234500078",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.PROTECT_LIGHT,
                "storage_notes": "Conserver a l'abri de la lumiere, emballage d'origine.",
                "notes_fr": "Injectable hospitalier, tracabilite du lot obligatoire.",
                "notes_en": "Hospital injectable, batch traceability mandatory.",
            },
            {
                "product_code": "MED-0079",
                "name_fr": "EFLARON 125MG SUS 125 mg/5 ml", "name_en": "EFLARON 125MG SUS 125 mg/5 ml",
                "generic_name": "Metronidazole (benzoate)",
                "brand_name_fr": "EFLARON 125MG SUS", "brand_name_en": "EFLARON 125MG SUS",
                "strength_fr": "125 mg/5 ml", "strength_en": "125 mg/5 ml",
                "dosage_form": DosageForm.SUSPENSION, "pack_size": "Flacon de 100 ml",
                "category": "ATB", "manufacturer": "DAWA", "unit": "FL",
                "unit_cost": D("680"), "selling_price": D("1220"),
                "wholesale_price": D("1100"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("104"), "safety_stock": D("42"),
                "max_stock_level": D("624"),
                "atc_code": "P01AB01", "registration_number": "BDI-2024-1546",
                "barcode": "6001234500079",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0080",
                "name_fr": "EFLARON 250MG", "name_en": "EFLARON 250MG",
                "generic_name": "Metronidazole",
                "brand_name_fr": "EFLARON 250MG", "brand_name_en": "EFLARON 250MG",
                "strength_fr": "250 mg", "strength_en": "250 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 1000 comprimes",
                "category": "ATB", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("1480"), "selling_price": D("2650"),
                "wholesale_price": D("2390"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "P01AB01", "registration_number": "BDI-2025-1553",
                "barcode": "6001234500080",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0081",
                "name_fr": "ERYTHROX 500MG", "name_en": "ERYTHROX 500MG",
                "generic_name": "Erythromycine (stearate)",
                "brand_name_fr": "ERYTHROX 500MG", "brand_name_en": "ERYTHROX 500MG",
                "strength_fr": "500 mg", "strength_en": "500 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 x 10 comprimes",
                "category": "ATB", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("14750"), "selling_price": D("26460"),
                "wholesale_price": D("23810"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("14"), "safety_stock": D("6"),
                "max_stock_level": D("84"),
                "atc_code": "J01FA01", "registration_number": "BDI-2022-1560",
                "barcode": "6001234500081",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antibiotique soumis a prescription.",
                "notes_en": "Prescription-only antibiotic.",
            },
            {
                "product_code": "MED-0082",
                "name_fr": "HYDROCHLOROTHIAZIDE 25MG", "name_en": "HYDROCHLOROTHIAZIDE 25MG",
                "generic_name": "Hydrochlorothiazide",
                "brand_name_fr": "HYDROCHLOROTHIAZIDE 25MG", "brand_name_en": "HYDROCHLOROTHIAZIDE 25MG",
                "strength_fr": "25 mg", "strength_en": "25 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 1000 comprimes",
                "category": "CARDIO", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("6490"), "selling_price": D("11640"),
                "wholesale_price": D("10480"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "C03AA03", "registration_number": "BDI-2023-1567",
                "barcode": "6001234500082",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Traitement chronique, dispensation mensuelle.",
                "notes_en": "Chronic treatment, monthly dispensing.",
            },
            {
                "product_code": "MED-0083",
                "name_fr": "IBUPAR TABS 400 mg/325 mg", "name_en": "IBUPAR TABS 400 mg/325 mg",
                "generic_name": "Ibuprofene 400 mg + Paracetamol 325 mg",
                "brand_name_fr": "IBUPAR TABS", "brand_name_en": "IBUPAR TABS",
                "strength_fr": "400 mg/325 mg", "strength_en": "400 mg/325 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 10 comprimes",
                "category": "ANALG", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("1330"), "selling_price": D("2390"),
                "wholesale_price": D("2150"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("16"), "safety_stock": D("6"),
                "max_stock_level": D("96"),
                "atc_code": "M01AE51", "registration_number": "BDI-2024-1574",
                "barcode": "6001234500083",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antalgique de large diffusion, forte rotation.",
                "notes_en": "Widely used analgesic, high turnover.",
            },
            {
                "product_code": "MED-0084",
                "name_fr": "KENAZOLE CREAM 2 % p/p", "name_en": "KENAZOLE CREAM 2 % p/p",
                "generic_name": "Ketoconazole",
                "brand_name_fr": "KENAZOLE CREAM", "brand_name_en": "KENAZOLE CREAM",
                "strength_fr": "2 % p/p", "strength_en": "2 % p/p",
                "dosage_form": DosageForm.CREAM, "pack_size": "Tube de 15 g",
                "category": "ANTIFONG", "manufacturer": "DAWA", "unit": "TUB",
                "unit_cost": D("590"), "selling_price": D("1060"),
                "wholesale_price": D("950"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("37"), "safety_stock": D("15"),
                "max_stock_level": D("222"),
                "atc_code": "D01AC08", "registration_number": "BDI-2025-1581",
                "barcode": "6001234500084",
                "requires_prescription": False, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antifongique, forte demande saisonniere.",
                "notes_en": "Antifungal, strong seasonal demand.",
            },
            {
                "product_code": "MED-0085",
                "name_fr": "LEVOCET-M 5 mg/10 mg", "name_en": "LEVOCET-M 5 mg/10 mg",
                "generic_name": "Levocetirizine 5 mg + Montelukast 10 mg",
                "brand_name_fr": "LEVOCET-M", "brand_name_en": "LEVOCET-M",
                "strength_fr": "5 mg/10 mg", "strength_en": "5 mg/10 mg",
                "dosage_form": DosageForm.TABLET, "pack_size": "Boite de 2 x 10 comprimes",
                "category": "ANTIHIST", "manufacturer": "DAWA", "unit": "BTE",
                "unit_cost": D("1770"), "selling_price": D("3170"),
                "wholesale_price": D("2850"), "vat_rate": D("18"),
                "is_vat_exempt": False,
                "reorder_level": D("10"), "safety_stock": D("4"),
                "max_stock_level": D("60"),
                "atc_code": "R06AE09", "registration_number": "BDI-2022-1588",
                "barcode": "6001234500085",
                "requires_prescription": True, "is_controlled": False,
                "storage_condition": StorageCondition.AMBIENT,
                "storage_notes": "Conserver a moins de 25 C, a l'abri de l'humidite.",
                "notes_fr": "Antihistaminique, vente de comptoir.",
                "notes_en": "Antihistamine, counter sales.",
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
        """
        The six supply lines the catalogue is actually bought from.

        Order matters to the callers below, which index this list: [0] is the
        supplier used for the received order and the dead-stock order, [1] the
        one left awaiting delivery.
        """
        specs = [
            {
                "supplier_code": "FRN-001",
                "name": "Biodeal Laboratories Ltd",
                "nif": "P051234567B",
                "contact_person": "Grace Mukamana",
                "email": "export@biodeal.co.ke",
                "phone": "+254 20 209 1234",
                "address": "Baba Dogo Road, Ruaraka",
                "city": "Nairobi",
                "country": "Kenya",
                "payment_terms": PaymentTerms.NET_30,
                "currency": "BIF",
                "lead_time_days": 21,
                "bank_name": "Kenya Commercial Bank",
                "bank_account": "KE12 3400 0000 9988 7766",
                "swift_code": "KCBLKENX",
                "is_approved": True,
                "approval_notes": "Dossier qualite regional valide le 08/02/2026.",
                "notes": "Generiques et antiparasitaires, delai de 21 jours.",
            },
            {
                "supplier_code": "FRN-002",
                "name": "Laboratory & Allied Ltd",
                "nif": "P051987654L",
                "contact_person": "Peter Wanjohi",
                "email": "orders@laballied.com",
                "phone": "+254 20 650 3300",
                "address": "Mombasa Road, Athi River EPZ",
                "city": "Nairobi",
                "country": "Kenya",
                "payment_terms": PaymentTerms.NET_60,
                "currency": "BIF",
                "lead_time_days": 30,
                "bank_name": "Equity Bank Kenya",
                "bank_account": "KE40 6800 0000 5544 3322",
                "swift_code": "EQBLKENA",
                "is_approved": True,
                "approval_notes": "Certification GMP verifiee le 15/01/2026.",
                "notes": "Gamme la plus large du catalogue, delai de 30 jours.",
            },
            {
                "supplier_code": "FRN-003",
                "name": "Harley's Limited",
                "nif": "P052345678H",
                "contact_person": "Susan Achieng",
                "email": "sales@harleysltd.com",
                "phone": "+254 20 445 5000",
                "address": "Sameer Business Park, Mombasa Road",
                "city": "Nairobi",
                "country": "Kenya",
                "payment_terms": PaymentTerms.NET_45,
                "currency": "BIF",
                "lead_time_days": 28,
                "bank_name": "Standard Chartered Kenya",
                "bank_account": "KE21 0200 0000 7766 5544",
                "swift_code": "SCBLKENX",
                "is_approved": True,
                "approval_notes": "Agrement importateur renouvele le 03/03/2026.",
                "notes": "Distributeur multi-marques, antipaludiques inclus.",
            },
            {
                "supplier_code": "FRN-004",
                "name": "Infinite Health Ltd",
                "nif": "P053456789I",
                "contact_person": "Daniel Kiptoo",
                "email": "procurement@infinitehealth.co.ke",
                "phone": "+254 20 380 7711",
                "address": "Enterprise Road, Industrial Area",
                "city": "Nairobi",
                "country": "Kenya",
                "payment_terms": PaymentTerms.NET_30,
                "currency": "BIF",
                "lead_time_days": 25,
                "bank_name": "Co-operative Bank of Kenya",
                "bank_account": "KE55 1100 0000 3322 1100",
                "swift_code": "KCOOKENA",
                "is_approved": True,
                "approval_notes": "Audit fournisseur realise le 22/11/2025.",
                "notes": "Injectables, solutes et consommables medicaux.",
            },
            {
                "supplier_code": "FRN-005",
                "name": "Dawa Limited",
                "nif": "P054567890D",
                "contact_person": "Mercy Wangui",
                "email": "export@dawalimited.com",
                "phone": "+254 20 802 1000",
                "address": "Kaptagat Road, Kikuyu",
                "city": "Nairobi",
                "country": "Kenya",
                "payment_terms": PaymentTerms.NET_60,
                "currency": "BIF",
                "lead_time_days": 35,
                "bank_name": "Absa Bank Kenya",
                "bank_account": "KE33 0300 0000 1199 8877",
                "swift_code": "BARCKENX",
                "is_approved": True,
                "approval_notes": "Dossier qualite valide le 19/12/2025.",
                "notes": "Fournisseur historique, gamme hospitaliere complete.",
            },
            {
                "supplier_code": "FRN-006",
                "name": "Pharmakina Distribution",
                "nif": "4000123456",
                "contact_person": "Emmanuel Ndikumana",
                "email": "commandes@pharmakina.bi",
                "phone": "+257 22 224 466",
                "address": "45 Boulevard de l'Uprona",
                "city": "Bujumbura",
                "country": "Burundi",
                "payment_terms": PaymentTerms.NET_15,
                "currency": "BIF",
                "lead_time_days": 7,
                "bank_name": "Banque de Credit de Bujumbura",
                "bank_account": "BI43 1010 0000 1234 5678",
                "swift_code": "BCBUBIBI",
                "is_approved": True,
                "approval_notes": "Agrement ministeriel verifie le 12/01/2026.",
                "notes": "Fournisseur local de depannage, delai de 7 jours.",
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
        by_code = {m.product_code: m for m in medicines}

        order = purchasing_services.create_order(
            supplier=supplier,
            warehouse=warehouse,
            lines=[
                {
                    "product": by_code["MED-0001"], "quantity_ordered": D("120"),
                    "unit_cost": D("4500"),
                    # Accepted short-dated: the minimum is set to the date the
                    # supplier could actually deliver. receive_goods refuses a
                    # batch expiring before this, so the two must agree.
                    "expected_expiry_date": self.today + timedelta(days=90),
                    "notes": "Lot court date accepte, rotation rapide.",
                },
                {
                    "product": by_code["MED-0002"], "quantity_ordered": D("80"),
                    "unit_cost": D("7200"),
                    "expected_expiry_date": self.today + timedelta(days=585),
                    "notes": "Antibiotique sur ordonnance.",
                },
                {
                    "product": by_code["MED-0003"], "quantity_ordered": D("100"),
                    "unit_cost": D("9800"),
                    "expected_expiry_date": self.today + timedelta(days=630),
                    "notes": "Programme antipaludique, exonere.",
                    },
                {
                    "product": by_code["MED-0008"], "quantity_ordered": D("90"),
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
        # Expiry horizons are deliberately mixed. Most batches sit comfortably
        # in date, but the first is inside the 180-day alert window so the
        # expiry report and the expiry-scanning task have something real to
        # find — an all-healthy catalogue exercises neither.
        short_dated = {0: 95}
        receipt_lines = []
        for index, line in enumerate(order.lines.all().order_by("line_number")):
            days = short_dated.get(index, 540 + index * 45)
            receipt_lines.append(
                {
                    "purchase_order_line": line,
                    "batch_number": f"LOT-2026-{index + 1:03d}",
                    "expiry_date": self.today + timedelta(days=days),
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

    def _slow_moving_order(self, supplier, warehouse, medicines, actor, approver):
        """
        An old delivery that has never sold: the dead-stock case.

        The receipt is backdated beyond the 180-day no-movement window the
        dead-stock report uses, and nothing below sells these lines. Without
        it that report is permanently empty and the screen looks broken rather
        than healthy.
        """
        by_code = {m.product_code: m for m in medicines}

        order = purchasing_services.create_order(
            supplier=supplier,
            warehouse=warehouse,
            lines=[
                {
                    "product": by_code["MED-0009"], "quantity_ordered": D("40"),
                    "unit_cost": D("5100"),
                    "expected_expiry_date": self.today + timedelta(days=300),
                    "notes": "Sirop antitussif, rotation lente.",
                },
                {
                    "product": by_code["MED-0010"], "quantity_ordered": D("35"),
                    "unit_cost": D("4100"),
                    "expected_expiry_date": self.today + timedelta(days=330),
                    "notes": "Pommade antifongique, rotation lente.",
                },
            ],
            expected_delivery_date=self.today - timedelta(days=220),
            freight_cost=D("40000"),
            customs_duty=D("20000"),
            other_charges=D("8000"),
            supplier_reference="PK-2025-0912",
            notes="Ancien reassort, sans rotation depuis la livraison.",
            actor=actor,
        )
        purchasing_services.submit_for_approval(order, actor=actor)
        purchasing_services.approve_order(
            order, actor=approver, notes="Approuve lors du precedent exercice."
        )
        purchasing_services.mark_sent(order, actor=actor)

        order.refresh_from_db()
        received_at = self.now - timedelta(days=210)
        purchasing_services.receive_goods(
            order,
            lines=[
                {
                    "purchase_order_line": line,
                    "batch_number": f"LOT-2025-{index + 1:03d}",
                    "expiry_date": self.today + timedelta(days=300 + index * 30),
                    "manufacturing_date": self.today - timedelta(days=400),
                    "quantity_received": line.quantity_ordered,
                    "unit_cost": line.unit_cost,
                }
                for index, line in enumerate(order.lines.all().order_by("line_number"))
            ],
            delivery_note_number="BL-2025-0431",
            receipt_date=received_at,
            quality_checked=True,
            quality_notes="Conforme a la reception.",
            actor=actor,
        )
        order.refresh_from_db()
        return order

    def _pending_order(self, supplier, warehouse, medicines, actor, approver):
        """An approved order still awaiting delivery: no stock impact yet."""
        by_code = {m.product_code: m for m in medicines}

        order = purchasing_services.create_order(
            supplier=supplier,
            warehouse=warehouse,
            lines=[
                {
                    "product": by_code["MED-0006"], "quantity_ordered": D("60"),
                    "unit_cost": D("11500"),
                    "expected_expiry_date": self.today + timedelta(days=480),
                    "notes": "Chaine du froid a maintenir a la livraison.",
                },
                {
                    "product": by_code["MED-0004"], "quantity_ordered": D("70"),
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
        by_code = {m.product_code: m for m in medicines}
        receipts = []
        specs = [
            {
                "lines": [
                    {"product": by_code["MED-0001"], "quantity": D("4")},
                    {"product": by_code["MED-0008"], "quantity": D("2")},
                ],
                "tendered": D("50000"),
                "reference": "CAISSE-001",
            },
            {
                "lines": [
                    {"product": by_code["MED-0003"], "quantity": D("3")},
                    {"product": by_code["MED-0002"], "quantity": D("1")},
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
        by_code = {m.product_code: m for m in medicines}
        invoices = []
        # Every line here draws on a medicine the received purchase order
        # actually brought into stock. Selling something with no batch behind
        # it would fail the stock check in confirm_sale, which is the correct
        # behaviour — the fix is to order it, not to skip the line.
        specs = [
            [
                {"product": by_code["MED-0001"], "quantity": D("15")},
                {"product": by_code["MED-0008"], "quantity": D("10")},
            ],
            [
                {"product": by_code["MED-0002"], "quantity": D("12")},
                {"product": by_code["MED-0003"], "quantity": D("20")},
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
