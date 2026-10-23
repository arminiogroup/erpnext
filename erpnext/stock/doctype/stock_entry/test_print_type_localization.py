# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import inspect
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
import frappe.permissions
from frappe.core.doctype.docfield.docfield import DocField
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils.print_format_generator import format_field_value, get_formatted_field_values
from frappe.utils.safe_exec import SafeDoc, safer_get_meta
from frappe.www.printview import set_link_titles

from erpnext.setup.setup_wizard.operations import install_fixtures
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry

TARGETS = {
	"Material Issue": "Scarico di materiale",
	"Material Receipt": "Carico di materiale",
	"Material Transfer": "Trasferimento di materiale",
	"Manufacture": "Produzione",
	"Repack": "Riconfezionamento",
	"Disassemble": "Disassembla",
	"Disassemble:Stock Entry Type": "Disassemblaggio",
	"Send to Subcontractor": "Invio al terzista",
	"Material Transfer for Manufacture": "Trasferimento di materiale per la produzione",
	"Material Consumption for Manufacture": "Consumo di materiale per la produzione",
	"Receive from Customer": "Ricevimento dal cliente",
	"Return Raw Material to Customer": "Reso di materie prime al cliente",
	"Subcontracting Delivery": "Consegna al cliente per conto lavoro",
	"Subcontracting Return": "Reso da cliente per conto lavoro",
}


class NoDatabase:
	def __bool__(self):
		return False

	def __getattr__(self, name):
		def blocked(*args, **kwargs):
			raise AssertionError(f"Unexpected database operation: {name}")

		return blocked


class _StockEntryPrintTypeFixture(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.messages = dict(TARGETS)
		self.reads = []
		self.records = {}
		self.metas = {}
		self.enterContext(patch.object(frappe, "db", NoDatabase()))
		self.enterContext(patch.object(frappe, "get_meta", side_effect=self.get_meta))
		self.enterContext(patch("frappe.model.document.get_cached_doc", side_effect=self.cached_doc))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.messages if lang == "it" else {},
			)
		)
		self.enterContext(patch.object(frappe, "get_roles", return_value=["Translator"]))
		self.load_meta("erpnext", "stock/doctype/stock_entry/stock_entry.json")
		self.load_meta("erpnext", "stock/doctype/stock_entry_type/stock_entry_type.json")
		with patch.object(install_fixtures, "get_root_of", return_value=None):
			self.types = [
				deepcopy(row)
				for row in install_fixtures.get_preset_records(country="Italy")
				if row.get("doctype") == "Stock Entry Type"
			]
		for source in self.types:
			record = Document.__new__(Document)
			record.__dict__.update(source)
			self.records[source["name"]] = record

	def load_meta(self, app, relative):
		data = json.loads(Path(frappe.get_app_path(app, relative)).read_text())
		meta = Meta.__new__(Meta)
		meta.__dict__.update({key: value for key, value in data.items() if key != "fields"})
		fields = []
		for source in data["fields"]:
			field = DocField.__new__(DocField)
			field.__dict__.update(source, parent=data["name"], doctype="DocField", flags=frappe._dict())
			fields.append(field)
		meta.fields = fields
		meta._fields = {field.fieldname: field for field in fields}
		for key, value in {"title_field": None, "show_title_field_in_link": 0, "istable": 0}.items():
			meta.__dict__.setdefault(key, value)
		self.metas[meta.name] = meta
		return meta

	def get_meta(self, name, cached=True):
		self.reads.append(("meta", name, cached))
		return self.metas[name]

	def cached_doc(self, doctype, name=None, *args, **kwargs):
		self.reads.append(("cached", doctype, name, args, kwargs))
		if doctype == "Accounts Settings":
			return frappe._dict(show_inclusive_tax_in_print=0, show_taxes_as_table_in_print=0)
		self.assertEqual(doctype, "Stock Entry Type")
		if name not in self.records:
			frappe.local.message_log.append("Missing cached type")
			raise frappe.DoesNotExistError(name)
		return self.records[name]

	def document(self, name="Material Receipt", titles=None):
		doc = StockEntry.__new__(StockEntry)
		doc.__dict__.update(
			doctype="Stock Entry",
			name="MEMORY-STOCK-ENTRY",
			stock_entry_type=name,
			purpose="Material Receipt",
			items=[],
			taxes=[],
			flags=frappe._dict(),
			meta=self.metas["Stock Entry"],
		)
		if titles is not None:
			doc.__dict__["__link_titles"] = deepcopy(titles)
		return doc

	def business_state(self, doc):
		return deepcopy(
			{
				key: doc.get(key)
				for key in ("doctype", "name", "stock_entry_type", "purpose", "items", "taxes")
			}
		)

	def before_print(self, doc):
		before = self.business_state(doc)
		StockEntry.before_print(doc, frappe._dict(compact_item_print=0))
		self.assertEqual(self.business_state(doc), before)
		self.assertEqual(doc.print_templates["items"], "templates/print_formats/includes/items.html")
		self.assertEqual(
			doc.child_print_templates["items"]["qty"], "templates/print_formats/includes/item_table_qty.html"
		)
		self.assertEqual(doc.flags.show_inclusive_tax_in_print, 0)
		return doc.get_formatted("stock_entry_type")

	def type_reads(self):
		return [read for read in self.reads if read[:2] == ("cached", "Stock Entry Type")]

	def safe_doc(self, doc):
		return SafeDoc(
			{key: deepcopy(value) for key, value in doc.__dict__.items() if key not in ("meta", "flags")}
		)

	def factory_environment(self, restricted):
		for key in ("jenv_restricted", "jenv_unrestricted"):
			self.addCleanup(setattr, frappe.local, key, getattr(frappe.local, key, None))
			setattr(frappe.local, key, None)
		self.addCleanup(setattr, frappe.local, "site", frappe.local.site)
		frappe.local.site = "translation-memory.invalid"
		self.enterContext(
			patch.object(
				frappe,
				"get_hooks",
				side_effect=lambda key=None, *args, **kwargs: {
					"methods": ["frappe.utils.jinja_globals"],
					"filters": [
						"frappe.utils.data.global_date_format",
						"frappe.utils.markdown",
						"frappe.website.utils.abs_url",
					],
				}
				if key == "jinja"
				else [],
			)
		)
		self.enterContext(patch.object(frappe, "get_active_apps", return_value=["frappe"]))
		self.enterContext(patch.object(frappe, "get_installed_apps", return_value=["frappe", "erpnext"]))
		env = frappe.get_jenv(restrict_globals=restricted)
		if restricted:
			self.assertIs(env.globals["frappe"].get_meta, safer_get_meta)
		return env

	def render(self, env, kind, doc):
		df = frappe._dict(self.metas["Stock Entry"].get_field("stock_entry_type").__dict__)
		df.update(renderer="Data", section=frappe._dict(field_orientation=""))
		path = (
			"templates/print_formats/standard_macros.html"
			if kind == "legacy"
			else "templates/print_format/macros.html"
		)
		return str(env.get_template(path).module.render_field(df, doc))


