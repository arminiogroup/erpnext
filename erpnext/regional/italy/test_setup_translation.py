# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.core.doctype.docfield.docfield import DocField
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.regional.italy import setup

TARGETS = {
	"Tax Rate": "Aliquota fiscale",
	"Tax Amount": "Importo imposta",
	"Total Amount": "Importo totale",
	"Customer PO": "Ordine d'acquisto del cliente",
	"First Name": "Nome",
	"Last Name": "Cognome",
	"Code": "Codice",
	"Bank Account": "Conto bancario",
	"Bank Name": "Nome banca",
	"Bank Account No": "Numero di conto bancario",
	"IBAN": "IBAN",
	"Document Type": "Tipo di documento",
	"E-Invoicing": "Fatturazione elettronica",
	"Fiscal Regime": "Regime fiscale",
	"Fiscal Code": "Codice fiscale",
	"VAT Collectability": "Esigibilità IVA",
	"Province of the Registrar Office": "Provincia dell’ufficio REA",
	"Registration Number": "Numero di iscrizione REA",
	"Share Capital": "Capitale sociale",
	"No of Members": "Numero di soci",
	"Liquidation State": "Stato di liquidazione",
	"Tax Exemption Reason": "Natura dell’operazione IVA",
	"Tax Exempt Under": "Riferimento normativo",
	"Recipient Code": "Codice destinatario",
	"Recipient PEC": "PEC del destinatario",
	"Is Public Administration": "Pubblica amministrazione",
	"Swift Code (BIC)": "Codice SWIFT (BIC)",
	"Company Fiscal Code": "Codice fiscale dell’azienda",
	"Company Fiscal Regime": "Regime fiscale dell’azienda",
	"Customer Fiscal Code": "Codice fiscale del cliente",
	"Type of Document": "Tipo di documento",
	"Country Code": "Codice del paese",
	"State Code": "Sigla della provincia",
	"Destination Code": "Codice destinatario",
	"Imported Grand Total": "Totale complessivo importato",
	"Customer PO No": "Numero dell’ordine d’acquisto del cliente",
	"Customer PO Date": "Data dell’ordine d’acquisto del cliente",
}

DESCRIPTIONS = {
	"Applicable if the company is an Individual or a Proprietorship": "Applicabile alle persone fisiche e alle ditte individuali",
	"Applicable if the company is SpA, SApA or SRL": "Applicabile a SpA, SApA o SRL",
	"Applicable if the company is a limited liability company": "Applicabile alle società a responsabilità limitata",
	"Set this if the customer is a Public Administration company.": "Selezionare se il cliente è una pubblica amministrazione.",
}
EXISTING_SOURCES = {
	"erpnext": (
		"accounts/doctype/account/account.json",
		"accounts/doctype/item_wise_tax_detail/item_wise_tax_detail.json",
		"accounts/doctype/invoice_discounting/invoice_discounting.json",
		"stock/report/delayed_item_report/delayed_item_report.py",
		"accounts/doctype/bank_transaction/bank_transaction.json",
		"accounts/doctype/bank/bank.json",
		"accounts/doctype/bank_account/bank_account.json",
	),
	"frappe": (
		"contacts/doctype/contact/contact.json",
		"core/doctype/docfield/docfield.json",
		"automation/doctype/assignment_rule/assignment_rule.json",
	),
}
ITEM_DOCTYPES = (
	"Purchase Invoice Item",
	"POS Invoice Item",
	"Sales Order Item",
	"Delivery Note Item",
	"Quotation Item",
	"Purchase Order Item",
	"Purchase Receipt Item",
	"Supplier Quotation Item",
)


def extracted_keys(paths):
	keys = set()
	for app, path in paths:
		root = Path(frappe.get_app_path(app)).parent
		relative = str(path.relative_to(root))
		methods = get_method_map(app) if app != "frappe" else []
		methods += get_method_map("frappe")
		method = next(method for pattern, method in methods if pathmatch(pattern, relative))
		keys.update(
			message
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
			if context is None and isinstance(message, str)
		)
	return keys


class TestItalySetupTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"

	def factory(self, lang="en"):
		with (
			patch.object(frappe.local, "lang", lang),
			patch("frappe.translate.get_all_translations", return_value=DESCRIPTIONS if lang == "it" else {}),
		):
			return setup.get_custom_fields()

	def available_keys(self):
		sources = [("erpnext", Path(setup.__file__))]
		sources += [
			(app, Path(frappe.get_app_path(app, relative)))
			for app, paths in EXISTING_SOURCES.items()
			for relative in paths
		]
		return extracted_keys(sources)

	def prepare_docfield_cache(self):
		controllers = frappe.controllers.setdefault(frappe.local.site, {})
		missing = object()
		previous = controllers.get("DocField", missing)
		controllers["DocField"] = DocField

		def restore_controller():
			if previous is missing:
				controllers.pop("DocField", None)
			else:
				controllers["DocField"] = previous

		self.addCleanup(restore_controller)

		columns = frappe.local.valid_columns
		previous_columns = columns.get("DocField", missing)
		field_meta = json.loads(
			Path(frappe.get_app_path("frappe", "core/doctype/docfield/docfield.json")).read_text()
		)
		columns["DocField"] = [
			"name",
			"owner",
			"creation",
			"modified",
			"modified_by",
			"docstatus",
			"idx",
			"parent",
			"parenttype",
			"parentfield",
			*(field["fieldname"] for field in field_meta["fields"] if field.get("fieldname")),
		]

		def restore_columns():
			if previous_columns is missing:
				columns.pop("DocField", None)
			else:
				columns["DocField"] = previous_columns

		self.addCleanup(restore_columns)

	def test_configured_extractor_registers_all_labels_and_preserves_descriptions(self):
		keys = extracted_keys([("erpnext", Path(setup.__file__))])
		self.assertEqual(keys, TARGETS.keys() | DESCRIPTIONS.keys())
		self.assertNotIn("", keys)

	def test_factory_keeps_ordinary_raw_labels_and_all_schema_data_across_languages(self):
		english = self.factory()
		translated = {**TARGETS, **DESCRIPTIONS}
		with patch("frappe.translate.get_all_translations", return_value=translated) as provider:
			italian = setup.get_custom_fields()
		self.assertEqual(provider.call_count, len(DESCRIPTIONS))
		self.assertEqual(len(english), 19)
		self.assertEqual(sum(map(len, english.values())), 71)
		self.assertEqual(
			{df["label"] for rows in english.values() for df in rows if df.get("label")}, set(TARGETS)
		)
		normalized = deepcopy(italian)
		for doctype, fields in english.items():
			for index, source in enumerate(fields):
				target = normalized[doctype][index]
				if "label" in source:
					self.assertIs(type(target["label"]), str)
					self.assertEqual(target["label"], source["label"])
				if description := source.get("description"):
					self.assertEqual(target["description"], DESCRIPTIONS[description])
					target["description"] = description
		self.assertEqual(normalized, english)

	def test_shared_item_schema_aliasing_and_fresh_factory_calls_are_unchanged(self):
		fields = self.factory()
		item_fields = fields[ITEM_DOCTYPES[0]]
		for doctype in ITEM_DOCTYPES:
			self.assertIs(fields[doctype], item_fields)
		self.assertIsNot(fields["Sales Invoice Item"], item_fields)
		for index, field in enumerate(item_fields):
			self.assertIs(fields["Sales Invoice Item"][index], field)
		fresh = self.factory()
		self.assertEqual(fresh, fields)
		self.assertIsNot(fresh, fields)
		self.assertIsNot(fresh[ITEM_DOCTYPES[0]], item_fields)
		self.assertIsNot(fresh[ITEM_DOCTYPES[0]][0], item_fields[0])

	def test_statutory_option_and_field_identity_contracts_stay_canonical(self):
		fields = self.factory()
		lookup = {doctype: {field["fieldname"]: field for field in rows} for doctype, rows in fields.items()}
		company = lookup["Company"]
		self.assertEqual(company["no_of_members"]["options"], "\nSU-Socio Unico\nSM-Piu Soci")
		self.assertEqual(
			company["liquidation_state"]["options"], "\nLS-In Liquidazione\nLN-Non in Liquidazione"
		)
		self.assertEqual(
			company["vat_collectability"]["options"], "I-Immediata\nD-Differita\nS-Scissione dei Pagamenti"
		)
		self.assertEqual(company["registrar_office_province"]["length"], 2)
		self.assertEqual(company["registration_number"]["length"], 20)
		self.assertEqual(lookup["Customer"]["recipient_code"]["default"], "0000000")
		self.assertIn("italy_customer_first_name", lookup["Customer"])
		self.assertIn("italy_customer_last_name", lookup["Customer"])
		self.assertNotIn("first_name", lookup["Customer"])
		self.assertEqual(lookup["Sales Invoice Item"]["customer_po_no"]["fetch_from"], "sales_order.po_no")
		self.assertEqual(
			lookup["Sales Invoice Item"]["customer_po_date"]["fetch_from"], "sales_order.po_date"
		)
		self.assertEqual(lookup["Sales Invoice Item"]["customer_po_clm_brk"]["label"], "")
		self.assertEqual(
			lookup["Sales Invoice"]["company_fiscal_regime"]["fetch_from"], "company.fiscal_regime"
		)
		self.assertEqual(
			lookup["Sales Invoice"]["customer_fiscal_code"]["fetch_from"], "customer.fiscal_code"
		)

	def test_actual_meta_displays_registered_labels_and_serializes_raw_schema(self):
		self.prepare_docfield_cache()
		fields = self.factory()
		dictionary = {key: value for key, value in TARGETS.items() if key in self.available_keys()}
		reads = []

		class ReadOnlyCustomFields:
			def table_exists(_self, doctype):
				self.assertEqual(doctype, "Custom Field")
				reads.append(("table_exists", doctype))
				return True

			def get_values(_self, doctype, **kwargs):
				self.assertEqual(doctype, "Custom Field")
				parent = kwargs["filters"]["dt"]
				self.assertEqual(
					kwargs,
					{
						"filters": {"dt": parent},
						"fieldname": "*",
						"as_dict": True,
						"order_by": "idx",
						"update": {"is_custom_field": 1},
					},
				)
				reads.append(("get_values", parent))
				return [frappe._dict({**deepcopy(df), "is_custom_field": 1}) for df in fields[parent]]

		with (
			patch.object(frappe, "db", ReadOnlyCustomFields()),
			patch("frappe.get_disabled_apps", return_value=[]),
			patch("frappe.translate.get_all_translations", return_value=dictionary),
		):
			for doctype, raw_fields in fields.items():
				meta = Meta.__new__(Meta)
				meta.__dict__.update(doctype="DocType", name=doctype, fields=[], flags=frappe._dict())
				meta.add_custom_fields()
				self.assertEqual(len(meta.fields), len(raw_fields))
				wire = meta.as_dict()
				for raw, df, serialized in zip(raw_fields, meta.fields, wire.fields, strict=True):
					with self.subTest(doctype=doctype, fieldname=df.fieldname):
						self.assertIsInstance(df, DocField)
						self.assertEqual(df.parent, doctype)
						self.assertEqual(df.parenttype, "DocType")
						self.assertEqual(df.parentfield, "fields")
						for key, value in raw.items():
							self.assertEqual(serialized[key], value)
						self.assertIs(type(serialized.get("label")), str if "label" in raw else type(None))
						if raw.get("label"):
							self.assertEqual(meta.get_translated_label(df.fieldname), TARGETS[raw["label"]])
						elif "label" in raw:
							self.assertEqual(serialized["label"], "")
		self.assertEqual(
			reads,
			[
				read
				for doctype in fields
				for read in (("table_exists", "Custom Field"), ("get_values", doctype))
			],
		)
