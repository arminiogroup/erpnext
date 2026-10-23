# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.buying.doctype.request_for_quotation import request_for_quotation as rfq


def forbidden(*args, **kwargs):
	raise AssertionError(
		"Unapproved boundary reached: no persistence, job, user, settings or mail action is allowed"
	)


def meta_from_packaged(doctype):
	candidates = list(
		Path(__file__)
		.resolve()
		.parents[3]
		.glob(f"**/doctype/{frappe.scrub(doctype)}/{frappe.scrub(doctype)}.json")
	)
	assert len(candidates) == 1, (doctype, candidates)
	data = json.loads(candidates[0].read_text())
	meta = Meta.__new__(Meta)
	meta.__dict__.update(data)
	meta.__dict__.setdefault("default_print_format", None)
	meta.fields = [frappe._dict(field) for field in data["fields"]]
	return meta


class TestRequestForQuotationPreviewCopy(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "en"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict(in_test=True)))
		self.enterContext(patch("frappe.translate.get_all_translations", return_value={}))
		self.enterContext(patch.object(frappe, "cache", SimpleNamespace(hget=lambda *args, **kwargs: {})))
		self.enterContext(
			patch.object(
				frappe,
				"get_hooks",
				side_effect=lambda name=None, default=None, **kwargs: default if default is not None else [],
			)
		)
		self.enterContext(patch.object(frappe, "get_active_apps", return_value=["frappe", "erpnext"]))
		self.enterContext(patch.object(frappe, "get_installed_apps", return_value=["frappe", "erpnext"]))
		self.enterContext(
			patch.object(
				frappe,
				"client_cache",
				SimpleNamespace(
					get_doc=lambda doctype, name: frappe._dict(
						date_format="yyyy-mm-dd", time_format="HH:mm:ss", number_format="#,###.##"
					)
					if doctype == "Language"
					else forbidden()
				),
			)
		)

	def test_packaged_send_document_print_defaults_to_disabled(self):
		meta = meta_from_packaged("Request for Quotation")
		field = next(field for field in meta.fields if field.fieldname == "send_document_print")
		self.assertEqual(field.fieldtype, "Check")
		self.assertEqual(field.default, "0")

	def test_preview_and_attachment_matrix_keeps_raw_fields_and_read_order(self):
		for send_print in (0, 1):
			for send_files in (0, 1):
				for preview in (False, True):
					reads, delivery, prints, attached_files = [], [], [], []
					serialized = {"name": "RFQ-RAW", "company": "COMPANY-RAW"}
					supplier = frappe._dict(
						supplier="SUPPLIER-RAW",
						supplier_name="Azienda raw",
						email_id="supplier@example.com",
						contact=None,
					)
					document = SimpleNamespace(
						doctype="Request for Quotation",
						name="RFQ-RAW",
						subject="Richiesta {{ supplier }}",
						email_template=None,
						message_for_supplier="Messaggio {{ supplier_name }}",
						mfs_html="<p>{{ supplier_name }}</p>",
						use_html=0,
						send_document_print=send_print,
						send_attached_files=send_files,
						letter_head="LETTERHEAD-RAW",
						meta=meta_from_packaged("Request for Quotation"),
						as_dict=lambda: deepcopy(serialized),
					)
					document.send_email = MethodType(rfq.RequestforQuotation.send_email, document)
					document.get_attachments = lambda: attached_files.append("read") or ["RAW-FILE"]

					def scalar(doctype, name, field, *args, **kwargs):
						reads.append([str(doctype), name, field])
						assert (doctype, name, field) == ("Supplier", "SUPPLIER-RAW", "language")
						return "it"

					def single(doctype, field):
						reads.append([doctype, field])
						assert (doctype, field) in (
							("Buying Settings", "fixed_email"),
							("System Settings", "language"),
						)
						return None if doctype == "Buying Settings" else "en"

					def attach(*args, **kwargs):
						assert args == ("Request for Quotation", "RFQ-RAW")
						assert kwargs["doc"] is document
						assert kwargs["lang"] == "it"
						assert kwargs["letterhead"] == "LETTERHEAD-RAW"
						prints.append({k: v for k, v in kwargs.items() if k != "doc"})
						return {"fname": "MEMORY-DOCUMENT.pdf", "fcontent": "MEMORY-ONLY"}

					def deliver(**kwargs):
						delivery.append(deepcopy(kwargs))
						return {"name": "MEMORY-COMMUNICATION"}

					before = deepcopy((serialized, supplier))
					with ExitStack() as stack:
						stack.enter_context(
							patch.object(
								frappe,
								"db",
								SimpleNamespace(
									get_single_value=single,
									get_value=scalar,
									get_default=lambda key: None,
									escape=lambda value: repr(value),
									exists=forbidden,
									sql=forbidden,
									count=forbidden,
								),
							)
						)
						stack.enter_context(patch.object(frappe, "get_doc", side_effect=forbidden))
						stack.enter_context(
							patch.object(rfq, "get_user_fullname", return_value="Translator in memory")
						)
						stack.enter_context(patch.object(frappe, "attach_print", side_effect=attach))
						stack.enter_context(patch.object(rfq, "make", side_effect=deliver))
						result = rfq.RequestforQuotation.supplier_rfq_mail(
							document, supplier, "", "/memory-rfq", preview
						)
					assert (serialized, supplier) == before
					if preview:
						assert result == {
							"message": "Messaggio Azienda raw",
							"subject": "Richiesta SUPPLIER-RAW",
						}
						assert not delivery and not prints and not attached_files
						assert reads == [["Buying Settings", "fixed_email"]]
					else:
						assert result is None and len(delivery) == 1
						assert len(prints) == send_print and len(attached_files) == send_files
						assert len(delivery[0]["attachments"]) == send_print + send_files
						assert delivery[0]["recipients"] == "supplier@example.com"
						assert (
							delivery[0]["doctype"] == "Request for Quotation"
							and delivery[0]["name"] == "RFQ-RAW"
						)
						assert (
							delivery[0]["subject"] == "Richiesta SUPPLIER-RAW"
							and delivery[0]["content"] == "Messaggio Azienda raw"
						)
