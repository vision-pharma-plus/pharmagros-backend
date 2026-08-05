"""
The permission catalogue and default role definitions.

This module IS the permission matrix — it is the single source of truth that
`manage.py seed_rbac` applies and that docs/permission-matrix.md is generated
from. Keeping it as code rather than fixtures or hand-maintained documentation
means the matrix cannot drift away from what the system actually enforces.

Design principle: least privilege. Roles start from what the job requires,
not from "administrator minus a few things".
"""

from __future__ import annotations

from .models import PermissionModule as M

# (code, name_fr, name_en, module, is_sensitive)
PERMISSIONS: list[tuple[str, str, str, str, bool]] = [
    # -- Catalog ------------------------------------------------------------
    ("catalog.view_medicine", "Consulter les médicaments", "View medicines", M.CATALOG, False),
    ("catalog.add_medicine", "Créer un médicament", "Create medicine", M.CATALOG, False),
    ("catalog.change_medicine", "Modifier un médicament", "Edit medicine", M.CATALOG, False),
    ("catalog.delete_medicine", "Supprimer un médicament", "Delete medicine", M.CATALOG, True),
    ("catalog.change_price", "Modifier les prix", "Change prices", M.CATALOG, True),
    ("catalog.manage_categories", "Gérer les catégories", "Manage categories", M.CATALOG, False),
    ("catalog.manage_manufacturers", "Gérer les fabricants", "Manage manufacturers", M.CATALOG, False),

    # -- Inventory ----------------------------------------------------------
    ("inventory.view_stock", "Consulter le stock", "View stock", M.INVENTORY, False),
    ("inventory.view_batch", "Consulter les lots", "View batches", M.INVENTORY, False),
    ("inventory.receive_stock", "Réceptionner du stock", "Receive stock", M.INVENTORY, False),
    ("inventory.issue_stock", "Sortir du stock", "Issue stock", M.INVENTORY, False),
    ("inventory.transfer_stock", "Transférer du stock", "Transfer stock", M.INVENTORY, False),
    ("inventory.adjust_stock", "Ajuster le stock", "Adjust stock", M.INVENTORY, True),
    ("inventory.approve_adjustment", "Approuver un ajustement", "Approve adjustment", M.INVENTORY, True),
    ("inventory.dispose_stock", "Détruire du stock périmé", "Dispose expired stock", M.INVENTORY, True),
    ("inventory.view_valuation", "Consulter la valorisation", "View valuation", M.INVENTORY, True),
    ("inventory.manage_warehouse", "Gérer les entrepôts", "Manage warehouses", M.INVENTORY, False),
    ("inventory.override_fifo", "Déroger au FIFO", "Override FIFO allocation", M.INVENTORY, True),

    # -- Sales --------------------------------------------------------------
    ("sales.view_sale", "Consulter les ventes", "View sales", M.SALES, False),
    ("sales.add_sale", "Créer une vente", "Create sale", M.SALES, False),
    ("sales.change_sale", "Modifier une vente", "Edit sale", M.SALES, False),
    ("sales.cancel_sale", "Annuler une vente", "Cancel sale", M.SALES, True),
    ("sales.sell_on_credit", "Vendre à crédit", "Sell on credit", M.SALES, True),
    ("sales.apply_discount", "Appliquer une remise", "Apply discount", M.SALES, True),
    # Granting a discount above MAX_MANUAL_DISCOUNT_PERCENT. Held only by
    # management roles: the ceiling exists precisely so that the one number an
    # operator can change to reduce revenue cannot be moved without authority.
    ("sales.override_discount_limit", "Déroger au plafond de remise", "Override discount limit", M.SALES, True),
    ("sales.override_credit_limit", "Déroger à la limite de crédit", "Override credit limit", M.SALES, True),
    ("sales.process_return", "Traiter un retour", "Process return", M.SALES, True),
    ("sales.view_margin", "Consulter les marges", "View margins", M.SALES, True),
    ("sales.print_receipt", "Imprimer un reçu de vente", "Print sales receipt", M.SALES, False),
    # Raising an invoice for an already-receipted cash sale. Sensitive because
    # it puts a second document for one transaction into the customer's hands,
    # and a fiscal one at that.
    ("sales.invoice_receipt", "Facturer un reçu de vente", "Invoice a sales receipt", M.SALES, True),

    # -- Invoicing ----------------------------------------------------------
    ("invoicing.view_invoice", "Consulter les factures", "View invoices", M.INVOICING, False),
    ("invoicing.add_invoice", "Créer une facture", "Create invoice", M.INVOICING, False),
    ("invoicing.post_invoice", "Valider une facture", "Post invoice", M.INVOICING, True),
    ("invoicing.cancel_invoice", "Annuler une facture", "Cancel invoice", M.INVOICING, True),
    ("invoicing.print_invoice", "Imprimer une facture", "Print invoice", M.INVOICING, False),
    ("invoicing.email_invoice", "Envoyer une facture par email", "Email invoice", M.INVOICING, False),
    ("invoicing.record_payment", "Enregistrer un paiement", "Record payment", M.INVOICING, True),
    ("invoicing.issue_credit_note", "Émettre une note de crédit", "Issue credit note", M.INVOICING, True),
    ("invoicing.issue_debit_note", "Émettre une note de débit", "Issue debit note", M.INVOICING, True),
    ("invoicing.declare_invoice", "Déclarer une facture à l'OBR", "Declare invoice to OBR", M.INVOICING, True),

    # -- Purchasing ---------------------------------------------------------
    ("purchasing.view_order", "Consulter les commandes", "View purchase orders", M.PURCHASING, False),
    ("purchasing.add_order", "Créer une commande", "Create purchase order", M.PURCHASING, False),
    ("purchasing.change_order", "Modifier une commande", "Edit purchase order", M.PURCHASING, False),
    ("purchasing.submit_order", "Soumettre pour approbation", "Submit for approval", M.PURCHASING, False),
    ("purchasing.approve_order", "Approuver une commande", "Approve purchase order", M.PURCHASING, True),
    ("purchasing.cancel_order", "Annuler une commande", "Cancel purchase order", M.PURCHASING, True),
    ("purchasing.receive_goods", "Réceptionner des marchandises", "Receive goods", M.PURCHASING, False),
    ("purchasing.record_supplier_invoice", "Enregistrer une facture fournisseur", "Record supplier invoice", M.PURCHASING, True),
    ("purchasing.view_supplier_invoice", "Consulter les factures fournisseurs", "View supplier invoices", M.PURCHASING, False),
    # Paying a supplier moves money out of the business, so it is separated
    # from recording what is owed: the person who enters an invoice should not
    # also be the one who settles it.
    ("purchasing.view_supplier_payment", "Consulter les paiements fournisseurs", "View supplier payments", M.PURCHASING, False),
    ("purchasing.record_supplier_payment", "Enregistrer un paiement fournisseur", "Record supplier payment", M.PURCHASING, True),
    ("purchasing.reverse_supplier_payment", "Annuler un paiement fournisseur", "Reverse supplier payment", M.PURCHASING, True),

    # -- Accounting ---------------------------------------------------------
    ("accounting.view_expense", "Consulter les dépenses", "View expenses", M.ACCOUNTING, False),
    ("accounting.add_expense", "Enregistrer une dépense", "Record expense", M.ACCOUNTING, False),
    ("accounting.change_expense", "Modifier une dépense", "Edit expense", M.ACCOUNTING, True),
    ("accounting.delete_expense", "Supprimer une dépense", "Delete expense", M.ACCOUNTING, True),
    ("accounting.approve_expense", "Approuver une dépense", "Approve expense", M.ACCOUNTING, True),
    ("accounting.manage_expense_categories", "Gérer les catégories de dépenses", "Manage expense categories", M.ACCOUNTING, False),
    ("accounting.view_financial_overview", "Consulter la synthèse financière", "View financial overview", M.ACCOUNTING, True),
    ("accounting.view_reports", "Consulter les rapports comptables", "View accounting reports", M.ACCOUNTING, True),

    # -- Partners -----------------------------------------------------------
    ("partners.view_customer", "Consulter les clients", "View customers", M.PARTNERS, False),
    ("partners.add_customer", "Créer un client", "Create customer", M.PARTNERS, False),
    ("partners.change_customer", "Modifier un client", "Edit customer", M.PARTNERS, False),
    ("partners.delete_customer", "Supprimer un client", "Delete customer", M.PARTNERS, True),
    ("partners.set_credit_limit", "Définir la limite de crédit", "Set credit limit", M.PARTNERS, True),
    ("partners.view_statement", "Consulter les relevés clients", "View customer statements", M.PARTNERS, False),
    ("partners.view_supplier", "Consulter les fournisseurs", "View suppliers", M.PARTNERS, False),
    ("partners.add_supplier", "Créer un fournisseur", "Create supplier", M.PARTNERS, False),
    ("partners.change_supplier", "Modifier un fournisseur", "Edit supplier", M.PARTNERS, False),
    ("partners.delete_supplier", "Supprimer un fournisseur", "Delete supplier", M.PARTNERS, True),

    # -- Reporting ----------------------------------------------------------
    ("reporting.view_dashboard", "Consulter le tableau de bord", "View dashboard", M.REPORTING, False),
    ("reporting.view_inventory_reports", "Rapports d'inventaire", "Inventory reports", M.REPORTING, False),
    ("reporting.view_sales_reports", "Rapports de ventes", "Sales reports", M.REPORTING, False),
    ("reporting.view_financial_reports", "Rapports financiers", "Financial reports", M.REPORTING, True),
    ("reporting.view_compliance_reports", "Rapports de conformité", "Compliance reports", M.REPORTING, True),
    ("reporting.export_data", "Exporter des données", "Export data", M.REPORTING, True),

    # -- Administration -----------------------------------------------------
    ("accounts.view_user", "Consulter les utilisateurs", "View users", M.ADMINISTRATION, False),
    ("accounts.add_user", "Créer un utilisateur", "Create user", M.ADMINISTRATION, True),
    ("accounts.change_user", "Modifier un utilisateur", "Edit user", M.ADMINISTRATION, True),
    ("accounts.deactivate_user", "Désactiver un utilisateur", "Deactivate user", M.ADMINISTRATION, True),
    ("accounts.reset_user_password", "Réinitialiser un mot de passe", "Reset user password", M.ADMINISTRATION, True),
    ("accounts.manage_roles", "Gérer les rôles", "Manage roles", M.ADMINISTRATION, True),
    ("accounts.assign_roles", "Attribuer des rôles", "Assign roles", M.ADMINISTRATION, True),
    ("accounts.view_sessions", "Consulter les sessions", "View sessions", M.ADMINISTRATION, True),
    ("accounts.revoke_sessions", "Révoquer des sessions", "Revoke sessions", M.ADMINISTRATION, True),
    ("core.change_documentsequence", "Configurer les numérotations", "Configure numbering", M.ADMINISTRATION, True),
    ("core.view_documentsequence", "Consulter les numérotations", "View numbering", M.ADMINISTRATION, False),
    ("notifications.manage_announcements", "Gérer les annonces", "Manage announcements", M.ADMINISTRATION, False),

    # -- Audit --------------------------------------------------------------
    ("core.view_auditlog", "Consulter le journal d'audit", "View audit log", M.AUDIT, False),
    ("core.verify_auditlog", "Vérifier l'intégrité de l'audit", "Verify audit integrity", M.AUDIT, True),
    ("core.export_auditlog", "Exporter le journal d'audit", "Export audit log", M.AUDIT, True),
]

