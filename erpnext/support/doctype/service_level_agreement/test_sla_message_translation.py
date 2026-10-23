# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils import get_weekdays

from erpnext.support.doctype.service_level_agreement import service_level_agreement as sla

MISSING_TIME = "Set {0} for priority {1} in row {2}."
REPEATED_DAYS = "Repeated workdays: {0}."
DEFAULT_DUPLICATE = "Default Service Level Agreement for {0} already exists."
ENTITY_DUPLICATE = "Service Level Agreement for {0} {1} already exists."
TIME_RANGE = "Start Time can't be greater than or equal to End Time for {0}."
STATUS_REQUIRED = "The Document Type {0} must have a Status field to configure Service Level Agreement"
TRACKING_DISABLED = "{0} is not enabled in {1}"
CLIENT_MESSAGES = {
	"This SLA is the default for documents of type {0}.": "Questo accordo sul livello di servizio è quello predefinito per i documenti di tipo {0}.",
	"Enable to make this SLA the default for documents of type {0}.": "Attivare per usare questo accordo sul livello di servizio come predefinito per i documenti di tipo {0}.",
	"SLA applies to documents of type {0} associated with {1}: {2}.": "L’accordo sul livello di servizio si applica ai documenti di tipo {0} associati a {1}: {2}.",
	"SLA applies to documents of type {0} associated with {1}: {2}, including descendants.": "L’accordo sul livello di servizio si applica ai documenti di tipo {0} associati a {1}: {2}, inclusi i discendenti.",
	"Service Level Agreement has been fulfilled": "Accordo sul livello di servizio rispettato",
	"Service Level Agreement is not fulfilled.": "Le condizioni dell’accordo sul livello di servizio non risultano soddisfatte.",
	"Time to Respond: {0}": "Prima risposta: {0}",
	"Time to Resolve: {0}": "Risoluzione: {0}",
}
TARGETS = {
	MISSING_TIME: "Impostare {0} per la priorità {1} nella riga {2}.",
	REPEATED_DAYS: "Giorni lavorativi ripetuti: {0}.",
	DEFAULT_DUPLICATE: "Esiste già un accordo sul livello di servizio predefinito per il tipo di documento {0}.",
	ENTITY_DUPLICATE: "Esiste già un accordo sul livello di servizio per {0}: {1}.",
	TIME_RANGE: "L’ora di inizio deve precedere l’ora di fine per {0}.",
	STATUS_REQUIRED: "Il tipo di documento {0} deve avere un campo Stato per configurare un accordo sul livello di servizio.",
	TRACKING_DISABLED: "L’opzione {0} non è abilitata in {1}.",
	"Track Service Level Agreement": "Monitora gli accordi sul livello di servizio",
	"Support Settings": "Impostazioni di assistenza",
	"First Response Time": "Tempo di prima risposta",
	"Resolution Time": "Tempo di risoluzione",
	"Priority": "Priorità",
	"Customer": "Cliente",
	"Customer Group": "Gruppo clienti",
	"Territory": "Territorio",
	"Warehouse": "Magazzino",
	"Lead": "Contatto commerciale",
	"Task": "Attività",
	"Monday": "Lunedì",
	"Tuesday": "Martedì",
	"Wednesday": "Mercoledì",
	"Thursday": "Giovedì",
	"Friday": "Venerdì",
	"Saturday": "Sabato",
	"Sunday": "Domenica",
	"Response Time for {0} priority in row {1} can't be greater than Resolution Time.": "Il tempo di risposta per la priorità {0} nella riga {1} non può superare il tempo di risoluzione.",
	"Priority {0} has been repeated.": "Priorità ripetuta: {0}.",
	"Select a Default Priority.": "Selezionare una priorità predefinita.",
	**CLIENT_MESSAGES,
}
METADATA = (
	("support", "Service Level Agreement"),
	("support", "Service Level Priority"),
	("support", "Service Day"),
	("support", "Support Settings"),
	("support", "Issue"),
	("crm", "Lead"),
	("projects", "Task"),
	("stock", "Warehouse"),
	("selling", "Customer"),
	("setup", "Customer Group"),
	("setup", "Territory"),
)
PRODUCERS = (
	("support/doctype/service_level_agreement/service_level_agreement.py", "python"),
	("support/doctype/service_level_agreement/service_level_agreement.js", "javascript"),
	("public/js/utils.js", "javascript"),
)
CUSTOM_TYPE = "Test SLA on Custom Dt"


