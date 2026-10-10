// Tabs plus the Postings and Experience views (plan sections 7 and 10).
// Uses el(), entryView(), request(), openAnalysis(), and constants from chat.js.

const tabButtons = [...document.querySelectorAll(".tabs [data-tab]")];
const postingsTab = document.querySelector("#tab-postings");
const experienceTab = document.querySelector("#tab-experience");
const STATUSES = ["saved", "applied", "interviewing", "offer", "rejected", "withdrawn"];
let currentTab = "chat";
let selectedPosting = null;
const postingFilters = {status: "", text: ""};
const experienceFilters = {source: "", onResume: "", text: ""};

function showTab(name) {
  currentTab = name;
  for (const button of tabButtons) button.setAttribute("aria-selected", String(button.dataset.tab === name));
  for (const tab of ["chat", "postings", "experience"]) document.querySelector(`#tab-${tab}`).hidden = tab !== name;
  if (name === "postings") loadPostings();
  if (name === "experience") loadExperience();
}
tabButtons.forEach(button => button.addEventListener("click", () => showTab(button.dataset.tab)));

// Called by chat.js on every refresh: approvals always surface on the Chat tab.
function notePending(hasPending) {
  document.querySelector("#approval-dot").hidden = !hasPending;
  if (hasPending && currentTab !== "chat") showTab("chat");
}

function askInChat(message) {
  showTab("chat");
  const box = document.querySelector("#prompt");
  box.value = message;
  document.querySelector("#send").click();
}

function statusBadge(status) {
  return el("span", {className: `status status-${status}`, textContent: status});
}

function filterSelect(label, value, options, onChange) {
  const select = el("select");
  for (const [optionValue, optionLabel] of options) select.append(el("option", {value: optionValue, textContent: optionLabel}));
  select.value = value;
  select.addEventListener("change", () => onChange(select.value));
  return el("label", {className: "filter"}, el("span", {textContent: label}), select);
}

function searchBox(placeholder, value, onInput) {
  const input = el("input", {type: "search", placeholder, value});
  input.addEventListener("input", () => {
    // Filtering re-renders the view; put the cursor back in the new search box.
    const position = input.selectionStart;
    onInput(input.value);
    const again = [...document.querySelectorAll('input[type="search"]')].find(box => box.placeholder === placeholder);
    if (again && again !== input) { again.focus(); again.setSelectionRange(position, position); }
  });
  return el("label", {className: "filter grow"}, el("span", {textContent: "Search"}), input);
}

// ----- Postings ---------------------------------------------------------------

async function loadPostings() {
  try {
    const {postings} = await request("/api/postings");
    renderPostings(postings);
  } catch (error) { postingsTab.replaceChildren(el("p", {className: "note-warn", textContent: error.message})); }
}

function renderPostings(postings) {
  const text = postingFilters.text.trim().toLowerCase();
  const visible = postings.filter(p =>
    (!postingFilters.status || p.status === postingFilters.status) &&
    (!text || `${p.id} ${p.title} ${p.company}`.toLowerCase().includes(text)));
  const table = el("table", {className: "postings-table"});
  table.append(el("thead", {}, el("tr", {}, ...["ID", "Title", "Company", "Added", "Status", "Applied", "Analyses"].map(h => el("th", {textContent: h})))));
  const body = el("tbody");
  for (const posting of visible) {
    const row = el("tr", {className: posting.id === selectedPosting ? "selected" : "", tabIndex: 0},
      el("td", {className: "mono", textContent: posting.id}),
      el("td", {textContent: posting.title}),
      el("td", {textContent: posting.company}),
      el("td", {textContent: posting.date_added}),
      el("td", {}, statusBadge(posting.status)),
      el("td", {textContent: posting.applied_on || "—"}),
      el("td", {textContent: posting.analyses ? String(posting.analyses) : "—"}));
    const open = () => { selectedPosting = posting.id; body.querySelectorAll("tr").forEach(r => r.classList.remove("selected")); row.classList.add("selected"); openPosting(posting.id); };
    row.addEventListener("click", open);
    row.addEventListener("keydown", event => { if (event.key === "Enter") open(); });
    body.append(row);
  }
  table.append(body);
  postingsTab.replaceChildren(
    el("div", {className: "view-head"},
      el("h2", {textContent: `Saved postings (${postings.length})`}),
      el("div", {className: "filters"},
        filterSelect("Status", postingFilters.status, [["", "All"], ...STATUSES.map(s => [s, s])], value => { postingFilters.status = value; renderPostings(postings); }),
        searchBox("Title, company, or ID", postingFilters.text, value => { postingFilters.text = value; renderPostings(postings); }))),
    visible.length ? el("div", {className: "table-wrap"}, table)
      : el("p", {className: "muted", textContent: postings.length ? "No postings match these filters." : "No postings yet. Paste one in the Chat tab and ask to save it."}),
    el("div", {id: "posting-detail"}));
  if (selectedPosting && visible.some(p => p.id === selectedPosting)) openPosting(selectedPosting);
}

