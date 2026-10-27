"""The user's job requirements, posting checks against them, and next-step buttons."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import webapp
from preferences import (
    DealbreakerDraft, DealbreakerDrafts, check_job_type, check_preferences, check_salary,
    dealbreaker_checks, monthly_salary_range, normalize_preferences,
)
from storage import Store, StorageError

PREFS = {
    "min_monthly_salary": 9000, "hours": ["full-time"], "contract_types": ["permanent"],
    "work_arrangements": ["hybrid", "remote"], "dealbreakers": ["No shift work"],
}


class SalaryTests(unittest.TestCase):
    def test_reads_ranges_annual_amounts_and_unknowns(self):
        self.assertEqual(monthly_salary_range("$8,000to$11,000 Monthly")[:2], (8000, 11000))
        self.assertEqual(monthly_salary_range("S$120k per annum")[:2], (10000, 10000))
        self.assertEqual(monthly_salary_range("$96,000 - $120,000")[:2], (8000, 10000))
        self.assertIsNone(monthly_salary_range("null"))
        self.assertIsNone(monthly_salary_range("$25 per hour"))
        self.assertIsNone(monthly_salary_range("Competitive"))

    def test_flags_below_partly_and_met(self):
        self.assertEqual(check_salary({"salary": "$5,000 - $7,000"}, 9000)["status"], "not met")
        self.assertEqual(check_salary({"salary": "$8,000 to $11,000 Monthly"}, 9000)["status"], "partly met")
        self.assertEqual(check_salary({"salary": "$9,500 - $12,000"}, 9000)["status"], "met")
        self.assertEqual(check_salary({"salary": None}, 9000)["status"], "unknown")
        self.assertEqual(check_salary({"salary": "USD 12,000 monthly"}, 9000)["status"], "unknown")


class JobTypeTests(unittest.TestCase):
    def test_a_full_time_contract_fails_a_permanent_requirement(self):
        prefs = normalize_preferences(PREFS)
        self.assertEqual(check_job_type({"employment_type": "Contract, Full Time"}, prefs)["status"], "not met")
        self.assertEqual(check_job_type({"employment_type": "Permanent, Full Time"}, prefs)["status"], "met")
        self.assertEqual(check_job_type({"employment_type": "Full Time"}, prefs)["status"], "unknown")
        self.assertEqual(check_job_type({"employment_type": "null"}, prefs)["status"], "unknown")


class NormalizeTests(unittest.TestCase):
    def test_rejects_unknown_choices_and_bad_salary(self):
        with self.assertRaises(ValueError):
            normalize_preferences({**PREFS, "hours": ["weekends"]})
        with self.assertRaises(ValueError):
            normalize_preferences({**PREFS, "min_monthly_salary": "lots"})
        saved = normalize_preferences({"min_monthly_salary": "7,500", "dealbreakers": [" a ", "", "a"]})
        self.assertEqual(saved["min_monthly_salary"], 7500)
        self.assertEqual(saved["dealbreakers"], ["a"])


class DealbreakerTests(unittest.TestCase):
    def test_a_conflict_needs_a_quote_that_is_in_the_posting(self):
        raw = "Data Scientist\nRotating shifts, including weekends.\n"
        drafts = DealbreakerDrafts(checks=[
            DealbreakerDraft(dealbreaker="No shift work", status="conflict", quote="Rotating shifts", reason="Shift work."),
            DealbreakerDraft(dealbreaker="No sales targets", status="conflict", quote="Monthly sales quota", reason="Sales."),
        ])
        checks = dealbreaker_checks(drafts, ["No shift work", "No sales targets", "Singapore only"], raw)
        self.assertEqual([check["status"] for check in checks], ["not met", "unknown", "unknown"])
        self.assertIn("could not be found", checks[1]["detail"])

    def test_checker_failure_leaves_dealbreakers_unknown_and_code_checks_intact(self):
        async def failing(*_args):
            raise RuntimeError("offline")
        posting = {"salary": "$10,000", "employment_type": "Permanent, Full Time", "location": "Singapore"}
        checks = asyncio.run(check_preferences(posting, "Hybrid work.", normalize_preferences(PREFS), ask=failing))
        self.assertEqual({check["key"]: check["status"] for check in checks}, {
            "salary": "met", "job_type": "met", "work_arrangement": "met", "dealbreaker.0": "unknown",
        })


class StoreAndNextStepTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        patcher = mock.patch.object(webapp, "default_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_preferences_round_trip_and_reject_invalid_input(self):
        self.assertIsNone(self.store.read_job_preferences()["min_monthly_salary"])
        status, view = webapp.save_preferences({"preferences": PREFS})
        self.assertEqual(status, 200)
        self.assertEqual(view["preferences"]["contract_types"], ["permanent"])
        self.assertEqual(webapp.save_preferences({"preferences": {"hours": ["nights"]}})[0], 400)
        with self.assertRaises(StorageError):
            self.store.write_job_preferences("nope")

    def test_next_steps_follow_the_latest_turn_and_stop_at_three(self):
        posting = self.store.create_posting({
            "title": "Data Scientist", "company": "Example", "location": "Singapore", "source": "x",
            "requirements": [{"text": "SQL", "type": "required"}], "responsibilities": [],
        }, "Data Scientist at Example")
        job_id = posting["id"]
        saved = webapp.next_steps({"status": "completed", "outcomes": [{"kind": "posting", "ids": [job_id]}], "analyses": []})
        self.assertEqual([step["label"] for step in saved],
                         [f"Analyze my fit for {job_id}", f"Mark {job_id} as applied", "Add another posting"])
        self.assertEqual(saved[0]["message"], f"How well do I fit {job_id}?")

        self.store.update_status(job_id, "applied")
        analyzed = webapp.next_steps({"status": "completed", "outcomes": [],
                                      "analyses": [{"analysis_id": "A", "job": f"{job_id} (Data Scientist, Example)"}]})
        labels = [step["label"] for step in analyzed]
        self.assertNotIn(f"Mark {job_id} as applied", labels)
        self.assertIn("Set my job requirements", labels)
        self.assertLessEqual(len(analyzed), 3)

        self.store.write_job_preferences(PREFS)
        first = webapp.next_steps(None)
        self.assertEqual(first[0]["action"], "upload")
        self.assertNotIn("Set my job requirements", [step["label"] for step in first])
        self.assertLessEqual(len(first), 3)


if __name__ == "__main__":
    unittest.main()
