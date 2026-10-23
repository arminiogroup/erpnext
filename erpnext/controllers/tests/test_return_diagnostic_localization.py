# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.docstatus import DocStatus
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.query_builder.builder import MariaDB
from frappe.tests import UnitTestCase

from erpnext.controllers import sales_and_purchase_return as returns

POSTING_MESSAGE = "Posting timestamp cannot be before {0}."
QUANTITY_MESSAGE = "The value of field {0} must be zero or negative in a return document."
EXCHANGE_MESSAGE = "Exchange Rate must be same as {0} {1} ({2})"
PARTY_MESSAGE = "The {0} {1} does not match with the {0} {2} in the {3} {4}"
MISSING_ITEM_MESSAGE = "Row # {0}: Returned Item {1} does not exist in {2} {3}"
RATE_MESSAGE = "Row # {0}: Rate cannot be greater than the rate used in {1} {2}"
BUNDLE_MESSAGE = "Row # {0}: Cannot return {1}, only {2} {3} of its component {4} is left to return"
DOCTYPES = {
	"Sales Invoice": ("accounts", "Fattura di vendita"),
	"Purchase Invoice": ("accounts", "Fattura di acquisto"),
	"Delivery Note": ("stock", "Documento di trasporto"),
	"Purchase Receipt": ("stock", "Ricevimento merci"),
	"POS Invoice": ("accounts", "Fattura POS"),
}
TARGETS = {
	POSTING_MESSAGE: "La data e l'ora di registrazione non possono precedere {0}.",
	QUANTITY_MESSAGE: "Il valore del campo {0} deve essere minore o uguale a zero in un documento di reso.",
	EXCHANGE_MESSAGE: "Il tasso di cambio deve coincidere con quello del documento {0} {1} ({2}).",
	PARTY_MESSAGE: "Il valore {1} del campo {0} non corrisponde al valore {2} del campo {0} nel documento {3} {4}.",
	MISSING_ITEM_MESSAGE: "Riga n. {0}: l'articolo reso {1} non è presente nel documento {2} {3}.",
	RATE_MESSAGE: "Riga n. {0}: il prezzo unitario non può superare quello del documento {1} {2}.",
	BUNDLE_MESSAGE: "Riga n. {0}: non è possibile rendere {1}; rimangono da restituire solo {2} {3} del suo componente {4}",
	"Party Mismatch": "Controparte diversa",
	"Customer": "Cliente",
	"Supplier": "Fornitore",
	"Quantity": "Quantità",
	"Qty as per Stock UOM": "Quantità in UdM di magazzino",
	"Qty in Stock UOM": "Quantità in UdM di magazzino",
	"Accepted Qty": "Quantità accettata",
	"Accepted Quantity": "Quantità accettata",
	"Accepted Qty in Stock UOM": "Quantità accettata in UdM di magazzino",
	"Received Qty": "Quantità ricevuta",
	"Received Quantity": "Quantità ricevuta",
	"Rejected Qty": "Quantità respinta",
	"Rejected Quantity": "Quantità respinta",
	"Box": "Scatola",
	"Set": "Imposta",
	"Set:UOM": "Set",
	**{doctype: translated for doctype, (_module, translated) in DOCTYPES.items()},
}


def metadata_path(module, doctype):
	slug = frappe.scrub(doctype)
	return Path(frappe.get_app_path("erpnext", module, "doctype", slug, f"{slug}.json"))


def read_meta(module, doctype):
	data = json.loads(metadata_path(module, doctype).read_text())
	meta = Meta.__new__(Meta)
	meta.__dict__.update(
		name=data["name"],
		fields=[frappe._dict(field) for field in data["fields"]],
		_fields={field["fieldname"]: frappe._dict(field) for field in data["fields"]},
	)
	return meta


