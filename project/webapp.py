"""Small local browser chat with SDK-backed approval cards."""

from __future__ import annotations

import json
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from threading import Event, Lock
from urllib.parse import parse_qs, unquote, urlparse

from analysis import evidence_view
from duplicates import judge_candidates_blocking
from posting_evidence import source_map
from storage import StorageError, default_store
from tools import (
    application_dates, approval_details, approval_tool_name, duplicate_candidates, find_pasted_text,
    normalize_requirements, posted_date_view,
)
from uploads import MAX_RESUME_BYTES, resume_uploads

ASSETS = Path(__file__).resolve().parent / "gui"


def approval_context(tool_name: str, arguments: dict) -> dict:
    """Facts the approval card shows beside the proposal, computed by code."""
    context = {}
    try:
        if tool_name == "add_posting":
            # Shown instead of the raw arguments: this is exactly what will be saved.
            context["requirements"] = normalize_requirements(arguments.get("requirements"))
            context["raw_text"] = find_pasted_text(
                arguments.get("title") or "", arguments.get("company") or "",
            )
            context["date_posted"] = posted_date_view(arguments.get("date_posted"))
            if context["raw_text"]:
                # Display lines and, per extracted item, its verified source span.
                context["evidence"] = source_map(context["raw_text"], arguments, context["requirements"])
            context["duplicates"] = duplicate_candidates(
                arguments.get("company") or "", arguments.get("title") or "",
            )
            if context["duplicates"]:
                draft = {**arguments, "requirements": context["requirements"]}
                try:
                    judgments = judge_candidates_blocking(draft, [item["id"] for item in context["duplicates"]])
                except Exception as exc:
                    context["judgment_error"] = f"The duplicate judgment is unavailable ({type(exc).__name__})."
                else:
                    for item in context["duplicates"]:
                        item.update(judgments.get(item["id"], {}))
        elif tool_name == "import_resume":
            context["draft"] = resume_uploads.draft(arguments.get("upload_id", ""))
            context["filename"] = resume_uploads.get(arguments.get("upload_id", "")).original_name
        elif tool_name == "propose_analysis_instructions":
            context["before"] = default_store().read_analysis_instructions()
        elif tool_name in ("update_experience", "delete_experience"):
            exp_id = arguments.get("exp_id")
            entries = default_store().read_experience()
            context["current"] = next((entry for entry in entries if entry["id"] == exp_id), None)
            if tool_name == "delete_experience":
                context["children"] = [entry for entry in entries if entry.get("parent_id") == exp_id]
    except (ValueError, OSError):
        # Context is a convenience; the proposal itself is still shown exactly.
        pass
    return context


class ApprovalBroker:
    """Pause the SDK runner while the browser reviews a proposed write."""

    def __init__(self):
        self.lock = Lock()
        self.pending = None
        self.response = None
        self.ready = None

    def request(self, interruption):
        event = Event()
        tool_name = approval_tool_name(interruption)
        arguments = approval_details(interruption)
        with self.lock:
            self.pending = {
                "tool_name": tool_name,
                "arguments": arguments,
                "context": approval_context(tool_name, arguments),
            }
            self.response = None
            self.ready = event
        started = time.monotonic()
        event.wait()
        with self.lock:
            decision = dict(self.response)
            decision["seconds"] = round(time.monotonic() - started, 1)
            self.pending = None
            self.response = None
            self.ready = None
            return decision

    def snapshot(self):
        with self.lock:
            return dict(self.pending) if self.pending is not None else None

    def submit(self, decision, arguments=None):
        with self.lock:
            if self.pending is None or self.ready is None:
                return False
            response = {"decision": decision}
            if decision in ("edit", "link"):
                if not isinstance(arguments, dict):
                    return False
                response["arguments"] = arguments
            elif decision == "reject" and self.pending.get("tool_name") == "import_resume":
                resume_uploads.discard(self.pending.get("arguments", {}).get("upload_id", ""))
            self.response = response
            event = self.ready
        event.set()
        return True


