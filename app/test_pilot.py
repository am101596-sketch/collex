"""Pilot state transitions, evidence checks, exact money, retry and HTTP boundaries.

python app/test_pilot.py (standard library only; all state is temporary).
"""
import csv
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server
from app import pilot


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=3)
        self.at = lambda hours: (self.base + timedelta(hours=hours)).isoformat()
        self.item = {"invoice_id": "INV-100001", "account_id": "ACC-1", "account_name": "Example",
                     "bucket": "agent", "currency": "USD", "balance": 75, "amount": 75, "amount_paid": 0,
                     "billing_contact_email": "ap@customer.example", "last_touch_date": "2026-01-01"}
        self.inv = dict(self.item, status="open", dispute_flag="false")
        self.world = SimpleNamespace(invoices={"INV-100001": self.inv}, notes=[], slack={}, items={"INV-100001": self.item})
        self.folder = self.root / "out" / "actions" / "INV-100001"
        self.folder.mkdir(parents=True)
        self.write("status.json", {"state": "approved", "action_id": "INV-100001:collections_outreach:snapshot"})
        self.write("queue_item.json", self.item)
        (self.folder / "customer_email.md").write_text("Please pay invoice INV-100001, $75.00.", encoding="utf-8")
        for p in (patch.object(pilot, "ROOT", self.root), patch.object(pilot, "DB", self.root / "pilot.sqlite"),
                  patch.object(pilot, "stamp", return_value=self.at(0))):
            p.start()
            self.addCleanup(p.stop)
        self.enrollment = {"invoice_id": "INV-100001", "mode": "synthetic", "by": "Collector",
                           "review_seconds": 45, "baseline_seconds": 120, "error_found": False}

    def write(self, name, value):
        (self.folder / name).write_text(json.dumps(value), encoding="utf-8")

    def enroll(self, **kw):
        return pilot.enroll(self.world, dict(self.enrollment, **kw))

    def event(self, kind="delivery", **kw):
        data = {"invoice_id": "INV-100001", "kind": kind, "reference": kind + "-001", "by": "Collector",
                "occurred_at": self.at(1 if kind == "delivery" else 2), "detail": "Source evidence",
                "recipient": "ap@customer.example"}
        return pilot.record_event(self.world, dict(data, **kw))

    def payment(self, **kw):
        return dict(transaction_id="TX-1", invoice_id="INV-100001", account_id="ACC-1", currency="USD",
                    amount="75.00", received_at=self.at(3), evidence_ref="bank-ledger:row-1", **kw)

    def reconcile(self, *rows, mode="synthetic"):
        content = io.StringIO()
        writer = csv.DictWriter(content, fieldnames=pilot.PAYMENT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        return pilot.reconcile({"by": "Reconciler", "mode": mode, "csv": content.getvalue()})

    def case(self):
        return pilot.report()["cases"][0]

    def test_full_loop_and_metrics(self):
        self.enroll(error_found=True, error_note="Corrected contact wording")
        self.assertEqual(self.case()["stage"], "reviewed")
        self.event()
        self.assertEqual(self.case()["stage"], "delivered")
        self.event("reply", detail="Payment was submitted")
        self.assertEqual(self.case()["stage"], "responded")
        self.reconcile(self.payment())
        self.assertEqual(self.case()["stage"], "closed")
        self.assertEqual(self.case()["remaining_cents"], 0)
        self.assertIn("Please pay", json.loads(self.case()["events"][0]["detail"])["message"])
        metrics = pilot.report()["metrics"]["synthetic"]
        self.assertEqual((metrics["closed"], metrics["responded"], metrics["error_rate"], metrics["time_difference_seconds"]), (1, 1, 1, 75))
        self.assertEqual(metrics["money_by_currency"]["USD"]["reconciled_cents"], 7500)
        self.assertEqual(pilot.report()["metrics"]["real"]["enrolled"], 0)

    def test_partial_payments_use_exact_cents_and_retries_are_idempotent(self):
        self.enroll()
        self.event()
        payment = dict(self.payment(), amount="0.10")
        self.assertEqual(self.reconcile(payment)["imported"], 1)
        self.assertEqual(self.case()["stage"], "partial_payment")
        self.assertEqual(self.reconcile(payment)["duplicates"], 1)
        self.reconcile(dict(self.payment(), transaction_id="TX-2", amount="74.90"))
        self.assertEqual(self.case()["paid_cents"], 7500)
        self.assertEqual(self.case()["stage"], "closed")

    def test_no_approval_or_wrong_bucket_cannot_enroll(self):
        for state in ("drafted", "edited", "rejected"):
            self.write("status.json", {"state": state, "action_id": "test"})
            with self.assertRaisesRegex(ValueError, "Approve"):
                self.enroll()
        self.write("status.json", {"state": "approved", "action_id": "test"})
        self.write("queue_item.json", dict(self.item, bucket="writeoff"))
        with self.assertRaisesRegex(ValueError, "routine"):
            self.enroll()

    def test_fresh_facts_block_delivery_without_sidecar(self):
        self.enroll()
        changes = ({"amount_paid": 1}, {"status": "paid"}, {"dispute_flag": "true"},
                   {"account_id": "ACC-OTHER"}, {"currency": "EUR"}, {"billing_contact_email": ""},
                   {"billing_contact_email": "new@customer.example"}, {"last_touch_date": "2026-01-02"})
        for change in changes:
            self.world.invoices["INV-100001"] = dict(self.inv, **change)
            with self.assertRaisesRegex(ValueError, "outdated"):
                self.event()
        self.world.invoices = {}
        with self.assertRaisesRegex(ValueError, "outdated"):
            self.event()
        self.assertEqual(self.case()["events"], [])

    def test_left_queue_and_new_notes_block_delivery(self):
        self.enroll()
        self.world.items = {}
        with self.assertRaisesRegex(ValueError, "queue"):
            self.event()
        self.world.items = {"INV-100001": self.item}
        self.world.notes = [{"invoice_id": "INV-100001", "touch_date": "2026-01-03"}]
        with self.assertRaisesRegex(ValueError, "touched"):
            self.event()

    def test_changed_message_or_action_blocks_delivery(self):
        self.enroll()
        path = self.folder / "customer_email.md"
        original = path.read_bytes()
        path.write_text("Changed after review", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.event()
        path.write_bytes(original)
        self.write("status.json", {"state": "approved", "action_id": "new snapshot"})
        with self.assertRaisesRegex(ValueError, "changed"):
            self.event()

    def test_delivery_recipient_chronology_and_duplicate_evidence(self):
        self.enroll()
        with self.assertRaisesRegex(ValueError, "Recipient"):
            self.event(recipient="wrong@customer.example")
        with self.assertRaisesRegex(ValueError, "predate"):
            self.event(occurred_at=self.at(-1))
        self.event()
        self.assertTrue(self.event()["duplicate"])
        with self.assertRaisesRegex(ValueError, "different details"):
            self.event(detail="Changed evidence")
        with self.assertRaisesRegex(ValueError, "already recorded"):
            self.event(reference="second-send")
        self.assertEqual(len(self.case()["events"]), 1)

    def test_reply_and_payment_require_delivery_and_valid_chronology(self):
        self.enroll()
        with self.assertRaisesRegex(ValueError, "delivery"):
            self.event("reply")
        with self.assertRaisesRegex(ValueError, "delivery"):
            self.reconcile(self.payment())
        self.event()
        with self.assertRaisesRegex(ValueError, "delivery"):
            self.reconcile(dict(self.payment(), received_at=self.at(0)))
        self.event("reply")
        self.assertTrue(self.event("reply")["duplicate"])

    def test_csv_mismatches_and_overpayment_rollback_whole_batch(self):
        self.enroll()
        self.event()
        for changes in ({"account_id": "wrong"}, {"currency": "EUR"}, {"invoice_id": "INV-999999"},
                        {"amount": "75.01"}, {"amount": "NaN"}, {"amount": "-1"}, {"amount": "1.001"}):
            with self.assertRaisesRegex(ValueError, "No rows"):
                self.reconcile(dict(self.payment(), transaction_id="valid", amount="0.01"),
                               dict(self.payment(), **changes))
            self.assertEqual(self.case()["paid_cents"], 0)

    def test_conflicting_transaction_ids_cannot_reallocate_or_increase_cash(self):
        self.enroll()
        self.event()
        self.reconcile(dict(self.payment(), amount="25.00"))
        with self.assertRaisesRegex(ValueError, "Transaction ID"):
            self.reconcile(dict(self.payment(), amount="30.00"))
        self.assertEqual(self.case()["paid_cents"], 2500)

    def test_cohorts_cannot_be_mixed(self):
        with self.assertRaisesRegex(ValueError, "synthetic"):
            self.enroll(mode="real")
        self.enroll()
        self.event()
        with self.assertRaisesRegex(ValueError, "cohort"):
            self.reconcile(self.payment(), mode="real")

    def test_missing_measurements_never_become_zero_error_or_time_saved(self):
        self.enroll(error_found=None, baseline_seconds="")
        metrics = pilot.report()["metrics"]["synthetic"]
        self.assertIsNone(metrics["error_rate"])
        self.assertIsNone(metrics["time_difference_seconds"])
        self.assertEqual(metrics["assessed"], 0)
        with self.assertRaisesRegex(ValueError, "already enrolled"):
            self.enroll(review_seconds=1)

    def test_real_cohort_and_currency_metrics_stay_separate(self):
        item = dict(self.item, billing_contact_email="ap@customer.com", currency="EUR")
        self.write("queue_item.json", item)
        self.world.invoices["INV-100001"] = dict(self.inv, billing_contact_email="ap@customer.com", currency="EUR")
        self.world.items["INV-100001"] = item
        self.enroll(mode="real", review_seconds=180)
        self.event(recipient="ap@customer.com")
        self.reconcile(dict(self.payment(), currency="EUR"), mode="real")
        report = pilot.report()
        self.assertEqual(report["metrics"]["synthetic"]["enrolled"], 0)
        real = report["metrics"]["real"]
        self.assertEqual(real["closed"], 1)
        self.assertEqual(real["time_difference_seconds"], -60)
        self.assertEqual(set(real["money_by_currency"]), {"EUR"})

    def test_rejected_approval_and_policy_bucket_change_block_delivery(self):
        self.enroll()
        self.write("status.json", {"state": "rejected", "action_id": "test"})
        with self.assertRaisesRegex(ValueError, "Approve"):
            self.event()
        self.write("status.json", {"state": "approved", "action_id": "test"})
        self.world.items["INV-100001"] = dict(self.item, bucket="writeoff")
        with self.assertRaisesRegex(ValueError, "routine outreach"):
            self.event()

    def test_input_validation(self):
        for bad in (float("nan"), float("inf"), -1, 0, 1.5, True, "no"):
            with self.assertRaises(ValueError):
                self.enroll(review_seconds=bad)
        for kw in ({"error_found": "false"}, {"error_found": True}, {"invoice_id": "../outside"}, {"mode": "bad"}):
            with self.assertRaises(ValueError):
                self.enroll(**kw)
        for value in ("2026-01-01", "bad", "2099-01-01T00:00:00Z"):
            with self.assertRaises(ValueError):
                pilot.timestamp(value)
        with self.assertRaisesRegex(ValueError, "header"):
            pilot.reconcile({"by": "Collector", "mode": "synthetic", "csv": "invoice_id,amount\nINV-100001,75"})

    def test_payments_remain_reconcilable_after_source_marks_invoice_paid(self):
        self.enroll()
        self.event()
        self.world.invoices["INV-100001"]["status"] = "paid"
        self.reconcile(self.payment())
        self.assertEqual(self.case()["stage"], "closed")

    def test_http_api_and_origin_boundary(self):
        with patch.object(server, "pilot_world", return_value=self.world):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            def request(path, data=None, origin=None):
                headers = {"Content-Type": "application/json"}
                if origin:
                    headers["Origin"] = origin
                req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
                with urllib.request.urlopen(req) as response:
                    return json.load(response)
            try:
                self.assertEqual(request("/api/pilot")["cases"], [])
                self.assertTrue(request("/api/pilot/enroll", self.enrollment)["ok"])
                with self.assertRaises(urllib.error.HTTPError) as err:
                    request("/api/pilot/enroll", self.enrollment, "https://elsewhere.example")
                self.assertEqual(err.exception.code, 403)
                with self.assertRaises(urllib.error.HTTPError) as err:
                    request("/api/pilot/enroll", self.enrollment)
                self.assertEqual(err.exception.code, 400)
                self.assertEqual(len(request("/api/pilot")["cases"]), 1)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