ALL_CODES = [p[0] for p in PERMISSIONS]


def _codes_for_modules(*modules: str) -> list[str]:
    return [p[0] for p in PERMISSIONS if p[3] in modules]


def _readonly_codes() -> list[str]:
    """Every 'view_' capability, for the Auditor role."""
    return [c for c in ALL_CODES if ".view_" in c]


# ---------------------------------------------------------------------------
# Default roles
#
# `inherits` names a parent role code; effective permissions are the union
# along the chain. The hierarchy encodes real escalation of trust:
#
#   Technician  -> Pharmacist                 (clinical/approval authority)
#   Inventory Officer -> Store Manager        (oversight/approval authority)
#   Auditor                                   (orthogonal: read-only, no writes)
#   Administrator                             (full surface)
# ---------------------------------------------------------------------------
ROLES: list[dict] = [
    {
        "code": "system-administrator",
        "name_fr": "Administrateur Système",
        "name_en": "System Administrator",
        "description_fr": "Accès complet au système, y compris la configuration et la gestion des utilisateurs.",
        "description_en": "Full system access including configuration and user management.",
        "inherits": None,
        "permissions": ALL_CODES,
    },
    {
        "code": "pharmacy-technician",
        "name_fr": "Technicien en Pharmacie",
        "name_en": "Pharmacy Technician",
        "description_fr": "Opérations courantes de vente et de stock, sans approbation ni remise.",
        "description_en": "Day-to-day sales and stock operations, without approval or discount authority.",
        "inherits": None,
        "permissions": [
            "catalog.view_medicine",
            "inventory.view_stock",
            "inventory.view_batch",
            "inventory.issue_stock",
            "sales.view_sale",
            "sales.add_sale",
            # A technician takes counter sales, so they must be able to hand
            # the customer the receipt that closes one.
            "sales.print_receipt",
            "invoicing.view_invoice",
            "invoicing.add_invoice",
            "invoicing.print_invoice",
            "partners.view_customer",
            "reporting.view_dashboard",
        ],
    },
    {
        "code": "pharmacist",
        "name_fr": "Pharmacien",
        "name_en": "Pharmacist",
        "description_fr": "Gestion des médicaments, du stock, des ventes et des approbations pharmaceutiques.",
        "description_en": "Manages medicines, stock, sales and pharmaceutical approvals.",
        "inherits": "pharmacy-technician",
        "permissions": [
            "catalog.add_medicine",
            "catalog.change_medicine",
            "catalog.manage_categories",
            "catalog.manage_manufacturers",
            "inventory.receive_stock",
            "inventory.adjust_stock",
            "inventory.dispose_stock",
            "inventory.transfer_stock",
            "sales.change_sale",
            "sales.sell_on_credit",
            "sales.apply_discount",
            "sales.process_return",
            "sales.cancel_sale",
            "sales.print_receipt",
            "sales.invoice_receipt",
            "invoicing.post_invoice",
            "invoicing.email_invoice",
            "invoicing.record_payment",
            "invoicing.issue_credit_note",
            "partners.add_customer",
            "partners.change_customer",
            "partners.view_statement",
            "partners.view_supplier",
            "purchasing.view_order",
            "purchasing.add_order",
            "purchasing.submit_order",
            "purchasing.view_supplier_invoice",
            "reporting.view_inventory_reports",
            "reporting.view_sales_reports",
            # A pharmacist runs the counter day to day and books the small
            # operating costs that arise there; approving them and seeing the
            # consolidated financial picture stay with management.
            "accounting.view_expense",
            "accounting.add_expense",
        ],
    },
    {
        "code": "inventory-officer",
        "name_fr": "Agent d'Inventaire",
        "name_en": "Inventory Officer",
        "description_fr": "Réception, transferts et ajustements de stock.",
        "description_en": "Stock receiving, transfers and adjustments.",
        "inherits": None,
        "permissions": [
            "catalog.view_medicine",
            "inventory.view_stock",
            "inventory.view_batch",
            "inventory.receive_stock",
            "inventory.issue_stock",
            "inventory.transfer_stock",
            "inventory.adjust_stock",
            "purchasing.view_order",
            "purchasing.receive_goods",
            "partners.view_supplier",
            "reporting.view_dashboard",
            "reporting.view_inventory_reports",
        ],
    },
    {
        "code": "store-manager",
        "name_fr": "Responsable de Magasin",
        "name_en": "Store Manager",
        "description_fr": "Supervision du stock, approbations et rapports opérationnels.",
        "description_en": "Inventory oversight, approvals and operational reporting.",
        "inherits": "inventory-officer",
        "permissions": [
            "inventory.approve_adjustment",
            "inventory.dispose_stock",
            "inventory.view_valuation",
            "inventory.manage_warehouse",
            "purchasing.add_order",
            "purchasing.change_order",
            "purchasing.submit_order",
            "purchasing.approve_order",
            "purchasing.cancel_order",
            "purchasing.record_supplier_invoice",
            "purchasing.view_supplier_invoice",
            "purchasing.view_supplier_payment",
            "purchasing.record_supplier_payment",
            "purchasing.reverse_supplier_payment",
            "partners.add_supplier",
            "partners.change_supplier",
            "sales.view_sale",
            "sales.view_margin",
            # The manager half of the discount control: authority to grant more
            # than the standing ceiling, which a technician or pharmacist
            # cannot. See settings.MAX_MANUAL_DISCOUNT_PERCENT.
            "sales.apply_discount",
            "sales.override_discount_limit",
            # Expenses, supplier settlement and the consolidated view of money
            # leaving the business all sit with the same oversight role.
            "accounting.view_expense",
            "accounting.add_expense",
            "accounting.change_expense",
            "accounting.delete_expense",
            "accounting.approve_expense",
            "accounting.manage_expense_categories",
            "accounting.view_financial_overview",
            "accounting.view_reports",
            # Re-declaring a document the OBR refused is a fiscal correction,
            # so it sits with financial oversight rather than the counter.
            "invoicing.declare_invoice",
            "reporting.view_sales_reports",
            "reporting.view_financial_reports",
            "reporting.export_data",
        ],
    },
    {
        "code": "auditor",
        "name_fr": "Auditeur",
        "name_en": "Auditor",
        "description_fr": "Accès en lecture seule aux rapports et au journal d'audit. Aucune écriture possible.",
        "description_en": "Read-only access to reports and the audit log. No write access whatsoever.",
        "inherits": None,
        # Read-only by construction: derived from view_* codes plus the audit
        # and reporting surface. An auditor who could alter records would
        # defeat the purpose of the role.
        "permissions": sorted(
            set(_readonly_codes())
            | {
                "core.view_auditlog",
                "core.verify_auditlog",
                "core.export_auditlog",
                "reporting.view_dashboard",
                "reporting.view_inventory_reports",
                "reporting.view_sales_reports",
                "reporting.view_financial_reports",
                "reporting.view_compliance_reports",
                "reporting.export_data",
                "inventory.view_valuation",
                "sales.view_margin",
                "partners.view_statement",
                # Money leaving the business is squarely within an audit remit;
                # these are read-only surfaces, so they carry no write risk.
                "accounting.view_financial_overview",
                "accounting.view_reports",
            }
        ),
    },
]