async function openPosting(jobId) {
  const holder = document.querySelector("#posting-detail");
  if (!holder) return;
  try {
    renderPostingDetail(holder, await request(`/api/posting?id=${encodeURIComponent(jobId)}`));
  } catch (error) { holder.replaceChildren(el("p", {className: "note-warn", textContent: error.message})); }
}

function renderPostingDetail(holder, detail) {
  const posting = detail.posting;
  const status = el("select");
  STATUSES.forEach(s => status.append(el("option", {value: s, textContent: s})));
  status.value = posting.status;
  const note = el("input", {type: "text", placeholder: "Optional note, e.g. referred by a friend"});
  const message = el("span", {className: "muted"});
  const update = el("button", {type: "button", className: "small", textContent: "Update status"});
  update.addEventListener("click", async () => {
    try {
      const fresh = await request("/api/posting/status", {job_id: posting.id, status: status.value, note: note.value});
      renderPostingDetail(holder, fresh);
      loadPostings();
    } catch (error) { message.textContent = error.message; }
  });

  const requirements = el("ol", {className: "req-list"});
  posting.requirements.forEach(req => requirements.append(el("li", {},
    el("span", {className: "req-id", textContent: req.id}),
    el("span", {className: `tag ${req.type === "preferred" ? "tag-soft" : ""}`, textContent: req.type}),
    el("span", {textContent: req.text}))));
  const responsibilities = el("ul");
  posting.responsibilities.forEach(item => responsibilities.append(el("li", {textContent: item})));

  const history = el("ul", {className: "plain-list history"});
  for (const event of detail.log) {
    const d = event.details || {};
    const text = event.event === "added" ? `Added from ${d.source || "unknown source"}`
      : event.event === "status_changed" ? `Status ${d.from} → ${d.to}${d.note ? ` — ${d.note}` : ""}`
      : event.event === "note" ? `Note: ${d.note}` : event.event;
    history.append(el("li", {}, el("span", {className: "mono muted", textContent: event.timestamp.replace("T", " ").slice(0, 16)}), el("span", {textContent: ` ${text}`})));
  }

  const analyses = el("ul", {className: "plain-list"});
  for (const item of detail.analyses) {
    const open = el("button", {type: "button", className: "secondary small", textContent: "Review evidence"});
    open.addEventListener("click", () => { showTab("chat"); openAnalysis(item.analysis_id); });
    analyses.append(el("li", {},
      el("span", {className: "mono", textContent: `${(item.created_at || "").slice(0, 16).replace("T", " ")} `}),
      el("span", {textContent: `${item.matches} matches, ${item.flagged} flagged, ${item.gaps} gaps `}), open));
  }
  const analyze = el("button", {type: "button", className: "small", textContent: "Analyze my fit in chat"});
  analyze.addEventListener("click", () => askInChat(`How well do I fit ${posting.id}?`));

  holder.replaceChildren(el("article", {className: "posting-detail"},
    el("div", {className: "analysis-head"},
      el("h2", {textContent: `${posting.id} · ${posting.title} · ${posting.company}`}),
      statusBadge(posting.status)),
    fieldTable(posting, [
      ["department", "Department"], ["location", "Location"], ["employment_type", "Employment type"],
      ["source", "Source"], ["source_url", "Source URL"], ["date_posted", "Date posted"],
      ["date_posted_is_estimate", "Date is an estimate"], ["date_posted_text", "Date as on the page"], ["date_added", "Date added"], ["salary", "Salary"],
      ["notes", "Notes"], ["related_postings", "Linked postings"]]),
    el("div", {className: "status-row"}, el("label", {className: "filter"}, el("span", {textContent: "Status"}), status), note, update, message),
    posting.about_role ? el("h3", {textContent: "About the role"}) : null,
    posting.about_role ? el("p", {className: "about-role", textContent: posting.about_role}) : null,
    posting.products?.length ? el("h3", {textContent: "Products and areas to research"}) : null,
    posting.products?.length ? el("div", {className: "chips"}, ...posting.products.map(item => el("span", {className: "chip", textContent: item}))) : null,
    posting.partner_teams?.length ? el("h3", {textContent: "Works with"}) : null,
    posting.partner_teams?.length ? el("div", {className: "chips"}, ...posting.partner_teams.map(item => el("span", {className: "chip", textContent: item}))) : null,
    el("h3", {textContent: `Requirements (${posting.requirements.length})`}), requirements,
    posting.responsibilities.length ? el("h3", {textContent: "Responsibilities"}) : null,
    posting.responsibilities.length ? responsibilities : null,
    el("h3", {textContent: "Fit analyses"}),
    detail.analyses.length ? analyses : el("p", {className: "muted", textContent: "No analyses yet."}),
    analyze,
    el("h3", {textContent: "Tracking history"}), history,
    el("details", {className: "raw-details"},
      el("summary", {textContent: "Your pasted text (saved verbatim)"}),
      el("pre", {textContent: detail.raw_text || "No pasted text saved."}))));
}

