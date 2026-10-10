# Job Search Assistant

A local job-search assistant for saving job postings, organizing experience, checking fit against evidence, and tracking applications. Personal records stay as JSON and Markdown files under `data/`.

The project design is described in [PLAN.md](PLAN.md). The three course ideas it is graded on (verifying AI output, agent memory with user approval, and human oversight at the right moment) are explained under [The three design ideas](#the-three-design-ideas).

## Configuration

Use Python 3.11 or newer. Create and use the virtual environment at `%LOCALAPPDATA%\venvs\cs619-project`; do not create one inside this project folder.

From the `project/` directory in PowerShell:

```powershell
py -3 -m venv "$env:LOCALAPPDATA\venvs\cs619-project"
& "$env:LOCALAPPDATA\venvs\cs619-project\Scripts\python.exe" -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `project/.env` and set both values:

```dotenv
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=provider/model-id
```

Use any non-empty model ID available to your OpenRouter account. The app sends requests to OpenRouter's OpenAI-compatible API.

Two optional settings choose separate models for fit analysis and for the independent claim check. Both default to `OPENROUTER_MODEL`. A checker from a different model family is less likely to share the analyst's blind spots:

```dotenv
OPENROUTER_ANALYSIS_MODEL=anthropic/claude-sonnet-5.5
OPENROUTER_CHECK_MODEL=openai/gpt-6-sol
```

The app reads only these four settings from `.env`; it does not interpolate variables or print the key. Keep `.env` private and out of version control.

The SDK dependencies are in `requirements.txt`. The Agents SDK is pinned to `openai-agents==0.22.2`.

## Start the app

Open PowerShell. The terminal often starts in the parent `lab` folder. `agent.py` is in `project`, so enter that folder first, then activate the environment for this window:

```powershell
cd project
& "$env:LOCALAPPDATA\venvs\cs619-project\Scripts\Activate.ps1"
python agent.py --gui
```

The prompt should begin with `(cs619-project)`. Open the local URL printed in the terminal. Stop the server with Ctrl+C.

Activation applies only to that terminal. In a new window, run the `Activate.ps1` line again. If PowerShell says it cannot open `lab\agent.py`, the shell is still in the parent folder. Run `cd project` and start the command again.

The same activated window can start a terminal conversation instead of the browser:

```powershell
python agent.py
```

Stop that mode with `/exit`.

## Using the app

The page has three tabs.

- **Chat:** talk to the assistant, paste job postings, upload a resume with the **Resume upload** control (PDF, DOCX, TXT, or Markdown), and review approval cards and fit analyses. A dot on the tab means an approval is waiting.
- **Postings:** a table of saved postings (ID, title, company, date added, status, date applied, number of analyses) with status and text filters. Click a row to see its fields, requirements, tracking history, past analyses, and your pasted text, or to change its status.
- **Experience:** every saved fact, grouped by resume section, with bullet points nested under their role, degree, or project. Filter by source and by whether a fact is on your resume, then edit or delete entries directly.

Things to ask in chat:

- "Save this posting" with the posting text pasted in.
- "How well do I fit JOB-0001?"
- "I applied to JOB-0002." The status updates directly and is added to the tracking log.
- "Have I applied to Harbour Bank before?" Company-name variants match, and near-misses are mentioned.
- "I also led my hackathon team last year." The assistant proposes a new experience entry for approval.
- "Delete EXP-0012, I added it by mistake."

The agent has no tool that accepts a filesystem path. A selected resume stays in server memory until you approve or reject its import. PDF import reads embedded text, so scanned image-only PDFs need OCR or conversion first.

## The three design ideas

| Idea | Course week | What you see |
|---|---|---|
| **A. Verifying AI output** | Week 2 | Every fit claim cites REQ and EXP IDs that code has checked; an independent checker rates each claim; the evidence panel puts flagged claims first and shows each requirement beside its evidence. |
| **B. Agent memory with user approval** | Week 4 | New experience, resume imports, edits, deletions, and changes to your analysis style are proposed on cards and saved only after you approve; your wording is never upgraded. |
| **C. Human oversight at the right moment** | Week 4 | Cards appear only for consequential saves, show the exact content, and support Approve, Edit, and Reject; possible duplicates are labelled for you to decide; clear status updates need no card. |

### A. Verifying AI output

The analyst model rates each requirement as a strong match, partial match, or gap, and must cite the REQ and EXP IDs it relies on. Code then does the checking that a model cannot be trusted with:

- **Valid IDs:** every cited ID must exist; a claim citing a missing entry is marked invalid and shown first.
- **Complete coverage:** a requirement the model skipped is added back as a flagged gap.
- **Resume suggestions:** "Add to your resume" is decided by code. It lists only facts you added yourself that are not on your resume yet and that support a valid match.
- **No invented experience:** experience missing from the database becomes a question ("Have you used Tableau?"), never a suggestion.

A second model, with separate instructions and ideally a different model family, re-reads each requirement beside the exact text of the cited entries. It rates the support as **supported**, **stretched**, or **unsupported**, with a one-line reason.

**Review evidence** opens a panel ordered so that invalid, unsupported, and stretched claims come first. Opening a claim highlights the matching line in your own pasted posting, saved verbatim, and shows the cited experience entries beside it. **I agree** and **I disagree** are recorded for the evaluation.

### B. Agent memory with user approval

- Experience mentioned in chat becomes a proposal card with the exact text to be stored. The assistant is instructed not to turn "participated" into "led".
- **Resume imports:** a structured parser sorts a resume into its own sections, with bullets nested under each role. You can edit, remove, or add lines before anything is saved.
- **Updates and deletions** each show their own card: a before/after view for an update, and the exact entry for a deletion.
- **Analysis style:** if you correct the analysis style repeatedly, the assistant can propose an edit to `data/config/analysis_instructions.md`. The card shows a line-by-line before/after diff.

### C. Human oversight at the right moment

| Action | Policy |
|---|---|
| Reading, searching, analyzing | Automatic |
| Saving a posting | Card with the structured record, numbered requirements, your pasted text, and any duplicate candidates |
| Saving resume entries or experience | Card with every line, editable before saving |
| Updating or deleting experience | Card with before/after, or with the entry to delete |
| Changing application status | Automatic when you name the job; the assistant asks if it is ambiguous |
| Treating a posting as a duplicate | Never automatic: code finds candidates, a model labels each one, and you choose **Save as new**, **Save as new and link**, or **Skip** |
| Sending anything off your machine | Not implemented |

**Edit** saves your version exactly as you leave it, without passing it back through the model. Each turn ends with a green **Changes saved** or red **Not saved** card built from what the tools actually did, not from the assistant's reply.

## Demo with sample data

The `samples/` folder holds fake data:

1. Upload `samples/resume_alex_tan.txt` and approve or edit the parsed resume.
2. Paste `samples/posting_1_linkedin_data_analyst.txt`. On the card, check that the recruiter is missing from the structured record and that your full paste is kept as evidence.
3. Paste `samples/posting_2_careers_site_duplicate.txt`. The card flags the first posting as a likely duplicate and offers **Save as new and link**.
4. Paste `samples/posting_3_analytics_lead.txt`, then ask "How well do I fit JOB-0003?". The role asks for people management, so look for stretched claims and questions in **Review evidence**.
5. Say "I applied to JOB-0001", then ask "Have I applied to Harbour Bank before?".
6. Open the Postings and Experience tabs.
7. Run `python scripts/summarize_log.py`.

## Evaluation log

Every human decision is appended to `data/interaction_log.jsonl`: approval decisions (approve, edit, link, reject) with the time taken, agree/disagree on fit claims with the checker's rating at the time, and direct edits in the Postings and Experience views. Summarize it with:

```powershell
python scripts/summarize_log.py
python scripts/summarize_log.py --json
```

The summary shows, per card type, how often you changed or rejected the assistant's proposal. For each checker rating, it shows how often you agreed or disagreed with the claim.

## Checks

Run these from the `project/` directory after the environment is activated:

```powershell
python -m unittest discover -s tests -v
python -c "import agent, webapp"
```

## Project files

- `agent.py` builds the assistant and manages terminal startup.
- `runtime.py` loads `.env`, builds the OpenRouter models, runs the SDK, applies approval decisions, and logs them.
- `tools.py` contains the Agents SDK tools.
- `llm.py` runs structured model calls made by code. `resume.py` parses resumes into nested drafts. `analysis.py` runs fit analysis, citation checks, and claim checking. `duplicates.py` labels duplicate candidates.
- `storage.py` manages local records, IDs, the tracking log, and the interaction log.
- `webapp.py` and `gui/` provide the browser interface: chat and approval cards (`chat.js`), the evidence panel (`analysis.js`), and the Postings and Experience views (`views.js`).
- `uploads.py` holds browser-selected resume files in memory and extracts their text.
- `templates/` holds the default agent and analysis instructions (copied into `data/config/` on first use) and the prompts for the analyst, checker, resume parser, and duplicate judge.
- `scripts/summarize_log.py` summarizes the interaction log.
- `samples/` holds fake postings and a fake resume for demos.
- `tests/` contains the unit and browser-workflow tests; `progress_log.md` records phase status.
- `data/` contains personal records and is ignored by Git.
