"""Phase 3 tests for local resume parsing and approved experience storage."""

import asyncio
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from storage import StorageError, Store
from agent import TOOLS
import resume
from runtime import apply_decision, describe_write_output
import tools as tools_module
from uploads import ResumeUploadStore, extract_resume_bytes, resume_uploads


class Phase3Tests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.store = Store(self.root)

    def tearDown(self):
        self._temporary.cleanup()

    def test_text_resume_is_read_without_saving(self):
        resume = self.root / "resume.txt"
        resume.write_text("Helped build a Python dashboard.\n", encoding="utf-8")
        uploads = ResumeUploadStore()
        result = uploads.add(resume.name, resume.read_bytes())
        self.assertEqual(result.text, "Helped build a Python dashboard.")
        self.assertEqual(len(result.sha256), 64)
        self.assertEqual(self.store.read_experience(), [])

    def test_docx_text_is_read_locally(self):
        resume = self.root / "resume.docx"
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Participated in a hackathon.</w:t></w:r></w:p></w:body>'
            '</w:document>'
        )
        with zipfile.ZipFile(resume, "w") as archive:
            archive.writestr("word/document.xml", document)
        self.assertEqual(
            extract_resume_bytes(resume.name, resume.read_bytes()),
            "Participated in a hackathon.",
        )

    def test_reviewed_resume_entries_are_atomic_batch_with_code_ids(self):
        resume = self.root / "resume.txt"
        resume.write_text("Resume source", encoding="utf-8")
        saved = self.store.import_resume_data([
            {
                "category": "work",
                "text": "Helped build a Python dashboard.",
                "skills": ["Python"],
                "organization": "Example Org",
                "dates": "2025",
            },
            {
                "category": "education",
                "text": "Completed a Bachelor of Computing.",
                "skills": [],
                "organization": "Example University",
                "dates": "2024",
            },
        ], resume.name, resume.read_bytes())
        self.assertEqual([entry["id"] for entry in saved["entries"]], ["EXP-0001", "EXP-0002"])
        self.assertTrue(all(entry["source"] == "resume" for entry in saved["entries"]))
        self.assertTrue(all(entry["on_resume"] for entry in saved["entries"]))
        self.assertEqual(saved["entries"][0]["text"], "Helped build a Python dashboard.")
        self.assertTrue((self.store.resume_dir / saved["source_file"]).is_file())

    def test_invalid_batch_saves_nothing(self):
        resume = self.root / "resume.txt"
        resume.write_text("Resume source", encoding="utf-8")
        with self.assertRaises(StorageError):
            self.store.import_resume_data([
                {"category": "work", "text": "Valid fact", "skills": []},
                {"category": "work", "text": "  ", "skills": []},
            ], resume.name, resume.read_bytes())
        self.assertEqual(self.store.read_experience(), [])
        self.assertEqual(list(self.store.resume_dir.iterdir()), [])

    def test_agent_has_upload_tools_and_no_path_reading_tool(self):
        tools = {tool.name: tool for tool in TOOLS}
        self.assertIn("parse_resume", tools)
        self.assertNotIn("read_resume_file", tools)
        for name in ("parse_resume", "import_resume"):
            schema = tools[name].params_json_schema
            self.assertNotIn("file_path", str(schema))
            # The chat model passes only the upload ID; it never relays entries.
            self.assertEqual(sorted(schema["properties"]), ["upload_id"])


SAMPLE_DRAFT = {
    "items": [
        {"category": "work", "text": "Data Analyst at Example Bank", "organization": "Example Bank",
         "dates": "2022 - present", "skills": [],
         "details": [{"text": "Built weekly SQL reports.", "skills": ["SQL"]},
                     {"text": "Helped migrate dashboards to Tableau.", "skills": ["Tableau"]}]},
        {"category": "education", "text": "BSc Statistics, Example University", "organization": "Example University",
         "dates": "2021", "skills": [], "details": []},
    ],
    "skills": ["Python", "SQL"],
}


class NestedResumeTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._temporary.name))
        patcher = mock.patch.object(tools_module, "default_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(resume_uploads.clear)

    def tearDown(self):
        self._temporary.cleanup()

    def test_flattened_draft_keeps_categories_and_links_details_to_their_item(self):
        saved = self.store.import_resume_data(resume.flatten_draft(SAMPLE_DRAFT), "cv.txt", b"cv")["entries"]
        by_text = {entry["text"]: entry for entry in saved}
        role = by_text["Data Analyst at Example Bank"]
        self.assertEqual(role["category"], "work")
        self.assertIsNone(role["parent_id"])
        detail = by_text["Built weekly SQL reports."]
        self.assertEqual((detail["category"], detail["parent_id"], detail["organization"], detail["dates"]),
                         ("work", role["id"], "Example Bank", "2022 - present"))
        self.assertEqual(by_text["BSc Statistics, Example University"]["category"], "education")
        self.assertEqual([e["category"] for e in saved if e["text"] in ("Python", "SQL")], ["skill", "skill"])
        self.assertEqual(len(saved), 6)

    def test_deleting_an_item_keeps_its_details_as_standalone_entries(self):
        saved = self.store.import_resume_data(resume.flatten_draft(SAMPLE_DRAFT), "cv.txt", b"cv")["entries"]
        self.store.delete_experience(saved[0]["id"])
        remaining = {entry["text"]: entry for entry in self.store.read_experience()}
        self.assertIsNone(remaining["Built weekly SQL reports."]["parent_id"])

    def test_parent_must_exist_and_be_top_level(self):
        saved = self.store.import_resume_data(resume.flatten_draft(SAMPLE_DRAFT), "cv.txt", b"cv")["entries"]
        detail_id = saved[1]["id"]
        with self.assertRaisesRegex(StorageError, "cannot have details"):
            self.store.add_experience({"source": "user-added", "category": "work", "text": "x", "parent_id": detail_id})
        with self.assertRaisesRegex(StorageError, "does not exist"):
            self.store.add_experience({"source": "user-added", "category": "work", "text": "x", "parent_id": "EXP-0999"})

    def test_normalize_draft_drops_blank_lines_and_rejects_headless_details(self):
        draft = resume.normalize_draft({
            "items": [{"category": "made-up", "text": " Volunteer ", "details": ["  ", "Taught coding"], "skills": "a, b"}],
            "skills": ["Python", " ", "Python"],
        })
        self.assertEqual(draft["items"][0]["category"], "other")
        self.assertEqual(draft["items"][0]["details"], [{"text": "Taught coding", "skills": []}])
        self.assertEqual(draft["items"][0]["skills"], ["a", "b"])
        self.assertEqual(draft["skills"], ["Python"])
        with self.assertRaisesRegex(ValueError, "headline"):
            resume.normalize_draft({"items": [{"category": "work", "text": "", "details": ["Did a thing"]}], "skills": []})

    def test_parse_then_import_saves_the_parsed_draft_and_an_edit_saves_the_users_version(self):
        upload = resume_uploads.add("cv.txt", b"Data Analyst at Example Bank")

        async def fake_ask(kind, instructions_file, payload, output_type):
            self.assertEqual((kind, instructions_file), ("analysis", "resume_parser_instructions.md"))
            self.assertEqual(payload, "Data Analyst at Example Bank")
            return output_type.model_validate(SAMPLE_DRAFT)

        with mock.patch.object(resume, "ask", fake_ask):
            draft = asyncio.run(resume.parse_resume_text(upload.text))
        resume_uploads.set_draft(upload.upload_id, draft)
        self.assertEqual(resume.draft_summary(draft)["total_entries"], 6)

        edited = dict(SAMPLE_DRAFT, items=[dict(SAMPLE_DRAFT["items"][0], details=[{"text": "Built weekly SQL reports.", "skills": ["SQL"]}])])
        result = tools_module.import_resume_action(upload.upload_id, draft=edited)
        self.assertEqual([e["text"] for e in result["entries"]],
                         ["Data Analyst at Example Bank", "Built weekly SQL reports.", "Python", "SQL"])
        # The upload and its draft are released after saving.
        with self.assertRaises(ValueError):
            resume_uploads.get(upload.upload_id)


    def test_delete_tool_needs_approval_and_detaches_details(self):
        tools = {tool.name: tool for tool in TOOLS}
        self.assertTrue(tools["delete_experience"].needs_approval)
        saved = self.store.import_resume_data(resume.flatten_draft(SAMPLE_DRAFT), "cv.txt", b"cv")["entries"]
        result = tools_module.delete_experience_action(saved[0]["id"])
        self.assertEqual(result["deleted"]["text"], "Data Analyst at Example Bank")
        self.assertEqual(result["detached_details"], [saved[1]["id"], saved[2]["id"]])
        self.assertNotIn(saved[0]["id"], [e["id"] for e in self.store.read_experience()])
        self.assertEqual(describe_write_output(result)["ids"], [saved[0]["id"]])
        self.assertTrue(describe_write_output(result)["text"].startswith(f"Deleted {saved[0]['id']}"))

    def test_rejected_delete_removes_nothing(self):
        saved = self.store.add_experience({"source": "user-added", "category": "project", "text": "Added by mistake."})
        state = SimpleNamespace(approve=mock.Mock(), reject=mock.Mock())
        interruption = SimpleNamespace(tool_name="delete_experience", raw_item=SimpleNamespace(call_id="c1"),
                                       arguments={"exp_id": saved["id"]})
        apply_decision(state, interruption, {"decision": "reject"}, {})
        self.assertEqual([e["id"] for e in self.store.read_experience()], [saved["id"]])
        with self.assertRaisesRegex(ValueError, "No experience entry"):
            tools_module.delete_experience_action("EXP-0999")

if __name__ == "__main__":
    unittest.main()
