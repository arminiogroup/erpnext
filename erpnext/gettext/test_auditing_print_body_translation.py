# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
import logging
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.core.doctype.docfield.docfield import DocField
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.printing.doctype.print_format.classic_converter import uses_beta_renderer
from frappe.printing.layout import iter_fields, iter_zones
from frappe.tests import UnitTestCase
from frappe.utils.print_format_generator import PrintFormatGenerator
from frappe.www.printview import get_print_format

import erpnext

TRANSLATIONS = {
	"Debit Amount": "Importo in Dare",
	"Credit Amount": "Importo in Avere",
	"Quantity": "Quantità",
	"Voucher No": "N. documento",
	"Date": "Data",
	"Account": "Conto",
	"Party Type": "Tipo di controparte",
	"Party": "Controparte",
	"Amount": "Importo",
	"Debit": "Dare",
	"Credit": "Avere",
	"Narration": "Causale",
	"Total (debit)": "Totale dare",
	"Total (credit)": "Totale avere",
	"Supplier": "Fornitore",
	"Customer": "Cliente",
	"Supplier Name": "Nome del fornitore",
	"Customer Name": "Nome cliente",
	"Due Date": "Data di scadenza",
	"Address": "Indirizzo",
	"Contact": "Contatto",
	"Mobile No": "Numero di cellulare",
	"No.": "N.",
	"Item Code": "Codice articolo",
	"Item Name": "Nome articolo",
	"UOM": "UdM",
	"Received Qty": "Quantità ricevuta",
	"Rejected Qty": "Quantità rifiutata",
	"Qty": "Quantità",
	"Rate": "Prezzo unitario",
	"Total Quantity": "Quantità totale",
	"Total": "Totale",
	"Net Weight": "Peso netto",
	"Taxes and Charges": "Imposte e oneri",
	"Taxes and Charges Added": "Imposte e oneri aggiunti",
	"Taxes and Charges Deducted": "Imposte e oneri detratti",
	"Total Taxes and Charges": "Totale imposte e oneri",
	"Grand Total": "Totale generale",
	"Purchase Order": "Ordine d'acquisto",
	"Customer Address": "Indirizzo cliente",
	"Customer Contact": "Contatto cliente",
	"Customer Mobile No": "Numero di cellulare del cliente",
	"Description": "Descrizione",
	"In Words": "In lettere",
	"Purchase Receipt": "Ricevimento merci",
	"Serial Nos": "Numeri di serie",
	"Batch Nos (Qty)": "Lotti (quantità)",
	"Prepared By": "Preparato da",
	"Authorised Signatory": "Firmatario autorizzato",
	"Received Payment as Above": "Ricevuto il pagamento sopra indicato",
	"Purchase Invoice": "Fattura di acquisto",
	"Payment Entry": "Registrazione di pagamento",
	"Journal Entry": "Registrazione contabile",
	"Sales Invoice": "Fattura di vendita",
	"Voucher Date": "Data del documento",
	"Reference / Cheque No.": "Numero di riferimento / dell’assegno",
	"Reference / Cheque Date": "Data di riferimento / dell’assegno",
	"Payment Advice": "Avviso di pagamento",
	"This amount is in full / part settlement of the listed bills": "Questo importo è a saldo totale o parziale dei documenti elencati",
	"References": "Riferimenti",
}


