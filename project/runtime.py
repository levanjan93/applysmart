"""Minimal project-owned OpenRouter and Agents SDK runtime."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import llm
from storage import default_store
from tools import (
    SAVE_FAILED, approval_details, approval_tool_name, execute_edited, find_pasted_text,
    give_answer, save_failed, set_user_messages,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
ALLOWED_SETTINGS = {
    "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
    # Optional. Fit analysis and claim checking default to OPENROUTER_MODEL.
    "OPENROUTER_ANALYSIS_MODEL", "OPENROUTER_CHECK_MODEL",
}


class ConfigurationError(ValueError):
    """Raised when project/.env is missing or malformed."""


@dataclass(frozen=True)
class OpenRouterConfig:
    api_key: str
    model: str
    analysis_model: str = ""
    check_model: str = ""


def load_openrouter_config(env_path: str | Path) -> OpenRouterConfig:
    """Load the two supported settings without interpolation or printing values."""
    path = Path(env_path)
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"Configuration file not found: {path}. Copy .env.example to .env and set both values."
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"Could not read configuration file: {path}.") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(contents.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(f"Invalid .env line {line_number}; expected NAME=value.")
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if name not in ALLOWED_SETTINGS:
            raise ConfigurationError(f"Unsupported .env setting on line {line_number}: {name or '(blank name)'}.")
        if name in values:
            raise ConfigurationError(f"Duplicate .env setting on line {line_number}: {name}.")
        value = raw_value.strip()
        if any(mark in value for mark in ("“", "”", "‘", "’")):
            raise ConfigurationError(
                f"{name} uses curly quotes. Use straight single or double quotes instead."
            )
        if value[:1] in {"'", '"'} and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1].strip()
        elif value[:1] in {"'", '"'} or value[-1:] in {"'", '"'}:
            raise ConfigurationError(f"{name} has an unmatched straight quote.")
        values[name] = value

    key = values.get("OPENROUTER_API_KEY", "").strip()
    model = values.get("OPENROUTER_MODEL", "").strip()
    if not key:
        raise ConfigurationError("OPENROUTER_API_KEY is missing or blank in project/.env.")
    if not model:
        raise ConfigurationError("OPENROUTER_MODEL is missing or blank in project/.env.")
    return OpenRouterConfig(
        api_key=key,
        model=model,
        analysis_model=values.get("OPENROUTER_ANALYSIS_MODEL", "").strip() or model,
        check_model=values.get("OPENROUTER_CHECK_MODEL", "").strip() or model,
    )


def describe_write_output(output) -> dict | None:
    """Summarize one tool return value as a save or a failed save, else None."""
    if isinstance(output, str) and output.startswith(SAVE_FAILED):
        return {"ok": False, "kind": "failed", "text": output.split(". Tell the user", 1)[0], "ids": []}
    if not isinstance(output, dict) or output.get("saved") is not True:
        return None
    if isinstance(output.get("posting"), dict):
        posting = output["posting"]
        text = f"Saved {posting['id']} ({posting['title']}, {posting['company']})"
        return {"ok": True, "kind": "posting", "text": text, "ids": [posting["id"]]}
    if isinstance(output.get("status_change"), dict):
        change = output["status_change"]
        text = f"{change['job_id']} ({change['title']}, {change['company']}): status {change['from']} → {change['to']}"
        return {"ok": True, "kind": "status", "text": text, "ids": [change["job_id"]]}
    if output.get("instructions_updated"):
        return {"ok": True, "kind": "instructions", "text": "Updated your analysis instructions", "ids": ["analysis_instructions"]}
    if isinstance(output.get("deleted"), dict):
        entry = output["deleted"]
        text = f"Deleted {entry['id']}: {entry['text']}"
        if output.get("detached_details"):
            text += f" (its details {', '.join(output['detached_details'])} are now standalone)"
        return {"ok": True, "kind": "deleted", "text": text, "ids": [entry["id"]]}
    if isinstance(output.get("entry"), dict):
        entry = output["entry"]
        return {"ok": True, "kind": "experience", "text": f"Saved {entry['id']}: {entry['text']}", "ids": [entry["id"]]}
    entries = output.get("entries")
    if isinstance(entries, list) and entries:
        span = entries[0]["id"] if len(entries) == 1 else f"{entries[0]['id']} to {entries[-1]['id']}"
        text = f"Saved {len(entries)} resume entries ({span})"
        return {"ok": True, "kind": "resume", "text": text, "ids": [entry["id"] for entry in entries]}
    return {"ok": True, "kind": "saved", "text": "Saved", "ids": []}


def collect_write_outcomes(result, outcomes: dict) -> None:
    """Add save outcomes from a run result, keyed by tool call ID to avoid repeats."""
    for item in getattr(result, "new_items", None) or []:
        if getattr(item, "type", None) != "tool_call_output_item":
            continue
        outcome = describe_write_output(item.output)
        if outcome is not None:
            outcomes[getattr(item, "call_id", None) or id(item)] = outcome


def message_text(item: dict) -> str:
    """Return the text of a chat input item whose content is a string or text parts."""
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def collect_analyses(result, found: dict) -> None:
    """Record analyses produced in a run so the GUI can offer the evidence review."""
    for item in getattr(result, "new_items", None) or []:
        output = getattr(item, "output", None)
        if getattr(item, "type", None) == "tool_call_output_item" and isinstance(output, dict) and output.get("analysis_id"):
            found[output["analysis_id"]] = {"analysis_id": output["analysis_id"], "job": output.get("job", "")}


def interruption_call_id(interruption) -> str:
    """Return the tool call ID that links an approval to its tool output."""
    raw = getattr(interruption, "raw_item", None)
    call_id = getattr(raw, "call_id", None)
    if call_id is None and isinstance(raw, dict):
        call_id = raw.get("call_id")
    return str(call_id or id(interruption))


def normalize_decision(decision) -> dict:
    """Accept a bare decision string or a decision dict from an approval handler."""
    if isinstance(decision, str):
        decision = {"decision": decision}
    if not isinstance(decision, dict) or decision.get("decision") not in {"approve", "reject", "edit", "link", "answer"}:
        return {"decision": "reject"}
    if decision["decision"] == "answer":
        answer = decision.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return {**decision, "decision": "reject"}
        decision = {**decision, "answer": answer.strip()}
    return decision


def apply_decision(state, interruption, decision: dict, outcomes: dict) -> dict:
    """Apply one human decision to the paused run and return its interaction record.

    An edit is saved here, directly from the user's edited arguments, using the
    same write code an approved call runs. The SDK call is then rejected with a
    message telling the model the user's version was saved, so the model never
    rewrites the user's edit.
    """
    tool_name = approval_tool_name(interruption)
    call_id = interruption_call_id(interruption)
    before = approval_details(interruption)
    choice = decision["decision"]
    after = None
    if tool_name == "ask_user":
        # A clarification, not a save: the answer goes back to the model as
        # the tool's result. Skipping tells the model to state its assumption.
        if choice == "answer":
            after = decision["answer"]
            give_answer(call_id, after)
            state.approve(interruption)
        else:
            choice = "skip"
            state.reject(
                interruption,
                rejection_message=(
                    "The user chose not to answer. Continue with the safest assumption, say plainly "
                    "what you assumed, and do not ask the same question again."
                ),
            )
        return {
            "event_type": "clarification",
            "item_type": "ask_user",
            "call_id": call_id,
            "decision": choice,
            "time_to_decision_seconds": decision.get("seconds"),
            "before": before,
            "after": after,
        }
    if choice == "approve":
        state.approve(interruption)
    elif choice in ("edit", "link"):
        # "link" saves the posting with related_postings set from the card.
        after = decision.get("arguments")
        try:
            output = execute_edited(tool_name, after)
        except Exception as exc:
            message = save_failed(None, exc)
            outcomes[call_id] = describe_write_output(message)
        else:
            outcomes[call_id] = describe_write_output(output) or {"ok": True, "text": "Saved", "ids": []}
            message = (
                f"The user {'linked' if choice == 'link' else 'edited'} this {tool_name} call and their version was saved "
                f"directly. Do not call {tool_name} again for it. Reply in one or two sentences naming the "
                "saved ID; do not ask the user to review or confirm it, and do not list its fields. Saved result: "
                + json.dumps(output, ensure_ascii=False)
            )
        state.reject(interruption, rejection_message=message)
    else:
        state.reject(
            interruption,
            rejection_message=(
                f"The user rejected this {tool_name} call. Nothing was saved. "
                "Do not retry it unless the user asks; ask what they would like changed."
            ),
        )
    return {
        "event_type": "approval",
        "item_type": tool_name,
        "call_id": call_id,
        "decision": choice,
        "time_to_decision_seconds": decision.get("seconds"),
        "before": before,
        "after": after,
    }


def finish_interaction(record: dict, outcomes: dict) -> dict:
    """Fill in saved record IDs from tool outcomes and drop the internal call ID."""
    outcome = outcomes.get(record.pop("call_id"))
    if record["event_type"] == "clarification":
        record["item_id"] = None
        return record
    record["item_id"] = ", ".join(outcome["ids"]) if outcome and outcome["ids"] else None
    record["saved"] = record["decision"] != "reject" and bool(outcome and outcome["ok"])
    if record["item_id"] is None and isinstance(record["before"], dict):
        record["item_id"] = record["before"].get("exp_id")
    return record


def terminal_decision(interruption) -> dict:
    """Ask for approval in the terminal. Editing is available in the browser only."""
    data = approval_details(interruption)
    tool_name = approval_tool_name(interruption)
    if tool_name == "ask_user":
        print(f"\nThe assistant asks: {data.get('question')}")
        print(f"Why: {data.get('reason')}")
        options = data.get("options") or []
        for number, option in enumerate(options, start=1):
            print(f"  {number}. {option}")
        started = time.monotonic()
        answer = input("Your answer (a number, your own words, or Enter to skip): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            answer = options[int(answer) - 1]
        return {
            "decision": "answer" if answer else "reject",
            "answer": answer,
            "seconds": round(time.monotonic() - started, 1),
        }
    print(f"\n{tool_name} requires your approval")
    print("Exact proposed arguments:")
    for key, value in data.items():
        print(f"  {key}: {json.dumps(value, ensure_ascii=False, indent=2)}")
    if tool_name == "add_posting":
        print("\nYour pasted text, saved verbatim as evidence:")
        print(find_pasted_text(data.get("title") or "", data.get("company") or "") or "(not found)")
    started = time.monotonic()
    answer = input("Approve this exact action? [y/N]: ").strip().casefold()
    return {
        "decision": "approve" if answer in {"y", "yes"} else "reject",
        "seconds": round(time.monotonic() - started, 1),
    }


class OpenRouterRuntime:
    """Own the OpenRouter client, configured agent, and approval-aware run loop."""

    def __init__(self, project_root: str | Path, agent_factory):
        from agents import OpenAIChatCompletionsModel, RunConfig
        from openai import AsyncOpenAI

        self.project_root = Path(project_root).resolve()
        self.config = load_openrouter_config(self.project_root / ".env")
        self.client = AsyncOpenAI(
            api_key=self.config.api_key,
            base_url=OPENROUTER_BASE_URL,
        )
        def chat_model(name: str):
            return OpenAIChatCompletionsModel(model=name, openai_client=self.client)

        llm.configure(
            chat_model(self.config.analysis_model),
            chat_model(self.config.check_model),
            {"analysis": self.config.analysis_model, "check": self.config.check_model},
        )
        self.agent = agent_factory(chat_model(self.config.model))
        self.run_config = RunConfig(tracing_disabled=True)
        # Every model call runs on this one thread. The shared HTTP client keeps
        # connections bound to the event loop that opened them; running a later
        # turn on another thread (each browser request has its own) reuses those
        # connections from a foreign loop and can hang forever.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-runtime")

    def run(self, messages: list[dict], approval_handler=None):
        """Run one conversation turn on the runtime's worker thread."""
        return self._worker.submit(self._run, messages, approval_handler).result()

    def _run(self, messages: list[dict], approval_handler=None):
        """Run one conversation turn and resume every SDK approval interruption."""
        from agents import Runner

        # What tools actually saved or failed to save, independent of what the
        # model says in its reply. Kept current so the web app can show saves
        # that happened before a turn failed.
        self.outcomes = outcomes = {}
        self.analyses = {}
        self.clarifications = []
        set_user_messages([message_text(item) for item in messages if item.get("role") == "user"])
        result = Runner.run_sync(
            self.agent, messages, max_turns=12, run_config=self.run_config,
        )
        collect_write_outcomes(result, outcomes)
        collect_analyses(result, self.analyses)
        handler = approval_handler or terminal_decision
        while result.interruptions:
            state = result.to_state()
            records = [
                apply_decision(state, interruption, normalize_decision(handler(interruption)), outcomes)
                for interruption in result.interruptions
            ]
            self.clarifications += [
                {"question": (record["before"] or {}).get("question"), "answer": record["after"]}
                for record in records if record["event_type"] == "clarification"
            ]
            try:
                result = Runner.run_sync(
                    self.agent, state, max_turns=12, run_config=self.run_config,
                )
                collect_write_outcomes(result, outcomes)
                collect_analyses(result, self.analyses)
            finally:
                # Log every decision, even if the resumed run fails afterwards.
                store = default_store()
                for record in records:
                    store.append_interaction(finish_interaction(record, outcomes))
        return result

    @property
    def last_analyses(self) -> list[dict]:
        """Analyses produced in the most recent turn."""
        return list(getattr(self, "analyses", {}).values())

    @property
    def last_clarifications(self) -> list[dict]:
        """Questions the assistant asked in the most recent turn, with the answers."""
        return list(getattr(self, "clarifications", []))

    @property
    def last_outcomes(self) -> list[dict]:
        """Save outcomes from the most recent turn, in the order they happened."""
        return list(getattr(self, "outcomes", {}).values())

    def close(self) -> None:
        """Close the HTTP client on the worker thread's loop, then stop the worker."""
        future = self._worker.submit(llm.run_blocking, self.client.close())
        try:
            future.result(timeout=10)
        except Exception:
            # A turn may still be waiting for an approval at shutdown; do not hang.
            pass
        self._worker.shutdown(wait=False, cancel_futures=True)