class TestStockEntryPrintTypeLocalization(_StockEntryPrintTypeFixture):
	def test_all_thirteen_factory_standard_names_use_target_context_without_changing_raw_values(self):
		standards = [row for row in self.types if row.get("is_standard") == 1]
		self.assertEqual(len(standards), 13)
		for row in standards:
			with self.subTest(name=row["name"]):
				self.reads.clear()
				doc = self.document(row["name"])
				expected = self.messages.get(row["name"] + ":Stock Entry Type", self.messages[row["name"]])
				self.assertEqual(self.before_print(doc), expected)
				self.assertEqual(doc.stock_entry_type, row["name"])
				self.assertEqual(len(self.type_reads()), 1)
				self.assertEqual(self.type_reads()[0][2], row["name"])

	def test_real_templates_and_safe_documents_receive_the_owned_title_in_both_factories(self):
		for restricted in (True, False):
			env = self.factory_environment(restricted)
			for safe in (False, True):
				for kind in ("legacy", "builder"):
					with self.subTest(restricted=restricted, safe=safe, kind=kind):
						doc = self.document()
						set_link_titles(doc)
						self.before_print(doc)
						rendered = self.safe_doc(doc) if safe else doc
						self.assertIn("Carico di materiale", self.render(env, kind, rendered))
						self.assertEqual(rendered.get_formatted("stock_entry_type"), "Carico di materiale")
						self.assertEqual(doc.stock_entry_type, "Material Receipt")

	def test_type_noun_context_keeps_global_action_and_separate_purpose_unchanged(self):
		doc = self.document("Disassemble")
		doc.purpose = "Disassemble"
		self.assertEqual(self.before_print(doc), "Disassemblaggio")
		self.assertEqual(frappe._(doc.stock_entry_type), "Disassembla")
		self.assertEqual(doc.get_formatted("purpose"), "Disassembla")
		self.assertEqual(doc.purpose, "Disassemble")

	def test_existing_authored_or_blank_title_slots_are_preserved(self):
		key = "Stock Entry Type::Material Receipt"
		for title in ("Authored & title", "", None):
			with self.subTest(title=title):
				titles = {key: title, "Company::Business name": "Other title"}
				doc = self.document(titles=titles)
				self.assertEqual(self.before_print(doc), title)
				self.assertEqual(doc.get("__link_titles"), titles)

	def test_absent_or_exact_raw_slot_changes_only_the_owned_cache_key(self):
		key = "Stock Entry Type::Material Receipt"
		for titles in (None, {}, {key: "Material Receipt", "Company::Raw company": "Authored company"}):
			with self.subTest(titles=titles):
				doc = self.document(titles=titles)
				self.assertEqual(self.before_print(doc), "Carico di materiale")
				expected = {**(titles or {}), key: "Carico di materiale"}
				self.assertEqual(doc.get("__link_titles"), expected)

	def test_noncanonical_custom_names_never_request_a_standard_record_or_translate(self):
		for name in ("Batch Split", "My <type> & company", "Unknown standard name"):
			with self.subTest(name=name):
				self.messages[name] = "A global translation must not rename this record"
				self.reads.clear()
				doc = self.document(name)
				self.assertEqual(self.before_print(doc), name)
				self.assertEqual(self.type_reads(), [])
				self.assertIsNone(doc.get("__link_titles"))

	def test_canonical_name_with_nonstandard_or_mismatched_record_keeps_raw_identity(self):
		for standard, purpose in ((0, "Material Receipt"), (1, "Material Issue")):
			with self.subTest(standard=standard, purpose=purpose):
				record = self.records["Material Receipt"]
				record.is_standard = standard
				record.purpose = purpose
				doc = self.document()
				self.assertEqual(self.before_print(doc), "Material Receipt")
				self.assertIsNone(doc.get("__link_titles"))

	def test_missing_blank_and_noncanonical_values_do_not_add_record_reads(self):
		for name in (None, "", " "):
			with self.subTest(name=name):
				self.reads.clear()
				doc = self.document(name)
				self.assertEqual(self.before_print(doc), "" if name is None else name)
				self.assertEqual(self.type_reads(), [])

	def test_actual_owning_field_controls_the_target_and_field_type(self):
		meta = self.metas["Stock Entry"]
		original = meta.get_field("stock_entry_type")
		for fieldtype, target, missing in (
			("Data", "Stock Entry Type", False),
			("Link", "Company", False),
			("Link", "Stock Entry Type", True),
		):
			with self.subTest(fieldtype=fieldtype, target=target, missing=missing):
				self.reads.clear()
				original.fieldtype = fieldtype
				original.options = target
				meta._fields.pop("stock_entry_type", None) if missing else meta._fields.update(
					stock_entry_type=original
				)
				self.assertEqual(self.before_print(self.document()), "Material Receipt")
				self.assertEqual(self.type_reads(), [])

	def test_translated_target_and_actual_select_options_are_required(self):
		meta = self.metas["Stock Entry Type"]
		purpose = meta.get_field("purpose")
		for translated, fieldtype, options, missing in (
			(0, "Select", "Material Receipt", False),
			(1, "Data", "Material Receipt", False),
			(1, "Select", "", False),
			(1, "Select", "Material Issue", False),
			(1, "Select", None, False),
			(1, "Select", "Material Receipt", True),
		):
			with self.subTest(translated=translated, fieldtype=fieldtype, options=options, missing=missing):
				self.reads.clear()
				meta.translated_doctype = translated
				purpose.fieldtype = fieldtype
				purpose.options = options
				meta._fields.pop("purpose", None) if missing else meta._fields.update(purpose=purpose)
				self.assertEqual(self.before_print(self.document()), "Material Receipt")
				self.assertEqual(self.type_reads(), [])

	def test_missing_cached_record_retains_raw_value_and_existing_missing_record_handling(self):
		self.addCleanup(setattr, frappe.local, "message_log", frappe.local.message_log)
		frappe.local.message_log = []
		self.records.pop("Material Receipt")
		self.assertEqual(self.before_print(self.document()), "Material Receipt")
		self.assertEqual(frappe.local.message_log, [])

	def test_language_and_missing_dictionary_fallback_do_not_change_stored_names(self):
		for lang, messages, expected in (
			("en", TARGETS, "Material Receipt"),
			("it", {}, "Material Receipt"),
			("it", {"Material Receipt:Stock Entry Type": "Context-owned caption"}, "Context-owned caption"),
		):
			with self.subTest(lang=lang, messages=messages):
				frappe.local.lang = lang
				self.messages = messages
				self.assertEqual(self.before_print(self.document()), expected)

	def test_existing_inherited_settings_and_bin_helper_order_is_preserved(self):
		doc = self.document()
		row = frappe._dict(
			name="MEMORY-ROW", item_code="RAW-ITEM", s_warehouse=None, t_warehouse=None, qty=5, basic_rate=12
		)
		doc.items = [row]
		original = deepcopy(row)
		permissions = []

		def permission(*args, **kwargs):
			permissions.append((args, kwargs))
			self.reads.append(("permission", args, kwargs))
			return True

		with patch.object(frappe, "has_permission", side_effect=permission):
			StockEntry.before_print(doc, frappe._dict(compact_item_print=0))
		self.assertEqual(permissions, [(("Item",), {"ptype": "select", "throw": True})])
		self.assertEqual(row, {**original, "projected_qty": 0, "actual_qty": 0, "reserved_qty": 0})
		ordered = [read[0:3] for read in self.reads if read[0] in ("cached", "permission")]
		self.assertEqual(ordered[:2], [("cached", "Accounts Settings", "Accounts Settings")] * 2)
		self.assertEqual(ordered[2][0], "permission")
		self.assertEqual(len(ordered), 4)
		self.assertEqual(ordered[3], ("cached", "Stock Entry Type", "Material Receipt"))
		self.assertEqual(doc.stock_entry_type, "Material Receipt")
		self.assertEqual(doc.get_formatted("stock_entry_type"), "Carico di materiale")


