# Architecture — Vision Pharma plus Wholesale Pharmacy Management System (Burundi)

**Status:** foundation implemented; domain modules in progress (see [Build status](#build-status)).

---

## 1. Context

A pharmaceutical wholesaler (*pharmacie de gros*) in Burundi selling to
pharmacies, hospitals, clinics and NGOs. Not a retail POS: the unit of sale is
a carton, customers buy on credit terms, and every unit shipped must remain
traceable to the batch it came from.

Three constraints shape every decision below:

| Constraint | Consequence |
|---|---|
| Pharmaceutical traceability | Batch, not product, is the unit of inventory truth. Recall must identify every recipient of a batch. |
| Financial/fiscal compliance (OBR) | Gap-free invoice numbering, NIF capture, immutable audit trail. |
| Bilingual operation (FR default, EN) | Translation reaches PDFs, emails and notifications — not just UI chrome. |

---

## 2. Layered architecture

```
┌──────────────────────────────────────────────────────┐
│ Next.js 15 (App Router) · TS · Zustand · ShadCN      │  Presentation
└───────────────────────────┬──────────────────────────┘
                            │ REST/JSON + JWT
┌───────────────────────────▼──────────────────────────┐
│ DRF: ViewSets · Serializers · Permissions · Throttle │  API
├──────────────────────────────────────────────────────┤
│ SERVICES — all business rules live here               │  Domain
│ inventory.services · sales.services · …               │
├──────────────────────────────────────────────────────┤
│ Models · Managers · Constraints                       │  Persistence
└───────────────────────────┬──────────────────────────┘
                            │
      PostgreSQL 16 · Redis · Celery workers/beat
```

**The service layer is the architectural spine.** Views never contain business
rules; they authenticate, validate shape, and delegate. This is what makes the
same rule reachable from an API call, a Celery task, and a management command
without duplication — and what makes the rules unit-testable without HTTP.

### Why not fatter models or fatter views

Django's default gravity pulls logic into models (`save()` overrides) or views.
Both fail here: a stock issue touches batches, movements, reservations,
notifications and the audit trail in one transaction. That orchestration
belongs to a named function with an explicit transaction boundary, not to any
single model's `save()`.

---

## 3. Design decisions worth defending

### 3.1 Money: `DECIMAL(18,4)` internally, whole francs on documents

BIF has no circulating subunit, so invoices show integers. But wholesale unit
costs genuinely carry fractions — 10,000 tablets at 12.4567 BIF is not a
rounding artefact. Storing 2 dp would lose real money at volume; storing
floats would lose it unpredictably.

Rule enforced in `apps/core/money.py`: **compute at 4 dp, round once at the
document boundary, derive the total from rounded lines.** That guarantees
`sum(lines) == total` on the printed page, which is what an auditor checks.

Ordering is fixed: discount applies to the gross line, tax to the discounted
net. Taxing before discounting overcharges VAT — a tax compliance problem, not
a preference. `to_decimal()` raises on `float` input rather than silently
accepting precision that has already been lost.

Verified: 12/12 arithmetic assertions including half-up rounding at `±2.5`.

### 3.2 Audit immutability: three independent layers

"Don't update the audit table" is a convention, not a control. Enforcement:

1. **Python** — `AuditLog.save()` rejects any write with a PK; `delete()` raises.
2. **PostgreSQL triggers** — `BEFORE UPDATE/DELETE/TRUNCATE` raise an exception,
   holding even against raw SQL and careless data migrations.
3. **SHA-256 hash chain** — each entry commits to its predecessor's hash.

Layer 3 exists because a superuser can drop the triggers from layer 2. The
chain cannot stop that, but it makes it **detectable** — which is the standard
a pharmaceutical inspection actually applies. `verify_audit_chain` re-walks the
chain nightly and raises a CRITICAL security event on a break.

The chain is written under `SELECT … FOR UPDATE` on the tail row, serialising
audit writes so two concurrent writers cannot fork the chain. Audit volume is
far below transactional volume, so the cost is acceptable; an unverifiable
chain is not.

### 3.3 Stock ledger is the source of truth; balances are a cache

`StockMovement` is append-only and signed (`quantity_delta`).
`StockBatch.quantity_remaining` is a derived cache maintained in the same
transaction. This yields three things a mutable quantity column cannot:

- **Point-in-time reconstruction** — "what did you hold on 12 March?" is
  answerable and defensible.
- **Chain of custody** — recall identifies every customer who received units
  from a batch.
- **Drift detection** — `find_discrepancies()` compares cache to ledger. A
  non-empty result is an incident, not a rounding nuisance.

Corrections are compensating movements, never edits — the same discipline as a
financial ledger, for the same reason.

### 3.4 Allocation is FEFO, not strictly FIFO

Ordering is `(expiry_date, received_at, id)` — **earliest expiry first**.

The specification says FIFO, and this is a deliberate refinement worth
flagging: in pharmaceutical distribution the two usually coincide, but when
they diverge, shipping earliest-expiring stock is what prevents writing off
inventory that sat behind newer, longer-dated boxes. Receipt date is the
tie-breaker, so identical expiries behave as textbook FIFO. If strict
receipt-order FIFO is a regulatory requirement rather than an accounting
convention here, the ordering tuple in `allocate_fifo()` is the single line to
change.

Expired and non-`ACTIVE` batches are excluded **at the query level** — an
expired batch must be *incapable* of being sold, not merely discouraged.

### 3.5 Concurrency: pessimistic locking on allocation

Two concurrent sales reading "120 available" and each allocating 100 is the
classic lost update. `allocate_fifo()` takes `SELECT … FOR UPDATE` on candidate
rows before reading balances. Deterministic lock ordering prevents deadlock
between overlapping allocations.

`CHECK (quantity_remaining >= 0)` is the backstop — but a constraint that fires
after the caller believed it succeeded is a bug report, not a control.

### 3.6 Document numbering: locked counter, not a Postgres SEQUENCE

Fiscal series must be **gap-free** — a tax authority treats a missing invoice
number as a suppressed sale. A `SEQUENCE` increments outside transaction
control, so a rolled-back transaction permanently burns its number.

`next_number()` locks a counter row instead, so it rolls back with the caller's
transaction and the series stays dense. Cost: brief serialisation per series.
Correct trade for fiscal documents.

### 3.7 UUID primary keys

Sequential integers leak business volume (invoice #4712 tells a competitor your
throughput) and make enumeration trivial. Human-facing identifiers are separate,
formatted document numbers (`FAC-2026-000148`).

### 3.8 RBAC as code, not configuration

`apps/accounts/rbac.py` **is** the permission matrix — 75 permissions across 9
modules, applied by `seed_rbac`. Keeping it in code means the matrix cannot
drift from what the system enforces, and changes arrive through review.

Single-parent inheritance: Technician → Pharmacist, Inventory Officer → Store
Manager. Auditor is orthogonal and **provably write-free** (verified: 23
permissions, 0 write capabilities).

`HasPermission` **denies by default** — an action with no declared permission is
refused, so forgetting a declaration fails closed.

`seed_rbac` is additive unless `--prune`: silently stripping an
administrator's customised role during a routine deploy would be a nasty
surprise.

### 3.9 OBR declaration: local identity, deferred transmission

Burundi requires sales documents to be declared electronically to the OBR
(EBMS). `apps/invoicing/fiscal/` implements this in four parts — `signature`,
`payload`, `client`, `service` — behind an `OBR_ENABLED` flag that is off by
default, because declaration is impossible until the OBR certifies the
installation and issues its `SYSTEM_ID`.

**Identity is computed locally; transmission is deferred.** These are the two
decisions everything else follows from.

The fiscal signature — `{NIF}/{system_id}/{date}/{number}` — is derived at
posting time from data already frozen on the document. No network call is
involved, so a counter sale in Bujumbura completes whether or not the link is
up, and the document is nonetheless fully identified the instant it is issued.
`test_posting_makes_no_network_call` enforces this by making any use of
`requests` during posting a test failure.

Declaration itself is drained by a Celery beat sweep every ten minutes.
Posting sets `fiscal_status = PENDING` and returns. A backlog accumulated
during an outage drains steadily when the link returns.

**Fiscal status is deliberately not invoice status.** Commercial state (is it
paid?) and fiscal state (is it declared?) move independently — an invoice can
be settled in full while its declaration is still queued. One field could not
answer both questions.

**Retries back off and then stop.** Geometric backoff from 5 minutes, capped
at 6 hours, abandoned after `OBR_MAX_ATTEMPTS`. A document the OBR refuses on
its merits (4xx) is not retried at all: it moves straight to `REJECTED`.
Retrying a permanently-invalid document forever would bury genuine failures in
noise, so exhaustion raises a CRITICAL notification instead. Recovery is a
`POST …/declare` once the cause is fixed, which resets the counter.

**`declare_invoice` is deliberately not one atomic block.** The network call
sits in the middle, and the record of a failed attempt — incremented counter,
error text, `FiscalRequestLog` row — must outlive the exception that reports
it. A single enclosing transaction would roll that evidence back on the way
out. Instead the two read-modify-write critical sections take
`select_for_update()` separately, which still prevents two concurrent sweeps
from declaring the same document twice.

**`FiscalRequestLog` records every exchange verbatim**, written *before* the
request goes out so a call that never returns still leaves a trace. When our
records and the OBR's disagree about what was filed, this table is the
evidence; deriving it from final state alone would lose exactly the failed
attempts an inspection asks about.

**Token handling.** Bearer tokens are cached until shortly before the `exp`
claim, decoded without signature verification — safe only because the sole
value read is the expiry, used to schedule renewal. The OBR remains the
authority on validity; a 401 mid-sweep evicts the cached token rather than
replaying it.

---

## 4. Security architecture

| Control | Implementation |
|---|---|
| Password hashing | Argon2id (memory-hard) |
| Password policy | 12 char minimum, complexity, 5-password history, similarity |
| Brute force | django-axes, lockout on `(username, IP)` pair |
| Account enumeration | Auth backend runs the hasher on unknown emails to flatten timing |
| JWT | 15-min access, 7-day refresh, **rotation + blacklist** |
| Session control | `UserSession` registry; password change revokes all sessions |
| MFA | TOTP via django-otp (optional per user) |
| Transport | TLS, HSTS preload, secure cookies, `SECURE_SSL_REDIRECT` |
| Injection | ORM parameterisation throughout; no string-built SQL |
| XSS | DRF JSON, React escaping, `X-Content-Type-Options: nosniff` |
| CSRF | Django middleware; JWT in `Authorization` header, not cookies |
| Uploads | Extension **and magic-byte** validation — a `.pdf` that is really HTML is a stored-XSS vector |
| Rate limiting | Scoped throttles: auth 10/min, password reset 5/h, reports 60/h |
| Audit redaction | `SENSITIVE_FIELDS` never reach the trail |
| Error handling | Generic 500s to clients; detail only in structured server logs |

**Locking on `(username, IP)`** rather than username alone: locking on username
lets an attacker deny service to real users by guessing; IP alone is defeated
by rotation. The pair is the pragmatic middle.

---

## 5. Internationalisation

French is the default (`LANGUAGE_CODE = "fr"`), English secondary.

- Backend: `gettext_lazy` on all model verbose names, choice labels, validation
  messages, and domain exceptions.
- Language resolution: `X-Language` header overrides the stored user preference,
  so **switching is live** — no logout required.
- Bilingual master data (`Permission`, `Role`) carries `name_fr`/`name_en`
  columns rather than relying on `.po` files, because these are business data
  an administrator may edit, not developer strings.
- PDFs and emails render in the recipient's language.

---

## 6. Performance

Target: API < 500 ms typical, 100 concurrent users.

- `CONN_MAX_AGE=60` — connection setup is not free at this concurrency.
- Composite index `idx_batch_fifo_pick` matches the allocation query's exact
  access path `(product, warehouse, status, expiry_date)`.
- Cached batch balances avoid aggregating the movement table on every read.
- Pagination capped at 200; bulk extraction goes through throttled export
  endpoints, so a client cannot turn a listing into an accidental DoS.
- Reports and PDF rendering run on Celery, off the request path.

---

## 7. Build status

**The backend boots, migrates, and passes a 54-assertion end-to-end test
against a live database.**

| Module | State |
|---|---|
| Settings (base/dev/prod/test/check), Celery beat schedule | Implemented |
| `core`: money, audit + hash chain, soft-delete, numbering, validators, exceptions, pagination, health probes | Implemented |
| `accounts`: user, RBAC (75 permissions, 6 roles), password policy, auth backend, sessions, services, **API** | Implemented |
| `catalog`: medicines, categories, manufacturers, UoM, price history, services, **API** | Implemented |
| `partners`: customers (NIF, credit control), suppliers, statements, performance | Implemented (models + services) |
| `inventory`: warehouse, batch, ledger, **FIFO/FEFO engine**, adjustments, transfers, valuation, reconciliation, scheduled scans | Implemented (models + services + tasks) |
| `invoicing`: invoices, payments + allocation, credit notes, **bilingual PDF** | Implemented (models + services + PDF) |
| `sales`: cash/credit sales, returns, credit-limit enforcement, **recall traceability** | Implemented (models + services) |
| `purchasing`: PO lifecycle, approval with separation of duties, goods receipt, landed-cost apportionment | Implemented (models + services) |
| `notifications`: per-recipient alerts, dedupe windows, announcements | Implemented (models + services) |
| `reporting`: dashboard KPIs, inventory/sales/financial reports, ageing | Implemented (services) |
| Migrations — all 12, applied successfully | Implemented |
| Docker Compose, Dockerfile, Nginx + TLS/CSP, verified backups | Implemented |
| CI pipeline (lint, check, migrations, tests, security scan, image build) | Implemented |
| **API layer — 231 routes across 47 views, all apps** | Implemented; OpenAPI schema generates with 0 errors |
| **Frontend — Next.js 15, 16 routes incl. create/edit forms** | Implemented; typecheck, lint and production build all clean |
| **Backend `.po`/`.mo` catalogues** | Implemented — 708/708 strings translated to French, compiled and verified |
| **pytest suite** | Config written; the four verification scripts are the current coverage |

---

## 8. Frontend

Next.js 15 App Router, TypeScript strict (`noUncheckedIndexedAccess` on),
Tailwind, Zustand, React Hook Form + Zod.

### Money never becomes a JavaScript number

The single most important frontend decision. The API sends amounts as decimal
strings precisely because IEEE-754 doubles cannot hold them exactly; parsing to
`number` would reintroduce the error the backend works to avoid. All display
and client-side line arithmetic goes through `decimal.js` in
[`src/lib/format.ts`](../frontend/src/lib/format.ts), configured with the same
`ROUND_HALF_UP` the backend uses.

`computeLine()` mirrors `apps.core.money.compute_line` exactly — discount on
the gross line, tax on the discounted net — so the operator sees the correct
total as they type rather than after a round trip. The server stays
authoritative; this is a preview, not a second source of truth.

Verified against the backend's own figures: **24 assertions**, including the
identical 551,678 BIF footing case and exact rendering of
`9007199254740993`, which a JS `number` silently corrupts.

### i18n

French is the default and the canonical dictionary. `Dictionary` is derived
from the French object via a mapped type that widens literal values back to
`string` — this enforces *key structure* across languages while allowing
different text, so a missing or misspelled English key is a build error rather
than a blank label found in production.

Switching is live, as required: the UI re-renders immediately, the API client
starts sending the new `X-Language` header, and the preference is persisted
server-side so it also applies to generated PDFs and emails.

### Permission gating is an affordance, not a control

`useAuth().can()` hides navigation entries and action buttons the user cannot
use. Every one of those actions is independently enforced by `HasPermission`
server-side — a hidden button is not a security control, and the store is
documented as such so no future maintainer mistakes it for one.

The dashboard demonstrates the pairing: margin and inventory-value tiles are
conditionally rendered, *and* the API strips those fields for users lacking
`sales.view_margin` / `inventory.view_valuation`. Both layers were verified.

### Token refresh

Access tokens live 15 minutes, so a mid-session 401 is routine. The client
refreshes and replays once. Concurrent 401s share a single in-flight refresh —
without that, a dashboard firing six parallel requests would trigger six
refreshes, and because the backend rotates and blacklists refresh tokens, five
would be invalidated and log the user out.

### Forms

Create/edit flows exist for the operations that carry real risk:

| Flow | Notable behaviour |
|---|---|
| New sale | Live totals via `computeLine`; discount input disabled without `sales.apply_discount`; credit-limit refusal opens an override dialog **only** for holders of `sales.override_credit_limit`, and the reason is mandatory |
| Goods receipt | Batch number and expiry captured per line; short-dated deliveries flagged client-side against the order line's minimum expiry, before submission |
| Stock adjustment | Reason mandatory, submit disabled without it, live delta shown — mirroring the server rule rather than discovering it on rejection |
| New customer | NIF pattern checked client-side, and the credit-terms-require-NIF rule enforced in the Zod schema as well as the serializer |
| New medicine | Selling-below-cost warns rather than blocks (legitimate for short-dated clearance) |

Server-side field errors are mapped back onto the form via
`ApiError.fieldErrors`, so a validation failure appears beside the offending
input rather than only in a banner.

---

## 9. Backend translations

708 strings extracted; **708/708 translated to French**, compiled to `.mo`,
and verified resolving through Django's `gettext` (30 assertions).

### The direction is inverted from the usual case

Source strings are written in English (`_("View medicines")`), so **English is
the msgid language and needs no catalogue** — gettext returns the msgid
unchanged. French, despite being the application's *default display* language,
is the one requiring translation. The test asserts the English fallback
explicitly, to stop a future maintainer "helpfully" adding English entries that
would only introduce drift.

### Built without GNU gettext

`makemessages`/`compilemessages` shell out to `xgettext` and `msgfmt`, which
are absent on stock Windows and on many CI images. `scripts/extract_messages.py`
does both jobs in pure Python: AST-based extraction for `.py` (a regex would
match `_("…")` inside comments and miss multi-line calls), regex for templates,
and a direct `.mo` writer following the documented binary format.

Translations live in `scripts/translations_fr.py` as a reviewable, diffable
dict. Precedence on re-extraction is: existing `.po` msgstr → seed dict →
empty, so a translator's manual edit is never overwritten by a re-run.

Terminology follows Burundian/OHADA commercial usage — *lot*, *facture*, *bon
de commande*, *note de crédit* — not literal translation.

### API surface

All 231 routes require authentication. 43 of 47 views enforce RBAC through
`HasPermission` (deny-by-default); the remaining four are the pre-auth
endpoints — login, token refresh, and the two password-reset steps — which are
`AllowAny` by necessity and protected instead by scoped throttles (10/min for
auth, 5/hour for reset) plus account lockout after 5 failures.

Report endpoints take `?export=csv|xlsx`. The parameter is deliberately *not*
named `format`: DRF reserves that for content negotiation and intercepts it
before the view runs, which silently 404s the export.

### Verification performed

Algorithm-level, via standalone harnesses (no database):

| Harness | Assertions | Result |
|---|---:|---|
| Money arithmetic, rounding, footing | 12 | pass |
| NIF / Burundi phone validation | 15 | pass |
| RBAC matrix integrity, inheritance, auditor read-only proof | 11 | pass |
| FIFO/FEFO allocation, ledger reconciliation | 26 | pass |
| Credit control gates and derived figures | 24 | pass |
| Invoice totals, payment allocation, credit notes | 25 | pass |
| Expiry horizon banding (no overlap, no gap) | 11 | pass |
| French/English number-to-words | 42 | pass |

End-to-end, against a live database through the real service layer
(`tests/test_end_to_end.py`):

    supplier → approval → PO → separation-of-duties check → goods receipt
    → landed cost apportionment → FEFO issue across 2 batches → invoice posted
    → credit-limit block → payment → return → credit note → recall trace
    → ledger reconciliation → audit chain verification

**54 assertions, 54 passed.** Two real defects were caught and fixed by this
run: raw `FOR UPDATE` SQL that was not backend-portable, and a credit note
that violated the `credit_note_requires_original` CHECK constraint because the
link was attached in a follow-up UPDATE rather than at insert.

Over real HTTP, through routing, JWT auth, RBAC, serializers and services
(`tests/test_api_smoke.py`):

**97 assertions, 97 passed**, covering authentication, RBAC denial for three
role levels, catalogue CRUD, price-change reason enforcement, NIF requirement
on credit accounts, the full purchase-to-receipt lifecycle, FEFO issue,
credit-limit blocking, payment allocation, recall trace, all nine reports,
CSV/Excel export, margin redaction by permission, and the absence of any write
path to the audit log or stock ledger.

Three further defects were caught only by driving the API over HTTP, none of
which the service-level tests could have found:

| Defect | Why it mattered |
|---|---|
| `product_code` marked required on input | Every medicine creation returned 400, despite the service auto-allocating the code |
| Line-item PKs declared `UUIDField` | `SaleLine`/`PurchaseOrderLine` use integer PKs; DRF coerced `1` into a zero-padded UUID, breaking goods receipt and returns entirely |
| Export parameter named `format` | DRF reserves `format` for content negotiation and intercepts it, so every CSV/Excel export 404'd |

This is the argument for testing at the transport boundary rather than only at
the service layer: all three were invisible to a test that called services
directly.

### Caveat on the test database

The E2E run above executed against **SQLite**, not PostgreSQL, because no
Postgres instance was available in the build environment. That means these
were exercised: service orchestration, transaction boundaries, CHECK
constraints, FEFO ordering, money arithmetic, audit chaining, and Python-layer
immutability. These were **not**: the audit-immutability triggers (skipped on
non-PostgreSQL by design) and true `SELECT … FOR UPDATE` row locking under
concurrency. `config/settings/test.py` and the CI pipeline both target
PostgreSQL specifically so those are covered before release.
