# Controlled collections pilot

The **Pilot** workspace tracks a small cohort through reviewed draft → externally delivered
message → customer reply → allocated payment evidence. This is a manually operated pilot,
not an email sender, bank connection, or production accounting integration.

## Start and rehearse

Follow the root README to import a portfolio, build its queue, draft messages with your
local model, and start the approvals service and collector workspace. Open the workspace
and choose **Pilot**. The agent desk is optional for this workflow.

1. Review an email in **Approvals**, edit it if necessary, and approve it. Record the
   elapsed review time with your own timer. Only routine collection drafts qualify;
   disputes and write-offs retain their existing workflows.
2. In **Pilot**, choose **Synthetic rehearsal** for the supplied demo data. Enroll the
   invoice with your name, measured review seconds, and an optional measured manual-work
   baseline for the same case. Assess whether the original draft needed a correction;
   leave this as *Not assessed* if you did not assess it. Describe any error found.
3. Select the case. For a real pilot, send the **exact reviewed email** through your
   authorized email system, then record its message ID/evidence reference, recipient,
   timestamp and delivery evidence note. For a rehearsal, record clearly labeled
   simulated delivery evidence. Collex sends no email.
4. Record the customer's reply with its source reference, timestamp and text. A reply is
   useful evidence but is optional: payment can arrive without a reply.
5. Import an allocated payment CSV. Use the invoice's account ID and currency shown on
   the case. Partial payments leave the case open; matching the opening balance closes
   the pilot case. This does **not** update the source invoice or post to the general ledger.
6. Use **Export evidence** for a JSON snapshot of cases, events, payments and cohort
   metrics. It contains customer messages and financial evidence; handle it as financial data.

Delivery must follow enrollment; replies and payments must follow delivery. Enter the
actual event time, not the import time. Browser date/time inputs use your local timezone;
CSV timestamps must include a timezone. All stored event timestamps use UTC. Future
events are rejected. Existing historic deliveries cannot be backfilled before enrollment
in this initial prospective pilot.

## Payment export

Use these exact columns in this order, with one allocated transaction per invoice:

```csv
transaction_id,invoice_id,account_id,currency,amount,received_at,evidence_ref
bank-unique-id,INV-123456,ACC-123,USD,75.00,2026-11-24T10:30:00-08:00,bank-export-2026-11-24:row-42
```

Replace every sample identifier, amount and timestamp with evidence for the enrolled
case. For rehearsal, use simulated records and remain in the synthetic cohort. `.example`
billing contacts cannot be enrolled as real cases. Other input provenance is operator
attested; the app cannot independently establish that data is real.

Imports accept up to 200 rows and 60 KB of CSV per request. Amounts must be positive,
with at most two decimal places. This first version supports currencies represented
with two decimal places; do not use it for currencies requiring other precision.

The whole import rolls back on an unknown invoice, account/currency/cohort mismatch,
payment before delivery, conflicting transaction ID or overpayment. Exact repeated
transaction rows are ignored, including retries within the same file. Transaction IDs
must be unique across the entire pilot database. Split allocations must have stable,
distinct allocation IDs assigned by the source system. The app does not allocate lump
payments, handle refunds/reversals, or authenticate bank evidence; resolve those in the
accounting system before importing.

## What the measurements mean

- **Reconciled amounts:** operator-supplied payment evidence matched to an enrolled
  invoice, summed separately per currency. A payment after outreach does not establish
  that Collex caused it. Synthetic amounts never appear in the real cohort.
- **Review seconds:** operator-measured elapsed review time for enrolled, approved
  drafts. This excludes drafting latency and subsequent operations.
- **Draft error rate:** enrolled drafts assessed as containing an error divided by
  assessed enrolled drafts. Unassessed cases are excluded, and no assessments means
  *Not assessed*. This is a selected cohort of approved drafts, not an overall model
  accuracy measure; rejected drafts are not included.
- **Baseline minus review:** measured manual baseline minus review seconds for cases
  with both values. It can be negative. Missing baselines remain unknown; the policy's
  six-minute estimate is never substituted. These measurements alone are not proof of
  total labor saved or causal uplift.

For a real evaluation, agree on the cohort and observation window in advance, track
rejected drafts separately, and compare against a comparable manual cohort. Begin with
a small number of accounts and review the evidence with the collections owner.

## Controls and limits

Before enrollment and delivery recording, the server reads current exports and reruns
triage. Paid invoices, new disputes, changed balance/contact, newer touches, or leaving
the queue block the record. Delivery must match the enrolled action ID and exact email
hash, and use the reviewed recipient. The delivered email content is preserved in its
event. Only one enrollment and one delivery are supported per invoice. If an enrolled
draft changes, the old review cannot authorize its delivery; resolve/re-enroll through
a future reviewed correction workflow rather than modifying this ledger by hand.

Checks happen when delivery is **recorded**, after the external send. They cannot
prevent an operator from sending an outdated message in another system. Recheck the
invoice and approval immediately before sending. There is no automated sender here.

State is in `out/pilot.sqlite`, independent of the mock CRM. SQLite serializes imports
and retains recording time, event time and operator name. The application has no
authenticated identity, role enforcement or tamper-proof audit log. Names and evidence
references are operator attestations. Keep this local and controlled; enterprise
identity, backups, provider verification and accounting integration remain deployment
work. Back up the database with SQLite's backup API or with the service stopped.

## API and verification

- `GET /api/pilot`: cases, evidence and separate synthetic/real metrics.
- `POST /api/pilot/enroll`: `invoice_id`, `mode` (`synthetic`/`real`), `by`,
  `review_seconds`, optional `baseline_seconds`, `error_found` (`true`/`false`/`null`),
  and `error_note` when an error was found.
- `POST /api/pilot/event`: `invoice_id`, `kind` (`delivery`/`reply`), `reference`,
  `occurred_at` (timezone timestamp), `by`, `detail`, and `recipient` for delivery.
  An exact repeated reference/event is idempotent; changed evidence is rejected.
- `POST /api/pilot/reconcile`: `by`, `mode` and the CSV text in `csv`.

POSTs require a JSON object and the workspace's same-origin checks. These are local
application controls, not user authentication.

```bash
python app/test_pilot.py
python app/test_ledger.py
python platform/preflight.py --no-model
```

Pilot tests use temporary files and a temporary database, including a complete synthetic
loop and HTTP boundary checks. No model, customer communication or bank access is used.
