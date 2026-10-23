from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.manufacturing.doctype.work_order.services.operations import OperationsService
from erpnext.manufacturing.doctype.work_order.work_order import WorkOrder


class TestOperationQuantityMessage(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		for name in ("lang", "message_log", "response"):
			self.addCleanup(setattr, frappe.local, name, getattr(frappe.local, name))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.message_log = []
		frappe.local.response = frappe._dict()
		self.enterContext(patch.dict(frappe.flags, {"mute_messages": False, "print_messages": False}))
		self.message = "The sum of Completed Qty and Process Loss Qty cannot exceed the maximum quantity allowed for the Work Order, including the overproduction allowance."
		self.italian = "La somma della quantità completata e della quantità persa nel processo non può superare la quantità massima consentita per l’ordine di produzione, inclusa la tolleranza di sovrapproduzione."
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
		self.rounding = "Banker's Rounding"
		self.enterContext(
			patch(
				"frappe.get_system_settings",
				side_effect=lambda field: {"rounding_method": self.rounding, "time_zone": "UTC"}.get(field),
			)
		)
		self.allowance = 0
		self.setting = Mock(side_effect=lambda doctype, field: self.allowance)
		self.enterContext(patch("frappe.db", SimpleNamespace(get_single_value=self.setting)))

	def make_operation(self, name, completed, loss, precision=2):
		self.assertEqual(frappe.session.user, "translator@example.com")
		return frappe._dict(
			name=name,
			completed_qty=completed,
			process_loss_qty=loss,
			precision=lambda field: precision,
			status="Pending",
		)

	def make_work_order(self, operations):
		doc = WorkOrder.__new__(WorkOrder)
		doc.__dict__.update(doctype="Work Order", name="WO-MEMORY", qty=10, operations=operations)
		return doc

	def assert_quantity_error(self, operation, service):
		self.setting.reset_mock()
		with self.assertRaises(frappe.ValidationError) as error:
			service.validate_operation_overproduction(operation)
		self.assertIs(type(error.exception), frappe.ValidationError)
		self.setting.assert_called_once_with(
			"Manufacturing Settings", "overproduction_percentage_for_work_order"
		)
		self.assertEqual(str(error.exception), self.italian if frappe.local.lang == "it" else self.message)
		message = frappe.local.message_log[-1]
		self.assertEqual(message.message, str(error.exception))
		self.assertEqual(message.title, "Messaggio" if frappe.local.lang == "it" else "Message")
		self.assertEqual(message.indicator, "red")
		self.assertEqual(message.raise_exception, 1)

	def test_completed_and_loss_sum_reports_allowed_quantity_in_each_language(self):
		for lang in ("it", "en"):
			for completed, loss, allowance in ((9, 2, 0), (10, 1.01, 10)):
				with self.subTest(lang=lang, completed=completed, loss=loss, allowance=allowance):
					frappe.local.lang = lang
					self.allowance = allowance
					operation = self.make_operation("OP-MEMORY", completed, loss)
					service = OperationsService(self.make_work_order([operation]))
					self.assert_quantity_error(operation, service)
					self.assertEqual(operation.completed_qty, completed)
					self.assertEqual(operation.process_loss_qty, loss)
					self.assertEqual(service.doc.qty, 10)

	def test_allowance_and_equality_keep_existing_acceptance_and_status(self):
		self.allowance = 10
		for completed, loss, status in (
			(10.5, 0, "Completed"),
			(10, 1, "Completed"),
			(8, 1, "Work in Progress"),
		):
			with self.subTest(completed=completed, loss=loss):
				self.setting.reset_mock()
				operation = self.make_operation("OP-MEMORY", completed, loss)
				service = OperationsService(self.make_work_order([operation]))
				self.assertIsNone(service.validate_operation_overproduction(operation))
				self.setting.assert_called_once_with(
					"Manufacturing Settings", "overproduction_percentage_for_work_order"
				)
				self.assertEqual(service._operation_status(operation), status)
		self.assertEqual(frappe.local.message_log, [])

	def test_rounding_configuration_precision_and_missing_values_keep_existing_decisions(self):
		for rounding in ("Banker's Rounding", "Banker's Rounding (legacy)", "Commercial Rounding"):
			with self.subTest(rounding=rounding):
				self.rounding = rounding
				operation = self.make_operation("OP-MEMORY", 10.004, 0.004)
				service = OperationsService(self.make_work_order([operation]))
				self.assertEqual(service._operation_qty(operation), 10)
				self.assertIsNone(service.validate_operation_overproduction(operation))
				operation.completed_qty = 10.006
				self.assertEqual(service._operation_qty(operation), 10.01)
				with self.assertRaises(frappe.ValidationError):
					service.validate_operation_overproduction(operation)
		operation = self.make_operation("OP-MEMORY", None, None)
		service = OperationsService(self.make_work_order([operation]))
		self.assertEqual(service._operation_qty(operation), 0)
		self.assertIsNone(service.validate_operation_overproduction(operation))
		self.assertEqual(service._operation_status(operation), "Pending")

	def test_actual_work_order_wrapper_validates_only_matching_operation(self):
		prior = self.make_operation("PRIOR-OP", 20, 0)
		current = self.make_operation("CURRENT-OP", 9, 1)
		doc = self.make_work_order([prior, current])
		self.assertIsNone(doc.update_operation_status("CURRENT-OP"))
		self.setting.assert_called_once_with(
			"Manufacturing Settings", "overproduction_percentage_for_work_order"
		)
		self.assertEqual([row.status for row in doc.operations], ["Completed", "Completed"])
		self.assertEqual([row.completed_qty for row in doc.operations], [20, 9])
		self.assertEqual([row.process_loss_qty for row in doc.operations], [0, 1])
		self.setting.reset_mock()
		self.assertIsNone(doc.update_operation_status())
		self.setting.assert_not_called()
		current.process_loss_qty = 2
		with self.assertRaises(frappe.ValidationError) as error:
			doc.update_operation_status("CURRENT-OP")
		self.assertIs(type(error.exception), frappe.ValidationError)
		self.assertEqual(str(error.exception), self.italian)
		self.assertEqual([row.status for row in doc.operations], ["Completed", "Completed"])