// ----- Experience -------------------------------------------------------------

async function loadExperience() {
  try {
    const {entries} = await request("/api/experience");
    renderExperience(entries);
  } catch (error) { experienceTab.replaceChildren(el("p", {className: "note-warn", textContent: error.message})); }
}

function experienceMatches(entry) {
  const text = experienceFilters.text.trim().toLowerCase();
  return (!experienceFilters.source || entry.source === experienceFilters.source) &&
    (!experienceFilters.onResume || String(entry.on_resume) === experienceFilters.onResume) &&
    (!text || [entry.id, entry.text, entry.organization, ...(entry.skills || [])].join(" ").toLowerCase().includes(text));
}

function experienceRow(entry, entries, {child = false} = {}) {
  const row = el("div", {className: child ? "exp-row exp-child" : "exp-row"});
  const meta = [entry.organization, entry.dates, entry.skills?.length ? `Skills: ${entry.skills.join(", ")}` : null].filter(Boolean).join(" · ");
  const edit = el("button", {type: "button", className: "secondary small", textContent: "Edit"});
  const remove = el("button", {type: "button", className: "secondary small", textContent: "Delete"});
  const view = () => row.replaceChildren(
    el("div", {className: "exp-main"},
      el("span", {className: "mono exp-id", textContent: entry.id}),
      el("span", {className: "exp-text", textContent: entry.text}),
      meta && !child ? el("span", {className: "entry-meta exp-meta", textContent: meta}) : null),
    el("div", {className: "exp-tags"},
      el("span", {className: "tag tag-soft", textContent: entry.source === "resume" ? "resume" : "added by you"}),
      entry.on_resume ? null : el("span", {className: "tag tag-warn", textContent: "not on resume"}),
      edit, remove));
  edit.addEventListener("click", () => {
    const inputs = entryInputs(entry, {withOnResume: true});
    const save = el("button", {type: "button", className: "small", textContent: "Save"});
    const cancel = el("button", {type: "button", className: "secondary small", textContent: "Cancel"});
    const message = el("span", {className: "muted"});
    save.addEventListener("click", async () => {
      try {
        await request("/api/experience/update", {exp_id: entry.id, fields: collectInputs(inputs)});
        loadExperience();
      } catch (error) { message.textContent = error.message; }
    });
    cancel.addEventListener("click", view);
    row.replaceChildren(el("fieldset", {className: "edit-row"},
      el("p", {className: "mono", textContent: `Editing ${entry.id}`}),
      ...Object.values(inputs).map(input => input.node),
      el("div", {className: "approval-actions"}, save, cancel, message)));
  });
  let armed = null;
  remove.addEventListener("click", async () => {
    if (!armed) {
      const children = entries.filter(other => other.parent_id === entry.id).length;
      remove.textContent = children ? `Confirm delete (keeps ${children} details)` : "Confirm delete";
      remove.className = "danger small";
      armed = setTimeout(() => { armed = null; remove.textContent = "Delete"; remove.className = "secondary small"; }, 4000);
      return;
    }
    clearTimeout(armed);
    try {
      await request("/api/experience/delete", {exp_id: entry.id});
      loadExperience();
    } catch (error) { statusLine.textContent = error.message; }
  });
  view();
  return row;
}