class PrintReadResults:
	"""Read results only: no database execution or authorization is under test."""

	def __init__(self):
		self.reads = []
		self.gl_rows = [
			frappe._dict(
				account="ACCOUNT<&>-RAW",
				party_type="Customer",
				party="PARTY<&>-RAW",
				debit=12.5,
				credit=0.0,
				remarks="REMARKS<b>RAW</b>",
			),
			frappe._dict(
				account="BANK<&>-RAW",
				party_type="Supplier",
				party="SUPPLIER-ID-RAW",
				debit=0.0,
				credit=12.5,
				remarks="SECOND-RAW",
			),
		]
		self.bundle_rows = [frappe._dict(serial_no="SERIAL<&>-RAW", batch_no="BATCH<&>-RAW", qty=12.5)]
		self.custom_module = False

	def __bool__(self):
		return False

	def get_list(self, *args, **kwargs):
		doctype = args[0] if args else kwargs.get("doctype")
		self.reads.append(("get_list", deepcopy(args), deepcopy(kwargs)))
		if doctype == "GL Entry":
			return deepcopy(self.gl_rows)
		assert doctype == "Serial and Batch Entry", (args, kwargs)
		return deepcopy(self.bundle_rows)

	def get_value(self, doctype, name, field=None, *args, **kwargs):
		self.reads.append(("get_value", doctype, deepcopy(name), field, deepcopy(kwargs)))
		if doctype == "DocType":
			assert field == "module"
			return {"Purchase Order": "Buying", "Purchase Receipt": "Stock"}.get(name, "Accounts")
		if doctype == "Currency":
			return None
		if doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
			assert field == "currency"
			return None
		assert doctype in ("Supplier", "Company", "Address"), (doctype, name, field)
		return f"{doctype}:{field}:RAW<&>"

	def cached(self, doctype, name, field):
		self.reads.append(("cached", doctype, name, field))
		if doctype == "Module Def" and field == "custom":
			return int(self.custom_module)
		assert (doctype, name, field) == ("Company", "COMPANY-RAW", "default_currency")
		return None

	def get_default(self, key):
		self.reads.append(("get_default", key))
		return {
			"currency": None,
			"currency_precision": "2",
			"float_precision": "3",
			"number_format": "#,###.##",
			"date_format": "yyyy-mm-dd",
			"time_format": "HH:mm:ss",
		}.get(key)

	def __getattr__(self, name):
		def blocked(*args, **kwargs):
			raise AssertionError(f"Unexpected database operation: {name}")

		return blocked


