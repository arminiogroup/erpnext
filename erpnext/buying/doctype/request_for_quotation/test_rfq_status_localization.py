from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils.formatters import format_value

from erpnext.buying.doctype.request_for_quotation.request_for_quotation import RequestforQuotation
from erpnext.buying.doctype.supplier_quotation.supplier_quotation import SupplierQuotation


class TestRFQStatusLocalization(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("translator@example.com")
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "it"
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={"Received": "Ricevuto", "Pending": "In attesa"},
			)
		)
		self.database = SimpleNamespace(db_type="mariadb", sql=Mock(), set_value=Mock(), bulk_update=Mock())
		self.enterContext(patch.object(frappe.local, "db", self.database, create=True))

	def rfq(self):
		doc = object.__new__(RequestforQuotation)
		doc.__dict__.update(
			name="RFQ-LOCALIZATION",
			items=[frappe._dict(name="RFQ-ITEM-1"), frappe._dict(name="RFQ-ITEM-2")],
			suppliers=[
				frappe._dict(name="RFQ-SUPPLIER-1", supplier="Supplier A", quote_status="Pending"),
				frappe._dict(name="RFQ-SUPPLIER-2", supplier="Supplier B", quote_status="Received"),
			],
		)
		return doc

	def quotation(self, item_names=("RFQ-ITEM-1", "RFQ-ITEM-2")):
		doc = object.__new__(SupplierQuotation)
		doc.__dict__.update(
			name="SQ-LOCALIZATION",
			supplier="Supplier A",
			items=[
				frappe._dict(request_for_quotation="RFQ-LOCALIZATION", request_for_quotation_item=name)
				for name in item_names
			],
		)
		return doc

	def counts(self, *values):
		self.database.sql.side_effect = [[frappe._dict(count=value)] for value in values]

	def update_quotation(self, quotation, include_me, previous_rfqs=None):
		rfq = self.rfq()
		with (
			patch("frappe.get_doc", return_value=rfq),
			patch("frappe.get_all", return_value=[rfq.suppliers[0]]),
		):
			quotation.update_rfq_supplier_status(include_me, previous_rfqs)
		return [call.args[-1] for call in self.database.set_value.call_args_list]

	def test_rfq_status_values_do_not_depend_on_the_display_language(self):
		rfq = self.rfq()
		self.counts(1, 1, 1, 0)
		rfq.update_rfq_supplier_status()
		self.assertEqual([row.quote_status for row in rfq.suppliers], ["Received", "Pending"])

	def test_rfq_supplier_filter_preserves_unselected_rows(self):
		rfq = self.rfq()
		self.counts(0, 0)
		rfq.update_rfq_supplier_status("Supplier A")
		self.assertEqual([row.quote_status for row in rfq.suppliers], ["Pending", "Received"])

	def test_submit_keeps_canonical_status_when_this_quotation_covers_all_items(self):
		self.counts(0, 0)
		self.assertEqual(self.update_quotation(self.quotation(), 1), ["Received", "Received"])

	def test_cancel_keeps_canonical_pending_status(self):
		self.counts(0, 0)
		self.assertEqual(self.update_quotation(self.quotation(), 0), ["Pending", "Pending"])

	def test_other_submitted_quotations_keep_the_received_status(self):
		self.counts(1, 1)
		self.assertEqual(self.update_quotation(self.quotation(), 0), ["Received", "Received"])

	def test_partial_item_coverage_remains_pending(self):
		self.counts(0, 0)
		self.assertEqual(self.update_quotation(self.quotation(("RFQ-ITEM-1",)), 1), ["Received", "Pending"])

	def test_removed_quotation_rows_recompute_previous_rfq(self):
		self.counts(1, 0)
		self.assertEqual(
			self.update_quotation(self.quotation(()), 1, {"RFQ-LOCALIZATION"}), ["Received", "Pending"]
		)

	def test_select_display_translates_the_canonical_values(self):
		field = frappe._dict(fieldtype="Select", options="Pending\nReceived")
		self.assertEqual(format_value("Pending", field), "In attesa")
		self.assertEqual(format_value("Received", field), "Ricevuto")

	def test_repair_derives_status_from_item_coverage_for_each_supplier(self):
		from erpnext.patches.v16_0.normalize_rfq_supplier_quote_status import execute

		self.database.sql.side_effect = [
			[
				frappe._dict(name="BAD-1", parent="RFQ-1", supplier="Supplier A"),
				frappe._dict(name="BAD-2", parent="RFQ-1", supplier="Supplier B"),
				frappe._dict(name="BAD-3", parent="RFQ-2", supplier="Supplier A"),
			],
			[
				frappe._dict(name="ITEM-1", parent="RFQ-1"),
				frappe._dict(name="ITEM-2", parent="RFQ-1"),
				frappe._dict(name="ITEM-3", parent="RFQ-2"),
			],
			[
				frappe._dict(supplier="Supplier A", request_for_quotation_item="ITEM-1"),
				frappe._dict(supplier="Supplier A", request_for_quotation_item="ITEM-2"),
				frappe._dict(supplier="Supplier B", request_for_quotation_item="ITEM-1"),
			],
			[],
		]
		execute()
		self.database.bulk_update.assert_called_once_with(
			"Request for Quotation Supplier",
			{
				"BAD-1": {"quote_status": "Received"},
				"BAD-2": {"quote_status": "Pending"},
				"BAD-3": {"quote_status": "Pending"},
			},
			update_modified=False,
		)

	def test_repair_with_no_invalid_rows_performs_no_update(self):
		from erpnext.patches.v16_0.normalize_rfq_supplier_quote_status import execute

		self.database.sql.return_value = []
		execute()
		self.database.bulk_update.assert_not_called()
		self.database.sql.assert_called_once()

	def test_repair_empty_item_list_matches_the_existing_controller(self):
		from erpnext.patches.v16_0.normalize_rfq_supplier_quote_status import execute

		row = frappe._dict(name="BAD-EMPTY", parent="RFQ-EMPTY", supplier="Supplier A")
		self.database.sql.side_effect = [[row], [], []]
		execute()
		self.database.bulk_update.assert_called_once_with(
			"Request for Quotation Supplier",
			{"BAD-EMPTY": {"quote_status": "Received"}},
			update_modified=False,
		)
		rfq = self.rfq()
		rfq.items = []
		rfq.update_rfq_supplier_status()
		self.assertEqual(rfq.suppliers[0].quote_status, "Received")

	def test_repair_processes_the_next_batch_after_normalizing_the_first(self):
		from erpnext.patches.v16_0.normalize_rfq_supplier_quote_status import execute

		self.database.sql.side_effect = [
			[frappe._dict(name="BAD-1", parent="RFQ-1", supplier="Supplier A")],
			[frappe._dict(name="ITEM-1", parent="RFQ-1")],
			[],
			[frappe._dict(name="BAD-2", parent="RFQ-2", supplier="Supplier B")],
			[frappe._dict(name="ITEM-2", parent="RFQ-2")],
			[frappe._dict(supplier="Supplier B", request_for_quotation_item="ITEM-2")],
			[],
		]
		execute()
		self.assertEqual(
			[call.args[1] for call in self.database.bulk_update.call_args_list],
			[{"BAD-1": {"quote_status": "Pending"}}, {"BAD-2": {"quote_status": "Received"}}],
		)
