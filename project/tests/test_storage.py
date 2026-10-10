"""Unit tests for the job-search storage layer. No model and no network."""

import json
import tempfile
import unittest
from unittest import mock
from datetime import date
from pathlib import Path

from storage import STATUSES, StorageError, Store


def sample_posting(**overrides):
    fields = {
        "title": "Data Analyst",
        "company": "DBS Bank",
        "location": "Singapore",
        "source": "linkedin",
        "requirements": [{"text": "SQL experience", "type": "required"}],
        "responsibilities": ["Build dashboards"],
    }
    fields.update(overrides)
    return fields


def sample_experience(**overrides):
    fields = {
        "source": "user-added",
        "category": "work",
        "text": "Built a Python dashboard for the operations team.",
        "skills": ["Python"],
        "organization": "Example Org",
        "dates": "2024",
    }
    fields.update(overrides)
    return fields


class StorageTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.store = Store(self.root)

    def tearDown(self):
        self._temporary.cleanup()

    def test_layout_creates_folders_and_keeps_edited_instructions(self):
        data = self.store.ensure_layout()
        self.assertEqual(data, self.store.root / "data")
        for relative in (
            "postings",
            "postings/raw",
            "experience",
            "experience/resume_source",
            "tracking",
            "config",
            "analyses",
        ):
            self.assertTrue((data / relative).is_dir(), relative)
        self.assertTrue((data / "experience" / "experience.json").is_file())
        self.assertTrue((data / "tracking" / "log.jsonl").is_file())
        self.assertTrue((data / "interaction_log.jsonl").is_file())
        agent_instructions = (data / "config" / "agent_instructions.md").read_text(encoding="utf-8")
        self.assertIn("find_duplicate_candidates", agent_instructions)
        (data / "config" / "agent_instructions.md").write_text("Keep it concise.\n", encoding="utf-8")
        original = self.store.read_analysis_instructions()
        self.assertIn("What fits", original)
        self.store.write_analysis_instructions("Put gaps first.\n")
        self.store.ensure_layout()
        self.assertEqual(self.store.read_analysis_instructions(), "Put gaps first.\n")
        self.assertEqual(
            (data / "config" / "agent_instructions.md").read_text(encoding="utf-8"),
            "Keep it concise.\n",
        )

    def test_job_ids_are_sequential_and_never_collide(self):
        ids = [self.store.create_posting(sample_posting(), f"raw {index}")["id"] for index in range(30)]
        self.assertEqual(ids[0], "JOB-0001")
        self.assertEqual(ids[-1], "JOB-0030")
        self.assertEqual(len(ids), len(set(ids)))
        again = Store(self.root).create_posting(sample_posting(title="Another role"), "raw")
        self.assertEqual(again["id"], "JOB-0031")

    def test_existing_posting_file_is_not_reused_when_the_counter_is_reset(self):
        self.store.create_posting(sample_posting(), "raw")
        leftover = self.store.postings_dir / "JOB-0009.json"
        leftover.write_text("{}\n", encoding="utf-8")
        self.store.ids_path.write_text(json.dumps({"next_job": 1, "next_exp": 1}) + "\n", encoding="utf-8")
        created = self.store.create_posting(sample_posting(title="Risk analyst"), "raw")
        self.assertEqual(created["id"], "JOB-0010")
        self.assertNotEqual(created["id"], "JOB-0001")

    def test_ids_keep_growing_past_four_digits(self):
        self.store.ensure_layout()
        self.store.ids_path.write_text(
            json.dumps({"next_job": 10000, "next_exp": 10000}) + "\n", encoding="utf-8"
        )
        posting = self.store.create_posting(sample_posting(), "raw")
        entry = self.store.add_experience(sample_experience())
        self.assertEqual(posting["id"], "JOB-10000")
        self.assertEqual(entry["id"], "EXP-10000")

    def test_experience_ids_are_independent_and_not_reused(self):
        self.store.create_posting(sample_posting(), "raw")
        first = self.store.add_experience(sample_experience())
        second = self.store.add_experience(sample_experience(text="Led a student hackathon team."))
        self.assertEqual(first["id"], "EXP-0001")
        self.assertEqual(second["id"], "EXP-0002")
        payload = self.store.read_experience()
        payload.append({
            "id": "EXP-0008",
            "source": "resume",
            "on_resume": True,
            "category": "skill",
            "text": "Uses SQL.",
            "skills": ["SQL"],
            "organization": None,
            "dates": None,
            "date_added": "2026-01-01",
        })
        self.store.experience_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        counters = json.loads(self.store.ids_path.read_text(encoding="utf-8"))
        counters["next_exp"] = 1
        self.store.ids_path.write_text(json.dumps(counters) + "\n", encoding="utf-8")
        created = self.store.add_experience(sample_experience(text="Taught a workshop."))
        self.assertEqual(created["id"], "EXP-0009")

    def test_create_posting_assigns_id_date_and_raw_text(self):
        raw = "Data Analyst at DBS\r\nRecruiter: Ada Lovelace\n"
        created = self.store.create_posting(
            sample_posting(date_posted="2026-09-01", salary="8000"),
            raw,
        )
        self.assertEqual(created["id"], "JOB-0001")
        self.assertEqual(created["date_added"], date.today().isoformat())
        self.assertEqual(created["date_posted"], "2026-09-01")
        self.assertFalse(created["date_posted_is_estimate"])
        self.assertEqual(created["status"], "saved")
        self.assertEqual(created["requirements"], [
            {"id": "REQ-1", "text": "SQL experience", "type": "required"},
        ])
        self.assertEqual(self.store.read_raw_text("JOB-0001"), raw)
        saved = (self.store.postings_dir / "JOB-0001.json").read_text(encoding="utf-8")
        self.assertNotIn("Lovelace", saved)
        events = self.store.read_tracking_log("JOB-0001")
        self.assertEqual(events[0]["event"], "added")
        self.assertEqual(events[0]["job_id"], "JOB-0001")
        self.assertIn("T", events[0]["timestamp"])

    def test_missing_posted_date_is_estimated_from_the_added_date(self):
        created = self.store.create_posting(sample_posting(), "Job text")
        self.assertEqual(created["date_posted"], created["date_added"])
        self.assertTrue(created["date_posted_is_estimate"])

    def test_caller_cannot_choose_a_posting_id(self):
        with self.assertRaises(StorageError):
            self.store.create_posting(sample_posting(id="JOB-9999"), "raw")
        self.assertEqual(self.store.list_postings(), [])
        created = self.store.create_posting(sample_posting(), "raw")
        self.assertEqual(created["id"], "JOB-0001")

    def test_requirement_ids_are_assigned_by_code(self):
        with self.assertRaises(StorageError):
            self.store.create_posting(
                sample_posting(requirements=[{"id": "REQ-99", "text": "SQL", "type": "required"}]),
                "raw",
            )
        saved = self.store.create_posting(
            sample_posting(requirements=[{"text": "SQL", "type": "required"}]),
            "raw",
        )
        self.assertEqual(saved["requirements"][0]["id"], "REQ-1")

    def test_invalid_posting_does_not_consume_an_id(self):
        with self.assertRaises(StorageError):
            self.store.create_posting(sample_posting(title="  "), "raw")
        with self.assertRaises(StorageError):
            self.store.create_posting(sample_posting(), "   ")
        created = self.store.create_posting(sample_posting(), "raw")
        self.assertEqual(created["id"], "JOB-0001")

    def test_failed_save_does_not_leave_a_record_or_reuse_the_id(self):
        real_append = self.store._append_jsonl

        def fail_once(path, payload):
            raise OSError("disk full")

        self.store._append_jsonl = fail_once
        with self.assertRaises(OSError):
            self.store.create_posting(sample_posting(), "raw")
        self.assertEqual(list(self.store.postings_dir.glob("JOB-*.json")), [])
        self.assertEqual(self.store.log_path.read_text(encoding="utf-8"), "")
        self.store._append_jsonl = real_append
        created = self.store.create_posting(sample_posting(), "raw")
        self.assertEqual(created["id"], "JOB-0002")
        self.assertFalse((self.store.postings_dir / "JOB-0001.json").exists())

    def test_list_and_search_are_case_insensitive(self):
        self.store.create_posting(sample_posting(department="Consumer Banking"), "one")
        self.store.create_posting(
            sample_posting(title="Finance intern", company="OCBC", status="applied"),
            "two",
        )
        listed = self.store.list_postings(status="Applied")
        self.assertEqual([item["id"] for item in listed], ["JOB-0002"])
        hits = self.store.search_postings(company="dbs", title_keywords="ANALYST")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "JOB-0001")
        self.assertEqual(hits[0]["matched"]["company"], "DBS Bank")
        self.assertEqual(hits[0]["matched"]["title"], "Data Analyst")
        consumer = self.store.search_postings(department="consumer")
        self.assertEqual([item["id"] for item in consumer], ["JOB-0001"])
        self.assertEqual(consumer[0]["matched"], {"department": "Consumer Banking"})
        self.assertEqual(self.store.search_postings(department="risk"), [])

    def test_status_change_updates_the_record_and_the_log(self):
        created = self.store.create_posting(sample_posting(), "raw")
        updated = self.store.update_status(created["id"], "Applied", note="Submitted the form")
        self.assertEqual(updated["status"], "applied")
        self.assertEqual(self.store.read_posting(created["id"])["status"], "applied")
        events = self.store.read_tracking_log(created["id"])
        self.assertEqual([event["event"] for event in events], ["added", "status_changed"])
        self.assertEqual(events[1]["details"], {
            "from": "saved",
            "to": "applied",
            "note": "Submitted the form",
        })

    def test_invalid_status_changes_nothing(self):
        created = self.store.create_posting(sample_posting(), "raw")
        before = self.store.log_path.read_text(encoding="utf-8")
        with self.assertRaises(StorageError):
            self.store.update_status(created["id"], "maybe")
        self.assertEqual(self.store.read_posting(created["id"])["status"], "saved")
        self.assertEqual(self.store.log_path.read_text(encoding="utf-8"), before)
        self.assertEqual(set(STATUSES), {
            "saved", "applied", "interviewing", "offer", "rejected", "withdrawn",
        })

    def test_status_change_rolls_back_when_the_log_cannot_be_written(self):
        created = self.store.create_posting(sample_posting(), "raw")
        before = self.store.log_path.read_text(encoding="utf-8")

        def fail_append(path, payload):
            raise OSError("disk full")

        self.store._append_jsonl = fail_append
        with self.assertRaises(OSError):
            self.store.update_status(created["id"], "interviewing", note="Phone screen")
        self.assertEqual(self.store.read_posting(created["id"])["status"], "saved")
        self.assertEqual(self.store.log_path.read_text(encoding="utf-8"), before)

    def test_unknown_job_does_not_append_a_log_event(self):
        self.store.ensure_layout()
        before = self.store.log_path.read_text(encoding="utf-8")
        with self.assertRaises(StorageError):
            self.store.update_status("JOB-0004", "applied")
        self.assertEqual(self.store.log_path.read_text(encoding="utf-8"), before)

    def test_note_updates_the_posting_and_keeps_history_in_the_log(self):
        created = self.store.create_posting(sample_posting(notes="first"), "raw")
        self.store.add_note(created["id"], "second note")
        self.assertEqual(self.store.read_posting(created["id"])["notes"], "second note")
        notes = [event for event in self.store.read_tracking_log(created["id"]) if event["event"] == "note"]
        self.assertEqual(notes[0]["details"]["note"], "second note")

    def test_experience_round_trip_update_and_delete(self):
        with self.assertRaises(StorageError):
            self.store.add_experience(sample_experience(id="EXP-0009"))
        created = self.store.add_experience(sample_experience())
        self.assertEqual(created["id"], "EXP-0001")
        self.assertFalse(created["on_resume"])
        self.assertEqual(created["date_added"], date.today().isoformat())
        updated = self.store.update_experience("EXP-0001", {"text": "Built a Python dashboard used by 10 people."})
        self.assertEqual(updated["id"], "EXP-0001")
        self.assertEqual(updated["text"], "Built a Python dashboard used by 10 people.")
        self.assertEqual(self.store.search_experience("python dashboard"), [updated])
        self.assertEqual(self.store.search_experience("   "), [])
        self.assertEqual(self.store.read_experience(source="resume"), [])
        self.assertEqual(len(self.store.read_experience(category="work")), 1)
        before = self.store.experience_path.read_text(encoding="utf-8")
        with self.assertRaises(StorageError):
            self.store.update_experience("EXP-0001", {"text": "  "})
        with self.assertRaises(StorageError):
            self.store.delete_experience("EXP-0099")
        self.assertEqual(self.store.experience_path.read_text(encoding="utf-8"), before)
        self.store.delete_experience("EXP-0001")
        self.assertEqual(self.store.read_experience(), [])

    def test_resume_entry_defaults_to_on_resume(self):
        created = self.store.add_experience(sample_experience(source="resume", category="education", text="BSc Computing."))
        self.assertTrue(created["on_resume"])
        self.assertEqual(created["source"], "resume")

    def test_rename_retries_when_a_sync_client_locks_the_file(self):
        self.store.ensure_layout()
        real_replace = Path.replace
        failures = {"left": 2}

        def flaky_replace(path, target):
            if failures["left"]:
                failures["left"] -= 1
                raise PermissionError("Access is denied")
            return real_replace(path, target)

        with mock.patch.object(Path, "replace", flaky_replace):
            saved = self.store.add_experience(sample_experience())
        self.assertEqual(saved["id"], "EXP-0001")
        self.assertEqual(self.store.read_experience()[0]["id"], "EXP-0001")
        self.assertEqual(list(self.store.config_dir.glob("*.tmp")), [])

    def test_resume_batch_reserves_ids_with_one_counter_write(self):
        self.store.ensure_layout()
        writes = []
        real_write = Store._write_json

        def counting_write(store, path, payload):
            writes.append(path.name)
            return real_write(store, path, payload)

        entries = [{"category": "skill", "text": f"Fact {n}", "skills": []} for n in range(5)]
        with mock.patch.object(Store, "_write_json", counting_write):
            saved = self.store.import_resume_data(entries, "resume.txt", b"source")
        self.assertEqual([e["id"] for e in saved["entries"]],
                         ["EXP-0001", "EXP-0002", "EXP-0003", "EXP-0004", "EXP-0005"])
        self.assertEqual(writes.count("ids.json"), 1)
        self.assertEqual(self.store.add_experience(sample_experience())["id"], "EXP-0006")

if __name__ == "__main__":
    unittest.main()
