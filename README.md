# ApplySmart: an LLM job-search assistant you can verify

ApplySmart is a local, agent-based job-search assistant. Paste a job posting and it extracts a structured record, checks how well your experience fits, flags duplicates, and tracks your applications. Everything is stored as plain JSON and Markdown files on your own machine.

Most LLM assistants ask you to trust their output. ApplySmart is designed so you don't have to: every claim it makes is tied to evidence that code checks, and nothing is written to your records until you approve it.

Built for CS619 Human-AI Interaction (Singapore Management University, MITB).

## Highlights

**Verifiable AI output**
- Every field extracted from a posting links back to an exact quote in your pasted text; code checks each quote, and items with no source are flagged ⚠.
- Fit analysis cites requirement and experience IDs (`REQ-…`, `EXP-…`). Code rejects invalid citations, restores requirements the model skipped, and decides resume suggestions itself.
- A second, independent model (ideally from a different model family) rates each claim as supported, stretched, or unsupported. An evidence panel lists flagged claims first, side by side with the source text.

**Agent memory with user approval**
- New experience, resume imports, edits, deletions, and changes to analysis style appear as approval cards showing the exact content to be saved: **Approve**, **Edit**, or **Reject**.
- The agent is told never to upgrade your wording ("participated" stays "participated", not "led"). Edits you make are saved verbatim without going back through the model.

**Human oversight at the right moment**
- Routine actions (reading, searching, status updates) run automatically; consequential writes always pause for you.
- Possible duplicate postings are found by code and labelled by a model, but you decide.
- Ambiguous requests get a short clarifying question with clickable options before the agent acts.
- Every human decision is logged with timing, and a script summarizes how often you changed or rejected the AI's proposals.

**Also:** resume import from PDF/DOCX/TXT/Markdown into nested sections; checks of each posting against your own salary, job-type, work-arrangement, and deal-breaker requirements; company-name matching ("D.B.S. Bank Pte Ltd" = "DBS Bank Ltd").

## Tech stack

- **Python 3.11+**, [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) (tool calls with human-in-the-loop interruptions, structured outputs, sub-agents)
- **Any model via OpenRouter**, with separate configurable models for chat, analysis, and claim checking
- **Vanilla JavaScript/HTML/CSS** browser UI served by a standard-library HTTP server
- **Local file storage** (JSON, JSONL, Markdown); no database or cloud service
- **92 unit and browser-workflow tests** (`unittest`)

## Quick start

```bash
cd project
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # then set OPENROUTER_API_KEY and OPENROUTER_MODEL
python agent.py --gui
```

Open the URL printed in the terminal. The [`project/samples/`](project/samples/) folder has a fictional resume and postings for a walkthrough; see [Demo with sample data](project/README.md#demo-with-sample-data).

## Repository layout

| Path | Contents |
|---|---|
| [`project/`](project/) | Application code, tests, prompts, and sample data. Its [README](project/README.md) covers configuration, usage, and the design in detail. |
| [`docs/design-plan.md`](docs/design-plan.md) | The original design brief: data model, phases, and acceptance checks. |
| [`docs/development-log.md`](docs/development-log.md) | What was built in each phase, bugs found in live testing, and how they were fixed. |

Personal data (`project/data/`) and credentials (`project/.env`) are git-ignored and never leave your machine except as model requests to OpenRouter.

## License

[MIT](LICENSE)
