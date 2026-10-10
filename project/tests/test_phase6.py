"""Checkpoint 3 tests: tracking, search across name variants, duplicates, views, log summary.

Model calls are replaced by fakes; nothing here uses the network.
"""

import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import duplicates
import llm
import tools
import webapp
from duplicates import DuplicateJudgment, DuplicateJudgments
from runtime import apply_decision, describe_write_output, finish_interaction
from storage import StorageError, Store

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "summarize_log.py"


def posting(title, company, **extra):
    return {"title": title, "company": company, "location": "Singapore", "source": "linkedin",
            "requirements": [{"text": "SQL", "type": "required"}], "responsibilities": [], **extra}


class Phase6Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        for module in (tools, webapp, duplicates):
            patcher = mock.patch.object(module, "default_store", return_value=self.store)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dbs = self.store.create_posting(posting("Data Analyst", "DBS Bank Ltd"), "raw 1")
        self.gic = self.store.create_posting(posting("AVP, Data Business Analyst", "GIC Private Limited"), "raw 2")
        self.harbour = self.store.create_posting(posting("Data Analyst, Consumer Banking", "Harbour Bank"), "raw 3")

    def tearDown(self):
        self.temporary.cleanup()

    def test_company_search_matches_name_variants(self):
        for query in ("DBS", "dbs bank", "D.B.S. Bank Pte Ltd", "DBS Group"):
            hits = tools.search_with_history(company=query)["matches"]
            self.assertEqual([hit["id"] for hit in hits], [self.dbs["id"]], query)
        self.assertEqual([h["id"] for h in tools.search_with_history(company="GIC")["matches"]], [self.gic["id"]])

    def test_no_match_reports_near_misses_plainly(self):
        result = tools.search_with_history(company="Harbor Bank")
        self.assertEqual(result["matches"], [])
        self.assertEqual([item["id"] for item in result["near_misses"]], [self.harbour["id"]])

    def test_status_update_changes_record_and_log_and_reports_applied_date(self):
        output = tools.update_status.on_invoke_tool  # registered tool exists
        self.assertTrue(callable(output))
        self.store.update_status(self.dbs["id"], "applied", "via referral")
        match = tools.search_with_history(company="DBS")["matches"][0]
        self.assertEqual(match["status"], "applied")
        self.assertIsNotNone(match["applied_on"])
        self.assertEqual(match["last_status_change"]["to"], "applied")
        events = self.store.read_tracking_log(self.dbs["id"])
        self.assertEqual(events[-1]["details"], {"from": "saved", "to": "applied", "note": "via referral"})

    def test_requirement_labels_at_either_end_set_the_type(self):
        self.assertEqual(tools.normalize_requirements(
            ["Banking knowledge is a plus (preferred)", "SQL (Required)", "Preferred - Retail", "Python"]), [
            {"text": "Banking knowledge is a plus", "type": "preferred"},
            {"text": "SQL", "type": "required"},
            {"text": "Retail", "type": "preferred"},
            {"text": "Python", "type": "required"},
        ])

    def test_status_outcome_text(self):
        outcome = describe_write_output({"saved": True, "status_change": {
            "job_id": "JOB-0001", "title": "Data Analyst", "company": "DBS Bank Ltd", "from": "saved", "to": "applied"}})
        self.assertEqual(outcome["text"], "JOB-0001 (Data Analyst, DBS Bank Ltd): status saved → applied")

    def test_link_decision_saves_new_posting_with_related_id_and_logs_link(self):
        tools.set_user_messages(["Senior Data Analyst at DBS Bank, careers site, SQL"])
        self.addCleanup(tools.set_user_messages, [])
        interruption = SimpleNamespace(tool_name="add_posting", raw_item=SimpleNamespace(call_id="c9"), arguments={
            "source": "company site", "title": "Senior Data Analyst", "company": "DBS Bank", "location": "Singapore",
            "requirements": ["required: SQL"], "responsibilities": []})
        state = SimpleNamespace(approve=mock.Mock(), reject=mock.Mock())
        outcomes = {}
        linked = dict(interruption.arguments, related_postings=[self.dbs["id"]])
        record = apply_decision(state, interruption, {"decision": "link", "arguments": linked, "seconds": 3.0}, outcomes)
        saved = self.store.read_posting("JOB-0004")
        self.assertEqual(saved["related_postings"], [self.dbs["id"]])
        logged = finish_interaction(record, outcomes)
        self.assertEqual((logged["decision"], logged["item_id"], logged["saved"]), ("link", "JOB-0004", True))
        with self.assertRaisesRegex(StorageError, "no such posting"):
            self.store.create_posting(posting("X", "Y", related_postings=["JOB-0999"]), "raw")

    def test_duplicate_judgments_are_attached_to_candidates(self):
        async def fake_ask(kind, instructions, payload, output_type):
            self.assertEqual(instructions, "duplicate_judge_instructions.md")
            self.assertEqual(payload["saved_candidates"][0]["job_id"], self.dbs["id"])
            return DuplicateJudgments(judgments=[
                DuplicateJudgment(job_id=self.dbs["id"], judgment="likely same posting", reason="Same role; only 'Senior' differs."),
                DuplicateJudgment(job_id="JOB-0999", judgment="unrelated", reason="Not a candidate."),
            ])
        with mock.patch.object(duplicates, "ask", fake_ask):
            context = webapp.approval_context("add_posting", posting("Senior Data Analyst", "DBS Bank"))
        candidate = context["duplicates"][0]
        self.assertEqual((candidate["id"], candidate["judgment"]), (self.dbs["id"], "likely same posting"))
        self.assertEqual(len(context["duplicates"]), 1)

    def test_judgment_failure_still_shows_candidates(self):
        async def broken(*_args):
            raise TimeoutError
        with mock.patch.object(duplicates, "ask", broken):
            context = webapp.approval_context("add_posting", posting("Senior Data Analyst", "DBS Bank"))
        self.assertEqual(context["duplicates"][0]["id"], self.dbs["id"])
        self.assertIn("unavailable", context["judgment_error"])

    def test_run_blocking_works_on_a_fresh_thread(self):
        async def answer():
            return 42
        outcome = {}
        import threading
        def work():
            outcome["value"] = llm.run_blocking(answer())
            asyncio.get_event_loop_policy().get_event_loop().close()
        worker = threading.Thread(target=work)
        worker.start()
        worker.join(5)
        self.assertEqual(outcome["value"], 42)

    def test_analysis_instruction_proposal_replaces_file_after_approval(self):
        # The model often drops the final newline; code restores it.
        result = tools.propose_analysis_instructions_action("# Shorter\nPut gaps first.", "User asked twice for gaps first.")
        self.assertEqual(self.store.read_analysis_instructions(), "# Shorter\nPut gaps first.\n")
        self.assertEqual(describe_write_output(result)["text"], "Updated your analysis instructions")
        self.assertEqual(webapp.approval_context("propose_analysis_instructions", {})["before"], "# Shorter\nPut gaps first.\n")

    def test_direct_view_edits_are_saved_and_logged(self):
        status, detail = webapp.change_status({"job_id": self.gic["id"], "status": "interviewing", "note": "phone screen"})
        self.assertEqual((status, detail["posting"]["status"]), (200, "interviewing"))
        entry = self.store.add_experience({"source": "user-added", "category": "skill", "text": "Pythn"})
        self.assertEqual(webapp.edit_experience({"exp_id": entry["id"], "fields": {"text": "Python", "id": "EXP-9"}})[1]["entry"]["text"], "Python")
        self.assertEqual(webapp.remove_experience({"exp_id": entry["id"]})[0], 200)
        self.assertEqual(webapp.remove_experience({"exp_id": entry["id"]})[0], 400)
        logged = [(r["item_type"], r["decision"]) for r in self.store.read_interactions()]
        self.assertEqual(logged, [("posting_status", "edit"), ("experience", "edit"), ("experience", "delete")])
        overview = {row["id"]: row for row in webapp.postings_overview()}
        self.assertEqual(overview[self.gic["id"]]["last_status_change"]["to"], "interviewing")
        self.assertEqual(webapp.posting_detail(self.gic["id"])["raw_text"], "raw 2")

    def test_summary_script_counts_decisions(self):
        spec = importlib.util.spec_from_file_location("summarize_log", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        summary = module.summarize([
            {"event_type": "approval", "item_type": "add_posting", "decision": "approve", "time_to_decision_seconds": 10},
            {"event_type": "approval", "item_type": "add_posting", "decision": "edit", "time_to_decision_seconds": 30},
            {"event_type": "approval", "item_type": "add_posting", "decision": "link", "time_to_decision_seconds": 20},
            {"event_type": "approval", "item_type": "import_resume", "decision": "reject"},
            {"event_type": "claim_review", "decision": "disagree", "before": {"valid": True, "check": {"rating": "stretched"}}},
            {"event_type": "claim_review", "decision": "agree", "before": {"valid": True, "check": {"rating": "stretched"}}},
            {"event_type": "claim_review", "decision": "disagree", "before": {"valid": False, "check": None}},
            {"event_type": "direct_edit", "item_type": "experience", "decision": "delete"},
        ])
        posting_row = next(row for row in summary["approvals"] if row["item_type"] == "add_posting")
        self.assertEqual((posting_row["total"], posting_row["changed_by_user_rate"], posting_row["median_seconds"]), (3, 0.67, 20))
        stretched = next(row for row in summary["claim_reviews"] if row["checker_rating"] == "stretched")
        self.assertEqual((stretched["agree"], stretched["disagree"], stretched["disagree_rate"]), (1, 1, 0.5))
        self.assertEqual(next(r for r in summary["claim_reviews"] if r["checker_rating"] == "invalid")["disagree"], 1)
        self.assertEqual(summary["direct_edits"], {"experience delete": 1})


if __name__ == "__main__":
    unittest.main()
