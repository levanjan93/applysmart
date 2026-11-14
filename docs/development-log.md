# Development log

Notes kept during development, newest first within each section. The original design brief is in [design-plan.md](design-plan.md); setup and usage are in the [project README](../project/README.md).

## Phase checklist

| Phase | State | Notes |
|---|---|---|
| 0 Explore | Done | Reviewed the supplied course examples and approval API. |
| 1 Storage | Done | Storage unit tests cover IDs, record validation, and status/log consistency. |
| 2 Add posting | Done | Live review completed by the user. |
| 3 Resume and experience | Done | Structured nested resume parsing, direct edits, approval cards, delete from chat. |
| 4 Analysis and claim check | Done | `analysis.py`: structured analyst call, code ID validation, code-decided resume suggestions, separate checker model. |
| 5 Verification GUI | Done | Evidence panel: flagged-first ordering, rating badges, highlighted raw line, side-by-side evidence, agree/disagree logging. |
| 6 Tracking, search, duplicates | Done | `update_status`, `read_tracking_log`, name-variant search with near misses and applied dates, model duplicate judgment, link option. |
| 7 Interaction log summary | Done | `scripts/summarize_log.py`: per-card decision rates, claim reviews by checker rating, direct edits. |
| 8 Polish | Done | Tabs with Postings and Experience views, instructions diff card, fake samples, README design-ideas section. |

## Runtime migration

- The project now owns its OpenRouter configuration and SDK runtime in `runtime.py`.
- Configuration is loaded from `project/.env`, with only `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` accepted. Model IDs are not restricted to a course model.
- The Agents SDK run loop handles `result.interruptions`, `result.to_state()`, `state.approve(...)`, and `state.reject(...)`. Browser edits reject the pending SDK call and submit edited JSON in a follow-up turn that requires fresh approval.
- Startup uses conversation history plus the new user message directly. There is no course session wrapper or local spending ledger.
- The project venv remains `%LOCALAPPDATA%\venvs\cs619-project`; it must stay outside this folder.

## Verification

- 2026-10-10 role context (user feedback: a Coinbase posting lost the team/mission paragraph and the list of consumer products, which matter for interview preparation). Postings gain `about_role`, `products`, and `partner_teams`, extracted by the chat model, verified like other fields (a shortened `about_role` is matched sentence by sentence), shown on the card under the record (products as clickable chips) and in the Postings detail view, and editable. Agent instructions template and the live `data/config` copy were updated. Also: the posting card now fits the window height with independently scrolling columns, and the card is shown without asking "Would you like me to save it?" first. Checked in the browser with a fake card built from the user's Coinbase paragraph. 78 tests pass.

- 2026-10-10 posting card (user feedback: the pasted-text box was capped at 360px and too dense to verify). The right column is now sticky and as tall as the window. `add_posting` takes `source_quotes`; `posting_evidence.py` verifies each quote in the paste (ignoring case, whitespace, curly quotes, with `...` elisions), falls back to the value itself and then to a 60% word-overlap line, and flags the rest. Clicking an item highlights its source, clicking a highlight selects the item, ⚠/≈ mark missing or approximate sources and made-up quotes, and unused lines are dimmed. The paste is displayed with headers and bullets by code (offsets only; text unchanged) with a Show original toggle. Relative dates ("Reposted 6 days ago", "a week ago", "30+ days ago", hours) are converted by code, and the page's phrase is saved as `date_posted_text`. Checked in the browser with a fake card built from `samples/posting_1`: a made-up salary and a made-up quote showed ⚠. Not yet checked with a live model. 76 tests pass. Note: `data/config/agent_instructions.md` is copied from the template only once, so the new quote guidance reaches the agent through the `add_posting` tool description.

- 2026-10-07 Checkpoint 3. A critical runtime bug was found and fixed: each browser request ran its SDK turn on a new thread and event loop while sharing one HTTP client, so later turns could hang forever on a connection owned by an earlier loop. Reproduced headlessly (turn 2 hung inside `run_sync`) and fixed by running every model call on one worker thread in `OpenRouterRuntime`. A regression test covers it. Also added: `update_status` (automatic) and `read_tracking_log`; storage-level company normalization ("D.B.S. Bank Pte Ltd" and "DBS Group" match "DBS Bank Ltd") with near misses; a duplicate judgment model call on the posting card plus a "Save as new and link" decision (logged as `link`, stored as `related_postings`); `propose_analysis_instructions` with a line-diff card; Chat/Postings/Experience tabs with direct edits logged as `direct_edit`; `scripts/summarize_log.py`; `samples/`; and the README design-ideas section. Requirement strings with a trailing "(preferred)" are now typed correctly; a live run had exposed this. A full live demo on an isolated copy covered resume import, posting save, duplicate judged "likely same posting" and linked, analysis with two stretched claims, status update, "Have I applied to Harbour Bank?", instructions diff approval, and Postings/Experience views with edit and delete. 65 tests pass.

