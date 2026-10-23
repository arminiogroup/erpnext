# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils import get_link_to_form

from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.buying.doctype.purchase_order.purchase_order import PurchaseOrder
from erpnext.controllers import status_updater
from erpnext.controllers.accounts_controller import InvalidQtyError
from erpnext.selling.doctype.sales_order.sales_order import SalesOrder
from erpnext.stock.doctype.delivery_note.delivery_note import DeliveryNote
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import PurchaseReceipt
from erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt import SubcontractingReceipt

NONNEGATIVE = "Quantity for item {0} cannot be negative."
RETURN_QUANTITY = "Quantity for item {0} cannot be positive in a return."
NEGATIVE_RATE = "The rate for item {0} cannot be negative. To allow negative rates, enable {1} in {2}."
TARGETS = {
	NONNEGATIVE: "La quantità dell’articolo {0} non può essere negativa.",
	RETURN_QUANTITY: "Nel reso, la quantità dell’articolo {0} non può essere positiva.",
	NEGATIVE_RATE: "Il prezzo unitario dell’articolo {0} non può essere negativo. Per consentire prezzi unitari negativi, attivare {1} in {2}.",
	"Selling Settings": "Impostazioni di vendita",
	"Buying Settings": "Impostazioni di acquisto",
	"Allow negative rates for Items": "Consenti prezzi negativi per gli articoli",
}
TYPES = {
	"Sales Order": (SalesOrder, "selling", "sales_order", "quotation_item", "stock_qty"),
	"Purchase Order": (PurchaseOrder, "buying", "purchase_order", "material_request_item", "stock_qty"),
	"Sales Invoice": (SalesInvoice, "accounts", "sales_invoice", "so_detail", "amount"),
	"Purchase Invoice": (PurchaseInvoice, "accounts", "purchase_invoice", "po_detail", "amount"),
	"Delivery Note": (DeliveryNote, "stock", "delivery_note", "so_detail", "qty"),
	"Purchase Receipt": (PurchaseReceipt, "stock", "purchase_receipt", "purchase_order_item", "stock_qty"),
	"Subcontracting Receipt": (
		SubcontractingReceipt,
		"subcontracting",
		"subcontracting_receipt",
		"subcontracting_order_item",
		"qty",
	),
	"POS Invoice": (POSInvoice, "accounts", "pos_invoice", "so_detail", "amount"),
}
SELLING = ("Sales Order", "Sales Invoice", "Delivery Note")
BUYING = ("Purchase Order", "Purchase Invoice", "Purchase Receipt")
RETURN_TYPES = (
	"Sales Invoice",
	"Purchase Invoice",
	"Delivery Note",
	"Purchase Receipt",
	"Subcontracting Receipt",
	"POS Invoice",
)


def metadata_path(module, slug):
	return Path(frappe.get_app_path("erpnext", module, "doctype", slug, f"{slug}.json"))


def read_meta(path):
	data = json.loads(path.read_text())
	meta = Meta.__new__(Meta)
	fields = [frappe._dict({"parent": data["name"], "is_virtual": 0, **field}) for field in data["fields"]]
	meta.__dict__.update(
		name=data["name"],
		fields=fields,
		_fields={field.fieldname: field for field in fields},
		issingle=data.get("issingle", 0),
		istable=data.get("istable", 0),
		autoname=data.get("autoname", ""),
	)
	return meta


def extracted_keys(paths):
	keys = set()
	for path, kind in [(Path(status_updater.__file__), "python"), *((path, "doctype") for path in paths)]:
		with path.open("rb") as stream:
			keys.update(
				message + (":" + context if context else "")
				for _, message, _, context in extract(
					f"frappe.gettext.extractors.{kind}.extract", stream, keywords=PYTHON_KEYWORDS
				)
			)
	return keys


class TestStatusUpdaterMessageTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "message_log", frappe.local.message_log)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.message_log = []
		self.paths = []
		self.metas = {}
		for _, module, slug, _, _ in TYPES.values():
			meta = self.add_meta(module, slug)
			self.add_meta(module, frappe.scrub(meta.get_field("items").options))
		self.add_meta("selling", "selling_settings")
		self.add_meta("buying", "buying_settings")
		self.add_meta("stock", "packed_item")
		keys = extracted_keys(self.paths)
		self.translations = self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={k: v for k, v in TARGETS.items() if k in keys},
			)
		)
		self.enterContext(patch.dict(frappe.flags, {"mute_messages": False, "print_messages": False}))
		self.enterContext(patch("frappe.db", new=SimpleNamespace()))
		self.meta_reads = self.enterContext(patch("frappe.get_meta", side_effect=self.metas.__getitem__))
		self.settings = {"Selling Settings": 0, "Buying Settings": 0}
		self.allow_zero_qty = 0
		self.setting_reads = self.enterContext(
			patch("frappe.get_single_value", side_effect=self.setting_value)
		)
		self.enterContext(
			patch(
				"frappe.utils.data.get_url",
				side_effect=lambda uri=None, **kwargs: "https://example.invalid" + (uri or ""),
			)
		)

	def add_meta(self, module, slug):
		path = metadata_path(module, slug)
		meta = read_meta(path)
		self.paths.append(path)
		self.metas[meta.name] = meta
		return meta

	def setting_value(self, doctype, fieldname):
		if fieldname == "allow_zero_qty_in_sales_order":
			return self.allow_zero_qty
		self.assertEqual(fieldname, "allow_negative_rates_for_items")
		return self.settings[doctype]

	def make_doc(self, doctype="Sales Order", qty=1, rate=1, is_return=0, item_code="Selling Settings"):
		cls, _, _, join_field, target_ref_field = TYPES[doctype]
		meta = self.metas[doctype]
		child_meta = self.metas[meta.get_field("items").options]
		row = Document.__new__(Document)
		row.__dict__.update(
			doctype=child_meta.name,
			name="ROW-RAW",
			meta=child_meta,
			flags=frappe._dict(),
			qty=qty,
			rate=rate,
			item_code=item_code,
			idx=1,
		)
		doc = cls.__new__(cls)
		doc.__dict__.update(
			doctype=doctype,
			name="DOC-RAW",
			meta=meta,
			flags=frappe._dict(),
			is_return=is_return,
			items=[row],
			status_updater=[
				{
					"source_dt": "Sales Invoice Item" if doctype == "POS Invoice" else child_meta.name,
					"join_field": join_field,
					"target_ref_field": target_ref_field,
				}
			],
		)
		return doc, row

	def raw_state(self, doc, row):
		return (
			doc.doctype,
			doc.name,
			doc.is_return,
			row.doctype,
			row.name,
			row.get("item_code"),
			row.get("qty"),
			row.get("rate"),
			deepcopy(doc.status_updater),
		)

	def assert_rejected(self, doc, row, message):
		before = self.raw_state(doc, row)
		with self.assertRaises(frappe.ValidationError) as raised:
			doc.validate_qty()
		self.assertEqual(str(raised.exception), message)
		self.assertEqual(frappe.local.message_log[-1].indicator, "red")
		self.assertEqual(frappe.local.message_log[-1].raise_exception, 1)
		self.assertEqual(self.raw_state(doc, row), before)

	def test_configured_extraction_registers_complete_rejection_messages(self):
		keys = extracted_keys(self.paths)
		for source in (NONNEGATIVE, RETURN_QUANTITY, NEGATIVE_RATE):
			with self.subTest(source=source):
				self.assertIn(source, keys)
		self.assertNotIn("`Allow Negative rates for Items`", keys)
		self.assertIn("Allow negative rates for Items", keys)

	def test_negative_nonreturn_quantity_keeps_raw_item_identity(self):
		for doctype in TYPES:
			self.assertIs(TYPES[doctype][0].validate_qty, status_updater.StatusUpdater.validate_qty)
			for quantity in (-1, -0.25, "-0.25"):
				with self.subTest(doctype=doctype, quantity=quantity):
					doc, row = self.make_doc(doctype, qty=quantity)
					self.assert_rejected(
						doc, row, "La quantità dell’articolo Selling Settings non può essere negativa."
					)

	def test_positive_return_quantity_uses_return_semantics(self):
		for doctype in RETURN_TYPES:
			for quantity in (1, 0.25, "0.25"):
				with self.subTest(doctype=doctype, quantity=quantity):
					doc, row = self.make_doc(doctype, qty=quantity, is_return=1)
					self.assert_rejected(
						doc,
						row,
						"Nel reso, la quantità dell’articolo Selling Settings non può essere positiva.",
					)

	def test_zero_nullable_and_correct_signed_quantities_stay_accepted(self):
		for doctype in TYPES:
			for is_return in (0, 1):
				for quantity in (0, None, "", "0", "-0", -0.25 if is_return else 0.25):
					with self.subTest(doctype=doctype, is_return=is_return, quantity=quantity):
						doc, row = self.make_doc(doctype, qty=quantity, rate=0, is_return=is_return)
						before = self.raw_state(doc, row)
						doc.validate_qty()
						self.assertEqual(self.raw_state(doc, row), before)

	def test_negative_unit_price_uses_selected_settings_label_and_link(self):
		for doctype in (*SELLING, *BUYING):
			settings_type = "Selling Settings" if doctype in SELLING else "Buying Settings"
			other_settings = "Buying Settings" if doctype in SELLING else "Selling Settings"
			label = "Impostazioni di vendita" if doctype in SELLING else "Impostazioni di acquisto"
			route = "selling-settings" if doctype in SELLING else "buying-settings"
			for is_return in (0, 1):
				for other_allowed in (0, 1):
					with self.subTest(doctype=doctype, is_return=is_return, other_allowed=other_allowed):
						self.settings = {settings_type: 0, other_settings: other_allowed}
						self.setting_reads.reset_mock()
						doc, row = self.make_doc(
							doctype, qty=-1 if is_return else 1, rate=-0.25, is_return=is_return
						)
						self.assert_rejected(
							doc,
							row,
							f'Il prezzo unitario dell’articolo <strong>Selling Settings</strong> non può essere negativo. Per consentire prezzi unitari negativi, attivare <strong>Consenti prezzi negativi per gli articoli</strong> in <a href="https://example.invalid/desk/{route}">{label}</a>.',
						)
						self.assertEqual(
							self.setting_reads.call_args_list,
							[
								call("Selling Settings", "allow_negative_rates_for_items"),
								call("Buying Settings", "allow_negative_rates_for_items"),
							],
						)

	def test_zero_prices_and_enabled_negative_prices_stay_accepted(self):
		for doctype in (*SELLING, *BUYING):
			for is_return in (0, 1):
				for price in (0, "0", -1):
					with self.subTest(doctype=doctype, is_return=is_return, price=price):
						self.settings = {
							"Selling Settings": int(doctype in SELLING),
							"Buying Settings": int(doctype in BUYING),
						}
						doc, row = self.make_doc(
							doctype, qty=-1 if is_return else 1, rate=price, is_return=is_return
						)
						before = self.raw_state(doc, row)
						doc.validate_qty()
						self.assertEqual(self.raw_state(doc, row), before)

	def test_existing_excluded_types_do_not_gain_a_negative_rate_rule(self):
		for doctype in ("POS Invoice", "Subcontracting Receipt"):
			with self.subTest(doctype=doctype):
				doc, row = self.make_doc(doctype, rate=-1)
				doc.validate_qty()
				self.assertEqual(row.rate, -1)

	def test_empty_missing_reference_and_explicit_false_configs_keep_bypassing(self):
		for config in (
			[],
			[{"source_dt": "Sales Order Item", "join_field": "quotation_item"}],
			[
				{
					"source_dt": "Sales Order Item",
					"join_field": "quotation_item",
					"target_ref_field": "stock_qty",
					"validate_qty": False,
				}
			],
		):
			with self.subTest(config=config):
				doc, _ = self.make_doc(qty=-1, rate=-1)
				doc.status_updater = config
				self.setting_reads.reset_mock()
				doc.validate_qty()
				self.setting_reads.assert_not_called()

	def test_field_presence_guards_stay_unchanged(self):
		for missing in ("qty", "rate", "item_code"):
			with self.subTest(missing=missing):
				doc, row = self.make_doc(rate=1 if missing != "item_code" else -1)
				del row.__dict__[missing]
				doc.validate_qty()
				self.assertNotIn(missing, row.__dict__)

	def test_independent_sales_order_zero_quantity_setting_still_decides(self):
		doc, row = self.make_doc(qty=0)
		doc.before_validate()
		self.assertEqual(doc.flags.allow_zero_qty, 0)
		with self.assertRaises(InvalidQtyError):
			doc.validate_qty_is_not_zero()
		self.allow_zero_qty = 1
		doc.before_validate()
		self.assertEqual(doc.flags.allow_zero_qty, 1)
		doc.validate_qty_is_not_zero()
		doc.validate_qty()
		self.assertEqual(row.qty, 0)

	def test_actual_metadata_custom_label_is_used_without_changing_the_setting(self):
		for doctype, settings_type in (
			("Sales Order", "Selling Settings"),
			("Purchase Order", "Buying Settings"),
		):
			with self.subTest(doctype=doctype):
				self.metas[settings_type].get_field(
					"allow_negative_rates_for_items"
				).label = "Custom negative-price policy"
				doc, row = self.make_doc(doctype, rate=-1)
				label = "Impostazioni di vendita" if doctype == "Sales Order" else "Impostazioni di acquisto"
				route = "selling-settings" if doctype == "Sales Order" else "buying-settings"
				self.assert_rejected(
					doc,
					row,
					f'Il prezzo unitario dell’articolo <strong>Selling Settings</strong> non può essere negativo. Per consentire prezzi unitari negativi, attivare <strong>Custom negative-price policy</strong> in <a href="https://example.invalid/desk/{route}">{label}</a>.',
				)
				self.assertEqual(self.settings[settings_type], 0)

	def test_mixed_children_and_multiple_configs_keep_sign_checks(self):
		doc, row = self.make_doc()
		doc.status_updater.insert(0, {**doc.status_updater[0], "validate_qty": False})
		packed = Document.__new__(Document)
		packed.__dict__.update(
			doctype="Packed Item",
			name="PACKED-RAW",
			meta=self.metas["Packed Item"],
			flags=frappe._dict(),
			item_code="PACKED-RAW",
			qty=-1,
		)
		doc.packed_items = [packed]
		self.setting_reads.reset_mock()
		with self.assertRaises(frappe.ValidationError) as raised:
			doc.validate_qty()
		self.assertEqual(
			str(raised.exception), "La quantità dell’articolo PACKED-RAW non può essere negativa."
		)
		self.assertEqual(len(self.setting_reads.call_args_list), 2)
		self.assertEqual((row.qty, packed.qty), (1, -1))

	def test_existing_single_argument_link_and_english_fallback(self):
		self.assertEqual(
			get_link_to_form("Selling Settings"),
			'<a href="https://example.invalid/desk/selling-settings">Impostazioni di vendita</a>',
		)
		self.translations.return_value = {}
		doc, _ = self.make_doc(qty=-1)
		with self.assertRaises(frappe.ValidationError) as raised:
			doc.validate_qty()
		self.assertEqual(str(raised.exception), "Quantity for item Selling Settings cannot be negative.")
		doc, _ = self.make_doc(rate=-1)
		with self.assertRaises(frappe.ValidationError) as raised:
			doc.validate_qty()
		self.assertEqual(
			str(raised.exception),
			'The rate for item <strong>Selling Settings</strong> cannot be negative. To allow negative rates, enable <strong>Allow negative rates for Items</strong> in <a href="https://example.invalid/desk/selling-settings">Selling Settings</a>.',
		)
