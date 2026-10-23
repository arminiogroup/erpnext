# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import itertools
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import TaxWithholdingEntry

FRAME = "Row #{0}: Could not find enough {1} entries to match. Remaining amount: {2}"
TARGETS = {
	FRAME: "Riga {0}: non sono state trovate registrazioni sufficienti con stato {1} da abbinare. Importo residuo: {2}.",
	"Under Withheld": "Ritenuta in difetto",
	"Over Withheld": "Ritenuta in eccesso",
}


class TestMatchingStatusTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		data = json.loads(Path(__file__).with_name("tax_withholding_entry.json").read_text())
		fields = [frappe._dict(parent=data["name"], **field) for field in data["fields"]]
		self.meta = Meta.__new__(Meta)
		self.meta.__dict__.update(
			name="Tax Withholding Entry",
			doctype="DocType",
			fields=fields,
			_fields={field.fieldname: field for field in fields},
		)
		self.events = []
		self.old_data = []
		self.enterContext(patch("frappe.get_meta", side_effect=self.get_meta))
		self.enterContext(patch("frappe.get_all", side_effect=self.get_all))
		self.enterContext(patch("frappe.get_doc", side_effect=self.get_doc))
		self.enterContext(patch.object(frappe, "db", SimpleNamespace(set_value=self.set_value)))
		for name in ("get_cached_value", "new_doc", "copy_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))

	def get_meta(self, name, *args, **kwargs):
		self.assertEqual((name, args, kwargs), ("Tax Withholding Entry", (), {}))
		self.events.append(("meta", name))
		return self.meta

	def get_all(self, doctype, **kwargs):
		self.assertEqual(doctype, "Tax Withholding Entry")
		self.events.append(("get_all", deepcopy(kwargs)))
		return deepcopy(self.old_data)

	def get_doc(self, doctype, **kwargs):
		self.assertEqual(doctype, "Tax Withholding Entry")
		self.events.append(("get_doc", deepcopy(kwargs)))
		doc = TaxWithholdingEntry.__new__(TaxWithholdingEntry)
		doc.__dict__.update(doctype=doctype, **kwargs)
		return doc

	def set_value(self, doctype, name, values):
		self.assertEqual(doctype, "Tax Withholding Entry")
		self.events.append(("set_value", doctype, name, deepcopy(values)))

	def document(self, amount=12.5, rate=22, precision=2):
		doc = TaxWithholdingEntry.__new__(TaxWithholdingEntry)
		doc.__dict__.update(
			doctype="Tax Withholding Entry",
			name="NEW-RAW",
			idx=3,
			docstatus=1,
			status="Settled",
			flags=frappe._dict(),
			tax_rate=rate,
			taxable_amount=amount,
			withholding_amount=amount,
			taxable_doctype="Purchase Invoice",
			taxable_name="PI-RAW",
			taxable_date="2026-10-20",
			withholding_doctype="Payment Entry",
			withholding_name="PE-RAW",
			withholding_date="2026-10-21",
			tax_withholding_category="CATEGORY-RAW",
			currency="EUR",
			conversion_rate=1,
			under_withheld_reason=None,
			lower_deduction_certificate=None,
			_precision={"main": {"taxable_amount": precision, "withholding_amount": precision}},
		)
		return doc

	def old_entry(self, amount):
		return frappe._dict(
			name="OLD-RAW",
			parenttype="Purchase Invoice",
			parent="OLD-PARENT",
			taxable_amount=amount,
			withholding_amount=amount,
			tax_rate=22,
		)

	def state(self, doc):
		return deepcopy({key: value for key, value in doc.__dict__.items() if key != "meta"})

	def assert_cache(self, doc, expected):
		if expected:
			self.assertIn("meta", doc.__dict__)
			self.assertIs(doc.__dict__["meta"], self.meta)
			self.assertEqual(doc.meta.name, doc.doctype)
		else:
			self.assertNotIn("meta", doc.__dict__)

	def assert_failure(self, doc, field_type, remaining, language="it"):
		before = self.state(doc)
		with self.assertRaises(frappe.ValidationError) as caught:
			doc._adjust_against_old_entries(field_type)
		status = "Under Withheld" if field_type == "taxable" else "Over Withheld"
		label = TARGETS[status] if language == "it" else status
		self.assertEqual(
			str(caught.exception), (TARGETS[FRAME] if language == "it" else FRAME).format(3, label, remaining)
		)
		self.assertEqual(self.state(doc), before)
		self.assert_cache(doc, True)
		return status

	def test_empty_results_translate_status_and_preserve_sign_zero_rate_and_precision(self):
		for field_type, amount, rate, precision in itertools.product(
			("taxable", "withholding"), (-12.5, 0, 12.5), (0, 22), (0, 2, 3)
		):
			with self.subTest(field_type=field_type, amount=amount, rate=rate, precision=precision):
				self.events.clear()
				doc = self.document(amount, rate, precision)
				status = self.assert_failure(doc, field_type, abs(amount))
				self.assertEqual(
					self.events,
					[
						(
							"get_all",
							{
								"filters": {
									"tax_withholding_category": "CATEGORY-RAW",
									"status": status,
									field_type + "_doctype": doc.get(field_type + "_doctype"),
									field_type + "_name": doc.get(field_type + "_name"),
									"docstatus": 1,
								},
								"fields": "*",
							},
						),
						("meta", "Tax Withholding Entry"),
					],
				)

	def test_opposite_signed_entry_does_not_write_before_failure(self):
		for field_type in ("taxable", "withholding"):
			with self.subTest(field_type=field_type):
				self.events.clear()
				self.old_data = [self.old_entry(-5)]
				before = deepcopy(self.old_data)
				self.assert_failure(self.document(), field_type, 12.5)
				self.assertEqual([event[0] for event in self.events], ["get_all", "get_doc", "meta"])
				self.assertEqual(self.old_data, before)

	def test_complete_match_keeps_actual_helper_payload_and_skips_display_metadata(self):
		for field_type in ("taxable", "withholding"):
			with self.subTest(field_type=field_type):
				self.events.clear()
				self.old_data = [self.old_entry(12.5)]
				doc = self.document()
				before = self.state(doc)
				self.assertIsNone(doc._adjust_against_old_entries(field_type))
				self.assertEqual(self.state(doc), before)
				self.assert_cache(doc, False)
				self.assertEqual([event[0] for event in self.events], ["get_all", "get_doc", "set_value"])
				other = "withholding" if field_type == "taxable" else "taxable"
				expected = {
					other + "_amount": 12.5,
					other + "_doctype": doc.get(other + "_doctype"),
					other + "_name": doc.get(other + "_name"),
					other + "_date": doc.get(other + "_date"),
					"tax_rate": 22,
					"status": "Duplicate",
					"under_withheld_reason": None,
				}
				if other == "taxable":
					expected.update(currency="EUR", conversion_rate=1, lower_deduction_certificate=None)
				self.assertEqual(self.events[2], ("set_value", "Tax Withholding Entry", "OLD-RAW", expected))

	def test_existing_update_before_insufficiency_remains_before_display(self):
		self.old_data = [self.old_entry(5)]
		self.assert_failure(self.document(), "taxable", 7.5)
		self.assertEqual([event[0] for event in self.events], ["get_all", "get_doc", "set_value", "meta"])
		self.assertEqual(
			self.events[2],
			(
				"set_value",
				"Tax Withholding Entry",
				"OLD-RAW",
				{
					"withholding_amount": 12.5,
					"withholding_doctype": "Payment Entry",
					"withholding_name": "PE-RAW",
					"withholding_date": "2026-10-21",
					"tax_rate": 22,
					"status": "Duplicate",
					"under_withheld_reason": None,
				},
			),
		)

	def test_english_fallback_retains_raw_status_and_amount_format(self):
		frappe.local.lang = "en"
		self.assert_failure(self.document(), "withholding", 12.5, language="en")
