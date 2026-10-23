# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.crm.doctype.email_campaign.email_campaign import EmailCampaign, send_mail

DUPLICATE = "The Campaign '{0}' already exists for the {1} '{2}'"
MISSING_EMAIL = "No email found for {0} {1}"
UNSUBSCRIBED = "{0} {1} has unsubscribed from emails"
TARGETS = {
	DUPLICATE: "La campagna '{0}' esiste già per '{2}' ({1}).",
	MISSING_EMAIL: "Nessun indirizzo email trovato per '{1}' ({0}).",
	UNSUBSCRIBED: "Il destinatario {0} {1} ha annullato l’iscrizione alle email",
	"Email Campaign Error": "Errore campagna email",
	"No recipients found for campaign {0}": "Nessun destinatario trovato per la campagna {0}",
	"Lead": "Contatto commerciale",
	"Contact": "Contatto",
	"Email Group": "Gruppo email",
	"Campaign": "Campagna",
}


class TestEmailCampaignMessageLabels(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		self.events = []
		self.exists_result = True
		self.email_result = (None, 0)
		self.enterContext(
			patch.object(frappe, "db", SimpleNamespace(exists=self.exists, get_value=self.get_value))
		)
		self.enterContext(patch("frappe.log_error", side_effect=self.log_error))
		for boundary in ("get_doc", "get_cached_doc", "sendmail", "render_template"):
			self.enterContext(
				patch("frappe." + boundary, side_effect=AssertionError("Unexpected I/O: " + boundary))
			)
		self.enterContext(
			patch(
				"frappe.get_all",
				side_effect=AssertionError("Unexpected member query outside Email Group control"),
			)
		)
		self.enterContext(
			patch(
				"erpnext.crm.doctype.email_campaign.email_campaign.make",
				side_effect=AssertionError("Unexpected Communication creation"),
			)
		)

	def exists(self, *args, **kwargs):
		self.events.append(("exists", args, kwargs))
		return self.exists_result

	def get_value(self, *args, **kwargs):
		self.events.append(("get_value", args, kwargs))
		return self.email_result

	def log_error(self, **kwargs):
		self.events.append(("log_error", kwargs))

	def document(self, kind):
		doc = EmailCampaign.__new__(EmailCampaign)
		doc.__dict__.update(
			doctype="Email Campaign",
			name="RAW-EMAIL-CAMPAIGN",
			campaign_name="Campaign",
			recipient="Lead",
			email_campaign_for=kind,
		)
		return doc

	def reset(self):
		self.events.clear()
		frappe.local.message_log.clear()

	def test_duplicate_message_translates_only_type_and_keeps_canonical_filters(self):
		for kind in ("Lead", "Contact", "Email Group", "Custom Recipient", "", None):
			with self.subTest(kind=kind):
				self.reset()
				doc = self.document(kind)
				before = deepcopy(doc.__dict__)
				with self.assertRaises(frappe.ValidationError) as caught:
					doc.validate_email_campaign_already_exists()
				self.assertEqual(
					str(caught.exception),
					TARGETS[DUPLICATE].format("Campaign", TARGETS.get(kind, kind), "Lead"),
				)
				self.assertEqual(doc.__dict__, before)
				self.assertEqual(
					self.events,
					[
						(
							"exists",
							(
								"Email Campaign",
								{
									"campaign_name": "Campaign",
									"recipient": "Lead",
									"status": ("in", ["In Progress", "Scheduled"]),
									"name": ("!=", "RAW-EMAIL-CAMPAIGN"),
								},
							),
							{},
						)
					],
				)

	def test_no_duplicate_returns_without_message_or_state_change(self):
		self.exists_result = False
		doc = self.document("Lead")
		before = deepcopy(doc.__dict__)
		self.assertIsNone(doc.validate_email_campaign_already_exists())
		self.assertEqual(doc.__dict__, before)
		self.assertEqual(len(self.events), 1)
		self.assertEqual(self.events[0][0], "exists")
		self.assertEqual(frappe.local.message_log, [])

	def test_missing_email_and_opt_out_logs_keep_raw_read_order_and_return_before_send(self):
		for kind in ("Lead", "Contact", "Custom Recipient", "", None):
			for email, unsubscribed, source in (
				(None, 0, MISSING_EMAIL),
				("lead@example.com", 1, UNSUBSCRIBED),
			):
				with self.subTest(kind=kind, source=source):
					self.reset()
					self.email_result = (email, unsubscribed)
					campaign = frappe._dict(
						email_campaign_for=kind,
						recipient="Lead",
						sender="sender@example.com",
						name="Campaign",
					)
					entry = frappe._dict(email_template="UNREAD-TEMPLATE")
					before = deepcopy((campaign, entry))
					self.assertIsNone(send_mail(entry, campaign))
					self.assertEqual((campaign, entry), before)
					self.assertEqual(
						self.events,
						[
							("get_value", (kind, "Lead", ["email_id", "unsubscribed"]), {}),
							(
								"log_error",
								{
									"title": TARGETS["Email Campaign Error"],
									"message": TARGETS[source].format(TARGETS.get(kind, kind), "Lead"),
								},
							),
						],
					)

	def test_email_group_empty_member_control_keeps_canonical_query(self):
		campaign = frappe._dict(email_campaign_for="Email Group", recipient="Lead", name="Campaign")
		with patch("frappe.get_all", return_value=[]) as members:
			self.assertIsNone(send_mail(frappe._dict(), campaign))
		members.assert_called_once_with(
			"Email Group Member", filters={"email_group": "Lead", "unsubscribed": 0}, pluck="email"
		)
		self.assertEqual(
			self.events,
			[
				(
					"log_error",
					{
						"title": TARGETS["Email Campaign Error"],
						"message": "Nessun destinatario trovato per la campagna Campaign",
					},
				)
			],
		)

	def test_existing_validation_sibling_keeps_bold_raw_recipient_and_new_document_guard(self):
		for kind in ("Lead", "Contact"):
			with self.subTest(kind=kind):
				doc = self.document(kind)
				doc.__dict__["__islocal"] = 1
				with self.assertRaises(frappe.ValidationError) as caught:
					doc.validate_not_unsubscribed(frappe._dict(unsubscribed=1))
				self.assertEqual(
					str(caught.exception), TARGETS[UNSUBSCRIBED].format(TARGETS[kind], frappe.bold("Lead"))
				)
				doc.__dict__["__islocal"] = 0
				self.assertIsNone(doc.validate_not_unsubscribed(frappe._dict(unsubscribed=1)))
				self.assertIsNone(doc.validate_not_unsubscribed(None))
		self.assertEqual(self.events, [])

	def test_english_complete_frames_keep_raw_type_and_business_names(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			self.document("Contact").validate_email_campaign_already_exists()
		self.assertEqual(str(caught.exception), DUPLICATE.format("Campaign", "Contact", "Lead"))
		self.reset()
		send_mail(frappe._dict(), frappe._dict(email_campaign_for="Contact", recipient="Lead"))
		self.assertEqual(
			self.events[-1],
			(
				"log_error",
				{"title": "Email Campaign Error", "message": MISSING_EMAIL.format("Contact", "Lead")},
			),
		)
