# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.accounts.report.general_ledger.general_ledger import validate_party
from erpnext.controllers.sales_and_purchase_return import validate_return

FRAME = "Invalid {0}: {1}"
TARGETS = {
	FRAME: "Valore non valido ({0}): {1}.",
	"Customer": "Cliente",
	"Supplier": "Fornitore",
	"Employee": "Dipendente",
	"Shareholder": "Azionista",
	"Return Against": "Reso a fronte di",
	"Return Against Purchase Invoice": "Reso a fronte di fattura di acquisto",
	"Return Against Delivery Note": "Documento di trasporto di riferimento del reso",
	"Return Against Purchase Receipt": "Ricevimento merci di riferimento del reso",
	"Return Against Subcontracting Receipt": "Reso relativo al ricevimento da conto lavoro",
}


class TestGeneralLedgerPartyTypeTranslation(UnitTestCase):
	def setUp(self):
		super().setUp()
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
		self.events = []
		self.existing_names = set()
		self.enterContext(patch.object(frappe, "db", SimpleNamespace(exists=self.exists)))
		for boundary in ("get_doc", "get_all", "get_cached_value", "new_doc", "sendmail", "enqueue"):
			self.enterContext(
				patch("frappe." + boundary, side_effect=AssertionError("Unexpected I/O: " + boundary))
			)

	def exists(self, *args, **kwargs):
		self.events.append((args, kwargs))
		return args[1] in self.existing_names

	def test_invalid_standard_party_type_translates_only_type_not_record_name(self):
		for party_type in ("Customer", "Supplier", "Employee", "Shareholder"):
			with self.subTest(party_type=party_type):
				self.events.clear()
				filters = frappe._dict(party_type=party_type, party=["Customer", "Supplier"])
				before = deepcopy(filters)
				with self.assertRaises(frappe.ValidationError) as caught:
					validate_party(filters)
				self.assertEqual(
					str(caught.exception), TARGETS[FRAME].format(TARGETS[party_type], "Customer")
				)
				self.assertEqual(self.events, [((party_type, "Customer"), {})])
				self.assertEqual(filters, before)

	def test_custom_party_type_and_english_fallback_remain_raw(self):
		for language, party_type in (("it", "Custom Party"), ("it", "Member"), ("en", "Customer")):
			frappe.local.lang = language
			filters = frappe._dict(party_type=party_type, party=["Customer"])
			with self.subTest(language=language, party_type=party_type), self.assertRaises(
				frappe.ValidationError
			) as caught:
				validate_party(filters)
			frame = TARGETS[FRAME] if language == "it" else FRAME
			self.assertEqual(str(caught.exception), frame.format(party_type, "Customer"))

	def test_existing_names_and_first_missing_keep_read_order_and_filters(self):
		self.existing_names = {"Customer", "Supplier"}
		filters = frappe._dict(party_type="Customer", party=["Customer", "Supplier"])
		before = deepcopy(filters)
		self.assertIsNone(validate_party(filters))
		self.assertEqual(self.events, [(("Customer", "Customer"), {}), (("Customer", "Supplier"), {})])
		self.assertEqual(filters, before)
		self.events.clear()
		filters.party += ["Missing", "Later"]
		before = deepcopy(filters)
		with self.assertRaises(frappe.ValidationError) as caught:
			validate_party(filters)
		self.assertEqual(str(caught.exception), TARGETS[FRAME].format("Cliente", "Missing"))
		self.assertEqual(self.events[-1], (("Customer", "Missing"), {}))
		self.assertEqual(len(self.events), 3)
		self.assertEqual(filters, before)

	def test_missing_party_or_type_skips_all_lookups(self):
		for filters in (
			{},
			{"party_type": "Customer", "party": []},
			{"party_type": None, "party": ["Customer"]},
			{"party_type": "Customer", "party": None},
		):
			before = deepcopy(filters)
			self.assertIsNone(validate_party(filters))
			self.assertEqual(filters, before)
		self.assertEqual(self.events, [])

	def test_return_controller_already_uses_translated_actual_meta_labels(self):
		root = Path(__file__).parents[3]
		for doctype, module in (
			("Sales Invoice", "accounts"),
			("POS Invoice", "accounts"),
			("Purchase Invoice", "accounts"),
			("Delivery Note", "stock"),
			("Purchase Receipt", "stock"),
			("Subcontracting Receipt", "subcontracting"),
		):
			with self.subTest(doctype=doctype):
				self.events.clear()
				name = frappe.scrub(doctype)
				data = json.loads((root / module / "doctype" / name / (name + ".json")).read_text())
				fields = {
					field["fieldname"]: frappe._dict(field, parent=data["name"]) for field in data["fields"]
				}
				meta = Meta.__new__(Meta)
				meta.__dict__.update(name=doctype, _fields=fields)
				self.assertEqual(fields["return_against"].options, doctype)
				doc = Document.__new__(Document)
				doc.__dict__.update(doctype=doctype, is_return=1, return_against="Customer", meta=meta)
				before = dict(doc.__dict__)
				with self.assertRaises(frappe.ValidationError) as caught:
					validate_return(doc)
				label = meta.get_translated_label("return_against")
				self.assertEqual(str(caught.exception), TARGETS[FRAME].format(label, "Customer"))
				self.assertEqual(self.events, [((doctype, "Customer"), {})])
				self.assertEqual(doc.__dict__, before)
				doc.is_return = 0
				self.events.clear()
				self.assertIsNone(validate_return(doc))
				self.assertEqual(self.events, [])