- 2026-10-07 delete from chat (user request; not in PLAN.md, which only has deletion in the Experience view). Added `delete_experience` behind approval: the card shows the exact entry and any nested details that will become standalone, with "Delete permanently" / "Keep it" and no Edit. Agent instructions now forbid mentioning screens that don't exist; the assistant had pointed the user to a nonexistent "experience management panel". Live run: a chat request produced the card, and approval deleted the entry. 52 tests pass.

- 2026-10-07 resume rework (user feedback: every entry landed in "other", and the list was long and flat). Gemini Flash flattened the entries to strings in its tool arguments. Resume parsing now runs as a structured call (`parse_resume` -> `resume.py`, `templates/resume_parser_instructions.md`), and `import_resume` takes only `upload_id`; code holds the draft. Entries gain `parent_id`, so bullets nest under their item. The approval card groups items by resume section with nested bullets and skill chips, and the edit form works per item. Live run with Gemini Flash on a fake multi-section resume: every item was correctly categorized, the wording was kept ("Helped", "Participated"), and an edit that removed one bullet saved 24 linked entries. 50 tests pass.

- 2026-10-07 Checkpoint 2: Posting raw text now comes from the user's own message (`tools.find_pasted_text`), and `add_posting` no longer takes `raw_text`. Fit analysis uses SDK sub-agents with `output_type` for structured output. Live run on an isolated copy: the analysis plus check took about 20 s. With Claude Sonnet 5.5 as analyst, all claims were partial and rated supported. With Gemini 3.7 Flash as analyst, it overclaimed "2+ years SQL" as a strong match and GPT-6 Sol rated it stretched, which the panel listed first. A disagree click was logged with timing. 46 tests pass.

- 2026-10-07 Checkpoint 1: An edit now saves the user's arguments directly through the same write function (`tools.WRITE_ACTIONS`), then rejects the SDK call with a `rejection_message` stating what was saved. There is no model rewrite and no second approval. Approval cards are readable and show code-computed context: duplicate candidates, normalized requirements and entries, the resume filename, and the current entry for updates. Requirements sent as strings such as "preferred: X" are now normalized; Gemini Flash sent that format, and storage had rejected it. Decisions are logged to `data/interaction_log.jsonl`. Agent instructions now split skills lists into separate entries and keep page noise out of notes. A live browser run on an isolated copy covered save posting, duplicate skip, resume edit with removal, and experience reject; all four were logged correctly. 37 tests pass.

- 2026-10-07: Fixed the Phase 3 "Approve does nothing" bug. The project folder is synced by iCloud, which briefly locks files; about 2% of atomic renames failed with `PermissionError`, and a resume import made one counter write per entry, so roughly one in four imports failed. Storage now retries locked renames and reserves a batch of IDs in one write (600-write stress test in `data/`: 24 locks retried, 0 failures). Write tools return an explicit `SAVE FAILED` message, and the GUI shows green "Saved" / red "Not saved" cards from the actual tool results, not the model's reply. A live browser run with a fake resume saved 7 entries. 33 tests pass.

- The project venv reports `openai-agents==0.22.2`.
- `python -B -m unittest tests.test_storage -v`: 19 tests passed.
- `python -B -m unittest tests.test_runtime -v`: 3 tests passed, including arbitrary model IDs and missing-key rejection.
- `python -B -c "import agent, webapp"`: succeeded.
- Phase 3 suite: 30 storage, runtime, resume/experience, and browser workflow tests passed.
- Fixed the browser edit path for SDK 0.22.2: `RunResult` has no `status` attribute. A regression test now covers reject, edited follow-up, fresh approval, and completion.
- Resume input now uses browser-selected bytes held in memory behind an opaque upload ID. The registered agent schema has no path-reading tool or `file_path` parameter.
- Fixed approved resume imports for models that return atomic entries as strings instead of nested objects. A synthetic live OpenRouter run completed one approval, saved two temporary EXP entries, and returned confirmation.
- The agent imports with all 12 Phase 2/3 tools registered under the pinned SDK.
- `pypdf==6.1.1` is installed in the external project venv for local PDF text extraction.
- The runtime and local GUI server initialized and closed using `project/.env`, without making a model request.
- No live model response or browser approval click has been checked in this log.

The two OpenRouter settings from the prior local credentials file were copied into `project/.env` without displaying their values. `.env` is ignored by Git.
