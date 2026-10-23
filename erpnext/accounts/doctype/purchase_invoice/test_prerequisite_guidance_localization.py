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

from erpnext.accounts.doctype.purchase_invoice import purchase_invoice as producer

PO = "To submit the invoice without purchase order please set {0} as {1} in {2}"
PR = "To submit the invoice without purchase receipt please set {0} as {1} in {2}"
PO_LABEL = "Is Purchase Order required for Purchase Invoice & Receipt creation?"
PR_LABEL = "Is Purchase Receipt required for Purchase Invoice creation?"
FRAMES = (PO, PR)
TARGETS = {
	PO: "Per confermare la fattura senza un ordine d’acquisto, impostare {0} su {1} in {2}.",
	PR: "Per confermare la fattura senza un ricevimento merci, impostare {0} su {1} in {2}.",
	PO_LABEL: "L’ordine di acquisto è obbligatorio per creare fatture di acquisto e ricevimenti merci?",
	PR_LABEL: "Il ricevimento merci è obbligatorio per creare una fattura di acquisto?",
	"Buying Settings": "Impostazioni acquisti",
	"No": "No",
}


class TestPurchaseInvoicePrerequisiteGuidance(UnitTestCase):
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

	def _invoice(self, kind, scenario):
		row = frappe._dict(
			item_code="ITEM-RAW",
			purchase_order="PO-RAW" if scenario == "linked" else "",
			purchase_receipt="PR-RAW" if scenario == "linked" else "",
			is_fixed_asset=int(scenario == "asset"),
		)
		doc = producer.PurchaseInvoice.__new__(producer.PurchaseInvoice)
		doc.__dict__.update(
			doctype="Purchase Invoice",
			supplier="SUPPLIER-RAW",
			company="COMPANY-RAW",
			represents_company="COMPANY-RAW",
			is_internal_supplier=scenario == "internal",
			update_stock=scenario == "update-stock",
			items=[row],
		)
		reads = []

		def single(dt, field):
			reads.append(("setting", dt, field))
			self.assertEqual((dt, field), ("Buying Settings", kind + "_required"))
			return "No" if scenario == "No" else "Yes"

		def value(dt, name, field, **kw):
			reads.append(("supplier", dt, name, field, kw))
			self.assertEqual((dt, name), ("Supplier", "SUPPLIER-RAW"))
			return scenario == "exempt"

		def values(dt, filters, **kw):
			reads.append(("stock", dt, filters, kw))
			return [] if scenario in ("nonstock", "asset") else ["ITEM-RAW"]

		self._database(get_single_value=single, get_value=value, get_values=values)
		return doc, reads

	def test_actual_prerequisite_matrix_and_owning_metadata(self):
		for kind in ("po", "pr"):
			for scenario in (
				"missing",
				"linked",
				"No",
				"exempt",
				"internal" if kind == "po" else "update-stock",
			):
				with self.subTest(kind=kind, scenario=scenario):
					self.meta_reads.clear()
					doc, reads = self._invoice(kind, scenario)
					before = deepcopy(doc.__dict__)
					if scenario == "missing":
						with self.assertRaises(frappe.ValidationError) as caught:
							getattr(doc, kind + "_required")()
						frame, label = (PO, PO_LABEL) if kind == "po" else (PR, PR_LABEL)
						link = '<a href="http://memory.invalid/desk/buying-settings/Buying%20Settings">Impostazioni acquisti</a>'
						self.assertTrue(
							str(caught.exception).endswith(
								TARGETS[frame].format(frappe.bold(TARGETS[label]), frappe.bold("No"), link)
							)
						)
						self.assertEqual(self.meta_reads, ["Buying Settings"])
					else:
						self.assertIsNone(getattr(doc, kind + "_required")())
						self.assertEqual(self.meta_reads, [])
					self._state(doc, before)
					self.assertEqual(reads[0], ("setting", "Buying Settings", kind + "_required"))
					if scenario in ("No", "internal", "update-stock"):
						self.assertEqual(len(reads), 1)

	def test_receipt_nonstock_and_fixed_asset_decisions(self):
		doc, _ = self._invoice("pr", "nonstock")
		self.assertIsNone(doc.pr_required())
		self.assertEqual(self.meta_reads, [])
		doc, _ = self._invoice("pr", "asset")
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.pr_required()
		self.assertIn(TARGETS[PR_LABEL], str(caught.exception))

	def test_custom_owner_label_and_english_fallback(self):
		self._meta("Buying Settings").get_field("po_required").label = "Custom question"
		self.messages["Custom question:Buying Settings"] = "Domanda personalizzata"
		doc, _ = self._invoice("po", "missing")
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.po_required()
		self.assertIn("<strong>Domanda personalizzata</strong>", str(caught.exception))
		self.messages.clear()
		doc, _ = self._invoice("po", "missing")
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.po_required()
		self.assertIn("<strong>Custom question</strong>", str(caught.exception))
		self.assertIn(">Buying Settings</a>", str(caught.exception))
