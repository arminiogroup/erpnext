from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.manufacturing.doctype.work_order import work_order


class TestBatchCreationMessage(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		for name in ("lang", "message_log", "response"):
			self.addCleanup(setattr, frappe.local, name, getattr(frappe.local, name))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.message_log = []
		frappe.local.response = frappe._dict()
		self.enterContext(patch.dict(frappe.flags, {"mute_messages": False, "print_messages": False}))
		self.message = "Batch not created for item {0} because automatic batch creation is disabled."
		self.italian = "Lotto non creato per l’articolo {0}: la creazione automatica dei lotti è disattivata."
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: {
					self.message: self.italian,
					"Message:Default title of the message dialog": "Messaggio",
				}
				if lang == "it"
				else {},
			)
		)
		self.item = {"create_new_batch": 0, "batch_number_series": "SERIES.#####"}
		self.item_lookup = self.enterContext(
			patch("frappe.get_cached_value", side_effect=lambda doctype, name, field: self.item[field])
		)
		self.make_batch = self.enterContext(patch.object(work_order, "make_batch"))

	def make_work_order(self, **values):
		self.assertEqual(frappe.session.user, "translator@example.com")
		doc = work_order.WorkOrder.__new__(work_order.WorkOrder)
		doc.__dict__.update(
			doctype="Work Order",
			name="WO-MEMORY",
			production_item="ITEM-MEMORY",
			qty=7,
			batch_size=3,
			production_plan=None,
			amended_from=None,
			has_serial_no=0,
			has_batch_no=1,
			track_semi_finished_goods=0,
		)
		doc.__dict__.update(values)
		return doc

	def assert_allocated_batches(self, quantities):
		self.assertEqual(
			[dict(call.args[0]) for call in self.make_batch.call_args_list],
			[
				{
					"item": "ITEM-MEMORY",
					"qty_to_produce": qty,
					"reference_doctype": "Work Order",
					"reference_name": "WO-MEMORY",
				}
				for qty in quantities
			],
		)

	def test_disabled_automatic_creation_reports_actual_reason_even_with_a_series(self):
		for lang in ("it", "en"):
			for series in ("SERIES.#####", None):
				with self.subTest(lang=lang, series=series):
					frappe.local.lang = lang
					frappe.local.message_log = []
					self.item["batch_number_series"] = series
					self.item_lookup.reset_mock()
					doc = self.make_work_order(batch_size=0)
					self.assertIsNone(doc.create_batch_for_finished_good())
					self.item_lookup.assert_called_once_with("Item", "ITEM-MEMORY", "create_new_batch")
					self.make_batch.assert_not_called()
					self.assertEqual(doc.batch_size, 7)
					self.assertEqual(len(frappe.local.message_log), 1)
					message = frappe.local.message_log[0]
					self.assertEqual(
						message.message,
						(self.italian if lang == "it" else self.message).format(frappe.bold("ITEM-MEMORY")),
					)
					self.assertEqual(message.title, "Messaggio" if lang == "it" else "Message")
					self.assertEqual(message.alert, 1)
					self.assertEqual(message.indicator, "orange")
					self.assertIsNone(message.raise_exception)

	def test_enabled_automatic_creation_without_item_series_preserves_batch_arguments(self):
		self.item.update(create_new_batch=1, batch_number_series=None)
		doc = self.make_work_order()
		self.assertIsNone(doc.create_batch_for_finished_good(7))
		self.item_lookup.assert_called_once_with("Item", "ITEM-MEMORY", "create_new_batch")
		self.assert_allocated_batches([3, 3, 1])
		self.assertEqual(doc.qty, 7)
		self.assertEqual(doc.batch_size, 3)
		self.assertEqual(frappe.local.message_log, [])

	def test_remaining_quantity_and_zero_or_missing_defaults_keep_existing_allocation(self):
		self.item["create_new_batch"] = 1
		for total, batch_size, expected in ((None, 0, [7]), (0, 0, [7]), (5, 3, [3, 2])):
			with self.subTest(total=total, batch_size=batch_size):
				self.make_batch.reset_mock()
				doc = self.make_work_order(batch_size=batch_size)
				self.assertIsNone(doc.create_batch_for_finished_good(total))
				self.assert_allocated_batches(expected)
				self.assertEqual(doc.batch_size, batch_size or 7)
		self.assertEqual(frappe.local.message_log, [])

	def test_before_submit_keeps_global_tracking_and_item_creation_gates(self):
		cases = (
			(1, 0, 0, 1, [], 0),
			(1, 1, 1, 1, [], 0),
			(0, 0, 1, 1, [], 1),
			(1, 0, 1, 0, [], 1),
			(1, 0, 1, 1, [3, 3, 1], 1),
		)
		for enabled, tracked, has_batch, automatic, quantities, config_reads in cases:
			with self.subTest(enabled=enabled, tracked=tracked, has_batch=has_batch, automatic=automatic):
				frappe.local.message_log = []
				self.item["create_new_batch"] = automatic
				self.item_lookup.reset_mock()
				self.make_batch.reset_mock()
				setting = Mock(return_value=enabled)
				doc = self.make_work_order(track_semi_finished_goods=tracked, has_batch_no=has_batch)
				with patch("frappe.db", SimpleNamespace(get_single_value=setting)):
					self.assertIsNone(doc.before_submit())
				self.assertEqual(setting.call_count, config_reads)
				if config_reads:
					setting.assert_called_once_with(
						"Manufacturing Settings", "make_serial_no_batch_from_work_order"
					)
				self.assert_allocated_batches(quantities)
				if enabled and not tracked and has_batch:
					self.item_lookup.assert_called_once_with("Item", "ITEM-MEMORY", "create_new_batch")
				else:
					self.item_lookup.assert_not_called()
				self.assertEqual(
					len(frappe.local.message_log),
					int(enabled and not tracked and has_batch and not automatic),
				)
