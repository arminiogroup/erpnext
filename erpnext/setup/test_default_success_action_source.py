# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from frappe.gettext.translate import PYTHON_KEYWORDS

from erpnext.setup import default_success_action as defaults

FRAME = "{0} has been submitted successfully"


class TestDefaultSuccessActionSource(unittest.TestCase):
	def setUp(self):
		previous = getattr(frappe.local, "lang", None)
		self.addCleanup(setattr, frappe.local, "lang", previous)
		self.reads = []
		self.providers = {
			"it": {FRAME: "{0}: conferma completata.", "Sales Invoice": "Fattura di vendita"},
			"de": {FRAME: "BESTAETIGT: {0}", "Sales Invoice": "RECHNUNG"},
		}
		provider = patch("frappe.translate.get_all_translations", side_effect=self.translations)
		provider.start()
		self.addCleanup(provider.stop)

	def translations(self, lang):
		self.reads.append(lang)
		return self.providers.get(lang, {})

	def expected(self):
		return [
			{
				"doctype": "Success Action",
				"ref_doctype": doctype,
				"message": FRAME.format(doctype),
				"first_success_message": FRAME.format(doctype),
				"next_actions": "new\nprint\nemail",
			}
			for doctype in defaults.doctype_list
		]

	def test_all_six_default_records_store_the_source_in_every_install_language(self):
		self.assertEqual(
			defaults.doctype_list,
			[
				"Purchase Receipt",
				"Purchase Invoice",
				"Quotation",
				"Sales Order",
				"Delivery Note",
				"Sales Invoice",
			],
		)
		before = deepcopy(defaults.doctype_list)
		for lang in ("en", "it", "de", "untranslated-language"):
			with self.subTest(lang=lang):
				frappe.local.lang = lang
				self.assertEqual(defaults.get_default_success_action(), self.expected())
				self.assertEqual(defaults.doctype_list, before)
				self.assertEqual(frappe.local.lang, lang)

	def test_both_message_producers_preserve_the_canonical_type(self):
		frappe.local.lang = "it"
		for doctype in [*defaults.doctype_list, "Custom Document", "", "Sales Invoice <raw>"]:
			with self.subTest(doctype=doctype):
				for producer in (defaults.get_message, defaults.get_first_success_message):
					self.assertEqual(producer(doctype), FRAME.format(doctype))

	def test_source_defaults_do_not_read_a_current_language_dictionary(self):
		frappe.local.lang = "it"
		defaults.get_default_success_action()
		self.assertEqual(self.reads, [])

	def test_mutating_one_return_value_never_changes_later_defaults(self):
		frappe.local.lang = "en"
		first = defaults.get_default_success_action()
		first[0]["message"] = "User configured message"
		first[1]["next_actions"] = "list"
		self.assertEqual(defaults.get_default_success_action(), self.expected())

	def test_missing_and_empty_dictionary_targets_do_not_change_source_storage(self):
		self.providers["it"] = {FRAME: "", "Sales Invoice": ""}
		frappe.local.lang = "it"
		self.assertEqual(defaults.get_default_success_action(), self.expected())
		self.assertEqual(self.reads, [])

	def test_the_complete_source_frame_remains_extractable(self):
		keys = [
			(message, context)
			for _, message, _, context in extract_from_file(
				"frappe.gettext.extractors.python.extract", defaults.__file__, keywords=PYTHON_KEYWORDS
			)
		]
		self.assertEqual(keys, [(FRAME, None)])
		test_keys = list(
			extract_from_file(
				"frappe.gettext.extractors.python.extract", str(Path(__file__)), keywords=PYTHON_KEYWORDS
			)
		)
		self.assertEqual(test_keys, [])
