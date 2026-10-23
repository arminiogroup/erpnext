# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.tests import UnitTestCase

from erpnext.maintenance.doctype.maintenance_schedule import maintenance_schedule

SOURCE = "Maintenance start date must be after the delivery date for Serial No {0}."
TARGET = "La data di inizio della manutenzione deve essere successiva alla data di consegna per il numero di serie {0}."
SERIAL_FIELDS = [
	"serial_no",
	"warranty_expiry_date",
	"amc_expiry_date",
	"warehouse",
	"item_code",
	"customer",
]
LEGACY_ROWS = [
	("SERIAL-ID", "2026-10-20 08:00:00"),
	("SERIAL-ID\nOTHER-ID", "2026-10-22 12:00:00"),
	("SERIAL-ID-EXT", "2026-10-25 08:00:00"),
]


class DeliveryReads:
	db_type = "mariadb"

	def __init__(self, delivery=None, legacy=(), details=None):
		self.delivery = delivery
		self.legacy = legacy
		self.details = details
		self.reads = []

	def get_value(self, doctype, name, fields, **kwargs):
		assert (doctype, name, fields, kwargs) == ("Serial No", "SERIAL-ID", SERIAL_FIELDS, {"as_dict": 1})
		self.reads.append(("serial", doctype, name, deepcopy(fields), kwargs))
		return deepcopy(self.details)

	def sql(self, query, params=None, **kwargs):
		self.reads.append(("sql", query, deepcopy(params), kwargs))
		assert kwargs == {"as_dict": True}
		if "tabSerial and Batch Entry" in query:
			assert "MAX(" in query and "GROUP BY" in query
			assert "`docstatus`=1" in query and "`is_cancelled`=0" in query
			assert set(params.values()) == {
				"SERIAL-ID",
				"ITEM-RAW",
				"Outward",
				"Delivery Note",
				"Sales Invoice",
			}
			return (
				[frappe._dict(serial_no="SERIAL-ID", posting_datetime=self.delivery)] if self.delivery else []
			)
		assert "tabStock Ledger Entry" in query
		assert "`actual_qty`<" in query and "`is_cancelled`=" in query
		assert set(params.values()) == {"ITEM-RAW", "Delivery Note", "Sales Invoice", "%SERIAL-ID%"}
		return [frappe._dict(serial_no=number, posting_datetime=date) for number, date in self.legacy]

	def __getattr__(self, name):
		raise AssertionError(f"Unexpected database operation: {name}")


