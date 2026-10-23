# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.payment_reconciliation import payment_reconciliation as producer

FRAME = "{0}: the allocated amount exceeds the current outstanding amount. Fetch the document again."
FRAMES = (FRAME,)
TARGETS = {
	FRAME: "{0}: l’importo allocato supera l’importo residuo attuale. Recuperare nuovamente il documento.",
	"Sales Invoice": "Fattura di vendita",
	"Purchase Invoice": "Fattura di acquisto",
}


class CurrencyBoundary(Exception):
	pass


class TestReconciliationCurrentOutstandingMessage(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.messages = dict(TARGETS)
		self.metadata = {}
		self.meta_reads = []
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe.local, "conf", frappe._dict(host_name="http://memory.invalid")))
		self.enterContext(
			patch.object(
				frappe.local,
				"system_settings",
				frappe._dict(rounding_method="Banker's Rounding"),
				create=True,
			)
		)
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.messages if lang == "it" else {},
			)
		)
		self.enterContext(patch.object(frappe, "get_meta", side_effect=self._get_meta))
		for name in ("get_all", "get_doc", "get_cached_value", "new_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		self.db = SimpleNamespace(db_type="mariadb")
		self.enterContext(patch.object(frappe, "db", self.db))
		frappe.local.db = self.db

	def _database(self, **providers):
		self.db.__dict__.clear()
		self.db.__dict__.update(db_type="mariadb", **providers)
		return self.db

	def _meta(self, doctype):
		if doctype not in self.metadata:
			slug = frappe.scrub(doctype)
			paths = list(Path(frappe.get_app_path("erpnext")).rglob(slug + ".json"))
			path = next(p for p in paths if p.parent.name == slug)
			data = json.loads(path.read_text())
			meta = Meta.__new__(Meta)
			meta.__dict__.update(data)
			meta.fields = [
				frappe._dict({**df, "parent": doctype, "parenttype": "DocType", "parentfield": "fields"})
				for df in data["fields"]
			]
			self.metadata[doctype] = meta
		return self.metadata[doctype]

	def _get_meta(self, doctype, *args, **kwargs):
		self.assertEqual((args, kwargs), ((), {}))
		self.meta_reads.append(doctype)
		return self._meta(doctype)

	def _state(self, doc, before, owner=None):
		expected = deepcopy(before)
		if owner and "meta" not in before and "meta" in doc.__dict__:
			self.assertIs(doc.__dict__["meta"], self.metadata[owner])
			self.assertEqual(doc.__dict__["meta"].name, owner)
			expected["meta"] = self.metadata[owner]
		self.assertEqual(doc.__dict__, expected)

	def test_configured_production_keys(self):
		path = Path(producer.__file__)
		relative = str(path.relative_to(Path(frappe.get_app_path("erpnext")).parent))
		method = next(
			m
			for pattern, m in get_method_map("erpnext") + get_method_map("frappe")
			if pathmatch(pattern, relative)
		)
		keys = {
			(message, context)
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
		}
		for frame in FRAMES:
			self.assertIn((frame, None), keys)

	def test_real_precision_sign_and_guard_matrix(self):
		for precision in (0, 2, 3):
			self._meta("Payment Reconciliation Allocation").get_field("allocated_amount").precision = str(
				precision
			)
			for dtype in ("Sales Invoice", "Purchase Invoice"):
				for current in (-10, 10):
					for delta in (0, 0.0004, 0.004, 1):
						with self.subTest(precision=precision, doctype=dtype, current=current, delta=delta):
							events = []
							row = frappe._dict(
								voucher_type=dtype,
								voucher_no="NOTE-RAW",
								allocated_amount=10 + delta,
								dr_or_cr="credit_in_account_currency",
								currency="EUR",
								account="ACCOUNT-RAW",
								party="PARTY-RAW",
								party_type="Customer",
								against_voucher_type="Sales Invoice",
								against_voucher="INVOICE-RAW",
								cost_center="CC-RAW",
								exchange_rate=1,
								unadjusted_amount=10,
							)
							before = deepcopy(row)

							def value(dt, name, field):
								events.append((dt, name, field))
								self.assertEqual((dt, name, field), (dtype, "NOTE-RAW", "outstanding_amount"))
								return current

							def boundary(company):
								events.append(("currency", company))
								raise CurrencyBoundary

							self._database(get_value=value)
							self.assertEqual(
								producer.get_field_precision(
									self._meta("Payment Reconciliation Allocation").get_field(
										"allocated_amount"
									)
								),
								precision,
							)
							self.assertEqual(producer.flt(1, precision), 1)
							rejected = producer.flt(abs(current) - row.allocated_amount, precision) < 0
							with patch("erpnext.get_company_currency", side_effect=boundary):
								with self.assertRaises(
									frappe.ValidationError if rejected else CurrencyBoundary
								) as caught:
									producer.reconcile_dr_cr_note([row], "COMPANY-RAW")
							if rejected:
								self.assertEqual(str(caught.exception), TARGETS[FRAME].format(TARGETS[dtype]))
							self.assertEqual(row, before)
							self.assertEqual(
								events,
								[(dtype, "NOTE-RAW", "outstanding_amount")]
								+ ([] if rejected else [("currency", "COMPANY-RAW")]),
							)

	def test_english_whole_frame_without_temporal_claim(self):
		self.messages.clear()
		self._meta("Payment Reconciliation Allocation").get_field("allocated_amount").precision = "2"
		self._database(get_value=lambda *args: -10)
		with self.assertRaises(frappe.ValidationError) as caught:
			producer.reconcile_dr_cr_note(
				[frappe._dict(voucher_type="Sales Invoice", voucher_no="RAW", allocated_amount=11)],
				"COMPANY-RAW",
			)
		self.assertEqual(str(caught.exception), FRAME.format("Sales Invoice"))
