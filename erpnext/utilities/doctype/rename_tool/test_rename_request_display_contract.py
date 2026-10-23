import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from erpnext.utilities.doctype.rename_tool import rename_tool


class TestRenameRequestDisplayContract(unittest.TestCase):
	def setUp(self):
		for key, value in {
			"session": frappe._dict(user="translator@example.com"),
			"lang": "en",
			"response": frappe._dict(),
			"message_log": [],
			"form_dict": frappe._dict(select_doctype="Customer"),
		}.items():
			previous = getattr(frappe.local, key, None)
			setattr(frappe.local, key, value)
			self.addCleanup(setattr, frappe.local, key, previous)

		translations = patch("frappe.translate.get_all_translations", return_value={})
		translations.start()
		self.addCleanup(translations.stop)

	def upload(
		self,
		count=1,
		*,
		tool_allowed=True,
		type_exists=True,
		type_allowed=True,
		file_exists=True,
		attached=True,
		doctype="Customer",
	):
		events = []
		content = "".join(f'"OLD-{index}","NEW-{index}"\n' for index in range(count))

		class MemoryFile:
			file_url = "/private/files/RAW.csv"

			def get_content(self):
				events.append(("read-content",))
				return content

			def delete(self):
				events.append(("memory-file-delete",))

		file = MemoryFile()

		def permission(kind, ptype=None, **kwargs):
			events.append(("permission", kind, ptype, kwargs))
			allowed = tool_allowed if kind == "Rename Tool" else type_allowed
			if not allowed and kwargs.get("throw"):
				raise frappe.PermissionError
			return allowed

		def exists(kind, filters):
			events.append(("exists", kind, filters))
			self.assertEqual(kind, "DocType")
			self.assertEqual(
				filters, {"name": doctype or "Customer", "allow_rename": 1, "module": ["!=", "Core"]}
			)
			return type_exists

		def value(kind, filters, field, **kwargs):
			events.append(("file-lookup", kind, filters, field, kwargs))
			self.assertEqual(kind, "File")
			self.assertEqual(
				filters,
				{
					"attached_to_doctype": "Rename Tool",
					"attached_to_name": "Rename Tool",
					"file_url": file.file_url,
				},
			)
			self.assertEqual((field, kwargs), ("name", {"order_by": "creation desc"}))
			return "FILE-RAW" if file_exists else None

		def get_doc(kind, name):
			events.append(("file-document", kind, name))
			self.assertEqual((kind, name), ("File", "FILE-RAW"))
			return file

		def enqueue(**kwargs):
			self.assertIs(kwargs["method"], rename_tool.rename_rows)
			events.append(("memory-enqueue", kwargs))

		def get_single(*args):
			self.assertEqual(args, ("Rename Tool", "file_to_rename"))
			events.append(("read-attachment",))
			return file.file_url if attached else None

		def set_single(*args):
			self.assertEqual(args, ("Rename Tool", "file_to_rename", None))
			events.append(("memory-clear-attachment",))

		db = SimpleNamespace(
			exists=exists, get_value=value, get_single_value=get_single, set_single_value=set_single
		)
		with patch.object(frappe, "has_permission", side_effect=permission), patch.object(
			frappe, "db", db
		), patch.object(frappe, "get_doc", side_effect=get_doc), patch.object(
			frappe, "enqueue", side_effect=enqueue
		):
			try:
				response = rename_tool.upload(doctype, file.file_url)
			except (frappe.PermissionError, frappe.ValidationError) as error:
				return error, events
		return response, events

	def test_empty_and_boundary_csv_batches_keep_raw_values_and_none_response(self):
		for count in [0, 1, 500, 501]:
			with self.subTest(count=count):
				response, events = self.upload(count)
				self.assertIsNone(response)
				jobs = [event[1] for event in events if event[0] == "memory-enqueue"]
				self.assertEqual(len(jobs), (count + 499) // 500)
				self.assertEqual(
					[row for job in jobs for row in job["rows"]],
					[[f"OLD-{i}", f"NEW-{i}"] for i in range(count)],
				)
				for job in jobs:
					self.assertEqual(set(job), {"method", "queue", "doctype", "rows", "enqueue_after_commit"})
					self.assertEqual(
						(job["queue"], job["doctype"], job["enqueue_after_commit"]),
						("long", "Customer", True),
					)
				self.assertEqual([event[0] for event in events[:3]], ["permission", "exists", "permission"])
				self.assertEqual(
					[event[0] for event in events[-3:]],
					["read-attachment", "memory-clear-attachment", "memory-file-delete"],
				)

	def test_permission_and_type_rejections_stop_before_file_or_queue(self):
		for arguments, exception, expected in [
			({"tool_allowed": False}, frappe.PermissionError, ["permission"]),
			({"type_exists": False}, frappe.ValidationError, ["permission", "exists"]),
			(
				{"doctype": "Core Type", "type_exists": False},
				frappe.ValidationError,
				["permission", "exists"],
			),
			({"type_allowed": False}, frappe.PermissionError, ["permission", "exists", "permission"]),
		]:
			with self.subTest(arguments=arguments):
				error, events = self.upload(**arguments)
				self.assertIsInstance(error, exception)
				self.assertEqual([event[0] for event in events], expected)

	def test_missing_type_and_file_keep_original_guards(self):
		frappe.local.form_dict.select_doctype = None
		error, events = self.upload(doctype=None)
		self.assertIsInstance(error, frappe.ValidationError)
		self.assertEqual([event[0] for event in events], ["permission"])
		error, events = self.upload(file_exists=False)
		self.assertIsInstance(error, frappe.ValidationError)
		self.assertEqual(
			[event[0] for event in events], ["permission", "exists", "permission", "file-lookup"]
		)

	def test_form_fallback_and_unreferenced_attachment_do_not_change_work_request(self):
		response, events = self.upload(doctype=None, attached=False)
		self.assertIsNone(response)
		self.assertEqual(
			[event[1]["doctype"] for event in events if event[0] == "memory-enqueue"], ["Customer"]
		)
		self.assertEqual([event[0] for event in events[-2:]], ["read-attachment", "memory-file-delete"])