def review_claim(payload: dict) -> tuple[int, dict]:
    """Save the user's agree/disagree on a fit claim and log the decision."""
    analysis_id = payload.get("analysis_id")
    requirement_id = payload.get("requirement_id")
    decision = payload.get("decision")
    seconds = payload.get("seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0:
        seconds = None
    store = default_store()
    try:
        claim = store.review_claim(analysis_id, requirement_id, decision)
    except StorageError as exc:
        return 400, {"error": str(exc)}
    store.append_interaction({
        "event_type": "claim_review",
        "item_type": "fit_claim",
        "item_id": f"{analysis_id}/{requirement_id}",
        "decision": decision,
        "time_to_decision_seconds": round(seconds, 1) if seconds is not None else None,
        "before": {
            "verdict": claim["verdict"],
            "evidence": claim["evidence"],
            "valid": claim["valid"],
            "check": claim["check"],
        },
        "after": None,
    })
    return 200, {"claim": claim}


def postings_overview() -> list[dict]:
    """Postings table: summary, application dates, and number of saved analyses."""
    store = default_store()
    rows = []
    for posting in store.list_postings():
        rows.append({
            **posting,
            **application_dates(store.read_tracking_log(posting["id"])),
            "analyses": len(store.list_analyses(posting["id"])),
        })
    return rows


def posting_detail(job_id: str) -> dict:
    """Everything the posting panel shows: record, pasted text, history, analyses."""
    store = default_store()
    posting = store.read_posting(job_id)
    try:
        raw_text = store.read_raw_text(job_id)
    except StorageError:
        raw_text = ""
    analyses = []
    for analysis_id in reversed(store.list_analyses(job_id)):
        saved = store.read_analysis(analysis_id)
        ratings = [(claim.get("check") or {}).get("rating") for claim in saved["claims"] if claim["verdict"] != "gap"]
        analyses.append({
            "analysis_id": analysis_id,
            "created_at": saved.get("created_at"),
            "matches": len(ratings),
            "flagged": sum(1 for rating in ratings if rating in ("stretched", "unsupported")),
            "gaps": sum(1 for claim in saved["claims"] if claim["verdict"] == "gap"),
        })
    return {"posting": posting, "raw_text": raw_text, "log": store.read_tracking_log(job_id), "analyses": analyses}


def log_direct_edit(item_type: str, item_id: str, decision: str, before, after) -> None:
    default_store().append_interaction({
        "event_type": "direct_edit", "item_type": item_type, "item_id": item_id,
        "decision": decision, "time_to_decision_seconds": None, "before": before, "after": after,
    })


def change_status(payload: dict) -> tuple[int, dict]:
    store = default_store()
    job_id = payload.get("job_id")
    try:
        before = store.read_posting(job_id)["status"]
        posting = store.update_status(job_id, payload.get("status"), payload.get("note") or None)
    except StorageError as exc:
        return 400, {"error": str(exc)}
    log_direct_edit("posting_status", job_id, "edit", before, posting["status"])
    return 200, posting_detail(job_id)


def edit_experience(payload: dict) -> tuple[int, dict]:
    store = default_store()
    exp_id = payload.get("exp_id")
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        return 400, {"error": "Send the fields to change."}
    allowed = {"text", "category", "skills", "organization", "dates", "on_resume"}
    try:
        before = next((entry for entry in store.read_experience() if entry["id"] == exp_id), None)
        updated = store.update_experience(exp_id, {key: value for key, value in fields.items() if key in allowed})
    except StorageError as exc:
        return 400, {"error": str(exc)}
    log_direct_edit("experience", exp_id, "edit", before, updated)
    return 200, {"entry": updated}


def remove_experience(payload: dict) -> tuple[int, dict]:
    store = default_store()
    exp_id = payload.get("exp_id")
    try:
        before = next((entry for entry in store.read_experience() if entry["id"] == exp_id), None)
        store.delete_experience(exp_id)
    except StorageError as exc:
        return 400, {"error": str(exc)}
    log_direct_edit("experience", exp_id, "delete", before, None)
    return 200, {"deleted": exp_id}


def remove_all_experience(payload: dict) -> tuple[int, dict]:
    # The view asks the user to confirm first; the flag guards against stray calls.
    if payload.get("confirm") is not True:
        return 400, {"error": "Deleting all experience needs confirmation."}
    removed = default_store().delete_all_experience()
    log_direct_edit("experience", "all", "delete_all", removed, None)
    return 200, {"deleted": len(removed)}


class JobSearchServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, runtime):
        self.token = token_urlsafe(32)
        self.busy = False
        self.turns = []
        self.state_lock = Lock()
        self.approvals = ApprovalBroker()
        self.runtime = runtime
        self.history = []
        super().__init__(("127.0.0.1", 0), JobSearchHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def snapshot(self):
        with self.state_lock:
            return {
                "turns": list(self.turns),
                "busy": self.busy,
                "pending": self.approvals.snapshot(),
            }

    def run_turn(self, prompt):
        with self.state_lock:
            if self.busy:
                return 409, {"error": "A turn is already running."}
            self.busy = True
            turn = {"prompt": prompt, "result": None}
            self.turns.append(turn)
        try:
            result = self.runtime.run(
                self.history + [{"role": "user", "content": prompt}],
                approval_handler=self.approvals.request,
            )
            self.history = result.to_input_list()
            outcomes = getattr(self.runtime, "last_outcomes", [])
            record = {
                "answer": str(result.final_output or ""),
                "status": "completed" if str(result.final_output or "").strip() else "empty",
                "error": None,
                "artifacts": [item["text"] for item in outcomes if item["ok"]],
                "save_errors": [item["text"] for item in outcomes if not item["ok"]],
                "analyses": getattr(self.runtime, "last_analyses", []),
            }
        except Exception as exc:
            traceback.print_exc()
            outcomes = getattr(self.runtime, "last_outcomes", [])
            record = {
                "answer": "",
                "status": "failed",
                "error": (
                    "The turn stopped with " + type(exc).__name__
                    + ". See the local terminal for details. Anything saved is listed below."
                ),
                "artifacts": [item["text"] for item in outcomes if item["ok"]],
                "save_errors": [item["text"] for item in outcomes if not item["ok"]],
                "analyses": getattr(self.runtime, "last_analyses", []),
            }
        finally:
            with self.state_lock:
                self.turns[-1] = {**turn, "result": record}
                self.busy = False
        return 200, record


class JobSearchHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, *, api=False):
        server = self.server
        if self.headers.get("Host") != server.origin.removeprefix("http://"):
            self.send_json(403, {"error": "Use the local URL printed in the terminal."})
            return False
        if self.headers.get("Origin") not in (None, server.origin):
            self.send_json(403, {"error": "Cross-origin requests are not allowed."})
            return False
        if api and not compare_digest(
            self.headers.get("X-Chat-Token", "").encode(), server.token.encode()
        ):
            self.send_json(403, {"error": "Reload the local page to reconnect."})
            return False
        return True

    def do_GET(self):
        if not self.allowed(api=self.path.startswith("/api/")):
            return
        if self.path == "/api/state":
            self.send_json(200, self.server.snapshot())
            return
        if self.path == "/api/postings":
            self.send_json(200, {"postings": postings_overview()})
            return
        if self.path.startswith("/api/posting?"):
            job_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            try:
                self.send_json(200, posting_detail(job_id))
            except StorageError as exc:
                self.send_json(404, {"error": str(exc)})
            return
        if self.path == "/api/experience":
            self.send_json(200, {"entries": default_store().read_experience()})
            return
        if self.path.startswith("/api/analysis?"):
            analysis_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            try:
                self.send_json(200, evidence_view(analysis_id))
            except (StorageError, OSError) as exc:
                self.send_json(404, {"error": str(exc)})
            return
        assets = {
            "/": ("index.html", "text/html"),
            "/chat.js": ("chat.js", "text/javascript"),
            "/analysis.js": ("analysis.js", "text/javascript"),
            "/views.js": ("views.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
        }
        if self.path not in assets:
            self.send_json(404, {"error": "Not found."})
            return
        filename, content_type = assets[self.path]
        content = (ASSETS / filename).read_text(encoding="utf-8")
        if self.path == "/":
            content = content.replace("CHAT_TOKEN", self.server.token)
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.allowed(api=True):
            return
        if self.path == "/api/resume-upload":
            self.receive_resume_upload()
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 400000 or self.headers.get("Transfer-Encoding"):
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeError, TimeoutError):
            self.send_json(400, {"error": "Invalid request."})
            return

        if self.path == "/api/chat":
            prompt = payload.get("message")
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 6000:
                self.send_json(400, {"error": "Enter a message of 1–6,000 characters."})
                return
            status, result = self.server.run_turn(prompt)
            self.send_json(status, result)
            return
        direct = {
            "/api/posting/status": change_status,
            "/api/experience/update": edit_experience,
            "/api/experience/delete": remove_experience,
            "/api/experience/delete-all": remove_all_experience,
        }
        if self.path in direct:
            status, result = direct[self.path](payload)
            self.send_json(status, result)
            return
        if self.path == "/api/claim-review":
            status, result = review_claim(payload)
            self.send_json(status, result)
            return
        if self.path == "/api/approval":
            decision = payload.get("decision")
            edited = payload.get("arguments")
            if decision not in {"approve", "reject", "edit", "link"}:
                self.send_json(400, {"error": "Choose approve, reject, or edit."})
                return
            if not self.server.approvals.submit(decision, edited):
                self.send_json(409, {"error": "No approval is waiting."})
                return
            self.send_json(200, {"ok": True})
            return
        self.send_json(404, {"error": "Not found."})

    def receive_resume_upload(self):
        """Accept raw bytes selected by the user in the local browser."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_RESUME_BYTES or self.headers.get("Transfer-Encoding"):
                raise ValueError("Resume must be between 1 byte and 10 MB.")
            encoded_name = self.headers.get("X-Resume-Filename", "")
            if not encoded_name or len(encoded_name) > 800:
                raise ValueError("Resume filename is missing or too long.")
            filename = unquote(encoded_name, encoding="utf-8", errors="strict")
            content = self.rfile.read(length)
            if len(content) != length:
                raise ValueError("Resume upload was incomplete.")
            upload = resume_uploads.add(filename, content)
        except (ValueError, UnicodeError, OSError) as exc:
            self.send_json(400, {"error": str(exc) or "The resume could not be uploaded."})
            return
        self.send_json(200, {
            "upload_id": upload.upload_id,
            "filename": upload.original_name,
        })


def serve(runtime):
    """Run the approval-enabled local interface until interrupted."""
    with JobSearchServer(runtime) as server:
        print(f"\nOpen {server.origin} to use the job-search assistant.\nStop with Ctrl+C.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping the local job-search interface.")
        finally:
            resume_uploads.clear()
