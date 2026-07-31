"""
Apply the permission catalogue and default roles from apps.accounts.rbac.

Idempotent: safe to re-run on every deploy, which is how new permissions
introduced by a release reach existing installations.

Deliberately additive for roles that already exist — it will grant newly
declared permissions but will not revoke permissions an administrator added
by hand, unless --prune is passed. Silently stripping a customised role
during a routine deploy would be a nasty surprise.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Permission, Role
from apps.accounts.rbac import PERMISSIONS, ROLES


class Command(BaseCommand):
    help = "Seed RBAC permissions and default roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Remove permissions from system roles that are no longer declared in rbac.py.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        prune = options["prune"]

        created_perms = updated_perms = 0
        for code, name_fr, name_en, module, sensitive in PERMISSIONS:
            _, created = Permission.objects.update_or_create(
                code=code,
                defaults={
                    "name_fr": name_fr,
                    "name_en": name_en,
                    "module": module,
                    "is_sensitive": sensitive,
                },
            )
            if created:
                created_perms += 1
            else:
                updated_perms += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Permissions: {created_perms} created, {updated_perms} updated "
                f"({Permission.objects.count()} total)."
            )
        )

        # Pass 1: create/update the roles themselves.
        for spec in ROLES:
            role, created = Role.objects.update_or_create(
                code=spec["code"],
                defaults={
                    "name_fr": spec["name_fr"],
                    "name_en": spec["name_en"],
                    "description_fr": spec.get("description_fr", ""),
                    "description_en": spec.get("description_en", ""),
                    "is_system": True,
                    "is_active": True,
                },
            )

            perms = Permission.objects.filter(code__in=spec["permissions"])
            missing = set(spec["permissions"]) - set(perms.values_list("code", flat=True))
            if missing:
                # A typo in rbac.py would otherwise silently under-grant.
                raise ValueError(
                    f"Role '{spec['code']}' references undeclared permissions: {sorted(missing)}"
                )

            if prune:
                role.permissions.set(perms)
            else:
                role.permissions.add(*perms)

            verb = "created" if created else "updated"
            self.stdout.write(f"  Role {role.code}: {verb}, {perms.count()} permissions granted.")

        # Pass 2: wire inheritance once every role exists.
        for spec in ROLES:
            parent_code = spec.get("inherits")
            if not parent_code:
                continue
            role = Role.objects.get(code=spec["code"])
            role.inherits_from = Role.objects.get(code=parent_code)
            role.save(update_fields=["inherits_from"])
            self.stdout.write(f"  Role {role.code} inherits from {parent_code}.")

        self.stdout.write(self.style.SUCCESS(f"RBAC seeded: {Role.objects.count()} roles."))
