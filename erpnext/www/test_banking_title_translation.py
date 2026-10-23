from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.tests import UnitTestCase
from jinja2.sandbox import SandboxedEnvironment


class PageTitle(HTMLParser):
	def __init__(self, html):
		super().__init__()
		self.in_title = False
		self.text = ""
		self.feed(html)

	def handle_starttag(self, tag, attrs):
		if tag == "title":
			self.in_title = True

	def handle_endtag(self, tag):
		if tag == "title":
			self.in_title = False

	def handle_data(self, data):
		if self.in_title:
			self.text += data


class TestBankingTitleTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.template = Path(frappe.get_app_path("erpnext")).parent / "banking/index.html"

	def render(self, translations, app_name):
		environment = SandboxedEnvironment(autoescape=True)
		environment.globals.update(_=frappe._)
		with patch("frappe.translate.get_all_translations", return_value=translations):
			return environment.from_string(self.template.read_text()).render(
				lang="it",
				layout_direction="ltr",
				app_name=app_name,
				boot='"{}"',
				frappe=frappe._dict(session=frappe._dict(csrf_token="local-test-token")),
			)

	def test_title_uses_the_selected_language_and_preserves_the_app_name(self):
		html = self.render({"Banking": "Servizi bancari"}, "Impresa <Centro> & Figli")
		self.assertEqual(PageTitle(html).text, "Servizi bancari | Impresa <Centro> & Figli")
		self.assertIn("Impresa &lt;Centro&gt; &amp; Figli", html)
		self.assertIn('id="root"', html)
		self.assertIn("frappe._translations_loaded = fetch(", html)

	def test_missing_translation_preserves_the_source_title(self):
		self.assertEqual(PageTitle(self.render({}, "ERPNext")).text, "Banking | ERPNext")

	def test_title_is_extracted_by_the_real_html_extractor(self):
		with self.template.open("rb") as file:
			messages = list(
				extract("frappe.gettext.extractors.html_template.extract", file, keywords=PYTHON_KEYWORDS)
			)
		self.assertIn(
			("Banking", None), [(message, context) for _line, message, _comments, context in messages]
		)