function renderExperience(entries) {
  const visible = entries.filter(experienceMatches);
  const visibleIds = new Set(visible.map(entry => entry.id));
  const fromResume = entries.filter(e => e.source === "resume").length;
  const notOnResume = entries.filter(e => !e.on_resume).length;
  const body = el("div", {className: "exp-groups"});
  const categories = [...ITEM_CATEGORIES, "skill"];
  for (const category of categories) {
    // A child shows under its parent when the parent is visible; otherwise on its own.
    const tops = visible.filter(e => e.category === category && (!e.parent_id || !visibleIds.has(e.parent_id)));
    if (!tops.length) continue;
    const section = el("section", {className: "exp-section"},
      el("h3", {className: "resume-section", textContent: category === "skill" ? "Skills" : CATEGORY_TITLES[category]}));
    for (const entry of tops) {
      const block = el("div", {className: "exp-block"}, experienceRow(entry, entries));
      if (entry.parent_id) block.prepend(el("p", {className: "muted small-print", textContent: `Detail of ${entry.parent_id} (hidden by filters)`}));
      visible.filter(child => child.parent_id === entry.id).forEach(child => block.append(experienceRow(child, entries, {child: true})));
      section.append(block);
    }
    body.append(section);
  }
  experienceTab.replaceChildren(
    el("div", {className: "view-head"},
      el("h2", {textContent: `Experience (${entries.length})`}),
      el("div", {className: "filters"},
        filterSelect("Source", experienceFilters.source, [["", "All"], ["resume", "Resume"], ["user-added", "Added by you"]], value => { experienceFilters.source = value; renderExperience(entries); }),
        filterSelect("On resume", experienceFilters.onResume, [["", "All"], ["true", "Yes"], ["false", "No"]], value => { experienceFilters.onResume = value; renderExperience(entries); }),
        searchBox("Text, skill, or organization", experienceFilters.text, value => { experienceFilters.text = value; renderExperience(entries); }))),
    el("p", {className: "muted", textContent:
      `${fromResume} from your resume, ${entries.length - fromResume} added by you, ${notOnResume} not on your resume. ` +
      "Edits and deletions here are saved directly and logged."}),
    visible.length ? body : el("p", {className: "muted", textContent: entries.length ? "No entries match these filters." : "No experience yet. Upload a resume or tell the assistant about your experience."}));
}