def metadata_path(module, doctype):
	slug = frappe.scrub(doctype)
	return Path(frappe.get_app_path("erpnext", module, "doctype", slug, f"{slug}.json"))


def read_meta(module, doctype):
	data = json.loads(metadata_path(module, doctype).read_text())
	return make_meta(doctype, data["fields"])


def make_meta(name, fields):
	meta = Meta.__new__(Meta)
	meta.__dict__.update(
		name=name,
		fields=[frappe._dict(field) for field in fields],
		_fields={field["fieldname"]: frappe._dict(field) for field in fields},
	)
	return meta


def extracted_keys():
	paths = [(Path(frappe.get_app_path("erpnext", relative)), kind) for relative, kind in PRODUCERS]
	paths.extend((metadata_path(module, doctype), "doctype") for module, doctype in METADATA)
	keys = set()
	for path, kind in paths:
		with path.open("rb") as handle:
			keys.update(
				message
				for _, message, _, context in extract(
					f"frappe.gettext.extractors.{kind}.extract", handle, keywords=PYTHON_KEYWORDS
				)
				if context is None
			)
	return keys


def make_doc(cls=Document, **values):
	doc = cls.__new__(cls)
	doc.__dict__.update(flags=frappe._dict(), **values)
	return doc


def reject(message, exc=frappe.ValidationError, **kwargs):
	raise exc(message)


class TestSLAMessageTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={k: v for k, v in TARGETS.items() if k in keys},
			)
		)
		self.metas = {doctype: read_meta(module, doctype) for module, doctype in METADATA}
		self.metas[CUSTOM_TYPE] = make_meta(
			CUSTOM_TYPE,
			[
				{
					"fieldname": "status",
					"fieldtype": "Select",
					"label": "Status",
					"options": "Open\nReplied\nClosed",
				},
				*sla.get_service_level_agreement_fields(CUSTOM_TYPE),
			],
		)
		self.meta_reads = self.enterContext(
			patch("frappe.get_meta", side_effect=lambda name: self.metas[name])
		)
		self.throw = self.enterContext(patch("frappe.throw", side_effect=reject))
		self.db = self.enterContext(patch("frappe.db", new=Mock()))
		self.db.get_single_value.return_value = 1
		self.db.exists.return_value = False
		self.enterContext(
			patch(
				"frappe.get_system_settings",
				side_effect=lambda key: {"time_zone": "UTC", "rounding_method": "Banker's Rounding"}.get(key),
			)
		)
		self.enterContext(
			patch("frappe.utils.data.get_url", side_effect=lambda uri: "https://example.invalid" + uri)
		)

	def agreement(self, **values):
		defaults = dict(
			doctype="Service Level Agreement",
			name="SLA-RAW",
			enabled=0,
			default_service_level_agreement=0,
			document_type="Lead",
			entity_type=None,
			entity=None,
			start_date=None,
			end_date=None,
			apply_sla_for_resolution=1,
			meta=self.metas["Service Level Agreement"],
		)
		defaults.update(values)
		return make_doc(sla.ServiceLevelAgreement, **defaults)

	def priority(self, **values):
		row = frappe._dict(
			priority="Customer", idx=4, response_time=60, resolution_time=120, default_priority=1
		)
		row.update(values)
		return row

	def error(self, method):
		with self.assertRaises(frappe.ValidationError) as caught:
			method()
		return str(caught.exception)

	def support_row(self, day, start="09:00:00", end="17:00:00"):
		return frappe._dict(workday=day, start_time=start, end_time=end, idx=99)

	def test_actual_configured_extractors_register_all_new_complete_templates(self):
		keys = extracted_keys()
		for key in [MISSING_TIME, REPEATED_DAYS, *CLIENT_MESSAGES]:
			with self.subTest(key=key):
				self.assertIn(key, keys)

	def test_missing_response_time_names_actual_field_and_keeps_priority_identity(self):
		for value in (None, 0, ""):
			with self.subTest(value=value):
				row = self.priority(response_time=value)
				self.assertEqual(
					self.error(self.agreement(priorities=[row]).check_priorities),
					"Impostare Tempo di prima risposta per la priorità Customer nella riga 4.",
				)
				self.assertEqual(
					(row.priority, row.idx, row.response_time, row.resolution_time),
					("Customer", 4, value, 120),
				)

	def test_missing_resolution_time_names_actual_field(self):
		for value in (None, 0, ""):
			with self.subTest(value=value):
				row = self.priority(resolution_time=value)
				self.assertEqual(
					self.error(self.agreement(priorities=[row]).check_priorities),
					"Impostare Tempo di risoluzione per la priorità Customer nella riga 4.",
				)
				self.assertEqual(row.priority, "Customer")

	def test_duration_message_uses_real_metadata_label_override(self):
		self.metas["Service Level Priority"].get_field("response_time").label = "Priority"
		self.assertEqual(
			self.error(self.agreement(priorities=[self.priority(response_time=0)]).check_priorities),
			"Impostare Priorità per la priorità Customer nella riga 4.",
		)

	def test_valid_response_only_equal_and_longer_resolution_controls(self):
		for resolution_enabled, response, resolution in ((0, 60, 0), (1, 60, 60), (1, 60, 120)):
			row = self.priority(response_time=response, resolution_time=resolution)
			before = row.copy()
			doc = self.agreement(apply_sla_for_resolution=resolution_enabled, priorities=[row])
			doc.check_priorities()
			self.assertEqual(doc.default_priority, "Customer")
			self.assertEqual(row, before)
		self.throw.assert_not_called()

	def test_greater_response_duplicate_priority_and_missing_default_keep_original_guards(self):
		self.assertEqual(
			self.error(self.agreement(priorities=[self.priority(response_time=121)]).check_priorities),
			"Il tempo di risposta per la priorità Customer nella riga 4 non può superare il tempo di risoluzione.",
		)
		rows = [
			self.priority(priority="Customer Group"),
			self.priority(priority="Customer Group", default_priority=0),
		]
		self.assertEqual(
			self.error(self.agreement(priorities=rows).check_priorities), "Priorità ripetuta: Customer Group."
		)
		self.assertEqual(
			self.error(self.agreement(priorities=[self.priority(default_priority=0)]).check_priorities),
			"Selezionare una priorità predefinita.",
		)
		self.assertEqual(
			sla.get_repeated(["Customer Group", "Task", "Task", "Customer Group"]), "Task Customer Group"
		)

	def test_invalid_time_ranges_translate_all_canonical_weekdays(self):
		translated = ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica")
		for index, (day, label) in enumerate(zip(get_weekdays(), translated, strict=True), start=1):
			for end in ("09:00:00", "08:59:59"):
				with self.subTest(day=day, end=end):
					row = self.support_row(day, end=end)
					self.assertEqual(
						self.error(self.agreement(support_and_resolution=[row]).check_support_and_resolution),
						f"L’ora di inizio deve precedere l’ora di fine per {label}.",
					)
					self.assertEqual(
						(row.workday, row.idx, row.start_time, row.end_time), (day, index, "09:00:00", end)
					)

	def test_repeated_workdays_translate_each_name_in_existing_repeat_order(self):
		for days, expected in (
			(["Monday", "Monday"], "Lunedì"),
			(["Monday", "Tuesday", "Tuesday", "Monday"], "Martedì, Lunedì"),
			(["Sunday", "Sunday", "Sunday"], "Domenica"),
		):
			with self.subTest(days=days):
				rows = [self.support_row(day) for day in days]
				self.assertEqual(
					self.error(self.agreement(support_and_resolution=rows).check_support_and_resolution),
					f"Giorni lavorativi ripetuti: {expected}.",
				)
				self.assertEqual([row.workday for row in rows], days)
				self.assertEqual([row.idx for row in rows], [get_weekdays().index(day) + 1 for day in days])

	def test_valid_workdays_preserve_time_values_and_canonical_indices(self):
		rows = [self.support_row(day) for day in reversed(get_weekdays())]
		self.agreement(support_and_resolution=rows).check_support_and_resolution()
		self.assertEqual([row.idx for row in rows], [7, 6, 5, 4, 3, 2, 1])
		self.assertEqual([row.workday for row in rows], list(reversed(get_weekdays())))
		self.assertTrue(all((row.start_time, row.end_time) == ("09:00:00", "17:00:00") for row in rows))
		self.throw.assert_not_called()

	def test_disabled_tracking_translates_singleton_link_body_preserving_href(self):
		self.db.get_single_value.return_value = 0
		message = self.error(self.agreement(enabled=1).validate_doc)
		self.assertEqual(
			message,
			'L’opzione <strong>Monitora gli accordi sul livello di servizio</strong> non è abilitata in <a href="https://example.invalid/desk/support-settings/Support%20Settings">Impostazioni di assistenza</a>.',
		)
		self.db.get_single_value.assert_called_once_with("Support Settings", "track_service_level_agreement")
		self.db.exists.assert_not_called()

	def test_default_duplicate_translates_target_type_only(self):
		self.db.exists.return_value = True
		for doctype, label in (
			("Lead", "Contatto commerciale"),
			("Task", "Attività"),
			("Warehouse", "Magazzino"),
			(CUSTOM_TYPE, CUSTOM_TYPE),
		):
			with self.subTest(doctype=doctype):
				doc = self.agreement(document_type=doctype, default_service_level_agreement=1)
				self.assertEqual(
					self.error(doc.validate_doc),
					f"Esiste già un accordo sul livello di servizio predefinito per il tipo di documento {label}.",
				)
				self.db.exists.assert_called_with(
					"Service Level Agreement",
					{
						"document_type": doctype,
						"default_service_level_agreement": "1",
						"name": ["!=", "SLA-RAW"],
					},
				)
				self.assertEqual(doc.document_type, doctype)

	def test_entity_duplicate_translates_type_and_preserves_business_name(self):
		self.db.exists.return_value = True
		for doctype, label in (
			("Customer", "Cliente"),
			("Customer Group", "Gruppo clienti"),
			("Territory", "Territorio"),
		):
			with self.subTest(doctype=doctype):
				doc = self.agreement(entity_type=doctype, entity="Customer")
				self.assertEqual(
					self.error(doc.validate_doc),
					f"Esiste già un accordo sul livello di servizio per <strong>{label}</strong>: <strong>Customer</strong>.",
				)
				self.db.exists.assert_called_with(
					"Service Level Agreement",
					{"entity_type": doctype, "entity": "Customer", "name": ["!=", "SLA-RAW"]},
				)
				self.assertEqual((doc.entity_type, doc.entity), (doctype, "Customer"))

	def test_disabled_and_no_duplicate_controls_preserve_setting_reads(self):
		self.db.get_single_value.return_value = 0
		self.agreement().validate_doc()
		self.db.get_single_value.assert_not_called()
		self.db.exists.assert_not_called()
		self.db.get_single_value.return_value = 1
		self.agreement(enabled=1, default_service_level_agreement=1).validate_doc()
		self.assertEqual(self.db.get_single_value.call_count, 1)
		self.assertEqual(self.db.exists.call_count, 1)
		self.throw.assert_not_called()

	def test_missing_status_names_canonical_type_with_actual_meta(self):
		self.assertEqual(
			self.error(self.agreement(document_type="Warehouse").validate_status_field),
			"Il tipo di documento <strong>Magazzino</strong> deve avere un campo Stato per configurare un accordo sul livello di servizio.",
		)
		self.meta_reads.assert_called_once_with("Warehouse")
		self.metas[CUSTOM_TYPE]._fields.pop("status")
		self.assertEqual(
			self.error(self.agreement(document_type=CUSTOM_TYPE).validate_status_field),
			f"Il tipo di documento <strong>{CUSTOM_TYPE}</strong> deve avere un campo Stato per configurare un accordo sul livello di servizio.",
		)

	def test_existing_standard_and_custom_status_fields_are_accepted(self):
		for doctype in ("Lead", "Task", "Issue", CUSTOM_TYPE):
			self.agreement(document_type=doctype).validate_status_field()
		self.assertEqual(
			self.meta_reads.call_args_list, [call(dt) for dt in ("Lead", "Task", "Issue", CUSTOM_TYPE)]
		)
		self.throw.assert_not_called()

	def test_selected_doctype_keeps_raw_filter_and_identifier_contract(self):
		with patch("frappe.get_all", return_value=[CUSTOM_TYPE]) as get_all:
			self.agreement(document_type=CUSTOM_TYPE).validate_selected_doctype()
		self.assertEqual(get_all.call_args.args[0], "DocType")
		filters = get_all.call_args.args[1]
		self.assertEqual(
			{key: filters[key] for key in ("issingle", "istable", "is_submittable")},
			{"issingle": 0, "istable": 0, "is_submittable": 0},
		)
		self.assertIn("Company", filters["name"][1])
		self.assertIn("Cost Center", filters["name"][1])
		self.assertEqual(get_all.call_args.kwargs, {"pluck": "name"})

	def test_actual_arbitrary_target_selection_and_permission_endpoint_keep_all_raw_payloads(self):
		value_reads, all_reads = [], []

		def get_value(doctype, name, field):
			value_reads.append((doctype, name, field))
			if doctype == "Customer":
				self.assertEqual(name, "Customer")
				return {"customer_group": "Customer Group", "territory": "Territory"}[field]
			self.assertEqual((doctype, name, field), (doctype, doctype, ["lft", "rgt"]))
			return 2, 3

		def get_all(doctype, *args, **kwargs):
			all_reads.append((doctype, deepcopy(args), deepcopy(kwargs)))
			if doctype in ("Customer Group", "Territory"):
				return ["ANCESTOR-" + doctype]
			if doctype == "Service Level Priority":
				return [frappe._dict(priority="Customer")]
			self.assertEqual(doctype, "Service Level Agreement")
			default = any(f[1] == "default_service_level_agreement" and f[3] == 1 for f in kwargs["filters"])
			return [
				frappe._dict(
					name="DEFAULT-RAW" if default else "ENTITY-RAW",
					entity=None if default else "Customer Group",
					start_date=None,
					end_date=None,
					condition=None,
				)
			]

		self.db.get_value.side_effect = get_value
		target = make_doc(
			doctype=CUSTOM_TYPE,
			name="TARGET-RAW",
			customer="Customer",
			priority="Customer",
			service_level_agreement=None,
		)
		before = target.__dict__.copy()
		with patch("frappe.get_all", side_effect=get_all), patch(
			"frappe.has_permission", return_value=True
		) as permission:
			selected = sla.get_active_service_level_agreement_for(target)
			result = sla.get_service_level_agreement_filters(target.doctype, selected.name, target.customer)
		self.assertEqual(selected.name, "ENTITY-RAW")
		self.assertEqual(result, {"priority": ["Customer"], "service_level_agreements": ["ENTITY-RAW"]})
		permission.assert_called_once_with(CUSTOM_TYPE, "read", throw=True)
		self.assertEqual(target.__dict__, before)
		for doctype, args, kwargs in all_reads:
			if doctype == "Service Level Agreement":
				self.assertIn(
					["Service Level Agreement", "document_type", "=", CUSTOM_TYPE], kwargs["filters"]
				)
			elif doctype in ("Customer Group", "Territory"):
				self.assertEqual(args, ({"lft": ["<", 2], "rgt": [">", 3]}, "name"))
				self.assertEqual(kwargs, {"order_by": "lft desc", "limit_page_length": None, "pluck": "name"})
		self.assertEqual(
			value_reads,
			[
				("Customer", "Customer", "customer_group"),
				("Customer Group", "Customer Group", ["lft", "rgt"]),
				("Customer", "Customer", "territory"),
				("Territory", "Territory", ["lft", "rgt"]),
			]
			* 2,
		)

	def test_raw_generated_field_return_is_unchanged_for_arbitrary_targets(self):
		fields = sla.get_service_level_agreement_fields(CUSTOM_TYPE)
		link = next(field for field in fields if field["fieldname"] == "service_level_agreement")
		self.assertEqual(
			json.loads(link["link_filters"]), [["Service Level Agreement", "document_type", "=", CUSTOM_TYPE]]
		)
		status = next(field for field in fields if field["fieldname"] == "agreement_status")
		self.assertEqual(
			(status["label"], status["default"], status["options"]),
			(
				"Service Level Agreement Status",
				"First Response Due",
				"First Response Due\nResolution Due\nFulfilled\nFailed",
			),
		)
		self.assertEqual(link["label"], "Service Level Agreement")
		self.db.insert.assert_not_called()
		self.db.set_value.assert_not_called()
		self.db.commit.assert_not_called()

	def test_english_fallback_retains_new_message_field_and_raw_business_name(self):
		frappe.local.lang = "en"
		with patch("frappe.translate.get_all_translations", return_value={}):
			self.assertEqual(
				self.error(self.agreement(priorities=[self.priority(resolution_time=0)]).check_priorities),
				"Set Resolution Time for priority Customer in row 4.",
			)
