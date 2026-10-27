"""Browser workflow regressions that do not call a model or the network."""

import json
import http.client
import threading
import time
import unittest
from types import SimpleNamespace
from urllib.parse import quote

from unittest import mock

from webapp import JobSearchServer, approval_context
from uploads import resume_uploads


class FakeResult:
    """Match the SDK result surface used by the web app (there is no status)."""

    def __init__(self, output):
        self.final_output = output

    def to_input_list(self):
        return [{"role": "assistant", "content": self.final_output}]


class EditingRuntime:
    def __init__(self):
        self.calls = []
        self.decisions = []
        self.last_outcomes = []

    def run(self, messages, approval_handler=None):
        self.calls.append(messages)
        interruption = SimpleNamespace(
            tool_name="import_resume",
            arguments={
                "upload_id": "UPL-test",
                "sha256": "abc",
                "entries": [{"category": "skill", "text": "Uses SQL", "skills": ["SQL"]}],
            },
        )
        self.decisions.append(approval_handler(interruption))
        self.last_outcomes = [{"ok": True, "text": "Saved 1 resume entries (EXP-0001)", "ids": ["EXP-0001"]}]
        return FakeResult("Resume entries saved.")


class WebAppEditTests(unittest.TestCase):
    def tearDown(self):
        resume_uploads.clear()

    def test_resume_upload_endpoint_accepts_selected_bytes_without_a_path(self):
        server = JobSearchServer(EditingRuntime())
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            content = b"Participated in a university hackathon."
            connection.request("POST", "/api/resume-upload", body=content, headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(content)),
                "X-Chat-Token": server.token,
                "X-Resume-Filename": quote("my resume.txt", safe=""),
            })
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["filename"], "my resume.txt")
            upload = resume_uploads.get(payload["upload_id"])
            self.assertEqual(upload.text, "Participated in a university hackathon.")
            self.assertFalse(hasattr(upload, "file_path"))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_edit_returns_the_users_exact_version_in_one_turn(self):
        runtime = EditingRuntime()
        server = JobSearchServer(runtime)
        outcome = {}

        def run():
            outcome["value"] = server.run_turn("Import my resume")

        worker = threading.Thread(target=run)
        worker.start()
        try:
            first = self._wait_for_pending(server)
            edited_arguments = dict(first["arguments"])
            edited_arguments["entries"] = [
                {"category": "skill", "text": "Uses SQL and Python", "skills": ["SQL", "Python"]}
            ]
            self.assertTrue(server.approvals.submit("edit", edited_arguments))
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        finally:
            server.server_close()

        status, record = outcome["value"]
        self.assertEqual(status, 200)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["artifacts"], ["Saved 1 resume entries (EXP-0001)"])
        # No second model turn and no second approval: the edit itself is the decision.
        self.assertEqual(len(runtime.calls), 1)
        decision = runtime.decisions[0]
        self.assertEqual(decision["decision"], "edit")
        self.assertEqual(decision["arguments"], edited_arguments)
        self.assertIsInstance(decision["seconds"], float)

    def test_posting_card_carries_code_computed_duplicates(self):
        with mock.patch("webapp.duplicate_candidates", return_value=[{"id": "JOB-0001"}]) as found:
            context = approval_context("add_posting", {
                "company": "DBS Bank Ltd", "title": "Data Analyst",
                "requirements": ["required: SQL", "Preferred - Banking knowledge", {"text": "Python", "type": "required"}],
            })
        found.assert_called_once_with("DBS Bank Ltd", "Data Analyst")
        self.assertEqual(context["duplicates"], [{"id": "JOB-0001"}])
        # The card shows requirements exactly as code will save them.
        self.assertEqual(context["requirements"], [
            {"text": "SQL", "type": "required"},
            {"text": "Banking knowledge", "type": "preferred"},
            {"text": "Python", "type": "required"},
        ])
        self.assertIn("source", context["missing"])
        self.assertNotIn("title", context["missing"])

    def _wait_for_pending(self, server):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            pending = server.approvals.snapshot()
            if pending is not None:
                return pending
            time.sleep(0.01)
        self.fail("approval did not appear")



if __name__ == "__main__":
    unittest.main()
