import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_dir
from frappe.apps import get_apps
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.tests import UnitTestCase

from erpnext import hooks


class TestSetupWizardHookTranslation(UnitTestCase):
	source = "Let's give your business a home."
	target = "Prepariamo uno spazio per la tua attività."

	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"

	def extracted(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "erpnext" / "hooks.py"
			path.parent.mkdir()
			path.write_bytes(Path(hooks.__file__).read_bytes())
			with patch("frappe.gettext.extractors.navbar.get_module", return_value=hooks):
				return {
					message
					for _filename, _line, message, _comments, context in extract_from_dir(
						directory, get_method_map("frappe"), keywords=PYTHON_KEYWORDS
					)
					if context is None
				}

	def app_payload(self, allowed=True):
		translations = {self.source: self.target} if self.source in self.extracted() else {}

		def read_hook(hook, app_name):
			if (hook, app_name) == ("add_to_apps_screen", "erpnext"):
				return hooks.add_to_apps_screen
			if (hook, app_name) == ("require_type_annotated_api_methods", "frappe"):
				return []
			raise AssertionError((hook, app_name))

		with (
			patch.object(frappe.local, "request_cache", None),
			patch("frappe.get_active_apps", return_value=["erpnext"]),
			patch("frappe.apps.get_docked_apps", return_value=set()),
			patch("frappe.apps.get_setup_wizard_completed_apps", return_value=["erpnext"]),
			patch("frappe.get_hooks", side_effect=read_hook) as get_hooks,
			patch("frappe.get_attr", return_value=lambda: allowed) as get_attr,
			patch("frappe.translate.get_all_translations", return_value=translations),
		):
			payload = get_apps()
		get_hooks.assert_any_call("add_to_apps_screen", app_name="erpnext")
		get_attr.assert_called_once_with("erpnext.check_app_permission")
		return payload

	def test_caption_is_registered_by_the_configured_hooks_extractor(self):
		self.assertIn(self.source, self.extracted())

	def test_raw_hook_and_loaded_hook_contract_remain_unchanged(self):
		self.assertEqual(hooks.add_to_apps_screen[0]["setup_wizard_text"], self.source)
		loaded = frappe._load_app_hooks("erpnext")
		self.assertEqual(loaded["add_to_apps_screen"], hooks.add_to_apps_screen)
		self.assertNotIn("N_", loaded)

	def test_actual_app_payload_translates_the_intro_and_preserves_app_identity(self):
		self.assertEqual(
			self.app_payload(),
			[
				{
					"name": "erpnext",
					"logo": hooks.app_logo_url,
					"title": "ERPNext",
					"route": hooks.app_home,
					"setup_wizard_text": self.target,
				}
			],
		)

	def test_denied_app_is_still_excluded(self):
		self.assertEqual(self.app_payload(allowed=False), [])