class MemoryPrintFixture:
	"""Private fixture shared by the special-body tests, with no discoverable tests."""

	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		for key in (
			"lang",
			"db",
			"module_app",
			"user_date_format",
			"user_time_format",
			"jenv_restricted",
			"jenv_unrestricted",
			"field_currency",
			"message_log",
		):
			self.addCleanup(setattr, frappe.local, key, getattr(frappe.local, key, None))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.module_app = {name: "erpnext" for name in ("accounts", "stock", "buying", "regional")}
		frappe.local.user_date_format = "yyyy-mm-dd"
		frappe.local.user_time_format = "HH:mm:ss"
		frappe.local.message_log = []
		self.db = PrintReadResults()
		frappe.local.db = self.db
		self.metas, self.regional_fields = {}, {}
		self.field_definition = json.loads(
			(Path(frappe.__file__).parent / "core/doctype/docfield/docfield.json").read_text()
		)
		self.field_defaults = {df["fieldname"]: None for df in self.field_definition["fields"]}
		self._dictionary = None
		self.enterContext(patch("frappe.get_meta", side_effect=self.get_meta))
		self.enterContext(patch("frappe.get_hooks", return_value=[]))
		self.enterContext(patch("frappe.get_active_apps", return_value=["frappe"]))
		self.enterContext(patch("frappe.get_roles", return_value=["Translator"]))
		self.enterContext(
			patch("frappe.utils.logger.get_logger", return_value=logging.getLogger("memory-print-body-test"))
		)
		self.enterContext(
			patch(
				"frappe.client_cache.get_doc",
				side_effect=lambda doctype, name: frappe._dict(
					date_format="yyyy-mm-dd", time_format="HH:mm:ss", number_format="#,###.##"
				),
			)
		)
		self.enterContext(patch("frappe.get_cached_value", side_effect=self.db.cached))
		# The real unrestricted get_list/get_all and restricted wrappers retain their
		# arguments; only the database query's result boundary is supplied.
		self.enterContext(
			patch(
				"frappe.model.qb_query.DatabaseQuery.execute",
				new=lambda query, *args, **kwargs: self.db.get_list(query.doctype, *args, **kwargs),
			)
		)
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.dictionary() if lang == "it" else {},
			)
		)

	def source_path(self, path):
		return Path(erpnext.__file__).parent.parent / path

	def extracted(self, path):
		methods = get_method_map("erpnext") + get_method_map("frappe")
		relative = str(path.relative_to(Path(erpnext.__file__).parent.parent))
		method = next(method for pattern, method in methods if pathmatch(pattern, relative))
		expected = (
			"frappe.gettext.extractors.print_format.extract"
			if path.suffix == ".json"
			else "frappe.gettext.extractors.html_template.extract"
			if path.suffix == ".html"
			else "frappe.gettext.extractors.python.extract"
		)
		self.assertEqual(method, expected)
		return list(extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS))

	def dictionary(self):
		if self._dictionary is None:
			keys = {
				message + (":" + context if context else "")
				for path in self.FORMAT_PATHS
				for _, message, _, context in self.extracted(self.source_path(path))
				if isinstance(message, str)
			}
			self._dictionary = {key: value for key, value in TRANSLATIONS.items() if key in keys}
		return self._dictionary

	def get_meta(self, name, cached=True):
		if name in self.metas:
			return self.metas[name]
		if name == "DocField":
			definition = self.field_definition
		else:
			paths = list(
				Path(erpnext.__file__).parent.glob(
					f"*/doctype/{frappe.scrub(name)}/{frappe.scrub(name)}.json"
				)
			)
			if not paths:
				paths = list(
					Path(frappe.__file__).parent.glob(
						f"*/doctype/{frappe.scrub(name)}/{frappe.scrub(name)}.json"
					)
				)
			self.assertEqual(len(paths), 1, name)
			definition = json.loads(paths[0].read_text())
		meta = Meta.__new__(Meta)
		meta.__dict__.update(
			{key: deepcopy(value) for key, value in definition.items() if key != "fields"},
			doctype="DocType",
			name=name,
			fields=[],
		)
		self.metas[name] = meta
		field_meta = meta if name == "DocField" else self.get_meta("DocField")
		for row in definition["fields"] + self.regional_fields.get(name, []):
			if any(field.fieldname == row.get("fieldname") for field in meta.fields):
				continue
			field = DocField.__new__(DocField)
			field.__dict__.update(self.field_defaults)
			field.__dict__.update(row, parent=name, doctype="DocField", meta=field_meta, flags=frappe._dict())
			meta.fields.append(field)
		meta._fields = {field.fieldname: field for field in meta.fields}
		return meta

	def document(self, doctype, empty=False):
		meta = self.get_meta(doctype)
		doc = Document.__new__(Document)
		doc.__dict__.update({field.fieldname: None for field in meta.fields})
		doc.__dict__.update(
			doctype=doctype,
			name="DOCUMENT-RAW",
			meta=meta,
			flags=frappe._dict(),
			docstatus=1,
			idx=1,
			company="COMPANY-RAW",
			supplier="SUPPLIER-ID-RAW",
			supplier_name="SUPPLIER-NAME<&>",
			customer="CUSTOMER-ID-RAW",
			customer_name="CUSTOMER-NAME<&>",
			creation="2026-10-20 10:00:00",
			posting_date="2026-10-21",
			transaction_date="2026-10-21",
			due_date="2026-11-01",
			address_display="ADDRESS<b>RAW</b>",
			contact_display="CONTACT<&>-RAW",
			contact_mobile="MOBILE-RAW",
			shipping_address_display="SHIP<b>RAW</b>",
			customer_contact_display="CUSTOMER-CONTACT-RAW",
			customer_contact_mobile="CUSTOMER-MOBILE-RAW",
			supplier_address="SUPPLIER-ADDRESS-RAW",
			shipping_address="SHIP-ADDRESS-RAW",
			taxes_and_charges="TAX-TEMPLATE-RAW",
			total_qty=12.5,
			total=156.25,
			grand_total=165.75,
			base_net_total=156.25,
			base_grand_total=165.75,
			total_net_weight=10,
			taxes_and_charges_added=9.5,
			taxes_and_charges_deducted=0,
			total_taxes_and_charges=9.5,
			in_words="AMOUNT-IN-WORDS-RAW",
			total_amount=12.5,
			total_amount_in_words="TWELVE-RAW",
			cheque_no="CHEQUE-RAW",
			cheque_date="2026-10-21",
			pay_to_recd_from="PAYEE-RAW",
			remark="REMARKS-RAW",
			items=[],
			taxes=[],
			payment_schedule=[],
			currency=None,
			print_templates=None,
			child_print_templates=None,
		)
		if not empty and meta.get_field("items"):
			doc.items = [self.document_row(meta.get_field("items").options, doc)]
		if not empty and meta.get_field("taxes"):
			row = self.document_row(meta.get_field("taxes").options, doc)
			row.__dict__.update(
				account_head="TAX-ACCOUNT<&>-RAW",
				tax_amount_after_discount_amount=9.5,
				tax_amount=9.5,
				description="TAX-DESCRIPTION-RAW",
				tax_rate="22",
			)
			doc.taxes = [row]
		if not empty and meta.get_field("payment_schedule"):
			row = self.document_row(meta.get_field("payment_schedule").options, doc)
			row.__dict__.update(
				mode_of_payment_code="MP05-RAW",
				bank_account_iban="IBAN-RAW",
				due_date="2026-11-01",
				payment_amount=165.75,
			)
			doc.payment_schedule = [row]
		return doc

	def document_row(self, doctype, parent):
		meta = self.get_meta(doctype)
		row = Document.__new__(Document)
		row.__dict__.update({field.fieldname: None for field in meta.fields})
		row.__dict__.update(
			doctype=doctype,
			name="ROW-RAW",
			parent="DOCUMENT-RAW",
			parenttype=parent.doctype,
			meta=meta,
			flags=frappe._dict(),
			idx=1,
			item_code="ITEM<&>-RAW",
			item_name="ITEM-NAME<&>-RAW",
			description="ITEM-DESCRIPTION<b>RAW</b>",
			uom="Nos",
			stock_uom="Nos",
			qty=12.5,
			received_qty=14,
			rejected_qty=1.5,
			rate=12.5,
			amount=156.25,
			serial_and_batch_bundle="BUNDLE-RAW",
			currency=None,
			warehouse="WAREHOUSE-RAW",
		)
		return row

	def state(self, value):
		if isinstance(value, Meta):
			return {"actual_meta_owner": value.name}
		if isinstance(value, Document):
			return {key: self.state(item) for key, item in value.__dict__.items()}
		if isinstance(value, dict):
			return {key: self.state(item) for key, item in value.items()}
		if isinstance(value, list | tuple):
			return [self.state(item) for item in value]
		return value

	def environment(self, language="it", restricted=True):
		frappe.local.lang = language
		frappe.local.field_currency = {}
		frappe.local.jenv_restricted = None
		frappe.local.jenv_unrestricted = None
		return frappe.get_jenv(restrict_globals=restricted)

	def render(self, path, language="it", restricted=True, empty=False, update=None):
		path = self.source_path(path)
		definition = json.loads(
			path.read_text() if path.suffix == ".json" else path.with_suffix(".json").read_text()
		)
		definition_before = deepcopy(definition)
		doc = self.document(definition["doc_type"], empty)
		if update:
			update(doc)
		before = self.state(doc)
		meta = doc.meta
		self.db.reads = []
		env = self.environment(language, restricted)
		generator, layout = None, None
		if uses_beta_renderer(frappe._dict(definition)):
			generator = PrintFormatGenerator.__new__(PrintFormatGenerator)
			generator.doc = doc
			generator.print_settings = frappe._dict(print_uom_after_quantity=0)
			generator._logged_conditions = set()
			generator.print_format = frappe._dict(deepcopy(definition), flags=frappe._dict())
			generator.context, generator.style_name = frappe._dict(), None
			layout = generator.get_layout(generator.print_format)
			macro = env.get_template("templates/print_format/macros.html").module
			html = "".join(
				str(macro.render_field(frappe._dict(field), doc))
				for _, zone in iter_zones(layout)
				for field in iter_fields(zone)
				if field.get("fieldtype") == "HTML"
			)
		else:
			template = get_print_format(doc.doctype, frappe._dict(definition))
			self.assertEqual(template, path.read_text() if path.suffix == ".html" else definition["html"])
			html = env.from_string(template).render(
				doc=doc, letter_head=None, no_letterhead=True, print_settings=frappe._dict()
			)
		self.assertIs(doc.meta, meta)
		self.assertEqual(definition, definition_before)
		self.assertEqual(frappe.session.user, "translator@example.com")
		return frappe._dict(
			html=html,
			generator=generator,
			layout=layout,
			doc=doc,
			before=before,
			after=self.state(doc),
			reads=deepcopy(self.db.reads),
			definition=definition,
		)


