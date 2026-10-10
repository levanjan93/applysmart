"""Posting card evidence: verified source quotes, display lines, and posted dates."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import tools
import webapp
from posting_evidence import format_lines, locate_quote, source_map
from storage import Store

RAW = (
    "Senior Data Analyst\n"
    "DBS Bank · Singapore · Reposted 6 days ago\n"
    "\n"
    "About the job\n"
    "You will build dashboards for the retail team.\n"
    "\n"
    "Requirements:\n"
    "- 3+ years of SQL experience\n"
    "• Familiarity with Tableau or Power BI\n"
    "Strong stakeholder communication\n"
    "Banking knowledge is a plus\n"
    "\n"
    "Similar jobs you may like"
)


def text_at(spans):
    return [RAW[start:end] for start, end in spans]


class QuoteTests(unittest.TestCase):
    def test_quote_matches_ignoring_case_whitespace_and_curly_quotes(self):
        self.assertEqual(text_at(locate_quote(RAW, "3+  YEARS of sql")), ["3+ years of SQL"])
        self.assertEqual(locate_quote("You’ll lead", "you'll lead"), [[0, 11]])

    def test_ellipsis_quote_matches_each_piece_in_order(self):
        spans = locate_quote(RAW, "Familiarity with Tableau ... Power BI")
        self.assertEqual(text_at(spans), ["Familiarity with Tableau", "Power BI"])
        self.assertIsNone(locate_quote(RAW, "Power BI ... Familiarity"))

    def test_invented_quote_is_not_found(self):
        self.assertIsNone(locate_quote(RAW, "5 years of Python"))
        self.assertIsNone(locate_quote(RAW, ""))


class FormatTests(unittest.TestCase):
    def test_lines_keep_exact_offsets_and_get_kinds(self):
        lines = format_lines(RAW)
        self.assertEqual("\n".join(RAW[line["start"]:line["end"]] for line in lines), RAW)
        kinds = {RAW[line["start"]:line["end"]]: line["kind"] for line in lines}
        self.assertEqual(kinds["About the job"], "header")
        self.assertEqual(kinds["Requirements:"], "header")
        self.assertEqual(kinds["- 3+ years of SQL experience"], "bullet")
        self.assertEqual(kinds["Strong stakeholder communication"], "item")
        self.assertEqual(kinds["Senior Data Analyst"], "text")
        bullet = next(line for line in lines if line["kind"] == "bullet")
        self.assertEqual(RAW[bullet["start"]:bullet["marker_end"]], "- ")

    def test_short_acronym_line_is_not_a_header(self):
        kinds = [line["kind"] for line in format_lines("Skills\nSQL\nPython")]
        self.assertEqual(kinds, ["header", "item", "item"])


class SourceMapTests(unittest.TestCase):
    def test_each_item_gets_a_verified_status(self):
        arguments = {
            "title": "Senior Data Analyst", "company": "DBS Bank", "location": "Singapore",
            "date_posted": "6 days ago", "salary": "SGD 8,000",
            "responsibilities": ["Build dashboards for the retail team"],
            "source_quotes": [
                {"field": "requirement", "value": "SQL, 3+ years", "quote": "3+ years of SQL experience"},
                {"field": "requirement", "value": "Banking knowledge", "quote": "Banking experience required"},
                {"field": "responsibility", "value": "Build dashboards for the retail team",
                 "quote": "build dashboards for the retail team"},
            ],
        }
        requirements = [
            {"text": "SQL, 3+ years", "type": "required"},
            {"text": "Banking knowledge", "type": "preferred"},
            {"text": "Stakeholder communication skills", "type": "required"},
            {"text": "Python", "type": "required"},
        ]
        sources = source_map(RAW, arguments, requirements)["sources"]
        self.assertEqual(sources["requirements.0"]["status"], "quoted")
        self.assertEqual(text_at(sources["requirements.0"]["spans"]), ["3+ years of SQL experience"])
        # A made-up quote is flagged; code's own search still finds the value.
        self.assertTrue(sources["requirements.1"]["quote_not_found"])
        self.assertEqual(sources["requirements.1"]["status"], "found")
        self.assertEqual(sources["requirements.2"]["status"], "approximate")
        self.assertEqual(text_at(sources["requirements.2"]["spans"]), ["Strong stakeholder communication"])
        self.assertEqual(sources["requirements.3"]["status"], "missing")
        self.assertEqual(sources["title"]["status"], "found")
        self.assertEqual(sources["date_posted"]["status"], "found")
        self.assertEqual(sources["salary"]["status"], "missing")
        self.assertEqual(sources["responsibilities.0"]["status"], "quoted")


class PostedDateTests(unittest.TestCase):
    today = date(2026, 10, 10)

    def test_relative_phrases_become_estimated_dates(self):
        cases = {
            "6 days ago": "2026-10-04", "Reposted 6 days ago": "2026-10-04", "1 week ago": "2026-10-03",
            "a week ago": "2026-10-03", "30+ days ago": "2026-09-10", "2 months ago": "2026-08-10",
            "3 hours ago": "2026-10-10",
        }
        for phrase, expected in cases.items():
            self.assertEqual(tools.normalize_posted_date(phrase, today=self.today), (expected, True), phrase)

    def test_iso_dates_are_exact_and_unknown_phrases_are_not_guessed(self):
        self.assertEqual(tools.normalize_posted_date("2026-10-01", today=self.today), ("2026-10-01", False))
        self.assertEqual(tools.normalize_posted_date("metadata ago", today=self.today), (None, True))

    def test_card_view_shows_date_and_its_origin(self):
        view = tools.posted_date_view("6 days ago", today=self.today)
        self.assertEqual(view, {"date": "2026-10-04", "is_estimate": True, "original": "6 days ago",
                                "counted_from": "2026-10-10", "recognized": True})
        self.assertIsNone(tools.posted_date_view("2026-10-01", today=self.today)["original"])


class PostingSaveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        patcher = mock.patch.object(tools, "default_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(tools.set_user_messages, [])
        tools.set_user_messages([RAW])

    def test_original_date_phrase_is_kept_and_quotes_are_not_saved(self):
        result = tools.add_posting_action(
            source="linkedin", title="Senior Data Analyst", company="DBS Bank", location="Singapore",
            requirements=[{"text": "SQL", "type": "required"}], responsibilities=[],
            date_posted="6 days ago",
            source_quotes=[{"field": "requirement", "value": "SQL", "quote": "3+ years of SQL"}],
        )
        posting = result["posting"]
        self.assertEqual(posting["date_posted_text"], "6 days ago")
        self.assertTrue(posting["date_posted_is_estimate"])
        self.assertNotIn("source_quotes", posting)

    def test_approval_context_carries_lines_sources_and_date(self):
        with mock.patch("webapp.duplicate_candidates", return_value=[]):
            context = webapp.approval_context("add_posting", {
                "title": "Senior Data Analyst", "company": "DBS Bank", "date_posted": "6 days ago",
                "requirements": ["required: 3+ years of SQL experience"], "responsibilities": [],
            })
        self.assertEqual(context["date_posted"]["original"], "6 days ago")
        self.assertEqual(context["evidence"]["sources"]["requirements.0"]["status"], "found")
        self.assertTrue(context["evidence"]["lines"])


if __name__ == "__main__":
    unittest.main()
