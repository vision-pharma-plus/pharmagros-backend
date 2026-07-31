"""
French translations for the backend catalogue.

Source strings in this codebase are written in English (`_("View medicines")`),
so English is the msgid language and needs no catalogue. French — the
application's *default display* language — is the one requiring translation,
which is the reverse of the usual arrangement and worth stating plainly.

Kept as a Python dict rather than editing django.po by hand so that
re-extraction cannot silently drop work, and so a reviewer can read the
translations as a diffable list. `extract_messages.py` seeds the .po from
here; any msgstr already present in the .po wins, so a translator's manual
edit is never overwritten.

Terminology follows Burundian/OHADA commercial usage:
  batch → lot · invoice → facture · purchase order → bon de commande
  stock movement → mouvement de stock · credit note → note de crédit
"""

TRANSLATIONS: dict[str, str] = {
    # -- Generic field labels ------------------------------------------------
    "created at": "créé le",
    "updated at": "modifié le",
    "deleted at": "supprimé le",
    "code": "code",
    "name": "nom",
    "description": "description",
    "notes": "notes",
    "status": "statut",
    "active": "actif",
    "city": "ville",
    "country": "pays",
    "address": "adresse",
    "email": "e-mail",
    "phone": "téléphone",
    "telephone": "téléphone",
    "website": "site web",
    "quantity": "quantité",
    "date": "date",
    "reason": "motif",
    "user": "utilisateur",
    "users": "utilisateurs",
    "role": "rôle",
    "roles": "rôles",
    "permission": "permission",
    "permissions": "permissions",
    "key": "clé",
    "label": "libellé",
    "prefix": "préfixe",
    "padding": "remplissage",
    "currency": "devise",
    "unit": "unité",
    "type": "type",
    "Type": "Type",
    "Date": "Date",
    "User": "Utilisateur",
    "Page": "Page",
    "Total": "Total",
    "Notes": "Notes",
    "Reason": "Motif",
    "Batch": "Lot",
    "Expiry": "Péremption",
    "Product": "Produit",
    "Quantity": "Quantité",
    "Category": "Catégorie",
    "Supplier": "Fournisseur",
    "Warehouse": "Entrepôt",
    "Customer": "Client",
    "Movement": "Mouvement",
    "Reference": "Référence",
    "Period": "Période",
    "Terms": "Conditions",
    "Payment": "Paiement",
    "Currency": "Devise",
    "Qty": "Qté",
    "Tel": "Tél",
    "NIF": "NIF",
    "RC": "RC",
    "VAT": "TVA",
    "Disc.": "Rem.",
    "Exp.": "Pér.",
    "Cash": "Comptant",
    "Paid": "Réglée",
    "Draft": "Brouillon",
    "Posted": "Validée",
    "Overdue": "En retard",
    "Cancelled": "Annulée",
    "Approved": "Approuvée",
    "Rejected": "Rejetée",
    "Sent": "Envoyée",
    "Received": "Reçue",
    "Closed": "Clôturée",
    "Pending": "En attente",
    "Inactive": "Inactif",
    "Blocked": "Bloqué",
    "Active": "Actif",
    "Expired": "Périmé",
    "Damaged": "Endommagé",
    "Recalled": "Rappelé",
    "Depleted": "Épuisé",
    "Disposed": "Détruit",
    "Quarantined": "En quarantaine",
    "Confirmed": "Confirmée",
    "Delivered": "Livrée",
    "Completed": "Terminée",
    "Returned": "Retournée",
    "Discontinued": "Arrêté",
    "Suspended": "Suspendu",
    "Other": "Autre",
    "Information": "Information",
    "Warning": "Avertissement",
    "Critical": "Critique",

    # -- Apps ----------------------------------------------------------------
    "Core": "Noyau",
    "Medicine catalog": "Catalogue des médicaments",
    "Inventory": "Stock",
    "Sales": "Ventes",
    "Invoicing": "Facturation",
    "Purchasing": "Achats",
    "Customers & suppliers": "Clients et fournisseurs",
    "Reporting": "Rapports",
    "Administration": "Administration",
    "Audit & compliance": "Audit et conformité",

    # -- Audit ---------------------------------------------------------------
    "audit log entry": "entrée du journal d'audit",
    "audit log": "journal d'audit",
    "Create": "Création",
    "Update": "Modification",
    "Delete": "Suppression",
    "Login": "Connexion",
    "Failed login": "Échec de connexion",
    "Logout": "Déconnexion",
    "Account lockout": "Verrouillage de compte",
    "Password change": "Changement de mot de passe",
    "Password reset": "Réinitialisation de mot de passe",
    "Permission change": "Changement de permissions",
    "Approval": "Approbation",
    "Rejection": "Rejet",
    "Cancellation": "Annulation",
    "Posting": "Validation",
    "Print / reprint": "Impression / réimpression",
    "Data export": "Export de données",
    "Stock movement": "Mouvement de stock",
    "Price change": "Changement de prix",

    # -- Numbering -----------------------------------------------------------
    "document sequence": "séquence de numérotation",
    "document sequences": "séquences de numérotation",
    "reset scope": "portée de réinitialisation",
    "current value": "valeur actuelle",
    "Continuous": "Continue",
    "Reset annually": "Réinitialisée chaque année",
    "Reset monthly": "Réinitialisée chaque mois",

    # -- Accounts ------------------------------------------------------------
    "user session": "session utilisateur",
    "user sessions": "sessions utilisateur",
    "password history entry": "entrée d'historique de mot de passe",
    "password history": "historique des mots de passe",
    "email address": "adresse e-mail",
    "first name": "prénom",
    "last name": "nom de famille",
    "employee code": "matricule",
    "job title": "fonction",
    "preferred language": "langue préférée",
    "admin site access": "accès à l'administration",
    "suspended": "suspendu",
    "MFA enabled": "MFA activée",
    "must change password": "doit changer de mot de passe",
    "password changed at": "mot de passe modifié le",
    "system role": "rôle système",
    "inherits from": "hérite de",
    "sensitive": "sensible",
    "module": "module",
    "name (French)": "nom (français)",
    "name (English)": "nom (anglais)",
    "description (French)": "description (français)",
    "description (English)": "description (anglais)",
    # Paired _fr/_en product columns. "Dénomination" is the catalogue's
    # established term for a commercial name, kept consistent here.
    "commercial name (French)": "dénomination commerciale (français)",
    "commercial name (English)": "dénomination commerciale (anglais)",
    "brand name (French)": "nom de marque (français)",
    "brand name (English)": "nom de marque (anglais)",
    "strength (French)": "dosage (français)",
    "strength (English)": "dosage (anglais)",
    "notes (French)": "notes (français)",
    "notes (English)": "notes (anglais)",
    "expires at": "expire le",
    "Role inheritance cannot contain a cycle.":
        "L'héritage des rôles ne peut pas contenir de cycle.",
    "This account is suspended. Please contact your administrator.":
        "Ce compte est suspendu. Veuillez contacter votre administrateur.",
    "The current password is incorrect.":
        "Le mot de passe actuel est incorrect.",
    "A new password is required.": "Un nouveau mot de passe est requis.",
    "Signed out successfully.": "Déconnexion réussie.",
    "Password changed. Please sign in again.":
        "Mot de passe modifié. Veuillez vous reconnecter.",
    "Password reset. You may now sign in.":
        "Mot de passe réinitialisé. Vous pouvez maintenant vous connecter.",
    "Password reset. The user must change it at next sign-in.":
        "Mot de passe réinitialisé. L'utilisateur devra le changer à la prochaine connexion.",
    "If an account exists for that address, a reset link has been sent.":
        "Si un compte existe pour cette adresse, un lien de réinitialisation a été envoyé.",
    "This reset link is invalid.": "Ce lien de réinitialisation est invalide.",
    "This reset link is invalid or has expired.":
        "Ce lien de réinitialisation est invalide ou a expiré.",
    "Too many failed sign-in attempts. Please try again in %(minutes)d minutes.":
        "Trop de tentatives de connexion échouées. Veuillez réessayer dans %(minutes)d minutes.",
    "System roles cannot be deleted. Deactivate the role instead.":
        "Les rôles système ne peuvent pas être supprimés. Désactivez le rôle à la place.",
    "This role is still assigned to users and cannot be deleted.":
        "Ce rôle est encore attribué à des utilisateurs et ne peut pas être supprimé.",
    "One or more of the selected roles do not exist or are inactive.":
        "Un ou plusieurs des rôles sélectionnés n'existent pas ou sont inactifs.",
    "at least %(n)d uppercase letter": "au moins %(n)d majuscule",
    "at least %(n)d lowercase letter": "au moins %(n)d minuscule",
    "at least %(n)d digit": "au moins %(n)d chiffre",
    "at least %(n)d special character": "au moins %(n)d caractère spécial",
    "The password must contain %(requirements)s.":
        "Le mot de passe doit contenir %(requirements)s.",
    "The password must contain at least one uppercase letter, one lowercase letter, one digit and one special character.":
        "Le mot de passe doit contenir au moins une majuscule, une minuscule, un chiffre et un caractère spécial.",
    "This password was used recently. Choose a password you have not used in your last %(n)d passwords.":
        "Ce mot de passe a été utilisé récemment. Choisissez un mot de passe que vous n'avez pas utilisé parmi vos %(n)d derniers.",
    "You cannot reuse any of your last %(n)d passwords.":
        "Vous ne pouvez pas réutiliser vos %(n)d derniers mots de passe.",

    # -- Validators ----------------------------------------------------------
    "Tax identification number (Numéro d'Identification Fiscale).":
        "Numéro d'Identification Fiscale (NIF).",
    "The NIF must contain between %(min)d and %(max)d characters.":
        "Le NIF doit contenir entre %(min)d et %(max)d caractères.",
    "The NIF must contain only letters and digits.":
        "Le NIF ne doit contenir que des lettres et des chiffres.",
    "The NIF format is invalid. Expected format: 4 digits followed by 4 to 8 alphanumeric characters.":
        "Le format du NIF est invalide. Format attendu : 4 chiffres suivis de 4 à 8 caractères alphanumériques.",
    "Enter a valid telephone number in international format, for example +257 68 606 080 or +44 20 7946 0958.":
        "Saisissez un numéro de téléphone valide au format international, par exemple +257 68 606 080 ou +44 20 7946 0958.",
    "The file exceeds the maximum permitted size of %(mb)d MB.":
        "Le fichier dépasse la taille maximale autorisée de %(mb)d Mo.",
    "Files of type '.%(ext)s' are not permitted.":
        "Les fichiers de type « .%(ext)s » ne sont pas autorisés.",
    "The file content does not match its declared type.":
        "Le contenu du fichier ne correspond pas au type déclaré.",

    # -- Core exceptions -----------------------------------------------------
    "The operation violates a business rule.":
        "L'opération enfreint une règle de gestion.",
    "Insufficient stock available to fulfil this request.":
        "Stock insuffisant pour satisfaire cette demande.",
    "This batch has expired and cannot be issued.":
        "Ce lot est périmé et ne peut pas être sorti du stock.",
    "This sale would exceed the customer's credit limit.":
        "Cette vente dépasserait la limite de crédit du client.",
    "This action is not permitted in the document's current state.":
        "Cette action n'est pas autorisée dans l'état actuel du document.",
    "This document has been posted and can no longer be modified.":
        "Ce document a été validé et ne peut plus être modifié.",
    "The submitted data is invalid.": "Les données soumises sont invalides.",
    "The requested resource was not found.":
        "La ressource demandée est introuvable.",
    "You do not have permission to perform this action.":
        "Vous n'avez pas la permission d'effectuer cette action.",
    "The request could not be completed.":
        "La requête n'a pas pu être traitée.",
    "An unexpected error occurred. Please contact support.":
        "Une erreur inattendue est survenue. Veuillez contacter le support.",

    # -- Catalog -------------------------------------------------------------
    "category": "catégorie",
    "categories": "catégories",
    "manufacturer": "fabricant",
    "manufacturers": "fabricants",
    "medicine": "médicament",
    "medicines": "médicaments",
    "unit of measure": "unité de mesure",
    "units of measure": "unités de mesure",
    "price history entry": "entrée d'historique de prix",
    "parent category": "catégorie parente",
    "base unit": "unité de base",
    "units per pack": "unités par conditionnement",
    "product code": "code produit",
    "commercial name": "dénomination commerciale",
    "generic name (INN)": "dénomination commune internationale (DCI)",
    "brand name": "marque",
    "strength": "dosage",
    "dosage form": "forme galénique",
    "pack size": "conditionnement",
    "reference unit cost": "coût unitaire de référence",
    "selling price": "prix de vente",
    "wholesale price": "prix de gros",
    "VAT rate (%)": "taux de TVA (%)",
    "VAT exempt": "exonéré de TVA",
    "reorder level": "seuil de réapprovisionnement",
    "safety stock": "stock de sécurité",
    "maximum stock level": "niveau de stock maximal",
    "expiry alert (days)": "alerte de péremption (jours)",
    "storage condition": "condition de conservation",
    "storage notes": "notes de conservation",
    "prescription required": "sur ordonnance",
    "controlled substance": "substance contrôlée",
    "ATC code": "code ATC",
    "marketing authorisation number": "numéro d'autorisation de mise sur le marché",
    "barcode": "code-barres",
    "previous cost": "coût précédent",
    "new cost": "nouveau coût",
    "previous selling price": "prix de vente précédent",
    "new selling price": "nouveau prix de vente",
    "changed by": "modifié par",
    "effective from": "en vigueur à partir du",
    "Tablet": "Comprimé",
    "Capsule": "Gélule",
    "Syrup": "Sirop",
    "Suspension": "Suspension",
    "Injection": "Injectable",
    "Infusion": "Perfusion",
    "Cream": "Crème",
    "Ointment": "Pommade",
    "Gel": "Gel",
    "Drops": "Gouttes",
    "Suppository": "Suppositoire",
    "Inhaler": "Inhalateur",
    "Patch": "Patch",
    "Powder": "Poudre",
    "Solution": "Solution",
    "Medical device": "Dispositif médical",
    "Consumable": "Consommable",
    "Ambient (15–25 °C)": "Ambiante (15–25 °C)",
    "Cool (8–15 °C)": "Frais (8–15 °C)",
    "Refrigerated (2–8 °C)": "Réfrigéré (2–8 °C)",
    "Frozen (below 0 °C)": "Congelé (sous 0 °C)",
    "Protect from light": "À l'abri de la lumière",
    "Keep dry": "Conserver au sec",
    "Pending approval": "En attente d'approbation",
    "For example: 500 mg, 250 mg/5 ml": "Par exemple : 500 mg, 250 mg/5 ml",
    "For example: box of 100 tablets": "Par exemple : boîte de 100 comprimés",
    "Set to 0 for exempt or zero-rated products.":
        "Mettre à 0 pour les produits exonérés ou à taux zéro.",
    "Many essential medicines are exempt; this overrides the rate.":
        "De nombreux médicaments essentiels sont exonérés ; ce réglage prime sur le taux.",
    "Subject to reinforced traceability requirements.":
        "Soumis à des exigences de traçabilité renforcées.",
    "A category cannot be its own ancestor.":
        "Une catégorie ne peut pas être son propre ancêtre.",
    "Initial pricing on product creation":
        "Tarification initiale à la création du produit",
    "A reason is required for any price change.":
        "Un motif est obligatoire pour tout changement de prix.",
    "The submitted prices are identical to the current ones.":
        "Les prix soumis sont identiques aux prix actuels.",
    "Prices cannot be negative.": "Les prix ne peuvent pas être négatifs.",
    "Unknown product status.": "Statut de produit inconnu.",
    "This product cannot be discontinued while stock remains on hand.":
        "Ce produit ne peut pas être arrêté tant qu'il reste du stock disponible.",

    # -- Inventory -----------------------------------------------------------
    "warehouse": "entrepôt",
    "warehouses": "entrepôts",
    "stock batch": "lot de stock",
    "stock batches": "lots de stock",
    "stock movement": "mouvement de stock",
    "stock movements": "mouvements de stock",
    "stock reservation": "réservation de stock",
    "stock reservations": "réservations de stock",
    "default warehouse": "entrepôt par défaut",
    "cold chain capable": "compatible chaîne du froid",
    "batch number": "numéro de lot",
    "manufacturing date": "date de fabrication",
    "expiry date": "date de péremption",
    "purchase order": "bon de commande",
    "purchase orders": "bons de commande",
    "quantity received": "quantité reçue",
    "quantity remaining": "quantité restante",
    "quantity reserved": "quantité réservée",
    "unit purchase cost": "coût d'achat unitaire",
    "landed unit cost": "coût de revient unitaire",
    "received at": "reçu le",
    "quantity change": "variation de quantité",
    "batch balance after movement": "solde du lot après mouvement",
    "unit cost at movement": "coût unitaire au mouvement",
    "movement value": "valeur du mouvement",
    "source document type": "type de document source",
    "source document id": "identifiant du document source",
    "source reference": "référence source",
    "performed by": "effectué par",
    "performed at": "effectué le",
    "movement type": "type de mouvement",
    "Goods receipt": "Réception de marchandises",
    "Sales issue": "Sortie pour vente",
    "Customer return": "Retour client",
    "Return to supplier": "Retour fournisseur",
    "Transfer out": "Transfert sortant",
    "Transfer in": "Transfert entrant",
    "Positive adjustment": "Ajustement positif",
    "Negative adjustment": "Ajustement négatif",
    "Damage write-off": "Sortie pour dommage",
    "Expiry write-off": "Sortie pour péremption",
    "Disposal": "Destruction",
    "Opening balance": "Solde d'ouverture",
    "Unit cost including freight, duty and clearing charges.":
        "Coût unitaire incluant le fret, les droits de douane et les frais de dédouanement.",
    "The quantity to allocate must be greater than zero.":
        "La quantité à allouer doit être supérieure à zéro.",
    "A stock movement must have a quantity greater than zero.":
        "Un mouvement de stock doit avoir une quantité supérieure à zéro.",
    "The quantity received must be greater than zero.":
        "La quantité reçue doit être supérieure à zéro.",
    "The adjusted quantity cannot be negative.":
        "La quantité ajustée ne peut pas être négative.",
    "A reason is required for every stock adjustment.":
        "Un motif est obligatoire pour tout ajustement de stock.",
    "A reason is required for a stock write-off.":
        "Un motif est obligatoire pour une sortie de stock.",
    "Invalid write-off type.": "Type de sortie invalide.",
    "The source and destination warehouses must differ.":
        "Les entrepôts source et destination doivent être différents.",
    "Insufficient stock for %(product)s: %(requested)s requested, %(available)s available.":
        "Stock insuffisant pour %(product)s : %(requested)s demandés, %(available)s disponibles.",
    "Batch %(batch)s holds %(available)s units; %(requested)s were requested.":
        "Le lot %(batch)s contient %(available)s unités ; %(requested)s ont été demandées.",
    "Batch %(batch)s expired on %(date)s and cannot be received into stock.":
        "Le lot %(batch)s a expiré le %(date)s et ne peut pas être réceptionné en stock.",
    "Batch %(batch)s already exists with expiry %(existing)s, which differs from the %(new)s supplied.":
        "Le lot %(batch)s existe déjà avec la péremption %(existing)s, différente de %(new)s fournie.",
    "Batch %(batch)s has expired; returned units must be quarantined for disposal.":
        "Le lot %(batch)s est périmé ; les unités retournées doivent être mises en quarantaine pour destruction.",
    "Expired batch %(batch)s cannot be transferred; it must be disposed of.":
        "Le lot périmé %(batch)s ne peut pas être transféré ; il doit être détruit.",
    "%(count)d batches expire within %(days)d days":
        "%(count)d lots périment dans %(days)d jours",
    "Stock value at risk: %(value)s BIF. Review the expiry report to prioritise clearance.":
        "Valeur de stock à risque : %(value)s BIF. Consultez le rapport de péremption pour prioriser l'écoulement.",
    "%(count)d batches have expired": "%(count)d lots sont périmés",
    "Value at cost: %(value)s BIF. These batches are quarantined and must be scheduled for destruction.":
        "Valeur au coût : %(value)s BIF. Ces lots sont en quarantaine et doivent être programmés pour destruction.",
    "%(count)d products are out of stock": "%(count)d produits sont en rupture de stock",
    "%(count)d products are below their reorder level":
        "%(count)d produits sont sous leur seuil de réapprovisionnement",
    "Affected products: %(names)s": "Produits concernés : %(names)s",
    "%(count)d stock discrepancies detected":
        "%(count)d écarts de stock détectés",
    "Batch balances disagree with the stock ledger. This indicates a data integrity failure and requires investigation.":
        "Les soldes des lots divergent du journal de stock. Cela indique une défaillance d'intégrité des données et nécessite une investigation.",

    # -- Partners ------------------------------------------------------------
    "customer": "client",
    "customers": "clients",
    "supplier": "fournisseur",
    "suppliers": "fournisseurs",
    "customer contact": "contact client",
    "customer contacts": "contacts clients",
    "credit limit change": "changement de limite de crédit",
    "credit limit changes": "changements de limite de crédit",
    "customer code": "code client",
    "supplier code": "code fournisseur",
    "business name": "raison sociale",
    "trading name": "nom commercial",
    "customer type": "type de client",
    "supplier name": "nom du fournisseur",
    "trade register number": "numéro de registre du commerce",
    "pharmacy licence number": "numéro d'agrément de pharmacie",
    "licence expiry": "expiration de l'agrément",
    "contact person": "personne de contact",
    "alternate telephone": "téléphone secondaire",
    "province": "province",
    "credit limit": "limite de crédit",
    "outstanding balance": "encours",
    "payment terms": "conditions de paiement",
    "credit blocked": "crédit bloqué",
    "reason for credit block": "motif du blocage de crédit",
    "standard discount (%)": "remise standard (%)",
    "first sale": "première vente",
    "last sale": "dernière vente",
    "primary contact": "contact principal",
    "previous limit": "limite précédente",
    "new limit": "nouvelle limite",
    "invoicing currency": "devise de facturation",
    "average lead time (days)": "délai de livraison moyen (jours)",
    "bank": "banque",
    "bank account": "compte bancaire",
    "SWIFT / BIC": "SWIFT / BIC",
    "approved supplier": "fournisseur agréé",
    "approval notes": "notes d'agrément",
    "Pharmacy": "Pharmacie",
    "Hospital": "Hôpital",
    "Clinic": "Clinique",
    "Health centre": "Centre de santé",
    "NGO / humanitarian organisation": "ONG / organisation humanitaire",
    "Government / public sector": "Secteur public / gouvernement",
    "Other wholesaler": "Autre grossiste",
    "Cash on delivery": "Paiement à la livraison",
    "7 days": "7 jours",
    "15 days": "15 jours",
    "30 days": "30 jours",
    "45 days": "45 jours",
    "60 days": "60 jours",
    "90 days": "90 jours",
    "Maximum outstanding balance permitted. Zero means cash only.":
        "Encours maximal autorisé. Zéro signifie paiement comptant uniquement.",
    "Blocks all credit sales regardless of the available limit.":
        "Bloque toutes les ventes à crédit quelle que soit la limite disponible.",
    "Operating licence issued by the Ministry of Health.":
        "Agrément d'exploitation délivré par le Ministère de la Santé.",
    "Currency in which this supplier invoices (BIF, USD, EUR).":
        "Devise dans laquelle ce fournisseur facture (BIF, USD, EUR).",
    "Used to compute reorder timing.":
        "Utilisé pour calculer le moment du réapprovisionnement.",
    "Only approved suppliers may be selected on a purchase order.":
        "Seuls les fournisseurs agréés peuvent être sélectionnés sur un bon de commande.",
    "The customer account is not active.": "Le compte client n'est pas actif.",
    "Credit is blocked for this customer: %(reason)s":
        "Le crédit est bloqué pour ce client : %(reason)s",
    "no reason recorded": "aucun motif enregistré",
    "This customer is configured for cash payment only.":
        "Ce client est configuré pour le paiement comptant uniquement.",
    "No credit limit has been set for this customer.":
        "Aucune limite de crédit n'a été définie pour ce client.",
    "Credit limit exceeded: balance %(balance)s + %(amount)s exceeds the limit of %(limit)s BIF.":
        "Limite de crédit dépassée : encours %(balance)s + %(amount)s dépasse la limite de %(limit)s BIF.",
    "A NIF is required for any customer with credit payment terms.":
        "Un NIF est obligatoire pour tout client bénéficiant de conditions de crédit.",
    "A reason is required for any credit limit change.":
        "Un motif est obligatoire pour tout changement de limite de crédit.",
    "A credit limit cannot be negative.":
        "Une limite de crédit ne peut pas être négative.",
    "The submitted credit limit is identical to the current one.":
        "La limite de crédit soumise est identique à la limite actuelle.",
    "A reason is required to block credit.":
        "Un motif est obligatoire pour bloquer le crédit.",
    "Initial credit limit set on account creation":
        "Limite de crédit initiale définie à la création du compte",

    # -- Sales ---------------------------------------------------------------
    "sale": "vente",
    "sales": "ventes",
    "sale line": "ligne de vente",
    "sale lines": "lignes de vente",
    "sale return": "retour de vente",
    "sale returns": "retours de vente",
    "sale return line": "ligne de retour de vente",
    "sale return lines": "lignes de retour de vente",
    "sale line batch allocation": "affectation de lot sur ligne de vente",
    "sale line batch allocations": "affectations de lots sur lignes de vente",
    "sale number": "numéro de vente",
    "sale type": "type de vente",
    "sale date": "date de vente",
    "Cash sale": "Vente au comptant",
    "Credit sale": "Vente à crédit",
    "salesperson": "vendeur",
    "delivery address": "adresse de livraison",
    "delivery note": "bon de livraison",
    "customer order reference": "référence de commande client",
    "credit override authorised by": "dérogation de crédit autorisée par",
    "credit override reason": "motif de la dérogation de crédit",
    "confirmed at": "confirmée le",
    "cancelled at": "annulée le",
    "cancellation reason": "motif d'annulation",
    "global discount (%)": "remise globale (%)",
    "total cost of goods sold": "coût total des marchandises vendues",
    "quantity returned": "quantité retournée",
    "unit price": "prix unitaire",
    "line total": "total de la ligne",
    "unit cost": "coût unitaire",
    "line cost": "coût de la ligne",
    "original sale": "vente d'origine",
    "return number": "numéro de retour",
    "return date": "date de retour",
    "refunded amount": "montant remboursé",
    "credit note": "note de crédit",
    "processed by": "traité par",
    "original line": "ligne d'origine",
    "refund": "remboursement",
    "return to sellable stock": "remise en stock vendable",
    "condition on return": "état à la réception",
    "return": "retour",
    "Only when storage conditions were verifiably maintained.":
        "Uniquement si les conditions de conservation ont été démontrablement maintenues.",
    "Comma-separated batches issued for this line.":
        "Lots sortis pour cette ligne, séparés par des virgules.",
    "A sale must contain at least one line.":
        "Une vente doit contenir au moins une ligne.",
    "A sale cannot be confirmed without lines.":
        "Une vente ne peut pas être confirmée sans lignes.",
    "Line quantities must be greater than zero.":
        "Les quantités des lignes doivent être supérieures à zéro.",
    "Only a draft sale can be confirmed; this one is %(status)s.":
        "Seule une vente en brouillon peut être confirmée ; celle-ci est %(status)s.",
    "A credit limit override must identify the authorising user.":
        "Une dérogation à la limite de crédit doit identifier l'utilisateur qui l'autorise.",
    "A reason is required to cancel a sale.":
        "Un motif est obligatoire pour annuler une vente.",
    "This sale has already been cancelled or fully returned.":
        "Cette vente a déjà été annulée ou entièrement retournée.",
    "A reason is required to process a return.":
        "Un motif est obligatoire pour traiter un retour.",
    "Returns can only be processed against a confirmed sale.":
        "Les retours ne peuvent être traités que sur une vente confirmée.",
    "A returned quantity must be greater than zero.":
        "Une quantité retournée doit être supérieure à zéro.",
    "Cannot return %(qty)s of %(product)s: only %(max)s remain returnable on this line.":
        "Impossible de retourner %(qty)s de %(product)s : seules %(max)s restent retournables sur cette ligne.",
    "No batch allocation was found for this sale line.":
        "Aucune affectation de lot n'a été trouvée pour cette ligne de vente.",
    "The operating licence for %(name)s expired on %(date)s. The sale cannot proceed until it is renewed.":
        "L'agrément d'exploitation de %(name)s a expiré le %(date)s. La vente ne peut pas être effectuée avant son renouvellement.",

    # -- Invoicing -----------------------------------------------------------
    "invoice": "facture",
    "invoices": "factures",
    "invoice line": "ligne de facture",
    "invoice lines": "lignes de facture",
    "payment": "paiement",
    "payments": "paiements",
    "payment allocation": "affectation de paiement",
    "payment allocations": "affectations de paiement",
    "invoice number": "numéro de facture",
    "invoice date": "date de facture",
    "due date": "date d'échéance",
    "document type": "type de document",
    "customer name": "nom du client",
    "customer NIF": "NIF du client",
    "customer address": "adresse du client",
    "customer telephone": "téléphone du client",
    "payment terms (days)": "conditions de paiement (jours)",
    "subtotal before discount": "sous-total avant remise",
    "discount": "remise",
    "taxable base": "base imposable",
    "amount paid": "montant réglé",
    "balance due": "solde dû",
    "credit sale": "vente à crédit",
    "external reference": "référence externe",
    "internal notes": "notes internes",
    "posted at": "validée le",
    "posted by": "validée par",
    "cancelled by": "annulée par",
    "times printed": "nombre d'impressions",
    "last printed": "dernière impression",
    "emailed at": "envoyée par e-mail le",
    "original invoice": "facture d'origine",
    "batch numbers": "numéros de lot",
    "expiry dates": "dates de péremption",
    "unit cost at sale": "coût unitaire à la vente",
    "payment reference": "référence du paiement",
    "payment date": "date du paiement",
    "amount": "montant",
    "allocated amount": "montant affecté",
    "payment method": "mode de paiement",
    "bank / transaction reference": "référence bancaire / de transaction",
    "received by": "reçu par",
    "reversed": "extourné",
    "reversal reason": "motif d'extourne",
    "Proforma invoice": "Facture proforma",
    "Credit note": "Note de crédit",
    "Debit note": "Note de débit",
    "Bank transfer": "Virement bancaire",
    "Cheque": "Chèque",
    "Mobile money": "Mobile money",
    "Bank card": "Carte bancaire",
    "Credit note offset": "Compensation par note de crédit",
    "Partially paid": "Partiellement réglée",
    "Not printed on the customer-facing document.":
        "Non imprimé sur le document destiné au client.",
    "Cheque number, transfer reference or mobile money code.":
        "Numéro de chèque, référence de virement ou code mobile money.",
    "An invoice must contain at least one line.":
        "Une facture doit contenir au moins une ligne.",
    "An invoice cannot be posted without lines.":
        "Une facture ne peut pas être validée sans lignes.",
    "Unit prices cannot be negative.":
        "Les prix unitaires ne peuvent pas être négatifs.",
    "A discount must be between 0 and 100 percent.":
        "Une remise doit être comprise entre 0 et 100 pour cent.",
    "Only a draft invoice can be posted; this one is %(status)s.":
        "Seule une facture en brouillon peut être validée ; celle-ci est %(status)s.",
    "A NIF is required to post a credit invoice.":
        "Un NIF est obligatoire pour valider une facture à crédit.",
    "Invoice %(number)s is %(status)s and can no longer be modified. Issue a credit note instead.":
        "La facture %(number)s est %(status)s et ne peut plus être modifiée. Émettez plutôt une note de crédit.",
    "A reason is required to cancel an invoice.":
        "Un motif est obligatoire pour annuler une facture.",
    "This invoice has already been cancelled.":
        "Cette facture a déjà été annulée.",
    "This invoice has received payments totalling %(paid)s BIF. Reverse them before cancelling.":
        "Cette facture a reçu des paiements totalisant %(paid)s BIF. Extournez-les avant l'annulation.",
    "A payment amount must be greater than zero.":
        "Un montant de paiement doit être supérieur à zéro.",
    "A reason is required to reverse a payment.":
        "Un motif est obligatoire pour extourner un paiement.",
    "This payment has already been reversed.":
        "Ce paiement a déjà été extourné.",
    "A reason is required to issue a credit note.":
        "Un motif est obligatoire pour émettre une note de crédit.",
    "A credit note can only be issued against a posted invoice.":
        "Une note de crédit ne peut être émise que sur une facture validée.",
    "This customer has no email address on file.":
        "Ce client n'a pas d'adresse e-mail enregistrée.",
    "%(company)s — Invoice %(number)s": "%(company)s — Facture %(number)s",
    "%(company)s — Password reset":
        "%(company)s — Réinitialisation du mot de passe",
    "%(count)d overdue invoices": "%(count)d factures en retard",
    "Total overdue: %(total)s BIF. Review the receivables ageing report.":
        "Total en retard : %(total)s BIF. Consultez la balance âgée clients.",
    "%(count)d invoices due within 7 days":
        "%(count)d factures échéant dans les 7 jours",
    "Contact customers to arrange settlement.":
        "Contactez les clients pour organiser le règlement.",

    # -- Invoice PDF ---------------------------------------------------------
    "Invoice": "Facture",
    "Proforma": "Proforma",
    "Billed to": "Facturé à",
    "Due date": "Date d'échéance",
    "Your reference": "Votre référence",
    "Designation": "Désignation",
    "Unit price": "Prix unitaire",
    "Subtotal": "Sous-total",
    "Discount": "Remise",
    "Taxable base": "Base imposable",
    "TOTAL": "TOTAL",
    "Amount paid": "Montant réglé",
    "Balance due": "Solde dû",
    "Amount in words": "Arrêté à la somme de",
    "Trade register": "Registre du commerce",
    "Credit — %(days)s days": "Crédit — %(days)s jours",
    "Correcting invoice": "Facture rectifiée",
    "Exempt": "Exonéré",
    "DUPLICATE": "DUPLICATA",
    "CANCELLED": "ANNULÉE",
    "Duplicate - copy no. %(n)s": "Duplicata - copie n° %(n)s",
    "Payment due by %(date)s. Late payment may incur interest at the legal rate.":
        "Paiement dû au plus tard le %(date)s. Tout retard peut donner lieu à des intérêts au taux légal.",
    "Goods sold are not returnable except by prior agreement and subject to storage conditions having been maintained.":
        "Les marchandises vendues ne sont pas reprises sauf accord préalable et sous réserve du maintien des conditions de conservation.",

    # -- Invoice PDF: OBR/EBMS layout ---------------------------------------
    # Column headers are upper-case on the fiscal document, matching the
    # layout the revenue authority's own certified invoices use.
    "INVOICE": "FACTURE",
    "CREDIT NOTE": "NOTE DE CRÉDIT",
    "DEBIT NOTE": "NOTE DE DÉBIT",
    "PROFORMA": "PROFORMA",
    "CODE": "CODE",
    "DESIGNATION": "DÉSIGNATION",
    "BATCH": "LOT",
    "EXP.": "PÉR.",
    "QTY": "QTÉ",
    "U.P. EXCL.": "P.U. HT",
    "U.P. INCL.": "P.U. TTC",
    "TAX": "TAX",
    "REF": "RÉF",
    "CASH": "COMPTANT",
    "CREDIT — %(days)s days": "CRÉDIT — %(days)s jours",
    "Bujumbura, on %(date)s": "Bujumbura, le %(date)s",
    "Liable for VAT": "Assujetti à la TVA",
    "YES": "OUI",
    "NO": "NON",
    "TOTAL A-EX": "TOTAL A-EX",
    "TOTAL B 18%": "TOTAL B 18%",
    "TOTAL C 0%": "TOTAL C 0%",
    "TOTAL TAX B": "TOTAL TAXE B",
    "TOTAL TAX": "TOTAL TAXE",
    "BANK DETAILS": "COORDONNÉES BANCAIRES",
    "SDC INFORMATION": "INFORMATIONS SDC",
    "TIME SDC": "HEURE SDC",
    "SDC ID": "ID SDC",
    "Signature": "Signature",
    "Invoice Signature": "Signature de la facture",
    "RECEIPT NUMBER": "NUMÉRO DE REÇU",
    "The Supplier": "Le Fournisseur",
    "The Recipient": "Le Destinataire",
    "Stamp": "Cachet",

    # -- Invoice PDF: Vision Pharma Plus layout ------------------------------
    # Header, customer block and signature row of the printed document.
    # Several labels are already French on the source document and are kept
    # verbatim so the printed invoice matches the pre-printed stationery.
    "Invoice N°": "Facture N°",
    "Dated": "Date",
    "TEL": "TEL",
    "Email": "Email",
    "Tax centre": "Centre Fiscal",
    "Sector of activity": "Secteur d'activité",
    "Legal form": "Forme juridique",
    "B.": "B.",
    "CUSTOMER NAME": "NOM DU CLIENT",
    "ADDRESS": "ADRESSE",
    "SECTOR OF ACTIVITY": "SECTEUR D'ACTIVITE",
    "LIABLE": "ASSUJETTI",
    "N": "N",
    "DATE": "DATE",
    "QTE": "QTE",
    "PU": "PU",
    "PT": "PT",
    "TOT": "TOT",
    "Amount chargeable (in words)": "Montant à payer (en lettres)",
    "Transporteur": "Transporteur",
    "Destinateur": "Destinateur",
    "Expediteur": "Expediteur",
    "Time": "Heure",

    # -- Purchasing ----------------------------------------------------------
    "purchase order line": "ligne de bon de commande",
    "purchase order lines": "lignes de bon de commande",
    "goods receipt": "bon de réception",
    "goods receipts": "bons de réception",
    "goods receipt line": "ligne de bon de réception",
    "goods receipt lines": "lignes de bon de réception",
    "order number": "numéro de commande",
    "order date": "date de commande",
    "expected delivery": "livraison prévue",
    "actual delivery": "livraison effective",
    "delivery warehouse": "entrepôt de livraison",
    "freight": "fret",
    "customs duty": "droits de douane",
    "other charges": "autres frais",
    "exchange rate to BIF": "taux de change vers le BIF",
    "supplier reference": "référence fournisseur",
    "supplier invoice number": "numéro de facture fournisseur",
    "supplier invoice date": "date de facture fournisseur",
    "requested by": "demandé par",
    "submitted at": "soumis le",
    "approved by": "approuvé par",
    "approved at": "approuvé le",
    "rejection reason": "motif de rejet",
    "sent to supplier at": "envoyé au fournisseur le",
    "quantity ordered": "quantité commandée",
    "expected expiry": "péremption attendue",
    "receipt number": "numéro de réception",
    "receipt date": "date de réception",
    "supplier delivery note": "bon de livraison fournisseur",
    "quality checked": "contrôle qualité effectué",
    "quality checked by": "contrôle qualité effectué par",
    "quality notes": "notes de contrôle qualité",
    "batch created": "lot créé",
    "quantity rejected": "quantité refusée",
    "Sent to supplier": "Envoyée au fournisseur",
    "Partially received": "Partiellement reçue",
    "Fully received": "Entièrement reçue",
    "Rate applied when the supplier invoices in foreign currency.":
        "Taux appliqué lorsque le fournisseur facture en devise étrangère.",
    "Minimum acceptable expiry date for this line.":
        "Date de péremption minimale acceptable pour cette ligne.",
    "A purchase order must contain at least one line.":
        "Un bon de commande doit contenir au moins une ligne.",
    "An order cannot be submitted without lines.":
        "Une commande ne peut pas être soumise sans lignes.",
    "Ordered quantities must be greater than zero.":
        "Les quantités commandées doivent être supérieures à zéro.",
    "%(name)s is not an approved supplier. Approve the supplier before ordering.":
        "%(name)s n'est pas un fournisseur agréé. Agréez le fournisseur avant de commander.",
    "Only a draft or rejected order can be submitted for approval.":
        "Seule une commande en brouillon ou rejetée peut être soumise à approbation.",
    "Only an order pending approval can be approved.":
        "Seule une commande en attente d'approbation peut être approuvée.",
    "Only an order pending approval can be rejected.":
        "Seule une commande en attente d'approbation peut être rejetée.",
    "Only an approved order can be sent to a supplier.":
        "Seule une commande approuvée peut être envoyée à un fournisseur.",
    "An approving user must be identified.":
        "Un utilisateur approbateur doit être identifié.",
    "A reason is required to reject an order.":
        "Un motif est obligatoire pour rejeter une commande.",
    "A reason is required to cancel an order.":
        "Un motif est obligatoire pour annuler une commande.",
    "This order cannot be cancelled in its current state.":
        "Cette commande ne peut pas être annulée dans son état actuel.",
    "Goods have already been received against this order. Close it short instead of cancelling.":
        "Des marchandises ont déjà été reçues sur cette commande. Clôturez-la partiellement au lieu de l'annuler.",
    "A goods receipt must contain at least one line.":
        "Un bon de réception doit contenir au moins une ligne.",
    "Received quantities must be greater than zero.":
        "Les quantités reçues doivent être supérieures à zéro.",
    "Goods cannot be received against an order with status %(status)s.":
        "Aucune marchandise ne peut être reçue sur une commande au statut %(status)s.",
    "Cannot receive %(qty)s of %(product)s: only %(max)s remain outstanding on this order line.":
        "Impossible de recevoir %(qty)s de %(product)s : seules %(max)s restent en attente sur cette ligne.",
    "Batch %(batch)s expires on %(actual)s, before the minimum acceptable date of %(expected)s for this line.":
        "Le lot %(batch)s périme le %(actual)s, avant la date minimale acceptable du %(expected)s pour cette ligne.",
    "You cannot approve a purchase order that you raised yourself. Approval must come from a different user.":
        "Vous ne pouvez pas approuver un bon de commande que vous avez vous-même créé. L'approbation doit provenir d'un autre utilisateur.",
    "Purchase order awaiting approval":
        "Bon de commande en attente d'approbation",
    "Order %(number)s for %(supplier)s (%(amount)s BIF) requires approval.":
        "La commande %(number)s pour %(supplier)s (%(amount)s BIF) requiert une approbation.",

    # -- Notifications -------------------------------------------------------
    "notification": "notification",
    "notifications": "notifications",
    "announcement": "annonce",
    "announcements": "annonces",
    "recipient": "destinataire",
    "severity": "gravité",
    "title": "titre",
    "body": "corps",
    "link": "lien",
    "read at": "lu le",
    "dismissed at": "masqué le",
    "title (French)": "titre (français)",
    "title (English)": "titre (anglais)",
    "body (French)": "corps (français)",
    "body (English)": "corps (anglais)",
    "target roles": "rôles ciblés",
    "visible from": "visible à partir du",
    "visible until": "visible jusqu'au",
    "published": "publiée",
    "Low stock": "Stock faible",
    "Out of stock": "Rupture de stock",
    "Expiring medicines": "Médicaments proches de la péremption",
    "Expired medicines": "Médicaments périmés",
    "Purchase order approval request":
        "Demande d'approbation de bon de commande",
    "Purchase order approved": "Bon de commande approuvé",
    "Purchase order rejected": "Bon de commande rejeté",
    "Stock discrepancy": "Écart de stock",
    "Invoice due": "Facture à échéance",
    "Invoice overdue": "Facture en retard",
    "Credit limit reached": "Limite de crédit atteinte",
    "Audit integrity failure": "Défaillance d'intégrité de l'audit",
    "Customer licence expiring": "Agrément client proche de l'expiration",
    "System announcement": "Annonce système",
    "Frontend route the notification points to.":
        "Route frontend vers laquelle pointe la notification.",
    "Leave empty to broadcast to every user.":
        "Laisser vide pour diffuser à tous les utilisateurs.",

    # -- Reporting -----------------------------------------------------------
    "Product code": "Code produit",
    "Days to expiry": "Jours avant péremption",
    "Unit cost (BIF)": "Coût unitaire (BIF)",
    "Value (BIF)": "Valeur (BIF)",
    "Value at risk (BIF)": "Valeur à risque (BIF)",
    "Revenue (BIF)": "Chiffre d'affaires (BIF)",
    "VAT (BIF)": "TVA (BIF)",
    "Net revenue (BIF)": "Chiffre d'affaires net (BIF)",
    "Discounts (BIF)": "Remises (BIF)",
    "Cost (BIF)": "Coût (BIF)",
    "Margin (BIF)": "Marge (BIF)",
    "Credit limit (BIF)": "Limite de crédit (BIF)",
    "Not yet due (BIF)": "Non échu (BIF)",
    "1–30 days (BIF)": "1–30 jours (BIF)",
    "31–60 days (BIF)": "31–60 jours (BIF)",
    "61–90 days (BIF)": "61–90 jours (BIF)",
    "Over 90 days (BIF)": "Plus de 90 jours (BIF)",
    "Total (BIF)": "Total (BIF)",
    # -- Gross trading result (P&L export) ------------------------------------
    "Gross revenue (BIF)": "Chiffre d'affaires brut (BIF)",
    "VAT collected (BIF)": "TVA collectée (BIF)",
    "Cost of goods sold (BIF)": "Coût des marchandises vendues (BIF)",
    "Gross profit (BIF)": "Marge brute (BIF)",
    "Gross margin (%)": "Taux de marge brute (%)",
    "Discounts granted (BIF)": "Remises accordées (BIF)",
    "Gross trading result": "Résultat brut",
    "Gross margin only; operating expenses are not tracked.":
        "Marge brute uniquement ; les charges d'exploitation ne sont pas suivies.",
    "Item": "Poste",
    "Amount": "Montant",
    # -- PDF report header ----------------------------------------------------
    "Generated": "Généré le",
    "Lines": "Lignes",
    "From": "Du",
    "To": "Au",
    "Note": "Note",
    "No data for the selected filters.":
        "Aucune donnée pour les filtres sélectionnés.",
    "Transactions": "Transactions",
    "Balance": "Solde",
    "Received": "Reçu",
    "Inventory valuation": "Valorisation du stock",
    "Expiry report": "Rapport de péremption",
    "Stock movements": "Mouvements de stock",
    "Dead stock": "Stock dormant",
    "Sales report": "Rapport de ventes",
    "Receivables ageing": "Balance âgée clients",
    "Compliance report": "Rapport de conformité",
    "Total value": "Valeur totale",
    "Batches": "Lots",
    "Horizon (days)": "Horizon (jours)",
    "Total value at risk": "Valeur totale à risque",
    "Total outstanding": "Encours total",

    # -- Remaining field labels ---------------------------------------------
    "price history": "historique des prix",
    "product": "produit",
    "batch": "lot",
    "receipt": "réception",
    "order line": "ligne de commande",
    "line number": "numéro de ligne",
    "discount (%)": "remise (%)",
    "subtotal": "sous-total",
    "total": "total",
    "Customer code": "Code client",
    "Partially returned": "Partiellement retournée",
    "Price applied to institutional customers, if different.":
        "Prix appliqué aux clients institutionnels, s'il diffère.",

    # -- blocktrans bodies ---------------------------------------------------
    # These are extracted with their template variables intact, so the
    # placeholders must be reproduced verbatim in the translation or Django
    # will fall back to the English source at render time.
    "Credit — {{ days }} days": "Crédit — {{ days }} jours",
    "Payment due by {{ date }}. Late payment may incur interest at the legal rate.":
        "Paiement dû au plus tard le {{ date }}. Tout retard peut donner lieu à des intérêts au taux légal.",
    "Duplicate - copy no. {{ n }}": "Duplicata - copie n° {{ n }}",

    # -- App verbose names already written in French -------------------------
    # `apps.py` declares these in French, so they are their own translation;
    # listing them keeps the catalogue at 100% rather than leaving apparent
    # gaps a translator would waste time investigating.
    "Comptes et sécurité": "Comptes et sécurité",
    "Catalogue des médicaments": "Catalogue des médicaments",
    "Gestion des stocks": "Gestion des stocks",
    "Clients et fournisseurs": "Clients et fournisseurs",
    "Facturation": "Facturation",
    "Achats": "Achats",
    "Ventes": "Ventes",
    "Rapports": "Rapports",
    "Notifications": "Notifications",

    # Column header for a line-number column; identical in both languages.
    "#": "#",
}