class TestMaintenanceDeliveryDateMessage(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		self.addCleanup(setattr, frappe.local, "message_log", getattr(frappe.local, "message_log", []))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"

	def extracted(self, path):
		methods = get_method_map("erpnext") + get_method_map("frappe")
		relative = str(
			Path(maintenance_schedule.__file__).relative_to(Path(frappe.get_app_path("erpnext")).parent)
		)
		method = next(method for pattern, method in methods if pathmatch(pattern, relative))
		self.assertEqual(method, "frappe.gettext.extractors.python.extract")
		return list(extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS))

	def dictionary(self):
		return {
			message: TARGET
			for _, message, _, context in self.extracted(Path(maintenance_schedule.__file__))
			if message == SOURCE and context is None
		}

	def validate_serials(
		self, start, delivery=None, legacy=(), changes=None, missing=False, serials=None, language="it"
	):
		details = frappe._dict(
			serial_no="SN<&>",
			warranty_expiry_date=None,
			amc_expiry_date=None,
			warehouse=None,
			item_code="ITEM-RAW",
			customer="CUSTOMER-RAW",
		)
		details.update(changes or {})
		db = DeliveryReads(delivery, legacy, None if missing else details)
		frappe.local.db = db
		frappe.local.message_log = []
		doc = maintenance_schedule.MaintenanceSchedule.__new__(maintenance_schedule.MaintenanceSchedule)
		doc.__dict__.update(
			doctype="Maintenance Schedule", name="SCHEDULE-RAW", customer="CUSTOMER-RAW", flags=frappe._dict()
		)
		state, input_details = deepcopy(doc.__dict__), deepcopy(details)
		error = None
		with (
			patch.object(frappe.local, "lang", language),
			patch(
				"frappe.translate.get_all_translations",
				return_value=self.dictionary() if language == "it" else {},
			),
		):
			try:
				doc.validate_serial_no("ITEM-RAW", ["SERIAL-ID"] if serials is None else serials, start)
			except frappe.ValidationError as exc:
				error = str(exc)
		self.assertEqual(doc.__dict__, state)
		self.assertEqual(details, input_details)
		self.assertEqual(frappe.session.user, "translator@example.com")
		return error, db.reads, deepcopy(frappe.local.message_log)

	def test_configured_extraction_and_test_source_have_expected_keys(self):
		keys = [
			(message, context)
			for _, message, _, context in self.extracted(Path(maintenance_schedule.__file__))
		]
		self.assertIn((SOURCE, None), keys)
		self.assertEqual(self.extracted(Path(__file__)), [])

	def test_whole_english_and_italian_error_escapes_the_displayed_serial(self):
		for language, frame in (("en", SOURCE), ("it", TARGET)):
			with self.subTest(language=language):
				error, reads, messages = self.validate_serials("2026-10-20", "2026-10-20", language=language)
				self.assertEqual(error, frame.format("SN&lt;&amp;&gt;"))
				self.assertEqual([read[0] for read in reads], ["sql", "serial"])
				self.assertEqual(messages[-1].message, error)

	def test_all_seven_delivery_boundaries_preserve_date_normalization_and_state(self):
		cases = [
			("before", "2026-10-19", "2026-10-20", (), True),
			("equal", "2026-10-20", "2026-10-20", (), True),
			("after", "2026-10-21", "2026-10-20", (), False),
			("same day times", "2026-10-20 23:59:00", "2026-10-20 01:00:00", (), True),
			("no delivery", "2026-10-20", None, (), False),
			("legacy equal", "2026-10-22", None, LEGACY_ROWS, True),
			("legacy after", "2026-10-23", None, LEGACY_ROWS, False),
		]
		for language, frame in (("en", SOURCE), ("it", TARGET)):
			for name, start, delivery, legacy, rejected in cases:
				with self.subTest(language=language, case=name):
					error, reads, _ = self.validate_serials(start, delivery, legacy, language=language)
					self.assertEqual(error, frame.format("SN&lt;&amp;&gt;") if rejected else None)
					self.assertEqual(
						[read[0] for read in reads], ["sql", *([] if delivery else ["sql"]), "serial"]
					)

	def test_earlier_missing_item_warranty_contract_and_stock_guards_keep_precedence(self):
		cases = [
			({}, True, "A selected Serial No no longer exists. Please select it again."),
			(
				{"item_code": "OTHER-ITEM"},
				False,
				"Serial No <strong>SN&lt;&amp;&gt;</strong> does not belong to Item <strong>ITEM-RAW</strong>",
			),
			(
				{
					"warranty_expiry_date": "2026-10-20",
					"amc_expiry_date": "2026-10-21",
					"warehouse": "WAREHOUSE-RAW",
				},
				False,
				"Serial No SN&lt;&amp;&gt; is under warranty until 2026-10-20",
			),
			(
				{"amc_expiry_date": "2026-10-20", "warehouse": "WAREHOUSE-RAW"},
				False,
				"Serial No SN&lt;&amp;&gt; is under maintenance contract until 2026-10-20",
			),
			(
				{"warehouse": "WAREHOUSE-RAW"},
				False,
				"Serial No <strong>SN&lt;&amp;&gt;</strong> is still in stock in Warehouse <strong>WAREHOUSE-RAW</strong>",
			),
		]
		for changes, missing, expected in cases:
			with self.subTest(changes=changes, missing=missing):
				error, reads, _ = self.validate_serials(
					"2026-10-20", "2026-10-20", changes=changes, missing=missing
				)
				self.assertEqual(error, expected)
				self.assertEqual([read[0] for read in reads], ["sql", "serial"])

	def test_different_customer_warns_but_does_not_reject_a_later_start(self):
		error, reads, messages = self.validate_serials(
			"2026-10-21", "2026-10-20", changes={"customer": "OTHER<&>"}
		)
		self.assertIsNone(error)
		self.assertEqual([read[0] for read in reads], ["sql", "serial"])
		self.assertEqual(len(messages), 1)
		self.assertEqual(messages[0].title, "Different Customer")
		self.assertEqual(messages[0].indicator, "orange")
		self.assertEqual(
			messages[0].message,
			"Serial No <strong>SN&lt;&amp;&gt;</strong> was sold to Customer <strong>OTHER&lt;&amp;&gt;</strong>, not <strong>CUSTOMER-RAW</strong>",
		)

	def test_empty_serial_list_performs_no_read_or_message(self):
		error, reads, messages = self.validate_serials("2026-10-20", serials=[])
		self.assertIsNone(error)
		self.assertEqual(reads, [])
		self.assertEqual(messages, [])

	def test_actual_legacy_query_uses_exact_tokens_and_latest_delivery(self):
		db = DeliveryReads(legacy=LEGACY_ROWS)
		frappe.local.db = db
		self.assertEqual(
			maintenance_schedule.get_legacy_delivery_dates("ITEM-RAW", ["SERIAL-ID"]),
			{"SERIAL-ID": "2026-10-22 12:00:00"},
		)
		self.assertEqual(len(db.reads), 1)