class MemoryDefaults(NoDatabase):
	def __init__(self):
		self.calls = []
		self.blocked = []

	def get_default(self, key):
		self.calls.append(("get_default", key))
		return {
			"currency": "EUR",
			"currency_precision": "2",
			"float_precision": "3",
			"number_format": "#.###,##",
			"date_format": "dd-mm-yyyy",
		}.get(key)

	def __getattr__(self, name):
		def blocked(*args, **kwargs):
			self.blocked.append((name, repr(args), repr(kwargs)))
			raise AssertionError("Unexpected database operation: " + name)

		return blocked


class TestStockEntryCanvasTypeLocalization(_StockEntryPrintTypeFixture):
	def setUp(self):
		self.observations = []
		self.user_language_reads = []
		super().setUp()
		self.addCleanup(setattr, frappe.local, "job", frappe.local.job)
		self.load_meta("erpnext", "stock/doctype/stock_entry_detail/stock_entry_detail.json")
		for meta in self.metas.values():
			meta.permissions = [frappe._dict(role="Translator", read=1, permlevel=0)]
			meta.__dict__.setdefault("issingle", 0)
			meta.__dict__.setdefault("is_virtual", 0)
			for field in meta.fields:
				field.__dict__.setdefault("permlevel", 0)
		self.db = MemoryDefaults()
		self.enterContext(patch.object(frappe, "db", self.db))
		self.enterContext(
			patch.object(frappe, "is_table", side_effect=lambda name: bool(self.metas[name].istable))
		)
		self.enterContext(patch("frappe.permissions.get_doctype_ptype_map", return_value={}))
		self.enterContext(patch("frappe.app_state.get_disabled_modules", return_value=set()))
		self.enterContext(
			patch("frappe.core.doctype.user_permission.user_permission.get_user_permissions", return_value={})
		)
		self.enterContext(patch("frappe.share.get_shared", return_value=[]))
		self.enterContext(
			patch.object(
				frappe,
				"get_hooks",
				side_effect=lambda key=None, *a, **kw: {
					"*": [
						"erpnext.stock.doctype.company_restriction.company_restriction.has_inherited_permission"
					]
				}
				if key == "has_permission"
				else [],
			)
		)
		self.enterContext(patch.object(frappe, "get_installed_apps", return_value=["frappe", "erpnext"]))
		self.enterContext(
			patch.object(
				frappe,
				"get_system_settings",
				side_effect=lambda key=None: {
					"currency_precision": 2,
					"float_precision": 3,
					"number_format": "#.###,##",
					"date_format": "dd-mm-yyyy",
					"time_format": "HH:mm:ss",
				}.get(key)
				if key
				else frappe._dict(),
			)
		)
		self.delivery = []
		self.current = None
		self.enterContext(patch.object(frappe, "get_doc", side_effect=self.deliver))
		self.enterContext(
			patch.object(
				frappe.client_cache,
				"get_doc",
				side_effect=lambda doctype, name: frappe._dict(
					name=name, number_format="#.###,##", date_format="dd-mm-yyyy", time_format="HH:mm:ss"
				)
				if doctype == "Language"
				else self.fail("Unexpected client cached doc: " + doctype),
			)
		)
		self.enterContext(
			patch(
				"frappe.defaults.get_global_default",
				side_effect=lambda key: "1" if key == "hide_currency_symbol" else None,
			)
		)
		frappe.local.role_permissions = {}
		self.addCleanup(setattr, frappe.local, "message_log", frappe.local.message_log)
		frappe.local.message_log = []

	def cached_doc(self, doctype, name=None, *args, **kwargs):
		if doctype == "User":
			self.assertEqual(name, "translator@example.com")
			self.user_language_reads.append((doctype, name, args, kwargs))
			return frappe._dict(language="it")
		return super().cached_doc(doctype, name, *args, **kwargs)

	def deliver(self, doctype, name=None, *args, **kwargs):
		self.delivery.append((doctype, name, args, kwargs))
		self.assertEqual((doctype, name), ("Stock Entry", "MEMORY-STOCK-ENTRY"))
		return self.current

	def state(self, doc):
		return deepcopy({k: v for k, v in doc.__dict__.items() if k != "meta"})

	def endpoint(self, doc):
		self.current = doc
		return get_formatted_field_values("Stock Entry", "MEMORY-STOCK-ENTRY")

	def observe(self, label, **values):
		self.observations.append({"case": label, **values})

	def test_canvas_all_thirteen_real_factory_pairs_and_raw_model_identity(self):
		standards = [row for row in self.types if row.get("is_standard") == 1]
		self.assertEqual(len(standards), 13)
		for row in standards:
			with self.subTest(name=row["name"]):
				self.reads.clear()
				doc = self.document(row["name"])
				before = self.state(doc)
				actual = self.endpoint(doc)
				expected = self.messages.get(row["name"] + ":Stock Entry Type", self.messages[row["name"]])
				self.observe(
					"factory",
					name=row["name"],
					actual=actual["values"]["stock_entry_type"],
					expected=expected,
					type_reads=len(self.type_reads()),
					parent_keys=len(actual["values"]),
					child_keys=sorted(actual["child"]),
				)
				self.assertEqual(actual["values"]["stock_entry_type"], expected)
				self.assertEqual(self.state(doc), {**before, "__link_titles": {}})
				self.assertEqual(len(self.type_reads()), 1)
				self.assertEqual(set(actual), {"values", "child"})
				self.assertEqual(doc.flags, {})

	def test_custom_and_unknown_names_stay_raw_even_when_globally_translatable(self):
		for name in ("My <type> & business", "Batch Split", "Unknown standard name"):
			with self.subTest(name=name):
				self.messages[name] = "Must not rename authored identity"
				self.reads.clear()
				doc = self.document(name)
				self.assertEqual(self.endpoint(doc)["values"]["stock_entry_type"], name)
				self.assertEqual(self.type_reads(), [])
				self.assertEqual(doc.stock_entry_type, name)

	def test_canonical_nonstandard_or_purpose_mismatch_stays_raw(self):
		for standard, purpose in ((0, "Material Receipt"), (1, "Material Issue")):
			with self.subTest(standard=standard, purpose=purpose):
				self.records["Material Receipt"].is_standard = standard
				self.records["Material Receipt"].purpose = purpose
				self.reads.clear()
				self.assertEqual(
					self.endpoint(self.document())["values"]["stock_entry_type"], "Material Receipt"
				)
				self.observe(
					"nonstandard-read-budget",
					standard=standard,
					purpose=purpose,
					cached_reads=len(self.type_reads()),
				)
				self.assertLessEqual(len(self.type_reads()), 1)

	def test_existing_authored_empty_none_and_raw_title_slots(self):
		key = "Stock Entry Type::Material Receipt"
		for value in ("Authored <caption> & business", "", None, "Material Receipt"):
			with self.subTest(value=value):
				doc = self.document(titles={key: value, "Company::Canonical": "Other"})
				before = self.state(doc)
				expected = "Carico di materiale" if value == "Material Receipt" else value
				actual = self.endpoint(doc)["values"]["stock_entry_type"]
				self.observe("title-slot", raw_slot=value, actual=actual, expected=expected)
				self.assertEqual(actual, expected)
				self.assertEqual(self.state(doc), before)

	def test_missing_record_none_blank_noncanonical_and_message_log(self):
		for name in (None, "", " "):
			with self.subTest(name=name):
				self.reads.clear()
				self.assertEqual(
					self.endpoint(self.document(name))["values"]["stock_entry_type"],
					"" if name is None else name,
				)
				self.assertEqual(self.type_reads(), [])
		self.records.pop("Material Receipt")
		self.reads.clear()
		self.assertEqual(self.endpoint(self.document())["values"]["stock_entry_type"], "Material Receipt")
		self.assertEqual(frappe.local.message_log, [])
		self.observe("missing-record-read-budget", cached_reads=len(self.type_reads()))
		self.assertLessEqual(len(self.type_reads()), 1)

	def test_context_english_and_missing_dictionary_controls(self):
		for lang, messages, expected in (
			("it", TARGETS, "Disassemblaggio"),
			("en", TARGETS, "Disassemble"),
			("it", {}, "Disassemble"),
			("it", {"Disassemble:Stock Entry Type": "Owned caption"}, "Owned caption"),
		):
			with self.subTest(lang=lang, messages=messages):
				frappe.local.lang = lang
				self.messages = dict(messages)
				doc = self.document("Disassemble")
				doc.purpose = "Disassemble"
				result = self.endpoint(doc)
				self.assertEqual(result["values"]["stock_entry_type"], expected)
				self.assertEqual(result["values"]["purpose"], frappe._(doc.purpose))
				self.assertEqual(doc.purpose, "Disassemble")

	def test_actual_permission_denial_precedes_link_titles_and_formatting(self):
		meta = self.metas["Stock Entry"]
		meta.permissions[0].read = 0
		frappe.local.role_permissions = {}
		self.reads.clear()
		doc = self.document()
		before = self.state(doc)
		with self.assertRaises(frappe.PermissionError):
			self.endpoint(doc)
		self.assertEqual(self.type_reads(), [])
		self.assertNotIn("__link_titles", doc.__dict__)
		self.assertEqual(self.state(doc), before)
		self.assertEqual(len(self.delivery), 1)
		self.assertEqual(frappe.session.user, "translator@example.com")

	def test_actual_permlevel_filter_and_child_response_shape(self):
		df = self.metas["Stock Entry"].get_field("stock_entry_type")
		df.permlevel = 1
		doc = self.document()
		child = Document.__new__(Document)
		child.__dict__.update(
			doctype="Stock Entry Detail",
			name="MEMORY-ROW",
			parenttype="Stock Entry",
			parent=doc.name,
			parentfield="items",
			meta=self.metas["Stock Entry Detail"],
			flags=frappe._dict(),
			item_name="Authored & item",
			qty=2,
			stock_qty=2,
			basic_rate=12,
		)
		doc.items = [child]
		self.metas["Stock Entry Detail"].get_field("item_name").permlevel = 1
		child_state = self.state(child)
		self.reads.clear()
		hidden = self.endpoint(doc)
		self.assertEqual(self.state(child), child_state)
		self.assertNotIn("stock_entry_type", hidden["values"])
		self.assertNotIn("item_name", hidden["child"]["items"][0])
		self.assertEqual(hidden["child"]["items"][0]["qty"], "2")
		self.assertEqual(self.type_reads(), [])
		self.metas["Stock Entry"].permissions.append(frappe._dict(role="Translator", read=1, permlevel=1))
		self.reads.clear()
		shown = self.endpoint(doc)
		self.assertEqual(self.state(child), child_state)
		self.assertEqual(shown["values"]["stock_entry_type"], "Carico di materiale")
		self.assertEqual(shown["child"]["items"][0]["item_name"], "Authored & item")
		self.assertEqual(shown["child"]["items"][0]["qty"], hidden["child"]["items"][0]["qty"])
		self.assertEqual(set(shown), set(hidden))
		self.assertEqual(
			{k: v for k, v in shown["values"].items() if k != "stock_entry_type"}, hidden["values"]
		)
		self.assertEqual(
			{k: v for k, v in shown["child"]["items"][0].items() if k != "item_name"},
			hidden["child"]["items"][0],
		)
		self.assertEqual(len(self.type_reads()), 1)
		self.observe(
			"permlevel",
			hidden_parent_count=len(hidden["values"]),
			shown_parent_count=len(shown["values"]),
			hidden_child_count=len(hidden["child"]["items"][0]),
			shown_child_count=len(shown["child"]["items"][0]),
		)

	def test_endpoint_full_response_parity_except_owned_caption(self):
		doc = self.document()
		doc.remarks = "Authored <remark> & text"
		base_doc = Document.__new__(Document)
		base_doc.__dict__.update(
			deepcopy({k: v for k, v in doc.__dict__.items() if k != "meta"}), meta=doc.meta
		)
		old = self.endpoint(base_doc)
		actual = self.endpoint(doc)
		expected = deepcopy(old)
		expected["values"]["stock_entry_type"] = "Carico di materiale"
		self.assertEqual(actual, expected)
		self.assertEqual(doc.remarks, "Authored <remark> & text")
		self.observe(
			"response-parity",
			parent_keys=sorted(actual["values"]),
			child_keys=sorted(actual["child"]),
			database_blocked=self.db.blocked,
		)
		self.assertEqual(self.db.blocked, [])

	def test_exact_signature_and_explicit_arguments_delegate_to_actual_base(self):
		self.assertEqual(
			inspect.signature(StockEntry.get_formatted), inspect.signature(Document.get_formatted)
		)
		doc = self.document()
		foreign = self.document(titles={"Stock Entry Type::Material Receipt": "Foreign authored title"})
		calls = [
			(("stock_entry_type", foreign), {}),
			(("stock_entry_type", None, "EUR"), {}),
			(("stock_entry_type", None, None, True), {}),
			(("stock_entry_type", None, None, False, True), {}),
			(("stock_entry_type",), {"format": "Authored format"}),
			(
				("stock_entry_type",),
				{
					"doc": foreign,
					"currency": "JPY",
					"absolute_value": True,
					"translated": True,
					"format": "custom",
				},
			),
		]
		for args, kwargs in calls:
			with self.subTest(args=repr(args), kwargs=repr(kwargs)):
				self.reads.clear()
				self.assertEqual(
					doc.get_formatted(*args, **kwargs), Document.get_formatted(doc, *args, **kwargs)
				)
				self.assertEqual(self.type_reads(), [])
		self.assertEqual(doc.get_formatted("stock_entry_type", doc=doc), "Carico di materiale")

	def test_other_fields_and_existing_generator_formatter_delegate(self):
		doc = self.document()
		doc.remarks = "First\nSecond & text"
		doc.total_outgoing_value = -12.5
		for args, kwargs in [
			(("purpose",), {}),
			(("remarks",), {}),
			(("name",), {}),
			(("total_outgoing_value",), {"absolute_value": True, "currency": "EUR"}),
		]:
			with self.subTest(args=args, kwargs=kwargs):
				self.reads.clear()
				self.assertEqual(
					doc.get_formatted(*args, **kwargs), Document.get_formatted(doc, *args, **kwargs)
				)
				self.assertEqual(self.type_reads(), [])
		df = self.metas["Stock Entry"].get_field("stock_entry_type")
		self.assertEqual(format_field_value(doc, df), "Carico di materiale")
		self.assertEqual(doc.stock_entry_type, "Material Receipt")

	def test_default_getter_pure_and_does_not_call_print_or_bin(self):
		doc = self.document()
		before = self.state(doc)
		with patch.object(
			StockEntry, "before_print", side_effect=AssertionError("before_print must not run")
		) as printing, patch.object(
			StockEntry, "update_items_from_bin_details", side_effect=AssertionError("bin must not run")
		) as bins:
			self.assertEqual(doc.get_formatted("stock_entry_type"), "Carico di materiale")
			printing.assert_not_called()
			bins.assert_not_called()
		self.assertEqual(self.state(doc), before)
		self.assertEqual(len(self.type_reads()), 1)

	def test_prepared_print_title_reuse_and_honest_english_read_cost(self):
		doc = self.document()
		self.before_print(doc)
		before_reads = len(self.type_reads())
		doc.get_formatted("stock_entry_type")
		self.assertEqual(len(self.type_reads()), before_reads)
		self.assertEqual(before_reads, 1)
		frappe.local.lang = "en"
		english = self.document()
		StockEntry.before_print(english, frappe._dict(compact_item_print=0))
		before_english = len(self.type_reads())
		self.assertEqual(english.get_formatted("stock_entry_type"), "Material Receipt")
		self.observe(
			"english-prepared-read-cost",
			before_read_count=before_english,
			after_read_count=len(self.type_reads()),
			extra_cached_reads=len(self.type_reads()) - before_english,
		)

	def test_actual_meta_ownership_and_target_opt_in_still_gate_caption(self):
		own = self.metas["Stock Entry"].get_field("stock_entry_type")
		target = self.metas["Stock Entry Type"]
		for fieldtype, options, translated in [
			("Data", "Stock Entry Type", 1),
			("Link", "Company", 1),
			("Link", "Stock Entry Type", 0),
		]:
			with self.subTest(fieldtype=fieldtype, options=options, translated=translated):
				own.fieldtype = fieldtype
				own.options = options
				target.translated_doctype = translated
				self.reads.clear()
				doc = self.document()
				self.assertEqual(doc.get_formatted("stock_entry_type"), "Material Receipt")
				self.assertEqual(self.type_reads(), [])

	def cold_job(self):
		frappe.local.lang = "en"
		frappe.local.job = frappe._dict(user="translator@example.com", lang_resolved=False)
		self.user_language_reads.clear()
		reads = []

		def dictionary(lang):
			reads.append(lang)
			return self.messages if lang == "it" else {}

		self.enterContext(patch("frappe.translate.get_all_translations", side_effect=dictionary))
		return reads

	def test_cold_job_authored_empty_none_titles_do_not_resolve_language_or_dictionary(self):
		key = "Stock Entry Type::Material Receipt"
		for caption in ("Authored <caption> & business", "", None):
			with self.subTest(caption=caption):
				dictionary_reads = self.cold_job()
				doc = self.document(titles={key: caption, "Company::Canonical": "Other"})
				titles = deepcopy(doc.get("__link_titles"))
				StockEntry.before_print(doc, frappe._dict(compact_item_print=0))
				actual = doc.get_formatted("stock_entry_type")
				self.observe(
					"cold-preserved-title",
					caption=caption,
					actual=actual,
					language=frappe.local.lang,
					job_resolved=frappe.local.job.lang_resolved,
					user_language_reads=len(self.user_language_reads),
					dictionary_reads=list(dictionary_reads),
				)
				self.assertEqual(actual, caption)
				self.assertEqual(doc.get("__link_titles"), titles)
				self.assertEqual(frappe.local.lang, "en")
				self.assertFalse(frappe.local.job.lang_resolved)
				self.assertEqual(self.user_language_reads, [])
				self.assertEqual(dictionary_reads, [])

	def test_cold_job_raw_before_print_still_resolves_inside_owned_slot_guard(self):
		dictionary_reads = self.cold_job()
		key = "Stock Entry Type::Material Receipt"
		doc = self.document(titles={key: "Material Receipt", "Company::Canonical": "Other"})
		StockEntry.before_print(doc, frappe._dict(compact_item_print=0))
		self.assertEqual(doc.get_formatted("stock_entry_type"), "Carico di materiale")
		self.assertEqual(
			doc.get("__link_titles"), {key: "Carico di materiale", "Company::Canonical": "Other"}
		)
		self.assertEqual(frappe.local.lang, "it")
		self.assertTrue(frappe.local.job.lang_resolved)
		self.assertEqual(len(self.user_language_reads), 1)
		self.assertEqual(dictionary_reads, ["it"])
		self.assertEqual(doc.stock_entry_type, "Material Receipt")
		self.observe(
			"cold-owned-before-print",
			language=frappe.local.lang,
			job_resolved=frappe.local.job.lang_resolved,
			user_language_reads=len(self.user_language_reads),
			dictionary_reads=list(dictionary_reads),
		)

	def test_cold_job_default_getter_resolves_only_at_owned_caption_return(self):
		key = "Stock Entry Type::Material Receipt"
		for titles in (None, {key: "Material Receipt"}):
			with self.subTest(titles=titles):
				dictionary_reads = self.cold_job()
				doc = self.document(titles=titles)
				before = self.state(doc)
				actual = doc.get_formatted("stock_entry_type")
				self.observe(
					"cold-new-caption-return",
					actual=actual,
					language=frappe.local.lang,
					job_resolved=frappe.local.job.lang_resolved,
					user_language_reads=len(self.user_language_reads),
					dictionary_reads=list(dictionary_reads),
				)
				self.assertEqual(actual, "Carico di materiale")
				self.assertEqual(self.state(doc), before)
				self.assertEqual(frappe.local.lang, "it")
				self.assertTrue(frappe.local.job.lang_resolved)
				self.assertEqual(len(self.user_language_reads), 1)
				self.assertEqual(dictionary_reads, ["it"])
