"""Structured model calls made by code (fit analysis, claim checks, resume parsing).

These calls bypass the chat agent: code sends JSON in and receives a typed
result back, so structure such as categories and cited IDs never depends on
how the chat model happens to format its tool arguments.
"""

from __future__ import annotations

import asyncio
import json
import warnings
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"

# Configured by the runtime: "analysis" (also used for resume parsing) and a
# separate "check" model for independent claim checking.
_models: dict = {"analysis": None, "check": None, "names": {}}


def configure(analysis_model, check_model, names: dict) -> None:
    """Set the SDK model objects used for structured calls."""
    _models.update(analysis=analysis_model, check=check_model, names=dict(names))


def model_names() -> dict:
    """Return the configured model IDs, for recording in saved results."""
    return dict(_models["names"])


def run_blocking(coroutine):
    """Run a coroutine on this thread's idle event loop and wait for the result.

    The approval handler runs between SDK ``run_sync`` calls on the same thread,
    so that thread's default loop exists and is idle; reusing it keeps the
    shared HTTP client on one loop.
    """
    policy = asyncio.get_event_loop_policy()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            loop = policy.get_event_loop()
    except RuntimeError:
        loop = policy.new_event_loop()
        policy.set_event_loop(loop)
    if loop.is_closed():
        loop = policy.new_event_loop()
        policy.set_event_loop(loop)
    if loop.is_running():
        coroutine.close()
        raise RuntimeError("run_blocking cannot be used inside a running event loop.")
    return loop.run_until_complete(coroutine)


async def ask(kind: str, instructions_file: str, payload, output_type):
    """Run one structured SDK call with the given model and instruction file."""
    from agents import Agent, RunConfig, Runner

    model = _models.get(kind)
    if model is None:
        raise RuntimeError("Structured-call models are not configured.")
    agent = Agent(
        name=f"structured-{kind}",
        model=model,
        instructions=(TEMPLATES / instructions_file).read_text(encoding="utf-8"),
        output_type=output_type,
    )
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    result = await Runner.run(agent, text, max_turns=1, run_config=RunConfig(tracing_disabled=True))
    return result.final_output