class TestAuditingPrintBodyTranslation(MemoryPrintFixture, UnitTestCase):
	FORMAT_PATHS = tuple(
		f"erpnext/accounts/print_format/{name}/{name}.html"
		for name in (
			"bank_and_cash_payment_voucher",
			"journal_auditing_voucher",
			"purchase_auditing_voucher",
			"sales_auditing_voucher",
		)
	)

	def test_actual_file_templates_translate_accounting_captions_in_both_factories(self):
		for path in self.FORMAT_PATHS:
			for restricted in (True, False):
				with self.subTest(path=path, restricted=restricted):
					result = self.render(path, restricted=restricted)
					for text in (
						"N. documento",
						"Data",
						"Conto",
						"Tipo di controparte",
						"Controparte",
						"Importo",
						"Dare",
						"Avere",
					):
						self.assertIn(text, result.html)
					self.assertIn("ACCOUNT<&>-RAW", result.html)
					self.assertIn("PARTY<&>-RAW", result.html)
					self.assertIn("12.5", result.html)
					self.assertEqual(result.definition["default_print_language"], "en")
					self.assertFalse(uses_beta_renderer(frappe._dict(result.definition)))

	def test_narration_and_total_debit_credit_have_accounting_meaning(self):
		for path in self.FORMAT_PATHS[:2]:
			for restricted in (True, False):
				result = self.render(path, restricted=restricted)
				for text in ("Causale", "Totale dare", "Totale avere", "12.5"):
					self.assertIn(text, result.html)
				self.assertIn("REMARKS<b>RAW</b>", result.html)
				self.assertIn(
					"Customer", result.html
				)  # Existing raw canonical DocType display is outside this fix.

	def test_invoice_captions_match_raw_party_links_unit_rate_grand_total_and_ordinals(self):
		for path, party in zip(self.FORMAT_PATHS[2:], ("Fornitore", "Cliente"), strict=True):
			for language in ("en", "it"):
				for restricted in (True, False):
					with self.subTest(path=path, language=language, restricted=restricted):
						result = self.render(path, language, restricted)
						label = (
							party if language == "it" else "Supplier" if party == "Fornitore" else "Customer"
						)
						self.assertIn(f"<strong>{label}: </strong>", result.html)
						for source in ("Rate", "Grand Total", "No.", "Taxes and Charges"):
							self.assertIn(TRANSLATIONS[source] if language == "it" else source, result.html)
						self.assertNotIn("Basic Rate", result.html)
						self.assertNotIn("Net Payable", result.html)
						self.assertIn("165.75", result.html)
						self.assertEqual(
							(result.doc.items[0].qty, result.doc.items[0].rate, result.doc.items[0].amount),
							(12.5, 12.5, 156.25),
						)

	def test_actual_GL_query_requests_and_complete_document_state_stay_canonical(self):
		for path in self.FORMAT_PATHS:
			pairs = []
			for language in ("en", "it"):
				for restricted in (True, False):
					result = self.render(path, language, restricted)
					requests = [read for read in result.reads if read[0] == "get_list"]
					self.assertEqual(len(requests), 1)
					args, kwargs = requests[0][1:]
					self.assertEqual(args, () if restricted else ("GL Entry",))
					if restricted:
						self.assertEqual(kwargs["doctype"], "GL Entry")
					else:
						self.assertNotIn("doctype", kwargs)
					self.assertEqual(
						kwargs["fields"], ["account", "party_type", "party", "debit", "credit", "remarks"]
					)
					self.assertEqual(
						kwargs["filters"], {"voucher_type": result.doc.doctype, "voucher_no": "DOCUMENT-RAW"}
					)
					state = deepcopy(result.after)
					state.pop("select_print_heading")
					before = deepcopy(result.before)
					before.pop("select_print_heading", None)
					self.assertEqual(set(state) - set(before), {"_table_fieldnames"})
					self.assertIs(result.doc.__dict__["_table_fieldnames"], result.doc.meta._table_doctypes)
					self.assertEqual(
						state.pop("_table_fieldnames"),
						{field.fieldname: field.options for field in result.doc.meta._table_fields},
					)
					self.assertEqual(state, before)
					pairs.append(result.before)
			self.assertTrue(all(state == pairs[0] for state in pairs))

	def test_tax_visibility_receipt_quantities_and_actual_journal_posting_date(self):
		for restricted in (True, False):
			journal = self.render(self.FORMAT_PATHS[1], restricted=restricted)
			self.assertIn("2026-10-21", journal.html)
			self.assertNotIn("2026-10-20", journal.html)
			purchase = self.render(self.FORMAT_PATHS[2], restricted=restricted)
			for text in (TRANSLATIONS["Received Qty"], TRANSLATIONS["Rejected Qty"], "14", "1.5"):
				self.assertIn(text, purchase.html)
			self.assertNotIn(TRANSLATIONS["Taxes and Charges Deducted"], purchase.html)

			def zero_tax(doc):
				doc.taxes[0].tax_amount_after_discount_amount = 0
				doc.taxes_and_charges_added = 0
				doc.taxes_and_charges_deducted = 2

			hidden = self.render(self.FORMAT_PATHS[2], restricted=restricted, update=zero_tax)
			self.assertNotIn("TAX-ACCOUNT", hidden.html)
			self.assertNotIn(TRANSLATIONS["Taxes and Charges Added"], hidden.html)
			self.assertIn(TRANSLATIONS["Taxes and Charges Deducted"], hidden.html)
			for path in self.FORMAT_PATHS[2:]:
				empty = self.render(path, restricted=restricted, empty=True)
				self.assertNotIn("ITEM-NAME", empty.html)
				self.assertIn("ACCOUNT<&>-RAW", empty.html)

	def test_authored_HTML_and_trusted_business_markup_keep_existing_behavior(self):
		for restricted in (True, False):
			env = self.environment(restricted=restricted)
			self.db.custom_module = True
			definition = frappe._dict(
				name="Authored Format",
				module="Accounts",
				disabled=0,
				custom_format=1,
				html="<h2>Account</h2>{{ doc.address_display }}",
				raw_printing=0,
			)
			template = get_print_format("Purchase Invoice", definition)
			self.assertEqual(template, definition.html)
			html = env.from_string(template).render(doc=self.document("Purchase Invoice"))
			self.assertEqual(html, "<h2>Account</h2>ADDRESS<b>RAW</b>")
			self.db.custom_module = False
			self.assertIn("ADDRESS<b>RAW</b>", self.render(self.FORMAT_PATHS[2], restricted=restricted).html)

	def test_configured_extractor_registers_real_body_keys_and_excludes_test_source(self):
		keys = {
			(message, context)
			for path in self.FORMAT_PATHS
			for _, message, _, context in self.extracted(self.source_path(path))
		}
		for source in (
			"Narration",
			"Total (debit)",
			"Total (credit)",
			"Supplier",
			"Customer",
			"Rate",
			"Grand Total",
			"No.",
		):
			self.assertIn((source, None), keys)
		self.assertEqual(self.extracted(Path(__file__)), [])
