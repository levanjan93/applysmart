"""Configuration checks for the project-owned OpenRouter runtime."""

import tempfile
import unittest
from pathlib import Path

from types import SimpleNamespace

from unittest import mock

import tools
from runtime import (
    ConfigurationError, apply_decision, collect_write_outcomes, finish_interaction,
    load_openrouter_config,
)
from storage import Store
from tools import save_failed


class OpenRouterConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temporary.name) / ".env"

    def tearDown(self):
        self.temporary.cleanup()

    def test_accepts_any_non_empty_model_id_and_strips_straight_quotes(self):
        self.env_path.write_text(
            'OPENROUTER_API_KEY="test-key-value"\n'
            "OPENROUTER_MODEL='vendor/arbitrary-model:free'\n",
            encoding="utf-8",
        )
        config = load_openrouter_config(self.env_path)
        self.assertEqual(config.api_key, "test-key-value")
        self.assertEqual(config.model, "vendor/arbitrary-model:free")

    def test_rejects_missing_key(self):
        self.env_path.write_text("OPENROUTER_MODEL=vendor/model\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "OPENROUTER_API_KEY"):
            load_openrouter_config(self.env_path)

    def test_rejects_curly_quotes_without_showing_the_value(self):
        self.env_path.write_text(
            "OPENROUTER_API_KEY=“secret-test-value”\nOPENROUTER_MODEL=vendor/model\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ConfigurationError, "curly quotes") as error:
            load_openrouter_config(self.env_path)
        self.assertNotIn("secret-test-value", str(error.exception))



class WriteOutcomeTests(unittest.TestCase):
    def test_saves_and_failures_come_from_tool_outputs_not_the_reply(self):
        def output(call_id, value):
            return SimpleNamespace(type="tool_call_output_item", call_id=call_id, output=value)

        result = SimpleNamespace(new_items=[
            SimpleNamespace(type="message_output_item"),
            output("a", [{"id": "JOB-0001"}]),
            output("b", {"saved": True, "entries": [{"id": "EXP-0001"}, {"id": "EXP-0003"}]}),
            output("c", save_failed(None, PermissionError("Access is denied"))),
        ])
        outcomes = {}
        collect_write_outcomes(result, outcomes)
        collect_write_outcomes(result, outcomes)
        self.assertEqual(list(outcomes.values()), [
            {"ok": True, "kind": "resume", "text": "Saved 2 resume entries (EXP-0001 to EXP-0003)", "ids": ["EXP-0001", "EXP-0003"]},
            {"ok": False, "kind": "failed", "text": "SAVE FAILED: nothing was saved (PermissionError: Access is denied)", "ids": []},
        ])

    def test_failed_validation_names_the_missing_fields(self):
        # The SDK reports only "Invalid JSON input" when a required field is missing.
        import asyncio
        import json
        from agents.tool_context import ToolContext

        arguments = json.dumps({
            "source": "x", "company": "Grab", "location": "", "requirements": [], "responsibilities": [],
        })
        ctx = ToolContext(context=None, tool_name="add_posting", tool_call_id="c1", tool_arguments=arguments)
        output = asyncio.run(tools.add_posting.on_invoke_tool(ctx, arguments))
        self.assertTrue(output.startswith("SAVE FAILED"))
        self.assertIn("missing required fields: title", output)


class FakeState:
    def __init__(self):
        self.approved = []
        self.rejected = []

    def approve(self, item):
        self.approved.append(item)

    def reject(self, item, rejection_message=None):
        self.rejected.append((item, rejection_message))


def experience_interruption(text):
    return SimpleNamespace(
        tool_name="propose_experience",
        raw_item=SimpleNamespace(call_id="call-1"),
        arguments={"text": text, "category": "project", "skills": []},
    )


class ApprovalDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        patcher = mock.patch.object(tools, "default_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def test_edit_saves_the_users_exact_text_without_the_model(self):
        state, outcomes = FakeState(), {}
        interruption = experience_interruption("Led my hackathon team.")
        edited = {"text": "Participated in a hackathon team.", "category": "project", "skills": []}
        record = apply_decision(state, interruption, {"decision": "edit", "arguments": edited, "seconds": 4.2}, outcomes)

        saved = self.store.read_experience()
        self.assertEqual([entry["text"] for entry in saved], ["Participated in a hackathon team."])
        self.assertEqual(state.approved, [])
        self.assertIn("their version was saved", state.rejected[0][1])
        logged = finish_interaction(record, outcomes)
        self.assertEqual(logged["decision"], "edit")
        self.assertEqual(logged["item_id"], saved[0]["id"])
        self.assertTrue(logged["saved"])
        self.assertEqual(logged["before"]["text"], "Led my hackathon team.")
        self.assertEqual(logged["after"]["text"], "Participated in a hackathon team.")
        self.assertEqual(logged["time_to_decision_seconds"], 4.2)

    def test_invalid_edit_is_reported_and_saves_nothing(self):
        state, outcomes = FakeState(), {}
        bad = {"text": "  ", "category": "project", "skills": []}
        record = apply_decision(state, experience_interruption("Fact"), {"decision": "edit", "arguments": bad}, outcomes)
        self.assertEqual(self.store.read_experience(), [])
        self.assertTrue(state.rejected[0][1].startswith("SAVE FAILED"))
        self.assertFalse(finish_interaction(record, outcomes)["saved"])

    def test_reject_saves_nothing_and_is_logged(self):
        state, outcomes = FakeState(), {}
        record = apply_decision(state, experience_interruption("Fact"), {"decision": "reject"}, outcomes)
        self.assertEqual(self.store.read_experience(), [])
        self.assertIn("rejected", state.rejected[0][1])
        logged = finish_interaction(record, outcomes)
        self.assertEqual((logged["decision"], logged["saved"], logged["item_id"]), ("reject", False, None))


class WorkerThreadTests(unittest.TestCase):
    def test_turns_from_different_threads_run_on_one_worker_thread(self):
        # Browser requests arrive on different threads; the SDK client must
        # always be used from the same thread and event loop, or turns can hang.
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from runtime import OpenRouterRuntime

        runtime = object.__new__(OpenRouterRuntime)
        runtime._worker = ThreadPoolExecutor(max_workers=1)
        seen = []
        runtime._run = lambda messages, handler=None: seen.append(threading.get_ident()) or messages
        callers = [threading.Thread(target=runtime.run, args=([{"role": "user", "content": str(n)}],)) for n in range(3)]
        for caller in callers:
            caller.start()
            caller.join(5)
        runtime._worker.shutdown()
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 1)
        self.assertNotIn(threading.get_ident(), seen)

if __name__ == "__main__":
    unittest.main()
