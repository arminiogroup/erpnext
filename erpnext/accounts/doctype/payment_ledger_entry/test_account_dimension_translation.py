# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import hashlib
import itertools
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.accounting_dimension_filter.accounting_dimension_filter import build_map
from erpnext.accounts.doctype.payment_ledger_entry import payment_ledger_entry
from erpnext.accounts.services import gl_validator
from erpnext.exceptions import InvalidAccountDimensionError, MandatoryAccountDimensionError

MANDATORY = "{0} is mandatory for account {1}"
INVALID = "Invalid value {0} for {1} against account {2}"
ACCOUNT_TYPE = "{0} account is not of type {1}"
TARGETS = {
	MANDATORY: "Il campo {0} è obbligatorio per il conto {1}.",
	INVALID: "Il valore {0} non è consentito per la dimensione {1} sul conto {2}.",
	ACCOUNT_TYPE: "Il conto {0} non è di tipo {1}.",
	"Receivable": "Crediti",
	"Payable": "Debiti",
	"Cost Center": "Centro di costo",
	"Project": "Progetto",
	"Territory": "Territorio",
}
PARENTS = ("Payment Ledger Entry", "GL Entry")


def read_meta(doctype):
	slug = frappe.scrub(doctype)
	path = Path(frappe.get_app_path("erpnext", f"accounts/doctype/{slug}/{slug}.json"))
	data = json.loads(path.read_text())
	meta = Meta.__new__(Meta)
	fields = [frappe._dict(parent=doctype, **field) for field in data["fields"]]
	fields += [
		frappe._dict(
			parent=doctype, fieldname="custom_axis", label="Territory", fieldtype="Link", options="Territory"
		),
		frappe._dict(
			parent=doctype,
			fieldname="custom_label",
			label="Area di competenza",
			fieldtype="Link",
			options="Territory",
		),
		frappe._dict(parent=doctype, fieldname="blank_label", label="", fieldtype="Data"),
	]
	meta.__dict__.update(
		doctype="DocType", name=doctype, fields=fields, _fields={field.fieldname: field for field in fields}
	)
	return meta


def extracted_keys():
	paths = [
		Path(payment_ledger_entry.__file__),
		Path(gl_validator.__file__),
		*[
			Path(frappe.get_app_path("erpnext", relative))
			for relative in (
				"accounts/doctype/payment_ledger_entry/payment_ledger_entry.json",
				"accounts/doctype/gl_entry/gl_entry.json",
				"setup/doctype/territory/territory.json",
			)
		],
	]
	keys = set()
	root = Path(frappe.get_app_path("erpnext")).parent
	methods = get_method_map("erpnext") + get_method_map("frappe")
	for path in paths:
		method = next(
			method for pattern, method in methods if pathmatch(pattern, str(path.relative_to(root)))
		)
		keys.update(
			message
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
			if not context and isinstance(message, str)
		)
	return keys


class TestAccountDimensionTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "message_log", getattr(frappe.local, "message_log", None))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={k: v for k, v in TARGETS.items() if k in keys},
			)
		)
		self.metas = {parent: read_meta(parent) for parent in PARENTS}
		self.meta_reads = self.enterContext(
			patch("frappe.get_meta", side_effect=lambda name: self.metas[name])
		)

	def entry(self, parent="Payment Ledger Entry", **values):
		if parent == "GL Entry":
			return frappe._dict(account="Cost Center", **values)
		doc = payment_ledger_entry.PaymentLedgerEntry.__new__(payment_ledger_entry.PaymentLedgerEntry)
		doc.__dict__.update(
			doctype=parent,
			name="PLE-RAW",
			account="Cost Center",
			company="Company",
			account_type="Receivable",
			amount=250,
			amount_in_account_currency=250,
			account_currency="EUR",
			voucher_type="Sales Invoice",
			voucher_no="INVOICE-RAW",
			against_voucher_type="Sales Invoice",
			delinked=0,
			flags=frappe._dict(adv_adj=False, from_repost=False, update_outstanding="No"),
		)
		doc.__dict__.update(values)
		return doc

	def raw_state(self, doc, parent):
		if parent == "GL Entry":
			return dict(doc)
		if cached_meta := doc.__dict__.get("meta"):
			self.assertIs(cached_meta, self.metas[parent])
			self.assertEqual(cached_meta.name, parent)
		return {key: value for key, value in doc.__dict__.items() if key != "meta"}

	def dimensions(self, parent, entry, filters):
		with patch.object(payment_ledger_entry, "get_dimension_filter_map", return_value=filters) as provider:
			if parent == "Payment Ledger Entry":
				entry.validate_allowed_dimensions()
				provider.assert_called_once_with()
			else:
				gl_validator.validate_allowed_dimensions(entry, filters)
				provider.assert_not_called()

	def error(self, method, exc=frappe.ValidationError):
		frappe.local.message_log = []
		with self.assertRaises(exc) as caught:
			method()
		return str(caught.exception)

	def test_actual_configured_extractors_supply_all_frame_and_label_keys(self):
		self.assertTrue(TARGETS.keys() <= extracted_keys())
		for parent in PARENTS:
			self.assertEqual(self.metas[parent].get_field("cost_center").label, "Cost Center")
			self.assertEqual(self.metas[parent].get_field("project").label, "Project")
		self.assertEqual(self.metas[PARENTS[0]].get_field("account_type").options, "Receivable\nPayable")

	def test_real_validate_translates_only_the_expected_account_type(self):
		for source, target in (("Receivable", "Crediti"), ("Payable", "Debiti"), (None, "None")):
			with self.subTest(account_type=source):
				doc = self.entry(account="Receivable", account_type=source)
				before = deepcopy(doc.__dict__)
				with patch(
					"frappe.get_cached_value",
					return_value=frappe._dict(company="Company", account_type="Bank"),
				) as cached:
					self.assertEqual(self.error(doc.validate), f"Il conto Receivable non è di tipo {target}.")
				cached.assert_called_once_with(
					"Account", "Receivable", fieldname=["account_type", "company"], as_dict=True
				)
				self.assertEqual(doc.__dict__, before)
		self.meta_reads.assert_not_called()

	def test_valid_types_and_company_mismatch_keep_original_reads_and_precedence(self):
		for value in ("Receivable", "Payable"):
			with self.subTest(value=value):
				doc = self.entry(account_type=value)
				before = deepcopy(doc.__dict__)
				with patch(
					"frappe.get_cached_value",
					return_value=frappe._dict(company="Company", account_type=value),
				) as cached:
					doc.validate()
				cached.assert_called_once_with(
					"Account", "Cost Center", fieldname=["account_type", "company"], as_dict=True
				)
				self.assertEqual(doc.__dict__, before)
		doc = self.entry()
		with patch(
			"frappe.get_cached_value", return_value=frappe._dict(company="OTHER", account_type="Bank")
		):
			self.assertEqual(self.error(doc.validate), "Cost Center account is not of company Company")
		self.meta_reads.assert_not_called()

	def test_both_ledgers_use_builtin_custom_and_fallback_dimension_labels(self):
		labels = {
			"cost_center": "Centro di costo",
			"project": "Progetto",
			"custom_axis": "Territorio",
			"custom_label": "Area di competenza",
			"blank_label": "Blank Label",
			"missing_axis": "Missing Axis",
		}
		for parent, (field, target) in itertools.product(PARENTS, labels.items()):
			with self.subTest(parent=parent, field=field):
				doc = self.entry(parent)
				filters = {}
				build_map(filters, field, "Cost Center", None, "Restrict", 1)
				before = deepcopy((self.raw_state(doc, parent), filters))
				self.assertEqual(
					self.error(lambda: self.dimensions(parent, doc, filters), MandatoryAccountDimensionError),
					f"Il campo <strong>{target}</strong> è obbligatorio per il conto <strong>Cost Center</strong>.",
				)
				self.assertEqual((self.raw_state(doc, parent), filters), before)

	def test_allow_and_restrict_errors_preserve_dimension_and_account_business_names(self):
		for parent, mode in itertools.product(PARENTS, ("Allow", "Restrict")):
			with self.subTest(parent=parent, mode=mode):
				doc = self.entry(parent, custom_axis="Territory")
				filters = {}
				build_map(
					filters,
					"custom_axis",
					"Cost Center",
					"OTHER" if mode == "Allow" else "Territory",
					mode,
					0,
				)
				before = deepcopy((self.raw_state(doc, parent), filters))
				self.assertEqual(
					self.error(lambda: self.dimensions(parent, doc, filters), InvalidAccountDimensionError),
					"Il valore <strong>Territory</strong> non è consentito per la dimensione <strong>Territorio</strong> sul conto <strong>Cost Center</strong>.",
				)
				self.assertEqual((self.raw_state(doc, parent), filters), before)

	def test_accepted_and_nonmatching_filter_cases_do_not_read_metadata(self):
		cases = (
			("OTHER-ACCOUNT", 1, "Allow", "", "ALLOWED"),
			("Cost Center", 0, "Allow", "", "ALLOWED"),
			("Cost Center", 0, "Allow", "ALLOWED", "ALLOWED"),
			("Cost Center", 0, "Restrict", "OTHER", "ALLOWED"),
			("Cost Center", 0, "Restrict", 0, "ALLOWED"),
		)
		for parent, (account, mandatory, mode, value, allowed) in itertools.product(PARENTS, cases):
			with self.subTest(parent=parent, case=(account, mandatory, mode, value)):
				doc = self.entry(parent, custom_axis=value)
				filters = {}
				build_map(filters, "custom_axis", account, allowed, mode, mandatory)
				self.dimensions(parent, doc, filters)
				if parent == PARENTS[0]:
					self.assertNotIn("meta", doc.__dict__)
		self.meta_reads.assert_not_called()

	def test_multiple_filters_keep_the_first_matching_error_and_label_read(self):
		for parent in PARENTS:
			with self.subTest(parent=parent):
				doc = self.entry(parent)
				filters = {}
				build_map(filters, "cost_center", "OTHER-ACCOUNT", None, "Restrict", 1)
				build_map(filters, "custom_axis", "Cost Center", None, "Restrict", 1)
				build_map(filters, "project", "Cost Center", None, "Restrict", 1)
				before = deepcopy((self.raw_state(doc, parent), filters))
				self.meta_reads.reset_mock()
				self.assertEqual(
					self.error(lambda: self.dimensions(parent, doc, filters), MandatoryAccountDimensionError),
					"Il campo <strong>Territorio</strong> è obbligatorio per il conto <strong>Cost Center</strong>.",
				)
				self.meta_reads.assert_called_once_with(parent)
				self.assertEqual((self.raw_state(doc, parent), filters), before)

	def test_actual_on_update_gates_and_provider_order_stay_unchanged(self):
		for repost, delinked in ((True, 0), (False, 1), (False, 0)):
			with self.subTest(repost=repost, delinked=delinked):
				doc = self.entry(delinked=delinked)
				doc.flags.from_repost = repost
				before = deepcopy(doc.__dict__)
				reads = []

				def cached(doctype, name, fieldname, **kwargs):
					self.assertEqual((doctype, name), ("Account", "Cost Center"))
					reads.append(fieldname)
					if fieldname == ["is_group", "docstatus", "company"]:
						self.assertEqual(kwargs, {"as_dict": True})
						return frappe._dict(is_group=0, docstatus=0, company="Company")
					return {"freeze_account": "No", "report_type": "Balance Sheet", "balance_must_be": None}[
						fieldname
					]

				with (
					patch("frappe.get_cached_value", side_effect=cached),
					patch.object(
						payment_ledger_entry, "get_checks_for_pl_and_bs_accounts", return_value=[]
					) as checks,
					patch.object(
						payment_ledger_entry, "get_dimension_filter_map", return_value={}
					) as filters,
				):
					doc.on_update()
					self.assertEqual(checks.call_count, int(not repost and not delinked))
					self.assertEqual(filters.call_count, int(not repost and not delinked))
				self.assertEqual(
					reads,
					[]
					if repost
					else ["freeze_account"]
					if delinked
					else [
						"freeze_account",
						["is_group", "docstatus", "company"],
						"report_type",
						"balance_must_be",
					],
				)
				self.assertEqual(doc.__dict__, before)
				self.assertNotIn("meta", doc.__dict__)
		self.meta_reads.assert_not_called()

	def test_canonical_dimension_decision_matrix_is_observable_without_writes(self):
		rows = []
		for parent, field, matches, mandatory, mode, value, allowed in itertools.product(
			PARENTS,
			("cost_center", "custom_axis", "missing_axis"),
			(False, True),
			(0, 1),
			("Allow", "Restrict"),
			("", "ALLOWED", "OTHER", 0),
			("ALLOWED", "OTHER"),
		):
			doc = self.entry(parent, **{field: value})
			filters = {}
			build_map(filters, field, "Cost Center" if matches else "OTHER-ACCOUNT", allowed, mode, mandatory)
			before = deepcopy((self.raw_state(doc, parent), filters))
			self.meta_reads.reset_mock()
			frappe.local.message_log = []
			try:
				self.dimensions(parent, doc, filters)
				outcome = "accepted"
				self.meta_reads.assert_not_called()
				if parent == PARENTS[0]:
					self.assertNotIn("meta", doc.__dict__)
			except (MandatoryAccountDimensionError, InvalidAccountDimensionError) as error:
				outcome = type(error).__name__
			self.assertEqual((self.raw_state(doc, parent), filters), before)
			rows.append([parent, field, matches, mandatory, mode, value, allowed, outcome])
		self.assertEqual(len(rows), 384)
		self.assertEqual(
			{row[-1] for row in rows},
			{"accepted", "MandatoryAccountDimensionError", "InvalidAccountDimensionError"},
		)
		self.parity_evidence = {
			"cases": rows,
			"sha256": hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest(),
			"accepted": sum(row[-1] == "accepted" for row in rows),
			"rejected": sum(row[-1] != "accepted" for row in rows),
		}