def extracted_keys():
	paths = [
		("controllers/sales_and_purchase_return.py", "frappe.gettext.extractors.python.extract"),
		("gettext/contextual_messages.py", "frappe.gettext.extractors.python.extract"),
		("setup/setup_wizard/data/uom_data.json", "erpnext.gettext.extractors.uom_data.extract"),
	]
	for doctype, (module, _translation) in DOCTYPES.items():
		for name in (doctype, doctype + " Item"):
			paths.append((metadata_path(module, name), "frappe.gettext.extractors.doctype.extract"))
	keys = set()
	for path, method in paths:
		path = path if isinstance(path, Path) else Path(frappe.get_app_path("erpnext", path))
		with path.open("rb") as source:
			for _line, message, _comments, context in extract(method, source, keywords=PYTHON_KEYWORDS):
				keys.add(message + (":" + context if context else ""))
	return keys


def make_doc(**values):
	doc = Document.__new__(Document)
	doc.__dict__.update(flags=frappe._dict(), **values)
	return doc


class TestReturnDiagnosticLocalization(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "user_date_format", "dd/mm/yyyy", create=True))
		self.enterContext(patch.object(frappe.local, "user_time_format", "HH:mm:ss", create=True))
		self.enterContext(patch.object(frappe.local, "request_cache", defaultdict(dict), create=True))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={k: v for k, v in TARGETS.items() if k in keys},
			)
		)
		self.meta = {}
		for doctype, (module, _translation) in DOCTYPES.items():
			for name in (doctype, doctype + " Item"):
				self.meta[name] = read_meta(module, name)
		self.db = SimpleNamespace(
			db_type="mariadb",
			qb=MariaDB,
			exists=Mock(return_value=True),
			get_value=Mock(return_value="EUR"),
			get_default=Mock(return_value="3"),
			sql=Mock(return_value=[]),
		)
		self.enterContext(patch.object(frappe, "db", self.db))
		self.enterContext(patch.object(frappe.local, "db", self.db, create=True))
		self.enterContext(patch.object(frappe.local, "qb", MariaDB, create=True))
		self.get_meta = self.enterContext(patch("frappe.get_meta", side_effect=self.meta.__getitem__))
		self.get_doc = self.enterContext(patch("frappe.get_doc", side_effect=lambda *_args: self.original))
		self.get_all = self.enterContext(patch("frappe.get_all", side_effect=self.reference_items))
		self.get_cached_value = self.enterContext(
			patch("frappe.get_cached_value", side_effect=self.cached_value)
		)
		self.enterContext(patch("frappe.get_system_settings", return_value="Banker's Rounding"))
		self.enterContext(patch("frappe.get_single_value", return_value=0))
		self.reference_rows = []
		self.valuation = "FIFO"
		self.uom = "Box"

	def documents(self, doctype="Sales Invoice", **overrides):
		values = dict(
			doctype=doctype,
			name="Sales Invoice",
			company="COMPANY-RAW",
			posting_date="2026-10-21",
			posting_time="10:00:00",
			conversion_rate=1,
			docstatus=DocStatus(1),
			meta=self.meta[doctype],
		)
		for field, value in (("customer", "CUSTOMER-RAW"), ("supplier", "SUPPLIER-RAW"), ("update_stock", 0)):
			if self.meta[doctype].has_field(field):
				values[field] = value
		self.original = make_doc(**values)
		values.update(name="RETURN-RAW", return_against="Sales Invoice", is_return=1)
		values.update(overrides)
		self.doc = make_doc(**values)
		return self.doc

	def item(self, doc, **overrides):
		values = dict(
			doctype=doc.doctype + " Item",
			idx=1,
			item_code="ITEM-RAW",
			qty=-1,
			stock_qty=-1,
			received_qty=-1,
			rejected_qty=0,
			rate=10,
			conversion_factor=1,
			warehouse="WAREHOUSE-RAW",
			meta=self.meta[doc.doctype + " Item"],
		)
		link = "dn_detail" if doc.doctype == "Delivery Note" else frappe.scrub(doc.doctype) + "_item"
		values[link] = "PREVIOUS-ROW"
		values.update(overrides)
		row = make_doc(**values)
		doc.items = [row]
		self.reference_rows = [
			frappe._dict(
				item_code=row.item_code,
				name="PREVIOUS-ROW",
				parenttype=doc.doctype,
				qty=10,
				stock_qty=10,
				received_qty=12,
				rejected_qty=2,
				rate=10,
				conversion_factor=1,
			)
		]
		return row

	def reference_items(self, doctype, **kwargs):
		return self.reference_rows if doctype == self.doc.doctype + " Item" else []

	def cached_value(self, doctype, name, field):
		self.assertEqual(doctype, "Item")
		if field == "valuation_method":
			return self.valuation
		self.assertEqual(field, "stock_uom")
		return self.uom

	def message(self, action, exception=frappe.ValidationError):
		with self.assertRaises(exception) as caught:
			action()
		return str(caught.exception)

	def test_timestamp_message_matches_strict_boundary_for_all_return_types(self):
		for doctype in DOCTYPES:
			with self.subTest(doctype=doctype):
				doc = self.documents(doctype, posting_time="09:59:59")
				self.assertEqual(
					self.message(lambda: returns.validate_return_against(doc)),
					"La data e l'ora di registrazione non possono precedere 21/10/2026 10:00:00.",
				)
				self.db.exists.assert_called_with(doctype, "Sales Invoice")
				self.get_doc.assert_called_with(doctype, "Sales Invoice")

	def test_equal_later_and_default_midnight_timestamps_remain_valid(self):
		for doctype in DOCTYPES:
			for time in ("10:00:00", "10:00:01"):
				with self.subTest(doctype=doctype, time=time):
					returns.validate_return_against(self.documents(doctype, posting_time=time))
			doc = self.documents(doctype, posting_time=None)
			self.original.posting_time = None
			returns.validate_return_against(doc)

	def test_reference_gates_and_non_return_passthrough_remain_unchanged(self):
		for company, status in (("OTHER-COMPANY", DocStatus(1)), ("COMPANY-RAW", DocStatus(0))):
			doc = self.documents(conversion_rate=2, posting_time="09:00:00")
			self.original.company, self.original.docstatus = company, status
			returns.validate_return_against(doc)
		for values in ({"is_return": 0}, {"return_against": None}):
			self.get_all.reset_mock()
			returns.validate_return(self.documents(**values))
			self.get_all.assert_not_called()

	def test_exchange_message_translates_type_but_preserves_source_name(self):
		for doctype, (_module, label) in DOCTYPES.items():
			with self.subTest(doctype=doctype):
				doc = self.documents(doctype, conversion_rate=2)
				self.assertEqual(
					self.message(lambda: returns.validate_return_against(doc)),
					f"Il tasso di cambio deve coincidere con quello del documento {label} Sales Invoice (1).",
				)
				self.assertEqual(
					(doc.doctype, doc.return_against, doc.conversion_rate), (doctype, "Sales Invoice", 2)
				)
				self.db.exists.assert_called_with(doctype, "Sales Invoice")
				self.get_doc.assert_called_with(doctype, "Sales Invoice")

	def test_party_message_preserves_metadata_label_names_markup_and_title(self):
		for doctype in ("Sales Invoice", "Delivery Note", "Purchase Invoice", "Purchase Receipt"):
			with self.subTest(doctype=doctype):
				party = "customer" if doctype in ("Sales Invoice", "Delivery Note") else "supplier"
				doc = self.documents(doctype, **{party: "Sales Invoice"})
				field = "Cliente" if party == "customer" else "Fornitore"
				original_name = "CUSTOMER-RAW" if party == "customer" else "SUPPLIER-RAW"
				self.assertEqual(
					self.message(lambda: returns.validate_return_against(doc)),
					f"Il valore <strong>Sales Invoice</strong> del campo {field} non corrisponde al valore "
					f"<strong>{original_name}</strong> del campo {field} nel documento {DOCTYPES[doctype][1]} Sales Invoice.",
				)
				self.assertEqual(frappe.local.message_log[-1]["title"], "Controparte diversa")
				self.assertEqual(doc.get(party), "Sales Invoice")

	def test_missing_linked_item_message_keeps_canonical_queries_and_severity(self):
		for doctype, (_module, label) in DOCTYPES.items():
			with self.subTest(doctype=doctype):
				doc = self.documents(doctype)
				row = self.item(doc, item_code="Sales Invoice")
				self.reference_rows = []
				self.get_all.reset_mock()
				self.assertEqual(
					self.message(lambda: returns.validate_returned_items(doc)),
					f"Riga n. 1: l'articolo reso Sales Invoice non è presente nel documento {label} Sales Invoice.",
				)
				query = self.get_all.call_args_list[0]
				self.assertEqual(query.args, (doctype + " Item",))
				self.assertEqual(query.kwargs["filters"], {"parent": "Sales Invoice"})
				self.assertEqual(query.kwargs["limit_page_length"], 0)
				self.assertEqual(row.item_code, "Sales Invoice")
				self.assertEqual(frappe.local.message_log[-1]["raise_exception"], 1)

	def test_unlinked_missing_item_is_still_a_warning(self):
		for doctype in ("Sales Invoice", "Purchase Invoice", "Purchase Receipt", "POS Invoice"):
			with self.subTest(doctype=doctype):
				doc = self.documents(doctype)
				row = self.item(doc)
				row.set(frappe.scrub(doctype) + "_item", None)
				self.reference_rows = []
				returns.validate_returned_items(doc)
				self.assertEqual(
					frappe.local.message_log[-1]["message"],
					f"Riga n. 1: l'articolo reso ITEM-RAW non è presente nel documento {DOCTYPES[doctype][1]} Sales Invoice.",
				)
				self.assertNotIn("raise_exception", frappe.local.message_log[-1])

	def test_full_rate_error_localizes_type_and_keeps_valuation_read(self):
		for doctype in ("Sales Invoice", "Delivery Note"):
			with self.subTest(doctype=doctype):
				doc = self.documents(doctype)
				row = self.item(doc, rate=11)
				self.assertEqual(
					self.message(lambda: returns.validate_return(doc)),
					f"Riga n. 1: il prezzo unitario non può superare quello del documento {DOCTYPES[doctype][1]} Sales Invoice.",
				)
				self.get_cached_value.assert_called_with("Item", "ITEM-RAW", "valuation_method")
				self.assertEqual((row.qty, row.stock_qty, row.rate), (-1, -1, 11))

	def test_rate_equality_lower_price_and_moving_average_remain_valid(self):
		for doctype in ("Sales Invoice", "Delivery Note"):
			for valuation, rate in (("FIFO", 9), ("FIFO", 10), ("Moving Average", 11)):
				with self.subTest(doctype=doctype, valuation=valuation, rate=rate):
					doc = self.documents(doctype)
					self.item(doc, rate=rate)
					self.valuation = valuation
					frappe.local.request_cache.clear()
					returns.validate_return(doc)

	def test_positive_quantity_uses_real_child_metadata_and_preserves_read_order(self):
		modes = (
			("Sales Invoice", 0, "qty", "Quantità"),
			("Sales Invoice", 1, "stock_qty", "Quantità in UdM di magazzino"),
			("Purchase Invoice", 0, "qty", "Quantità accettata"),
			("Purchase Invoice", 1, "stock_qty", "Quantità accettata in UdM di magazzino"),
			("Purchase Receipt", 0, "stock_qty", "Quantità accettata in UdM di magazzino"),
			("Delivery Note", 0, "stock_qty", "Quantità in UdM di magazzino"),
			("POS Invoice", 0, "stock_qty", "Quantità in UdM di magazzino"),
		)
		for doctype, stock, column, label in modes:
			with self.subTest(doctype=doctype, stock=stock):
				doc = self.documents(doctype, update_stock=stock)
				row = self.item(doc, qty=1, stock_qty=1)
				reads = Mock()
				reads.attach_mock(self.db.get_value, "currency")
				reads.attach_mock(self.get_meta, "meta")
				frappe.flags.company_currency = {}
				self.db.get_value.reset_mock()
				self.get_meta.reset_mock()
				with patch.object(
					self.meta[doctype + " Item"],
					"get_translated_label",
					wraps=self.meta[doctype + " Item"].get_translated_label,
				) as labels:
					self.assertEqual(
						self.message(
							lambda: returns.validate_quantity(
								doc, "ITEM-RAW", row, self.reference_rows[0], {}, {}
							)
						),
						f"Il valore del campo {label} deve essere minore o uguale a zero in un documento di reso.",
					)
					labels.assert_called_once_with(column)
				self.assertEqual(
					reads.mock_calls,
					[
						call.currency("Company", "COMPANY-RAW", "default_currency", cache=True),
						call.meta(doctype + " Item"),
					],
				)

	def test_received_and_rejected_labels_come_from_metadata(self):
		for doctype in ("Purchase Receipt", "Purchase Invoice"):
			for column, label in (
				("received_qty", "Quantità ricevuta"),
				("rejected_qty", "Quantità respinta"),
			):
				with self.subTest(doctype=doctype, field=column):
					doc = self.documents(doctype, update_stock=1)
					row = self.item(doc, **{column: 1})
					self.assertEqual(
						self.message(
							lambda: returns.validate_quantity(
								doc, "ITEM-RAW", row, self.reference_rows[0], {}, {}
							)
						),
						f"Il valore del campo {label} deve essere minore o uguale a zero in un documento di reso.",
					)

	def test_quantity_conversion_stale_values_and_zero_reference_are_unchanged(self):
		for qty, stock_qty, factor, reference in ((-1, 99, 2, 10), (0, 0, 1, 10), (1, 1, 1, 0)):
			with self.subTest(qty=qty, stock_qty=stock_qty, reference=reference):
				doc = self.documents("Sales Invoice", update_stock=1)
				row = self.item(doc, qty=qty, stock_qty=stock_qty, conversion_factor=factor)
				ref = self.reference_rows[0]
				ref.stock_qty = reference
				returns.validate_quantity(doc, "ITEM-RAW", row, ref, {}, {})
				self.assertEqual((row.qty, row.stock_qty, row.conversion_factor), (qty, stock_qty, factor))

	def test_rejected_only_full_receipt_accepts_zero_accepted_qty_after_prior_returns(self):
		for prior in (False, True):
			with self.subTest(prior=prior):
				doc = self.documents("Purchase Receipt")
				row = self.item(doc, qty=0, stock_qty=0, received_qty=-1, rejected_qty=-1)
				previous = (
					[
						frappe._dict(
							item_code="ITEM-RAW",
							purchase_receipt_item="PREVIOUS-ROW",
							qty=10,
							stock_qty=10,
							received_qty=10,
							rejected_qty=0,
						)
					]
					if prior
					else []
				)
				self.db.sql.return_value = previous
				returns.validate_return(doc)
				self.assertEqual((row.qty, row.stock_qty, row.received_qty, row.rejected_qty), (0, 0, -1, -1))

	def test_overreturn_zero_only_and_rejected_warehouse_buckets_remain_enforced(self):
		doc = self.documents("Purchase Receipt")
		row = self.item(doc, qty=0, stock_qty=0, received_qty=0, rejected_qty=0)
		ref = self.reference_rows[0]
		previous = {"ITEM-RAW": {"stock_qty": 10, "received_qty": 12, "rejected_qty": 2}}
		self.message(
			lambda: returns.validate_quantity(doc, "ITEM-RAW", row, ref, {}, previous),
			returns.StockOverReturnError,
		)
		row.qty, row.stock_qty, row.received_qty = -11, -11, -11
		self.message(
			lambda: returns.validate_quantity(doc, "ITEM-RAW", row, ref, {}, {}), returns.StockOverReturnError
		)
		row.qty, row.stock_qty, row.received_qty = -1, -1, -1
		row.return_qty_from_rejected_warehouse = 1
		returns.validate_quantity(
			doc, "ITEM-RAW", row, ref, {}, {"ITEM-RAW": {"stock_qty": 10, "rejected_qty": 0}}
		)
		self.message(
			lambda: returns.validate_quantity(
				doc, "ITEM-RAW", row, ref, {}, {"ITEM-RAW": {"stock_qty": 0, "rejected_qty": 2}}
			),
			returns.StockOverReturnError,
		)

	def test_actual_bundle_version_components_and_units_are_display_only(self):
		for unit, label in (("Box", "Scatola"), ("Set", "Set"), ("CUSTOM-UOM-RAW", "CUSTOM-UOM-RAW")):
			with self.subTest(unit=unit):
				doc = self.documents("Delivery Note")
				row = self.item(
					doc, item_code="BUNDLE-RAW", qty=-2, stock_qty=-2, product_bundle="VERSION-RAW"
				)
				self.uom = unit
				bundle = frappe._dict(new_item_code="BUNDLE-RAW", docstatus=1, disabled=0)
				with patch.object(self.db, "get_value", return_value=bundle) as version, patch.object(
					self.db, "sql", return_value=[frappe._dict(item_code="COMPONENT-RAW", qty=1)]
				) as components, patch("frappe.get_all", return_value=["BUNDLE-RAW"]):
					returned = {}
					self.assertEqual(
						self.message(
							lambda: returns.validate_returned_bundle_components(
								doc, {"COMPONENT-RAW": frappe._dict(stock_qty=1)}, returned
							),
							returns.StockOverReturnError,
						),
						f"Riga n. 1: non è possibile rendere <strong>BUNDLE-RAW</strong>; rimangono da restituire solo 1.0 {label} del suo componente <strong>COMPONENT-RAW</strong>",
					)
					version.assert_called_once_with(
						"Product Bundle",
						"VERSION-RAW",
						["new_item_code", "docstatus", "disabled"],
						as_dict=True,
					)
					self.assertEqual(components.call_args.args[1], {"param1": "VERSION-RAW"})
					self.assertEqual(returned, {"COMPONENT-RAW": {"qty": 0, "stock_qty": 2}})
					self.get_cached_value.assert_called_with("Item", "COMPONENT-RAW", "stock_uom")
					self.assertEqual((row.product_bundle, row.item_code), ("VERSION-RAW", "BUNDLE-RAW"))

	def test_bundle_equality_keeps_quantity_and_version_behavior(self):
		doc = self.documents("Delivery Note")
		self.item(doc, item_code="BUNDLE-RAW", qty=-1, stock_qty=-1, product_bundle="VERSION-RAW")
		bundle = frappe._dict(new_item_code="BUNDLE-RAW", docstatus=1, disabled=0)
		with patch.object(self.db, "get_value", return_value=bundle), patch.object(
			self.db, "sql", return_value=[frappe._dict(item_code="COMPONENT-RAW", qty=1)]
		), patch("frappe.get_all", return_value=["BUNDLE-RAW"]):
			returned = {}
			returns.validate_returned_bundle_components(
				doc, {"COMPONENT-RAW": frappe._dict(stock_qty=1)}, returned
			)
			self.assertEqual(returned, {"COMPONENT-RAW": {"qty": 0, "stock_qty": 1}})
			self.get_cached_value.assert_not_called()
