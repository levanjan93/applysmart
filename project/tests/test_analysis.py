"""Checkpoint 2 tests: verbatim posting text, analysis validation, claim checks, reviews.

The model calls are replaced by fakes; nothing here uses the network.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import analysis
import tools
import webapp
from analysis import AnalysisDraft, CheckDraft, CheckDrafts, ClaimDraft, QuestionDraft
from runtime import load_openrouter_config
from storage import Store

RAW = """Please save this posting.
Data Analyst
Example Bank Ltd · Singapore
Requirements
- 2+ years of SQL experience
- Experience leading a project team
- Tableau dashboards
- Banking knowledge is a plus
"""


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        for module in (analysis, tools, webapp):
            patcher = mock.patch.object(module, "default_store", return_value=self.store)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.posting = self.store.create_posting({
            "title": "Data Analyst", "company": "Example Bank Ltd", "location": "Singapore",
            "source": "linkedin",
            "requirements": [
                {"text": "2+ years of SQL experience", "type": "required"},
                {"text": "Experience leading a project team", "type": "required"},
                {"text": "Tableau dashboards", "type": "required"},
                {"text": "Banking knowledge", "type": "preferred"},
            ],
            "responsibilities": [],
        }, RAW)
        add = self.store.add_experience
        self.sql = add({"source": "resume", "category": "work", "text": "Wrote SQL reports for three years.", "skills": ["SQL"]})
        self.hack = add({"source": "user-added", "on_resume": False, "category": "project",
                         "text": "Participated in a hackathon team.", "skills": []})
        self.lead = add({"source": "user-added", "on_resume": True, "category": "leadership",
                         "text": "Led a four-person capstone team.", "skills": []})

    def tearDown(self):
        self.temporary.cleanup()

    def draft(self):
        return AnalysisDraft(
            claims=[
                ClaimDraft(requirement_id="REQ-1", verdict="strong match", evidence=[self.sql["id"]], explanation="Three years of SQL."),
                ClaimDraft(requirement_id="REQ-2", verdict="strong match", evidence=[self.hack["id"], "EXP-0999"], explanation="Team project."),
                ClaimDraft(requirement_id="REQ-3", verdict="partial match", evidence=[], explanation="Dashboards."),
                ClaimDraft(requirement_id="REQ-9", verdict="strong match", evidence=[self.sql["id"]], explanation="Not real."),
            ],
            questions=[
                QuestionDraft(requirement_id="REQ-4", question="Have you worked in banking?"),
                QuestionDraft(requirement_id="REQ-77", question="Unrelated?"),
            ],
        )

    def test_validation_flags_invalid_ids_and_fills_skipped_requirements(self):
        result = analysis.validate_analysis(self.draft(), self.posting, self.store.read_experience())
        claims = {claim["requirement_id"]: claim for claim in result["claims"]}
        self.assertEqual(list(claims), ["REQ-1", "REQ-2", "REQ-3", "REQ-4"])
        self.assertTrue(claims["REQ-1"]["valid"])
        # A cited ID that does not exist makes the claim invalid and is reported.
        self.assertFalse(claims["REQ-2"]["valid"])
        self.assertEqual(claims["REQ-2"]["evidence"], [self.hack["id"]])
        self.assertEqual(claims["REQ-2"]["invalid_evidence"], ["EXP-0999"])
        # A match with no evidence is invalid.
        self.assertFalse(claims["REQ-3"]["valid"])
        # A requirement the model skipped becomes a flagged gap.
        self.assertEqual(claims["REQ-4"]["verdict"], "gap")
        self.assertIn("Not assessed", claims["REQ-4"]["issues"][0])
        # A claim about a requirement that does not exist is discarded.
        self.assertEqual(result["invalid_claims"][0]["requirement_id"], "REQ-9")
        self.assertEqual([q["requirement_id"] for q in result["questions"]], ["REQ-4"])

    def test_keeps_at_most_three_questions_required_ones_first(self):
        draft = self.draft()
        draft.questions = [
            QuestionDraft(requirement_id="REQ-4", question="Have you worked in a bank?"),
            QuestionDraft(requirement_id="REQ-3", question="Have you built Tableau dashboards?"),
            QuestionDraft(requirement_id="REQ-3", question="A second question on the same thing?"),
            QuestionDraft(requirement_id="REQ-1", question="How many years of SQL?"),
            QuestionDraft(requirement_id="REQ-2", question="Have you led a team?"),
        ]
        result = analysis.validate_analysis(draft, self.posting, self.store.read_experience())
        self.assertEqual([q["requirement_id"] for q in result["questions"]], ["REQ-3", "REQ-1", "REQ-2"])

    def test_add_to_resume_only_lists_valid_user_added_entries_not_on_resume(self):
        draft = AnalysisDraft(claims=[
            ClaimDraft(requirement_id="REQ-1", verdict="strong match", evidence=[self.sql["id"]], explanation="."),
            ClaimDraft(requirement_id="REQ-2", verdict="partial match", evidence=[self.hack["id"], self.lead["id"]], explanation="."),
        ], questions=[])
        result = analysis.validate_analysis(draft, self.posting, self.store.read_experience())
        self.assertEqual(result["add_to_resume"], [
            {"exp_id": self.hack["id"], "text": self.hack["text"], "requirement_ids": ["REQ-2"]},
        ])

    def fake_ask(self, check_ratings=None, fail_check=False):
        async def ask(kind, _instructions, payload, _output_type):
            if kind == "analysis":
                return self.draft()
            if fail_check:
                raise RuntimeError("checker down")
            return CheckDrafts(checks=[
                CheckDraft(requirement_id=claim["requirement_id"], rating=(check_ratings or {}).get(claim["requirement_id"], "supported"), reason="Because.")
                for claim in payload["claims"]
            ])
        return ask

    def test_full_run_saves_checks_and_orders_flagged_claims_first(self):
        with mock.patch.object(analysis, "_ask", self.fake_ask({"REQ-2": "stretched"})):
            saved = asyncio.run(analysis.run_fit_analysis("JOB-0001"))
        self.assertRegex(saved["analysis_id"], r"^JOB-0001_\d{8}T\d{6}$")
        claims = {claim["requirement_id"]: claim for claim in saved["claims"]}
        self.assertEqual(claims["REQ-1"]["check"]["rating"], "supported")
        self.assertEqual(claims["REQ-2"]["check"]["rating"], "stretched")
        # No evidence: rated unsupported by a code rule, not sent to the checker.
        self.assertEqual(claims["REQ-3"]["check"], {"rating": "unsupported", "reason": "No existing experience entry was cited.", "by": "code"})
        self.assertIsNone(claims["REQ-4"]["check"])
        summary = analysis.agent_summary(saved)
        self.assertEqual([c["requirement_id"] for c in summary["claims"]][:2], ["REQ-2", "REQ-3"])
        self.assertIn("What fits", summary["format_instructions"])

    def test_checker_failure_keeps_the_analysis_and_reports_it(self):
        with mock.patch.object(analysis, "_ask", self.fake_ask(fail_check=True)):
            saved = asyncio.run(analysis.run_fit_analysis("JOB-0001"))
        self.assertEqual(saved["check_status"], "failed: RuntimeError")
        self.assertIsNone(next(c for c in saved["claims"] if c["requirement_id"] == "REQ-1")["check"])

    def test_evidence_view_highlights_the_requirement_line_in_the_raw_posting(self):
        with mock.patch.object(analysis, "_ask", self.fake_ask()):
            saved = asyncio.run(analysis.run_fit_analysis("JOB-0001"))
        view = analysis.evidence_view(saved["analysis_id"])
        claims = {claim["requirement_id"]: claim for claim in view["analysis"]["claims"]}
        self.assertEqual(view["raw_lines"][claims["REQ-1"]["raw_line"]], "- 2+ years of SQL experience")
        self.assertEqual(view["experience"][self.sql["id"]]["text"], self.sql["text"])

    def test_claim_review_is_saved_and_logged(self):
        with mock.patch.object(analysis, "_ask", self.fake_ask()):
            saved = asyncio.run(analysis.run_fit_analysis("JOB-0001"))
        status, body = webapp.review_claim({
            "analysis_id": saved["analysis_id"], "requirement_id": "REQ-1", "decision": "disagree", "seconds": 6.25,
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["claim"]["review"]["decision"], "disagree")
        stored = self.store.read_analysis(saved["analysis_id"])
        self.assertEqual(stored["claims"][0]["review"]["decision"], "disagree")
        logged = self.store.read_interactions()[-1]
        self.assertEqual((logged["event_type"], logged["decision"], logged["item_id"]),
                         ("claim_review", "disagree", f"{saved['analysis_id']}/REQ-1"))
        self.assertEqual(logged["time_to_decision_seconds"], 6.2)
        self.assertEqual(logged["before"]["check"]["rating"], "supported")
        self.assertEqual(webapp.review_claim({"analysis_id": saved["analysis_id"], "requirement_id": "REQ-1", "decision": "maybe"})[0], 400)


class VerbatimPostingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        patcher = mock.patch.object(tools, "default_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(tools.set_user_messages, [])

    def tearDown(self):
        self.temporary.cleanup()

    def test_saved_raw_text_is_the_users_message_not_the_models_copy(self):
        pasted = "Home Jobs\nSenior Data Analyst\nDBS Bank Ltd · Singapore\nMeet the team: Jane Lim\n- SQL"
        tools.set_user_messages([pasted, "yes please save it", "Import the resume I uploaded. Upload ID: UPL-x"])
        result = tools.add_posting_action(
            source="linkedin", title="Senior Data Analyst", company="DBS Bank",
            location="Singapore", requirements=["required: SQL"], responsibilities=[],
        )
        self.assertEqual(self.store.read_raw_text(result["posting"]["id"]), pasted)
        self.assertEqual(result["posting"]["requirements"], [{"id": "REQ-1", "text": "SQL", "type": "required"}])

    def test_most_recent_matching_message_wins_and_missing_text_fails(self):
        tools.set_user_messages(["Data Analyst at Grab", "Data Analyst at Grab (updated paste)"])
        self.assertEqual(tools.find_pasted_text("Data Analyst", "Grab"), "Data Analyst at Grab (updated paste)")
        tools.set_user_messages(["hello"])
        with self.assertRaisesRegex(ValueError, "paste the posting again"):
            tools.add_posting_action(source="x", title="Data Analyst", company="Grab", location="", requirements=[], responsibilities=[])


class ModelConfigTests(unittest.TestCase):
    def test_analysis_and_check_models_default_to_the_main_model(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("OPENROUTER_API_KEY=k\nOPENROUTER_MODEL=a/main\n", encoding="utf-8")
            config = load_openrouter_config(env)
            self.assertEqual((config.analysis_model, config.check_model), ("a/main", "a/main"))
            env.write_text("OPENROUTER_API_KEY=k\nOPENROUTER_MODEL=a/main\nOPENROUTER_CHECK_MODEL=b/judge\n", encoding="utf-8")
            config = load_openrouter_config(env)
            self.assertEqual((config.analysis_model, config.check_model), ("a/main", "b/judge"))


if __name__ == "__main__":
    unittest.main()
