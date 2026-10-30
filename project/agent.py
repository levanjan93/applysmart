"""Run the local job-search assistant from the project directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from agents import Agent

from tools import (
    add_posting, ask_user, delete_experience, propose_analysis_instructions, read_job_preferences, read_tracking_log, update_status, analyze_fit, check_claims, find_duplicate_candidates, list_postings,
    import_resume, propose_experience, read_experience, read_posting,
    parse_resume, search_experience, search_postings, update_experience,
)
from storage import default_store
from runtime import OpenRouterRuntime

PROJECT_ROOT = Path(__file__).resolve().parent
INSTRUCTIONS_FILE = PROJECT_ROOT / "data" / "config" / "agent_instructions.md"

TOOLS = [
    list_postings, read_posting, search_postings,
    find_duplicate_candidates, add_posting,
    read_experience, search_experience, parse_resume, import_resume,
    propose_experience, update_experience, delete_experience,
    analyze_fit, check_claims,
    update_status, read_tracking_log, propose_analysis_instructions,
    read_job_preferences, ask_user,
]
def create_agent(model):
    """Create the job-search assistant with the selected OpenRouter model."""
    default_store().ensure_layout()
    instructions = INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    return Agent(
        name="Job-search assistant",
        model=model,
        instructions=instructions,
        tools=TOOLS,
    )


def main() -> int:
    """Open project/.env settings and run a terminal or browser conversation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true", help="open the local approval-enabled browser chat")
    args = parser.parse_args()
    runtime = OpenRouterRuntime(PROJECT_ROOT, create_agent)
    try:
        if args.gui:
            from webapp import serve
            serve(runtime)
            return 0
        history = []
        print("ApplySmart job-search assistant. Type /exit to finish.")
        while True:
            try:
                prompt = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSession ended.")
                break
            if prompt.casefold() in {"/exit", "/quit"}:
                break
            if not prompt:
                continue
            result = runtime.run(history + [{"role": "user", "content": prompt}])
            history = result.to_input_list()
            print("\nAssistant> " + str(result.final_output or "No answer returned."))
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
