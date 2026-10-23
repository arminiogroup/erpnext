import json
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils.formatters import format_value

from erpnext.utilities.report.youtube_interactions.youtube_interactions import (
	get_chart_summary_data,
	get_columns,
)


class TestMetadataTranslationContexts(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"

	def test_medium_metadata_labels_distinguish_channels_from_priority(self):
		with Path(__file__).with_name("contextual_messages.py").open("rb") as file:
			messages = {
				(message, context)
				for _line, message, _comments, context in extract(
					"frappe.gettext.extractors.python.extract", file, keywords=PYTHON_KEYWORDS
				)
			}
		cases = [
			("accounts", "pos_invoice", "utm_medium", "UTM Medium"),
			("accounts", "pos_profile", "utm_medium", "UTM Campaign"),
			("accounts", "sales_invoice", "utm_medium", "UTM Medium"),
			("crm", "lead", "utm_medium", "UTM Medium"),
			("crm", "opportunity", "utm_medium", "UTM Medium"),
			("selling", "quotation", "utm_medium", "UTM Medium"),
			("selling", "sales_order", "utm_medium", "UTM Medium"),
			("stock", "delivery_note", "utm_medium", "UTM Medium"),
			("telephony", "call_log", "medium", None),
		]
		for module, doctype, fieldname, options in cases:
			with self.subTest(doctype=doctype):
				metadata = json.loads(
					Path(
						frappe.get_app_path("erpnext", module, "doctype", doctype, f"{doctype}.json")
					).read_text()
				)
				fields = {field["fieldname"]: frappe._dict(field) for field in metadata["fields"]}
				meta = Meta.__new__(Meta)
				meta.__dict__.update(name=metadata["name"], _fields=fields)
				translations = {"Medium": "Media"}
				if ("Medium", meta.name) in messages:
					translations[f"Medium:{meta.name}"] = "Canale"
				with patch("frappe.translate.get_all_translations", return_value=translations):
					self.assertEqual(meta.get_translated_label(fieldname), "Canale")
					self.assertEqual(frappe._("Medium"), "Media")
				self.assertEqual(meta.get_label(fieldname), "Medium")
				self.assertEqual(fields[fieldname].options, options)
		for doctype in ("project", "task"):
			with self.subTest(priority=doctype):
				metadata = json.loads(
					Path(
						frappe.get_app_path("erpnext", "projects", "doctype", doctype, f"{doctype}.json")
					).read_text()
				)
				field = next(field for field in metadata["fields"] if field["fieldname"] == "priority")
				options = field["options"]
				with patch("frappe.translate.get_all_translations", return_value={"Medium": "Media"}):
					self.assertEqual(
						format_value("Medium", frappe._dict(field, parent=metadata["name"])), "Media"
					)
					self.assertEqual(frappe.N_("Medium"), "Medium")
				self.assertEqual(field["options"], options)
		web_form = json.loads(
			Path(frappe.get_app_path("erpnext", "projects", "web_form", "tasks", "tasks.json")).read_text()
		)
		priority = next(field for field in web_form["web_form_fields"] if field["fieldname"] == "priority")
		self.assertEqual(priority["options"], "Low\nMedium\nHigh\nUrgent")

	def test_metadata_labels_keep_business_context(self):
		cases = [
			("manufacturing", "work_order", "time", "Tempi"),
			("manufacturing", "work_order_operation", "time_in_mins", "Tempo (minuti)"),
			("accounts", "sales_invoice_timesheet", "section_break_3", "Tempi"),
			("utilities", "video", "view_count", "Visualizzazioni"),
			("accounts", "bank_account", "mask", "Cifre finali del conto"),
			("accounts", "applicable_on_account", "applicable_on_account", "Conti"),
			("accounts", "bank_transaction_rule", "accounts", "Conti"),
			("accounts", "mode_of_payment", "accounts", "Conti"),
			("accounts", "payment_entry", "payment_accounts_section", "Conti"),
			("accounts", "tax_withholding_category", "accounts", "Conti"),
			("assets", "asset_category", "accounts", "Conti"),
			("setup", "customer_group", "accounts", "Conti"),
			("setup", "supplier_group", "accounts", "Conti"),
			(
				"accounts",
				"accounting_dimension_detail",
				"reference_document",
				"Tipo di documento di riferimento",
			),
			("stock", "inventory_dimension", "reference_document", "Tipo di documento di riferimento"),
			("quality_management", "quality_meeting_minutes", "minute", "Verbale"),
		]
		with Path(__file__).with_name("contextual_messages.py").open("rb") as file:
			messages = {
				(message, context)
				for _line, message, _comments, context in extract(
					"frappe.gettext.extractors.python.extract", file, keywords=PYTHON_KEYWORDS
				)
			}
		translations = {
			"Time": "Ora",
			"Reference Document": "Documento di riferimento",
			"Minute": "Minuto",
			"Views": "Viste",
			"Accounts": "Contabilità",
			"Mask": "Visualizza dati non oscurati",
		}
		for module, doctype, fieldname, target in cases:
			with self.subTest(doctype=doctype, fieldname=fieldname):
				metadata = json.loads(
					Path(
						frappe.get_app_path("erpnext", module, "doctype", doctype, f"{doctype}.json")
					).read_text()
				)
				fields = {field["fieldname"]: field for field in metadata["fields"]}
				field = fields[fieldname]
				context = metadata["name"]
				self.assertIn((field["label"], context), messages)
				translations[f"{field['label']}:{context}"] = target
				with patch("frappe.translate.get_all_translations", return_value=translations):
					meta = frappe._dict(name=context, get_label=lambda name: fields[name]["label"])
					self.assertEqual(Meta.get_translated_label(meta, fieldname), target)

	def test_video_report_and_chart_count_views_without_changing_data(self):
		translations = {"Views": "Viste", "Views:Video": "Visualizzazioni", "Likes": "Mi piace"}
		rows = [{"title": "Video dimostrativo", "like_count": 2, "view_count": 7}]
		with patch("frappe.translate.get_all_translations", return_value=translations):
			columns = get_columns()
			chart, summary = get_chart_summary_data(rows)
		self.assertEqual(
			next(column for column in columns if column["fieldname"] == "view_count")["label"],
			"Visualizzazioni",
		)
		self.assertEqual(
			chart["data"]["datasets"],
			[{"name": "Mi piace", "values": [2]}, {"name": "Visualizzazioni", "values": [7]}],
		)
		self.assertEqual(chart["data"]["labels"], ["Video dimostrativo"])
		self.assertEqual(summary[0]["value"], 7)
		self.assertEqual(rows, [{"title": "Video dimostrativo", "like_count": 2, "view_count": 7}])

	def test_overloaded_metadata_terms_use_extracted_contexts(self):
		cases = [
			("quality_management", "quality_goal_objective", "target", None, "Valore obiettivo"),
			("quality_management", "quality_review_objective", "target", None, "Valore obiettivo"),
			("accounts", "coupon_code", "used", None, "Utilizzi"),
			("accounts", "pos_invoice", "section_break_88", None, "Resto"),
			("accounts", "sales_invoice", "section_break_88", None, "Resto"),
			("manufacturing", "job_card_operation", "status", "Complete", "Completata"),
		]
		with Path(__file__).with_name("contextual_messages.py").open("rb") as file:
			messages = {
				(message, context)
				for _line, message, _comments, context in extract(
					"frappe.gettext.extractors.python.extract", file, keywords=PYTHON_KEYWORDS
				)
			}
		generic_translations = {
			"Account": "Conto",
			"Target": "Destinazione",
			"Used": "Utilizzata",
			"Changes": "Modifiche",
			"Allow": "Consenti",
			"Complete": "Completa",
			"Retry": "Riprova",
		}
		for module, doctype, fieldname, value, target in cases:
			with self.subTest(doctype=doctype, fieldname=fieldname, value=value):
				metadata = json.loads(
					Path(
						frappe.get_app_path("erpnext", module, "doctype", doctype, f"{doctype}.json")
					).read_text()
				)
				fields = {field["fieldname"]: field for field in metadata["fields"]}
				field = fields[fieldname]
				source = value or field["label"]
				context = metadata["name"]
				translations = dict(generic_translations)
				if (source, context) in messages:
					translations[f"{source}:{context}"] = target
				with patch("frappe.translate.get_all_translations", return_value=translations):
					self.assertEqual(frappe._(source), generic_translations[source])
					if value:
						options = field["options"]
						self.assertIn(value, options.split("\n"))
						self.assertEqual(format_value(value, frappe._dict(field, parent=context)), target)
						self.assertEqual(field["options"], options)
					else:
						meta = Meta.__new__(Meta)
						meta.__dict__.update(name=context, _fields={fieldname: frappe._dict(field)})
						self.assertEqual(meta.get_translated_label(fieldname), target)
						self.assertEqual(meta.get_label(fieldname), source)

	def test_selector_and_outcome_metadata_preserve_canonical_values(self):
		self.assertEqual(frappe.session.user, "translator@example.com")
		cases = [
			("stock", "inventory_dimension", "type_of_transaction", "Both", "Entrata e uscita"),
			("accounts", "tax_withholding_entry", "status", "Duplicate", "Duplicato"),
			("quality_management", "quality_review", "status", "Failed", "Esito negativo"),
			("quality_management", "quality_review_objective", "status", "Failed", "Esito negativo"),
			("quality_management", "quality_review", "status", "Open", "Da valutare"),
			("quality_management", "quality_review_objective", "status", "Open", "Da valutare"),
			("support", "issue", "agreement_status", "Failed", "Non rispettato"),
			("bulk_transaction", "bulk_transaction_log", "failed", None, "Operazioni non riuscite"),
		]
		with Path(__file__).with_name("contextual_messages.py").open("rb") as file:
			messages = {
				(message, context)
				for _line, message, _comments, context in extract(
					"frappe.gettext.extractors.python.extract", file, keywords=PYTHON_KEYWORDS
				)
			}
		generic = {"Both": "Entrambi", "Duplicate": "Duplica", "Failed": "Fallito", "Open": "Apri"}
		for module, doctype, fieldname, value, target in cases:
			with self.subTest(doctype=doctype, fieldname=fieldname, value=value):
				metadata = json.loads(
					Path(
						frappe.get_app_path("erpnext", module, "doctype", doctype, f"{doctype}.json")
					).read_text()
				)
				field_data = next(field for field in metadata["fields"] if field["fieldname"] == fieldname)
				meta = Meta.__new__(Meta)
				meta.__dict__.update(name=metadata["name"], doctype="DocType")
				field = Document.__new__(Document)
				field.__dict__.update(field_data, doctype="DocField")
				field = meta._init_child(field, "fields")
				meta.fields = [field]
				self.assertEqual(field.parent, metadata["name"])
				source = value or field.label
				translations = dict(generic)
				if (source, metadata["name"]) in messages:
					translations[f"{source}:{metadata['name']}"] = target
				with patch("frappe.translate.get_all_translations", return_value=translations):
					if value:
						options = field.options
						self.assertIn(value, options.split("\n"))
						self.assertEqual(format_value(value, field), target)
						self.assertEqual(field.options, options)
					else:
						self.assertEqual(field.fieldtype, "Int")
						self.assertEqual(meta.get_translated_label(fieldname), target)
						self.assertEqual(meta.get_label(fieldname), source)
					self.assertEqual(frappe._(source), generic[source])
					self.assertEqual(frappe._(source, context="Unrelated settings"), generic[source])
