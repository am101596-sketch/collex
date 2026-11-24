"""Evidence ledger for a small, manually operated collections pilot. No messages are sent.

Money is stored in integer minor units. SQLite transactions make payment imports atomic;
external transaction IDs are unique across cases so retries cannot double-count cash.
"""
import csv
import hashlib
import io
import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "triage"))
DB = ROOT / "out" / "pilot.sqlite"
PAYMENT_COLUMNS = ("transaction_id", "invoice_id", "account_id", "currency", "amount", "received_at", "evidence_ref")


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(data, key, limit=1000):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{key} is required (maximum {limit} characters)")
    return value.strip()


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            raise ValueError()
        if dt > datetime.now(timezone.utc):
            raise ValueError()
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Use a timestamp with a timezone, at or before now") from None


def cents(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > Decimal("1000000000") or number * 100 != (number * 100).to_integral_value():
            raise ValueError()
        return int(number * 100)
    except (InvalidOperation, ValueError):
        raise ValueError("Amount must be positive with at most two decimal places") from None


def seconds(data, key, optional=False):
    value = data.get(key)
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be whole seconds between 1 and 86400")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value() or not 1 <= number <= 86400:
            raise ValueError()
        return int(number)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{key} must be whole seconds between 1 and 86400") from None


@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                invoice_id TEXT PRIMARY KEY, action_id TEXT NOT NULL, mode TEXT NOT NULL,
                account_id TEXT NOT NULL, account_name TEXT NOT NULL, currency TEXT NOT NULL,
                opening_cents INTEGER NOT NULL, recipient TEXT NOT NULL, message_hash TEXT NOT NULL,
                reviewer TEXT NOT NULL, review_seconds INTEGER NOT NULL, baseline_seconds INTEGER,
                error_found INTEGER, error_note TEXT NOT NULL, enrolled_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES cases(invoice_id),
                kind TEXT NOT NULL, reference TEXT NOT NULL, occurred_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL, detail TEXT NOT NULL,
                UNIQUE(invoice_id, kind, reference)
            );
            CREATE TABLE IF NOT EXISTS payments (
                transaction_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES cases(invoice_id),
                account_id TEXT NOT NULL, currency TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                received_at TEXT NOT NULL, evidence_ref TEXT NOT NULL,
                recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL
            );
        """)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def approved(world, iid):
    """Check current source facts, not just a possibly old revalidation sidecar."""
    import revalidate
    folder = ROOT / "out" / "actions" / iid
    status, item = read(folder / "status.json"), read(folder / "queue_item.json")
    if status.get("state") != "approved" or item.get("bucket") != "agent" or not status.get("action_id"):
        raise ValueError("Approve a routine collection draft in Approvals before enrolling or recording delivery")
    queued = set(world.items)
    notes = {}
    for note in world.notes:
        notes.setdefault(note.get("invoice_id"), []).append(note)
    reasons = revalidate.check(item, world.invoices.get(iid), queued, notes, world.slack)
    current = world.invoices.get(iid) or {}
    for key in ("account_id", "currency", "billing_contact_email"):
        if str(current.get(key) or "").strip().casefold() != str(item.get(key) or "").strip().casefold():
            reasons.append(f"{key} changed or is missing")
    if world.items.get(iid, {}).get("bucket") != "agent":
        reasons.append("invoice is no longer eligible for routine outreach")
    if reasons:
        raise ValueError("Draft is outdated: " + "; ".join(reasons))
    message = (folder / "customer_email.md").read_bytes()
    return status, item, hashlib.sha256(message).hexdigest()


def enroll(world, data):
    iid = text(data, "invoice_id", 80).upper()
    # Reject paths before reading a draft directory.
    if not iid.startswith("INV-") or not iid[4:].isdigit():
        raise ValueError("Use an INV- invoice ID")
    mode = text(data, "mode", 20)
    if mode not in ("synthetic", "real"):
        raise ValueError("mode must be synthetic or real")
    status, item, digest = approved(world, iid)
    if mode == "real" and str(item.get("billing_contact_email", "")).strip().lower().endswith(".example"):
        raise ValueError("Demo .example contacts must stay in the synthetic cohort")
    error = data.get("error_found")
    if error is not None and type(error) is not bool:
        raise ValueError("error_found must be true, false or null (not assessed)")
    note = text(data, "error_note") if error else ""
    values = (iid, status["action_id"], mode, text(item, "account_id", 100), item.get("account_name", ""),
              text(item, "currency", 3).upper(), cents(item["balance"]), text(item, "billing_contact_email", 254),
              digest, text(data, "by", 100), seconds(data, "review_seconds"), seconds(data, "baseline_seconds", True),
              None if error is None else int(error), note, stamp())
    with connection() as db:
        if db.execute("SELECT 1 FROM cases WHERE invoice_id = ?", (iid,)).fetchone():
            raise ValueError("Invoice is already enrolled; its original review measurements are retained")
        db.execute("INSERT INTO cases VALUES (" + ",".join("?" for _ in values) + ")", values)
    return {"ok": True, "invoice_id": iid}


def record_event(world, data):
    iid, kind = text(data, "invoice_id", 80).upper(), text(data, "kind", 20)
    if kind not in ("delivery", "reply"):
        raise ValueError("kind must be delivery or reply")
    reference, at = text(data, "reference", 200), timestamp(data.get("occurred_at"))
    who, detail = text(data, "by", 100), text(data, "detail", 4000)
    with connection() as db:
        case = db.execute("SELECT * FROM cases WHERE invoice_id = ?", (iid,)).fetchone()
        if not case:
            raise ValueError("Enroll this invoice first")
        old = db.execute("SELECT * FROM events WHERE invoice_id = ? AND kind = ? AND reference = ?",
                         (iid, kind, reference)).fetchone()
        if old:
            old_detail = json.loads(old["detail"])["note"] if kind == "delivery" else old["detail"]
            if (old["occurred_at"], old_detail) != (at, detail) or (kind == "delivery" and data.get("recipient", "").casefold() != case["recipient"].casefold()):
                raise ValueError("That evidence reference already has different details")
            return {"ok": True, "duplicate": True}
        delivery = db.execute("SELECT * FROM events WHERE invoice_id = ? AND kind = 'delivery'", (iid,)).fetchone()
        if kind == "delivery":
            if delivery:
                raise ValueError("Delivery already recorded; this pilot tracks one message per invoice")
            status, item, digest = approved(world, iid)
            if status["action_id"] != case["action_id"] or digest != case["message_hash"]:
                raise ValueError("The enrolled draft has changed; do not use its old review for delivery")
            if text(data, "recipient", 254).casefold() != case["recipient"].casefold():
                raise ValueError("Recipient must match the reviewed billing contact")
            if at < case["enrolled_at"]:
                raise ValueError("Delivery cannot predate enrollment and review")
            # Store the approved content, not a mutable path alone.
            detail = json.dumps({"note": detail, "recipient": case["recipient"],
                                 "message": (ROOT / "out" / "actions" / iid / "customer_email.md").read_text(encoding="utf-8")})
        elif not delivery or at < delivery["occurred_at"]:
            raise ValueError("A reply must follow a recorded delivery")
        db.execute("INSERT INTO events (invoice_id,kind,reference,occurred_at,recorded_at,recorded_by,detail) VALUES (?,?,?,?,?,?,?)",
                   (iid, kind, reference, at, stamp(), who, detail))
    return {"ok": True, "duplicate": False}


def reconcile(data):
    who = text(data, "by", 100)
    mode = text(data, "mode", 20)
    if mode not in ("synthetic", "real"):
        raise ValueError("mode must be synthetic or real")
    source = text(data, "csv", 60000)
    reader = csv.DictReader(io.StringIO(source.lstrip("\ufeff")))
    if reader.fieldnames != list(PAYMENT_COLUMNS):
        raise ValueError("CSV header must be: " + ",".join(PAYMENT_COLUMNS))
    rows = list(reader)
    if not 1 <= len(rows) <= 200:
        raise ValueError("Import between 1 and 200 payment rows per batch")
    imported = duplicates = 0
    with connection() as db:
        for line, row in enumerate(rows, 2):
            try:
                if None in row:
                    raise ValueError("Unexpected extra CSV columns")
                tx, iid = text(row, "transaction_id", 200), text(row, "invoice_id", 80).upper()
                account, currency = text(row, "account_id", 100), text(row, "currency", 3).upper()
                amount, at = cents(row.get("amount")), timestamp(row.get("received_at"))
                ref = text(row, "evidence_ref", 200)
                values = (tx, iid, account, currency, amount, at, ref)
                case = db.execute("SELECT * FROM cases WHERE invoice_id = ?", (iid,)).fetchone()
                if not case or account != case["account_id"] or currency != case["currency"]:
                    raise ValueError("Invoice, account and currency must match an enrolled case")
                if case["mode"] != mode:
                    raise ValueError("Invoice belongs to a different pilot cohort")
                old = db.execute("SELECT * FROM payments WHERE transaction_id = ?", (tx,)).fetchone()
                if old:
                    if tuple(old[k] for k in ("transaction_id", "invoice_id", "account_id", "currency", "amount_cents", "received_at", "evidence_ref")) != values:
                        raise ValueError("Transaction ID already exists with different payment details")
                    duplicates += 1
                    continue
                delivery = db.execute("SELECT occurred_at FROM events WHERE invoice_id = ? AND kind = 'delivery'", (iid,)).fetchone()
                if not delivery or at < delivery["occurred_at"]:
                    raise ValueError("Payment must follow the recorded delivery")
                paid = db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE invoice_id = ?", (iid,)).fetchone()[0]
                if paid + amount > case["opening_cents"]:
                    raise ValueError("Payment exceeds the remaining pilot balance; resolve allocation outside the pilot")
                db.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?)", values + (stamp(), who))
                imported += 1
            except ValueError as e:
                raise ValueError(f"CSV row {line}: {e}. No rows from this batch were saved") from None
    return {"ok": True, "imported": imported, "duplicates": duplicates}


def report():
    with connection() as db:
        cases = [dict(r) for r in db.execute("SELECT * FROM cases ORDER BY enrolled_at DESC, invoice_id")]
        for case in cases:
            iid = case["invoice_id"]
            case["events"] = [dict(r) for r in db.execute("SELECT * FROM events WHERE invoice_id = ? ORDER BY occurred_at,id", (iid,))]
            case["payments"] = [dict(r) for r in db.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY received_at,transaction_id", (iid,))]
            case["paid_cents"] = sum(p["amount_cents"] for p in case["payments"])
            case["remaining_cents"] = case["opening_cents"] - case["paid_cents"]
            kinds = {e["kind"] for e in case["events"]}
            case["stage"] = ("closed" if case["remaining_cents"] == 0 else "partial_payment" if case["paid_cents"]
                             else "responded" if "reply" in kinds else "delivered" if "delivery" in kinds else "reviewed")
    metrics = {}
    for mode in ("synthetic", "real"):
        group = [c for c in cases if c["mode"] == mode]
        assessed = [c for c in group if c["error_found"] is not None]
        paired = [c for c in group if c["baseline_seconds"] is not None]
        money = {}
        for c in group:
            m = money.setdefault(c["currency"], {"opening_cents": 0, "reconciled_cents": 0})
            m["opening_cents"] += c["opening_cents"]
            m["reconciled_cents"] += c["paid_cents"]
        metrics[mode] = {"enrolled": len(group), "closed": sum(c["stage"] == "closed" for c in group),
                         "delivered": sum(any(e["kind"] == "delivery" for e in c["events"]) for c in group),
                         "responded": sum(any(e["kind"] == "reply" for e in c["events"]) for c in group),
                         "review_seconds": sum(c["review_seconds"] for c in group), "assessed": len(assessed),
                         "errors": sum(c["error_found"] for c in assessed),
                         "error_rate": sum(c["error_found"] for c in assessed) / len(assessed) if assessed else None,
                         "paired_baselines": len(paired),
                         "time_difference_seconds": sum(c["baseline_seconds"] - c["review_seconds"] for c in paired) if paired else None,
                         "money_by_currency": money}
    return {"cases": cases, "metrics": metrics,
            "notice": "Delivery and bank evidence are recorded by the operator, not verified with a provider. Reconciled payments do not establish causal recovery. Review times and baselines are operator measured; identities are not authenticated."}
