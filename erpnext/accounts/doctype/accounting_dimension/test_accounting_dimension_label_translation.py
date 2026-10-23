# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from unittest.mock import Mock, call, patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import AccountingDimension
from erpnext.accounts.doctype.budget.test_budget_label_translation import (
	FORBIDDEN_DIMENSION,
	METADATA,
	STANDALONE_DIMENSION,
	TARGETS,
	extracted_keys,
	make_doc,
	raise_message,
	read_meta,
)


class TestAccountingDimensionLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={k: v for k, v in TARGETS.items() if k in keys},
			)
		)
		self.metas = {doctype: read_meta(module, doctype) for module, doctype in METADATA}
		self.meta_reads = self.enterContext(
			patch("frappe.get_meta", side_effect=lambda name: self.metas[name])
		)
		self.throw = self.enterContext(patch("frappe.throw", side_effect=raise_message))
		self.db = self.enterContext(patch("frappe.db", new=Mock()))
		self.db.get_value.return_value = None

	def dimension(self, document_type, new=True):
		return make_doc(
			AccountingDimension,
			doctype="Accounting Dimension",
			name="DIMENSION-RAW",
			document_type=document_type,
			fieldname="axis_raw",
			label="LABEL-RAW",
			__islocal=int(new),
		)

	def error(self, method):
		with self.assertRaises(frappe.ValidationError) as caught:
			method()
		return str(caught.exception)

	def test_configured_python_and_javascript_extractors_register_actual_keys(self):
		keys = extracted_keys()
		for key in (
			FORBIDDEN_DIMENSION,
			STANDALONE_DIMENSION,
			"Show {0}",
			"Project",
			"Sales Team",
			"Selling Settings",
		):
			self.assertIn(key, keys)

	def test_forbidden_doctypes_translate_only_error_argument(self):
		for source, target in (
			("Project", "Progetto"),
			("Cost Center", "Centro di costo"),
			("Company", "Azienda"),
			("Account", "Conto"),
			("Accounting Dimension", "Dimensione contabile"),
		):
			with self.subTest(doctype=source):
				doc = self.dimension(source)
				before = doc.__dict__.copy()
				self.assertEqual(
					self.error(doc.validate_doctype),
					f"Non è consentito creare una dimensione contabile per {target}.",
				)
				self.assertEqual(doc.__dict__, before)
		self.meta_reads.assert_not_called()
		self.db.get_value.assert_not_called()

	def test_real_child_table_and_single_metadata_translate_error_argument(self):
		for source, target in (
			("Sales Team", "Team di vendita"),
			("Selling Settings", "Impostazioni di vendita"),
		):
			with self.subTest(doctype=source):
				doc = self.dimension(source)
				self.assertEqual(
					self.error(doc.validate_doctype),
					f"<strong>{target}</strong> non può essere usato come dimensione contabile perché non è un tipo di documento autonomo.",
				)
				self.assertEqual(doc.document_type, source)
		self.assertEqual(self.meta_reads.call_args_list, [call("Sales Team"), call("Selling Settings")])
		self.assertTrue(self.metas["Sales Team"].istable)
		self.assertTrue(self.metas["Selling Settings"].issingle)
		self.db.get_value.assert_not_called()

	def test_valid_new_standalone_dimension_uses_raw_canonical_read(self):
		doc = self.dimension("Warehouse")
		before = doc.__dict__.copy()
		doc.validate_doctype()
		self.meta_reads.assert_called_once_with("Warehouse")
		self.db.get_value.assert_called_once_with(
			"Accounting Dimension", {"document_type": "Warehouse"}, ["name"]
		)
		self.throw.assert_not_called()
		self.assertEqual(doc.__dict__, before)

	def test_duplicate_new_dimension_keeps_canonical_lookup_and_rejection(self):
		self.db.get_value.return_value = "EXISTING-DIMENSION-RAW"
		doc = self.dimension("Warehouse")
		self.assertEqual(self.error(doc.validate_doctype), "Tipo di documento già usato come dimensione")
		self.db.get_value.assert_called_once_with(
			"Accounting Dimension", {"document_type": "Warehouse"}, ["name"]
		)
		self.assertEqual(doc.document_type, "Warehouse")

	def test_existing_unchanged_type_runs_actual_change_validation(self):
		self.db.get_value.side_effect = ["DIMENSION-RAW", "Warehouse"]
		doc = self.dimension("Warehouse", new=False)
		doc.validate_doctype()
		self.assertEqual(
			self.db.get_value.call_args_list,
			[
				call("Accounting Dimension", {"document_type": "Warehouse"}, ["name"]),
				call("Accounting Dimension", "DIMENSION-RAW", "document_type"),
			],
		)
		self.throw.assert_not_called()

	def test_existing_changed_type_keeps_original_rejection_and_raw_values(self):
		self.db.get_value.side_effect = [None, "PREVIOUS-TYPE-RAW"]
		doc = self.dimension("Warehouse", new=False)
		self.assertEqual(
			self.error(doc.validate_doctype),
			"Cannot change Reference Document Type.Please create a new Accounting Dimension if required.",
		)
		self.assertEqual(
			(doc.document_type, doc.name, doc.fieldname, doc.label),
			("Warehouse", "DIMENSION-RAW", "axis_raw", "LABEL-RAW"),
		)

	def test_english_fallback_preserves_canonical_type_and_markup(self):
		frappe.local.lang = "en"
		self.enterContext(patch("frappe.translate.get_all_translations", return_value={}))
		self.assertEqual(
			self.error(self.dimension("Project").validate_doctype),
			"Not allowed to create accounting dimension for Project",
		)
		self.assertEqual(
			self.error(self.dimension("Sales Team").validate_doctype),
			"<strong>Sales Team</strong> cannot be used as an accounting dimension as it is not a standalone document type.",
		)
