"""
Database-enforced immutability for the audit trail.

Application code can be bypassed — a data migration, a psql session, a
future maintainer using .update(). These triggers make UPDATE and DELETE on
core_auditlog fail at the database level regardless of the path taken.

Note on privilege: a superuser can still drop these triggers. That is why the
hash chain exists alongside them — the triggers stop accidents and ordinary
misuse; the chain makes deliberate tampering *detectable*.
"""

from django.db import migrations

# The '%%' below is deliberate. It is a PL/pgSQL RAISE placeholder that must
# reach the server as a single '%', but psycopg parses the statement for its
# own parameter placeholders first and rejects a lone '%'. Doubling it escapes
# it at that layer; the function body Postgres finally stores contains '%'.
FORWARD = """
CREATE OR REPLACE FUNCTION core_auditlog_block_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'core_auditlog is append-only: %% is not permitted on audit records',
        TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS core_auditlog_no_update ON core_auditlog;
CREATE TRIGGER core_auditlog_no_update
    BEFORE UPDATE ON core_auditlog
    FOR EACH ROW EXECUTE FUNCTION core_auditlog_block_mutation();

DROP TRIGGER IF EXISTS core_auditlog_no_delete ON core_auditlog;
CREATE TRIGGER core_auditlog_no_delete
    BEFORE DELETE ON core_auditlog
    FOR EACH ROW EXECUTE FUNCTION core_auditlog_block_mutation();

DROP TRIGGER IF EXISTS core_auditlog_no_truncate ON core_auditlog;
CREATE TRIGGER core_auditlog_no_truncate
    BEFORE TRUNCATE ON core_auditlog
    FOR EACH STATEMENT EXECUTE FUNCTION core_auditlog_block_mutation();
"""

REVERSE = """
DROP TRIGGER IF EXISTS core_auditlog_no_update ON core_auditlog;
DROP TRIGGER IF EXISTS core_auditlog_no_delete ON core_auditlog;
DROP TRIGGER IF EXISTS core_auditlog_no_truncate ON core_auditlog;
DROP FUNCTION IF EXISTS core_auditlog_block_mutation();
"""


def apply_triggers(apps, schema_editor):
    """
    Install the triggers, but only on PostgreSQL.

    SQLite is used for offline `check`/`makemigrations` and for fast unit
    tests of pure logic; it has no PL/pgSQL. Skipping there keeps those
    workflows usable while production — which is always PostgreSQL — gets the
    real enforcement. Integration tests run against PostgreSQL specifically so
    this protection is exercised before release.
    """
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(FORWARD)


def remove_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(REVERSE)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunPython(apply_triggers, remove_triggers)]
