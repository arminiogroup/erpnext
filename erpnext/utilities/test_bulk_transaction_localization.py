# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.utilities import bulk_transaction as bulk

QUEUE_MESSAGE = "Starting a background job to create documents of type {0} from {1} records. {2}"
SKIP_MESSAGE = "{0} creation for the following records will be skipped."
SUCCESS_MESSAGE = "Processing for documents of type <b><a href='/app/{0}'>{1}</a></b> completed."
PARTIAL_MESSAGE = 'Processing for documents of type {0} partially completed. Check <b><a href="/app/bulk-transaction-log">Bulk Transaction Log</a></b>.'
FAILURE_MESSAGE = 'Processing for documents of type {0} failed. Check <b><a href="/app/bulk-transaction-log">Bulk Transaction Log</a></b>.'
TARGETS = {
	QUEUE_MESSAGE: "Avvio di un processo in background per creare documenti di tipo {0} a partire da {1} record. {2}",
	SKIP_MESSAGE: "La creazione di documenti di tipo {0} sarà saltata per i seguenti record.",
	SUCCESS_MESSAGE: "Elaborazione completata per documenti di tipo <b><a href='/app/{0}'>{1}</a></b>.",
	PARTIAL_MESSAGE: 'Elaborazione completata solo in parte per documenti di tipo {0}. Consulta il <b><a href="/app/bulk-transaction-log">Registro delle transazioni in blocco</a></b>.',
	FAILURE_MESSAGE: 'Elaborazione non riuscita per documenti di tipo {0}. Consulta il <b><a href="/app/bulk-transaction-log">Registro delle transazioni in blocco</a></b>.',
	"Bulk processing completed": "Elaborazione in blocco completata",
	"Bulk processing partially completed": "Elaborazione in blocco parziale",
	"Bulk processing failed": "Elaborazione in blocco non riuscita",
	"Could not be created from:": "Impossibile creare da:",
	"Sales Invoice": "Fattura di vendita",
	"Delivery Note": "Documento di trasporto",
	"Payment Entry": "Registrazione di pagamento",
	"Invalid name": "Nome non valido",
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
	keys = set()
	paths = [(Path(bulk.__file__), "frappe.gettext.extractors.python.extract")]
	for module, doctype in (
		("accounts", "Sales Invoice"),
		("stock", "Delivery Note"),
		("accounts", "Payment Entry"),
	):
		paths.append((metadata_path(module, doctype), "frappe.gettext.extractors.doctype.extract"))
	for path, method in paths:
		with path.open("rb") as source:
			keys.update(
				message
				for _, message, _, context in extract(method, source, keywords=PYTHON_KEYWORDS)
				if context is None
			)
	return keys


class TestBulkTransactionLocalization(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={k: v for k, v in TARGETS.items() if k in keys},
			)
		)
		self.meta = read_meta("selling", "Sales Order")
		self.get_meta = self.enterContext(patch("frappe.get_meta", return_value=self.meta))
		self.permissions = self.enterContext(patch("frappe.has_permission", return_value=True))
		self.get_all = self.enterContext(patch("frappe.get_all", return_value=[]))
		self.enqueue = self.enterContext(patch("frappe.enqueue"))
		self.publish = self.enterContext(patch("frappe.publish_realtime"))
		self.enterContext(patch("frappe.get_system_settings", return_value="UTC"))
		self.enterContext(patch.object(bulk, "today", return_value="2026-10-21"))
		self.enterContext(patch.object(bulk, "datetime", wraps=datetime)).now.return_value = datetime(
			2026, 10, 21, 10, 0
		)
		self.saved = []
		self.mapped = []
		self.db = SimpleNamespace(savepoint=Mock(), rollback=Mock())
		self.enterContext(patch("frappe.db", self.db))
		self.new_doc = self.enterContext(patch("frappe.new_doc", side_effect=self.new_log))
		self.hooks = self.enterContext(patch("frappe.get_hooks", return_value=[]))
		self.mapper = self.enterContext(
			patch(
				"erpnext.selling.doctype.sales_order.mapper.make_sales_invoice", side_effect=self.map_invoice
			)
		)

	def new_log(self, doctype):
		self.assertEqual(doctype, "Bulk Transaction Log Detail")
		doc = Document.__new__(Document)
		doc.__dict__.update(doctype=doctype)
		doc.save = Mock(
			side_effect=lambda **kwargs: self.saved.append(
				(deepcopy({k: v for k, v in doc.__dict__.items() if k != "save"}), kwargs)
			)
		)
		return doc

	def map_invoice(self, name):
		if name.startswith("FAIL"):
			raise ValueError("in-memory validation failure")
		if name == "NO-TARGET-RAW":
			return None
		doc = SimpleNamespace(flags=frappe._dict(), set_title_field=Mock(), insert=Mock())
		self.mapped.append((name, doc))
		return doc

	def payload(self):
		self.publish.assert_called_once()
		self.assertEqual(self.publish.call_args.kwargs["event"], "msgprint")
		self.assertEqual(frappe.session.user, "translator@example.com")
		return self.publish.call_args.kwargs["message"]

	def test_request_notice_translates_type_and_preserves_data_and_args(self):
		for encoded in (False, True):
			for args in (None, {}, {"until_delivery_date": "2026-10-21"}):
				with self.subTest(encoded=encoded, args=args):
					rows = [{"name": "Sales Invoice"}, {"name": "SOURCE-RAW"}]
					data = json.dumps(rows) if encoded else rows
					passed_args = json.dumps(args) if encoded else args
					self.enqueue.reset_mock()
					bulk.transaction_processing(data, "Sales Order", "Sales Invoice", passed_args)
					self.assertEqual(
						frappe.local.message_log[-1].message,
						"Avvio di un processo in background per creare documenti di tipo Fattura di vendita a partire da 2 record. ",
					)
					self.enqueue.assert_called_once_with(
						bulk.job,
						deserialized_data=rows,
						from_doctype="Sales Order",
						to_doctype="Sales Invoice",
						args=frappe._dict(args or {}),
					)
					self.assertEqual(rows, [{"name": "Sales Invoice"}, {"name": "SOURCE-RAW"}])

	def test_saved_status_filter_and_notice_use_remaining_source_count(self):
		rows = [{"name": "HELD-RAW", "status": "Open"}, {"name": "Sales Invoice", "status": "Closed"}]
		self.get_all.return_value = [("HELD-RAW", "On Hold"), ("Sales Invoice", "Open")]
		bulk.transaction_processing(rows, "Sales Order", "Sales Invoice")
		self.assertEqual(
			frappe.local.message_log[-1].message,
			"Avvio di un processo in background per creare documenti di tipo Fattura di vendita a partire da 1 record. La creazione di documenti di tipo Fattura di vendita sarà saltata per i seguenti record.<br><br><ul><li><strong>HELD-RAW</strong></li></ul>",
		)
		self.enqueue.assert_called_once_with(
			bulk.job,
			deserialized_data=[rows[1]],
			from_doctype="Sales Order",
			to_doctype="Sales Invoice",
			args={},
		)
		self.get_all.assert_called_once_with(
			"Sales Order",
			filters={"name": ["in", ["HELD-RAW", "Sales Invoice"]]},
			fields=["name", "status"],
			as_list=True,
		)

	def test_all_skipped_records_keep_raw_names_and_do_not_enqueue(self):
		rows = [{"name": "Sales Invoice"}, {"name": "CLOSED-RAW"}]
		self.get_all.return_value = [("Sales Invoice", "On Hold"), ("CLOSED-RAW", "Closed")]
		bulk.transaction_processing(rows, "Sales Order", "Sales Invoice")
		self.assertEqual(
			frappe.local.message_log[-1].message,
			"La creazione di documenti di tipo Fattura di vendita sarà saltata per i seguenti record.<br><br><ul><li><strong>Sales Invoice</strong></li><li><strong>CLOSED-RAW</strong></li></ul>",
		)
		self.enqueue.assert_not_called()
		self.permissions.assert_has_calls(
			[
				call("Sales Order", "read", throw=True),
				call("Sales Invoice", "create", throw=True),
				call("Sales Order", "read", "Sales Invoice", throw=True),
				call("Sales Order", "read", "CLOSED-RAW", throw=True),
			]
		)

	def test_permissions_precede_status_reads_and_enqueue(self):
		trace = Mock()
		for name, provider in (
			("permission", self.permissions),
			("meta", self.get_meta),
			("statuses", self.get_all),
			("enqueue", self.enqueue),
		):
			trace.attach_mock(provider, name)
		bulk.transaction_processing([{"name": "SOURCE-RAW"}], "Sales Order", "Sales Invoice")
		self.assertEqual(
			trace.mock_calls,
			[
				call.permission("Sales Order", "read", throw=True),
				call.permission("Sales Invoice", "create", throw=True),
				call.permission("Sales Order", "read", "SOURCE-RAW", throw=True),
				call.meta("Sales Order"),
				call.statuses(
					"Sales Order",
					filters={"name": ["in", ["SOURCE-RAW"]]},
					fields=["name", "status"],
					as_list=True,
				),
				call.enqueue(
					bulk.job,
					deserialized_data=[{"name": "SOURCE-RAW"}],
					from_doctype="Sales Order",
					to_doctype="Sales Invoice",
					args={},
				),
			],
		)

	def test_invalid_and_unreadable_names_fail_before_status_reads(self):
		for name in (None, 0, ""):
			with self.subTest(name=name):
				with self.assertRaises(frappe.PermissionError):
					bulk.transaction_processing([{"name": name}], "Sales Order", "Sales Invoice")

		def permission(doctype, ptype, name=None, **kwargs):
			if name == "HELD-RAW":
				raise frappe.PermissionError("Denied")

		self.permissions.side_effect = permission
		with self.assertRaises(frappe.PermissionError):
			bulk.transaction_processing(
				[{"name": "HELD-RAW", "status": "On Hold"}], "Sales Order", "Sales Invoice"
			)
		self.get_all.assert_not_called()
		self.enqueue.assert_not_called()

	def test_full_job_success_keeps_mapper_insert_log_and_realtime_contracts(self):
		args = frappe._dict(until_delivery_date="2026-10-21")
		bulk.job([{"name": "Sales Invoice"}], "Sales Order", "Sales Invoice", args)
		self.mapper.assert_called_once_with("Sales Invoice")
		self.hooks.assert_called_once_with("bulk_transaction_task_mapper")
		self.assertEqual(frappe.flags.args, args)
		self.assertNotIn("bulk_transaction", frappe.flags)
		self.db.savepoint.assert_called_once_with("before_creation_state")
		self.db.rollback.assert_not_called()
		name, target = self.mapped[0]
		self.assertEqual(name, "Sales Invoice")
		self.assertEqual(target.flags, {"ignore_validate": True})
		target.set_title_field.assert_called_once_with()
		target.insert.assert_called_once_with(ignore_mandatory=True)
		self.assertEqual(
			self.saved[0][0],
			dict(
				doctype="Bulk Transaction Log Detail",
				transaction_name="Sales Invoice",
				date="2026-10-21",
				time="10:00:00",
				transaction_status="Success",
				error_description="None",
				from_doctype="Sales Order",
				to_doctype="Sales Invoice",
				retried=0,
			),
		)
		self.assertEqual(self.saved[0][1], {"ignore_permissions": True})
		payload = self.payload()
		self.assertEqual(
			payload.message,
			'Elaborazione completata per documenti di tipo <b><a href="/app/sales-invoice" rel="noopener noreferrer">Fattura di vendita</a></b>.',
		)
		self.assertEqual((payload.title, payload.indicator), ("Elaborazione in blocco completata", "green"))

	def test_mapper_none_stays_successful_without_claiming_document_creation(self):
		bulk.job([{"name": "NO-TARGET-RAW"}], "Sales Order", "Sales Invoice", None)
		self.assertEqual(self.mapped, [])
		self.assertEqual(self.saved[0][0]["transaction_status"], "Success")
		payload = self.payload()
		self.assertEqual(
			payload.message,
			'Elaborazione completata per documenti di tipo <b><a href="/app/sales-invoice" rel="noopener noreferrer">Fattura di vendita</a></b>.',
		)
		self.assertEqual(payload.title, "Elaborazione in blocco completata")
		self.assertNotIn("creat", payload.message.lower())

	def test_payment_mapper_keeps_its_canonical_source_type_argument(self):
		with patch(
			"erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
			side_effect=lambda doctype, name: self.map_invoice(name),
		) as payment:
			bulk.task("SOURCE-RAW", "Sales Invoice", "Payment Entry")
			payment.assert_called_once_with("Sales Invoice", "SOURCE-RAW")
		self.mapped[0][1].insert.assert_called_once_with(ignore_mandatory=True)
		self.assertEqual(self.mapped[0][1].flags, {"ignore_validate": True})

	def test_custom_mapper_hook_keeps_canonical_types_and_target_flags(self):
		self.hooks.return_value = ["custom.bulk_mapper"]
		with patch(
			"frappe.get_attr", return_value=lambda: {"Sales Order": {"CUSTOM-TYPE": self.map_invoice}}
		) as provider:
			bulk.task("SOURCE-RAW", "Sales Order", "CUSTOM-TYPE")
			provider.assert_called_once_with("custom.bulk_mapper")
		self.assertEqual(self.mapped[0][0], "SOURCE-RAW")
		self.mapped[0][1].set_title_field.assert_called_once_with()
		self.mapped[0][1].insert.assert_called_once_with(ignore_mandatory=True)
		self.assertNotIn("bulk_transaction", frappe.flags)

	def test_full_job_partial_result_preserves_statuses_rollback_and_sources(self):
		bulk.job([{"name": "OK-RAW"}, {"name": "FAIL-RAW"}], "Sales Order", "Sales Invoice", None)
		self.assertEqual(
			[fields["transaction_status"] for fields, _kwargs in self.saved], ["Success", "Failed"]
		)
		self.assertEqual(
			[fields["transaction_name"] for fields, _kwargs in self.saved], ["OK-RAW", "FAIL-RAW"]
		)
		self.db.rollback.assert_called_once_with(save_point="before_creation_state")
		self.assertIn("in-memory validation failure", self.saved[1][0]["error_description"])
		payload = self.payload()
		self.assertEqual(
			payload.message,
			'Elaborazione completata solo in parte per documenti di tipo Fattura di vendita. Consulta il <b><a href="/app/bulk-transaction-log" rel="noopener noreferrer">Registro delle transazioni in blocco</a></b>.<br><br>Impossibile creare da:<ul><li><strong>FAIL-RAW</strong></li></ul>',
		)
		self.assertEqual((payload.title, payload.indicator), ("Elaborazione in blocco parziale", "orange"))

	def test_full_failed_job_keeps_failure_log_and_indicator(self):
		bulk.job([{"name": "FAIL-RAW"}], "Sales Order", "Sales Invoice", None)
		self.assertEqual(self.saved[0][0]["transaction_status"], "Failed")
		payload = self.payload()
		self.assertEqual(
			payload.message,
			'Elaborazione non riuscita per documenti di tipo Fattura di vendita. Consulta il <b><a href="/app/bulk-transaction-log" rel="noopener noreferrer">Registro delle transazioni in blocco</a></b>.<br><br>Impossibile creare da:<ul><li><strong>FAIL-RAW</strong></li></ul>',
		)
		self.assertEqual((payload.title, payload.indicator), ("Elaborazione in blocco non riuscita", "red"))

	def test_success_link_keeps_raw_route_and_known_or_custom_type_identity(self):
		for doctype, label in (
			("Delivery Note", "Documento di trasporto"),
			("Payment Entry", "Registrazione di pagamento"),
			("CUSTOM-TYPE", "CUSTOM-TYPE"),
		):
			for count in (1, 3):
				with self.subTest(doctype=doctype, count=count):
					self.publish.reset_mock()
					bulk.show_job_status([], count, doctype)
					payload = self.payload()
					self.assertEqual(
						payload.message,
						f'Elaborazione completata per documenti di tipo <b><a href="/app/{doctype.lower().replace(" ", "-")}" rel="noopener noreferrer">{label}</a></b>.',
					)
					self.assertEqual(payload.title, "Elaborazione in blocco completata")

	def test_failed_source_escaping_is_preserved_for_both_result_branches(self):
		for count, title in (
			(1, "Elaborazione in blocco non riuscita"),
			(2, "Elaborazione in blocco parziale"),
		):
			with self.subTest(count=count):
				self.publish.reset_mock()
				bulk.show_job_status(['<script> & "name"', "Sales Invoice"], count + 1, "Sales Invoice")
				payload = self.payload()
				self.assertIn(
					'<li><strong>&lt;script&gt; &amp; "name"</strong></li><li><strong>Sales Invoice</strong></li>',
					payload.message,
				)
				self.assertNotIn("<script>", payload.message)
				self.assertNotIn("\\n", payload.message)
				self.assertNotIn("\\t", payload.message)
				self.assertEqual(payload.title, title)
