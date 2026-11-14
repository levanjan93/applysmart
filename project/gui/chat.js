const form = document.querySelector("#chat-form");
const promptBox = document.querySelector("#prompt");
const send = document.querySelector("#send");
const statusLine = document.querySelector("#status");
const conversation = document.querySelector("#conversation");
const approval = document.querySelector("#approval");
const nextSteps = document.querySelector("#next-steps");
const resumeForm = document.querySelector("#resume-form");
const resumeFile = document.querySelector("#resume-file");
const uploadResume = document.querySelector("#upload-resume");
const token = document.querySelector('meta[name="chat-token"]').content;
let previous = "";
let previousApproval = "";
let previousSteps = "";
let submitting = false;

async function request(path, payload) {
  const response = await fetch(path, {
    method: payload ? "POST" : "GET",
    headers: {"Content-Type": "application/json", "X-Chat-Token": token},
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request failed.");
  return result;
}

async function uploadSelectedResume(file) {
  const response = await fetch("/api/resume-upload", {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Chat-Token": token,
      "X-Resume-Filename": encodeURIComponent(file.name),
    },
    body: file,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The resume upload failed.");
  return result;
}

function addMessage(label, text, kind) {
  const article = document.createElement("article");
  article.className = kind;
  const title = document.createElement("h2");
  title.textContent = label;
  let body;
  if (kind === "assistant") {
    body = markdownView(text);
  } else {
    body = document.createElement("p");
    body.textContent = text;
  }
  article.append(title, body);
  conversation.append(article);
}

// Inline Markdown: **bold**, *italic* / _italic_, and `code`. Text is only ever
// set as textContent, so a reply cannot inject HTML.
function inlineMarkdown(text) {
  const nodes = [];
  const pattern = /\*\*(.+?)\*\*|`([^`]+)`|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const [, bold, code, star, underscore] = match;
    nodes.push(bold !== undefined ? el("strong", {}, ...inlineMarkdown(bold))
      : code !== undefined ? el("code", {textContent: code})
      : el("em", {}, ...inlineMarkdown(star ?? underscore)));
    last = match.index + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

// Block Markdown as the assistant writes it: headings, bullet and numbered
// lists, horizontal rules, and paragraphs (single line breaks are kept).
function markdownView(text) {
  const root = el("div", {className: "md"});
  let paragraph = null;
  let list = null;
  const closeBlocks = () => { paragraph = null; list = null; };
  for (const line of String(text).split(/\r?\n/)) {
    const heading = line.match(/^\s*(#{1,6})\s+(.*?)\s*#*\s*$/);
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (!line.trim()) {
      closeBlocks();
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      closeBlocks();
      root.append(el("hr"));
    } else if (heading) {
      closeBlocks();
      root.append(el(heading[1].length <= 2 ? "h3" : "h4", {}, ...inlineMarkdown(heading[2])));
    } else if (bullet || numbered) {
      paragraph = null;
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = el(tag);
        if (numbered && numbered[1] !== "1") list.start = Number(numbered[1]);
        root.append(list);
      }
      list.append(el("li", {}, ...inlineMarkdown(bullet ? bullet[1] : numbered[2])));
    } else if (list && /^\s+\S/.test(line) && list.lastChild) {
      // An indented line continues the previous list item.
      list.lastChild.append(" ", ...inlineMarkdown(line.trim()));
    } else {
      list = null;
      if (paragraph) paragraph.append(el("br"));
      else root.append(paragraph = el("p"));
      paragraph.append(...inlineMarkdown(line));
    }
  }
  return root;
}

function renderTurns(turns) {
  const encoded = JSON.stringify(turns);
  if (encoded === previous) return;
  previous = encoded;
  conversation.replaceChildren();
  if (!turns.length) addMessage("Ready", "Paste a posting to begin.", "empty");
  for (const turn of turns) {
    addMessage("You", turn.prompt, "user");
    const result = turn.result;
    for (const item of result?.clarifications || []) {
      const reply = item.answer == null ? "You skipped this question." : `You answered: ${item.answer}`;
      addMessage("The assistant asked", `${item.question}\n${reply}`, "clarification");
    }
    if (!result) {
      addMessage("Assistant", "Working…", "assistant");
      continue;
    }
    addMessage(result.status === "completed" ? "Assistant" : `Assistant · ${result.status}`,
      result.answer || result.error || "No answer returned.", "assistant");
    if (result.artifacts?.length) addMessage("Changes saved", result.artifacts.join("\n"), "artifacts");
    if (result.save_errors?.length) addMessage("Not saved", result.save_errors.join("\n"), "save-error");
    for (const item of result.analyses || []) {
      const button = el("button", {type: "button", textContent: `Review evidence for ${item.job}`});
      button.addEventListener("click", () => openAnalysis(item.analysis_id));
      conversation.append(el("article", {className: "analysis-link"},
        el("h2", {textContent: "Fit analysis saved"}),
        el("p", {className: "muted", textContent: "Every claim cites requirement and experience IDs and has been rated by an independent checker."}),
        button));
    }
  }
}

const CATEGORIES = ["work", "project", "education", "skill", "leadership", "award", "other"];
const POSTING_FIELDS = [
  ["title", "Title"], ["company", "Company"], ["department", "Department"],
  ["location", "Location"], ["employment_type", "Employment type"], ["source", "Source"],
  ["source_url", "Source URL"], ["date_posted", "Date posted"],
  ["date_posted_is_estimate", "Date is an estimate"], ["salary", "Salary"], ["notes", "Notes"],
];
const ENTRY_FIELDS = [["organization", "Organization"], ["dates", "Dates"]];
const ITEM_CATEGORIES = ["work", "project", "education", "leadership", "award", "other"];
const CATEGORY_TITLES = {
  work: "Work experience", project: "Projects", education: "Education",
  leadership: "Leadership and activities", award: "Awards", other: "Other",
};

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children.filter(child => child !== null && child !== undefined));
  return node;
}

function shown(value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return Array.isArray(value) ? value.join(", ") : String(value);
}

function entryObject(entry) {
  return typeof entry === "string"
    ? {category: "other", text: entry, skills: [], organization: null, dates: null}
    : entry;
}

// Compare drafts by content, ignoring key order and empty optional values, so an
// untouched form counts as an approval rather than an edit.
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      const item = value[key];
      if (item === null || item === "" || (Array.isArray(item) && !item.length)) continue;
      out[key] = canonical(item);
    }
    return out;
  }
  return value;
}

function sameDraft(a, b) {
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
}

function fieldTable(object, fields) {
  const list = el("dl", {className: "fields"});
  for (const [key, label] of fields) {
    if (!(key in object)) continue;
    const value = shown(object[key]);
    list.append(el("dt", {textContent: label}), el("dd", {textContent: value, className: value === "—" ? "muted" : ""}));
  }
  return list;
}

function entryView(entry) {
  entry = entryObject(entry);
  const meta = [entry.organization, entry.dates, entry.skills?.length ? `Skills: ${entry.skills.join(", ")}` : null]
    .filter(Boolean).join(" · ");
  return el("div", {className: "entry"},
    el("span", {className: "tag", textContent: entry.category || "other"}),
    el("p", {className: "entry-text", textContent: entry.text || "(empty)"}),
    meta ? el("p", {className: "entry-meta", textContent: meta}) : null,
    "on_resume" in entry && entry.on_resume !== null ? el("p", {className: "entry-meta", textContent: `On resume: ${shown(entry.on_resume)}`}) : null,
  );
}

const JUDGMENT_CLASS = {
  "likely same posting": "badge-unsupported",
  "same company, different role": "badge-stretched",
  "unrelated": "badge-supported",
};

function duplicatesView(duplicates, context, onLink) {
  if (!duplicates) return null;
  if (!duplicates.length) {
    return el("p", {className: "note-ok", textContent: "Duplicate check: no saved posting shares this company and title words."});
  }
  const list = el("ul", {className: "dup-list"});
  for (const item of duplicates) {
    const link = el("button", {type: "button", className: "secondary small", textContent: `Save as new and link to ${item.id}`});
    link.addEventListener("click", () => onLink(item.id));
    list.append(el("li", {},
      el("p", {className: "dup-title", textContent: `${item.id} · ${item.title} · ${item.company}`}),
      el("p", {className: "entry-meta", textContent:
        `Status ${item.status}, added ${item.date_added}. Shared title words: ${item.overlapping_title_keywords.join(", ")}.`}),
      item.judgment ? el("p", {},
        el("span", {className: `badge ${JUDGMENT_CLASS[item.judgment] || "badge-unchecked"}`, textContent: item.judgment}),
        el("span", {className: "dup-reason", textContent: ` ${item.reason}`})) : null,
      link));
  }
  return el("div", {className: "note-warn"},
    el("h3", {textContent: `Possible duplicate${duplicates.length > 1 ? "s" : ""} — you decide`}),
    context.judgment_error ? el("p", {className: "muted", textContent: context.judgment_error}) : null,
    list,
    el("p", {textContent: "Save as new keeps both records. Linking also records the connection. Skip saves nothing. Records are never merged automatically."}));
}

function lineDiff(before, after) {
  // A missing final newline is not a change worth showing (code adds it on save).
  const a = before.replace(/\n$/, "").split("\n");
  const b = after.replace(/\n$/, "").split("\n");
  const table = Array.from({length: a.length + 1}, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const lines = [];
  let i = 0, j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { lines.push(["same", a[i]]); i++; j++; }
    else if (j < b.length && (i >= a.length || table[i][j + 1] >= table[i + 1][j])) { lines.push(["add", b[j]]); j++; }
    else { lines.push(["del", a[i]]); i++; }
  }
  return el("div", {className: "diff"}, ...lines.map(([kind, text]) =>
    el("div", {className: `diff-line diff-${kind}`, textContent: `${kind === "add" ? "+ " : kind === "del" ? "− " : "  "}${text}`})));
}

// ----- Posting card: each extracted item linked to its source -----------------

const SOURCE_MARKS = {
  approximate: ["≈", "source-approx", "No exact quote. The highlighted line shares most of its words; check it."],
  missing: ["⚠", "source-missing", "No source found in your pasted text. Check this item yourself."],
};

function dateText(view) {
  if (!view) return null;
  if (!view.recognized) {
    return view.original
      ? `${view.date} (today: “${view.original}” could not be read as a date)`
      : `${view.date} (not on the page; today's date is used)`;
  }
  if (view.original) return `${view.date} (estimate: “${view.original}”, counted back from ${view.counted_from})`;
  return view.is_estimate ? `${view.date} (estimate)` : view.date;
}

// Split each display line at every source boundary, so a span can belong to
// several items. The text is shown exactly as pasted; only styling changes.
function formattedPosting(raw, lines, sources, onPick) {
  const cuts = [];
  for (const [key, source] of Object.entries(sources)) {
    for (const [start, end] of source.spans) cuts.push({start, end, key});
  }
  const box = el("div", {className: "posting-text"});
  let previousBlank = false;
  for (const line of lines) {
    if (line.kind === "blank") {
      if (!previousBlank) box.append(el("div", {className: "pt-gap"}));
      previousBlank = true;
      continue;
    }
    previousBlank = false;
    const points = new Set([line.start, line.end]);
    if (line.marker_end) points.add(line.marker_end);
    for (const cut of cuts) {
      if (cut.end <= line.start || cut.start >= line.end) continue;
      points.add(Math.max(cut.start, line.start));
      points.add(Math.min(cut.end, line.end));
    }
    const sorted = [...points].sort((a, b) => a - b);
    const row = el("div", {className: `pt-line pt-${line.kind}`});
    let used = false;
    for (let i = 0; i < sorted.length - 1; i++) {
      const [start, end] = [sorted[i], sorted[i + 1]];
      if (start === end) continue;
      const keys = cuts.filter(cut => cut.start <= start && cut.end >= end).map(cut => cut.key);
      const isMarker = line.marker_end && end <= line.marker_end;
      const span = el("span", {textContent: raw.slice(start, end), className: isMarker ? "pt-marker" : ""});
      if (keys.length && !isMarker) {
        used = true;
        span.classList.add("pt-source");
        span.dataset.keys = keys.join(" ");
        span.title = "Click to show the extracted item";
        span.addEventListener("click", () => onPick(keys[0], false));
      }
      row.append(span);
    }
    // Lines no extracted item points to are dimmed, so omissions stand out.
    if (!used && line.kind !== "header") row.classList.add("pt-unused");
    box.append(row);
  }
  return box;
}

function postingView(args, context) {
  const evidence = context.evidence || {lines: [], sources: {}};
  const sources = evidence.sources;
  let textBox = null;

  const pick = (key, scroll = true) => {
    const card = approval;
    card.querySelectorAll(".src-item.active, .pt-source.active").forEach(node => node.classList.remove("active"));
    card.querySelectorAll(`.src-item[data-key="${key}"]`).forEach(node => node.classList.add("active"));
    const spans = [...card.querySelectorAll(".pt-source")].filter(span => span.dataset.keys.split(" ").includes(key));
    spans.forEach(span => span.classList.add("active"));
    if (scroll && spans.length && textBox) {
      // Scroll only the text box, not the page.
      // .posting-scroll is positioned, so offsetTop is measured from its top.
      const top = spans[0].offsetTop - textBox.clientHeight / 3;
      textBox.scrollTo({top: Math.max(0, top), behavior: "smooth"});
    } else if (!scroll) {
      card.querySelector(`.src-item[data-key="${key}"]`)?.scrollIntoView({block: "nearest", behavior: "smooth"});
    }
  };

  const checkable = (key, node) => {
    const source = sources[key];
    if (!source) return node;
    node.classList.add("src-item");
    node.dataset.key = key;
    node.tabIndex = 0;
    node.addEventListener("click", () => pick(key));
    node.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pick(key); } });
    const mark = SOURCE_MARKS[source.status];
    const notes = [];
    if (mark) notes.push(mark[2]);
    if (source.quote_not_found) notes.push(`The assistant quoted “${source.quote}”, but that text is not in your paste.`);
    if (mark || source.quote_not_found) {
      const [symbol, className] = mark || ["⚠", "source-missing"];
      node.append(el("span", {className: `source-mark ${className}`, textContent: symbol, title: notes.join(" ")}));
      node.title = notes.join(" ");
    }
    return node;
  };

  const fields = el("dl", {className: "fields"});
  for (const [key, label] of POSTING_FIELDS) {
    if (key === "date_posted_is_estimate") continue;
    if (!(key in args) && key !== "date_posted" && !context.missing?.includes(key)) continue;
    const value = key === "date_posted" ? dateText(context.date_posted) || shown(args[key]) : shown(args[key]);
    fields.append(el("dt", {textContent: label}),
      checkable(key, el("dd", {textContent: value, className: value === "—" ? "muted" : ""})));
  }
  const requirements = el("ol", {className: "req-list"});
  (args.requirements || []).forEach((req, index) => requirements.append(checkable(`requirements.${index}`, el("li", {},
    el("span", {className: "req-id", textContent: `REQ-${index + 1}`}),
    el("span", {className: `tag ${req.type === "preferred" ? "tag-soft" : ""}`, textContent: req.type || "required"}),
    el("span", {textContent: req.text})))));
  const responsibilities = el("ul", {className: "resp-list"});
  (args.responsibilities || []).forEach((item, index) =>
    responsibilities.append(checkable(`responsibilities.${index}`, el("li", {textContent: item}))));

  // Interview preparation context: what the team does and what to research.
  const chipList = (key, items) => el("div", {className: "chips"}, ...items.map((item, index) =>
    checkable(`${key}.${index}`, el("span", {className: "chip", textContent: item}))));
  const products = args.products || [];
  const partners = args.partner_teams || [];
  const roleContext = [
    el("h3", {textContent: "About the role"}),
    args.about_role
      ? checkable("about_role", el("p", {className: "about-role", textContent: args.about_role}))
      : el("p", {className: "muted", textContent: "—"}),
    el("h3", {textContent: `Products and areas to research (${products.length})`}),
    products.length ? chipList("products", products) : el("p", {className: "muted", textContent: "None named."}),
    el("h3", {textContent: "Works with"}),
    partners.length ? chipList("partner_teams", partners) : el("p", {className: "muted", textContent: "Not stated."}),
  ];

  const counts = {quoted: 0, found: 0, approximate: 0, missing: 0};
  for (const source of Object.values(sources)) counts[source.status] += 1;
  const flagged = counts.approximate + counts.missing;
  const summary = Object.keys(sources).length
    ? el("p", {className: flagged ? "note-warn source-summary" : "note-ok source-summary", textContent:
        `Click any item to highlight where it came from in your text. ` +
        `${counts.quoted + counts.found} of ${Object.keys(sources).length} items were found word for word` +
        (flagged ? `; ${counts.missing} have no source (⚠) and ${counts.approximate} only a close match (≈) — check those first.` : ".") +
        " Dimmed lines were not used by any item: check nothing important was missed."})
    : null;

  let right;
  if (context.raw_text) {
    const formatted = formattedPosting(context.raw_text, evidence.lines, sources, pick);
    const original = el("pre", {className: "posting-original", textContent: context.raw_text, hidden: true});
    const toggle = el("button", {type: "button", className: "secondary small", textContent: "Show original"});
    toggle.addEventListener("click", () => {
      const showOriginal = original.hidden;
      original.hidden = !showOriginal;
      formatted.hidden = showOriginal;
      toggle.textContent = showOriginal ? "Show formatted" : "Show original";
    });
    textBox = el("div", {className: "posting-scroll"}, formatted, original);
    right = el("section", {className: "posting-source"},
      el("div", {className: "posting-source-head"},
        el("h3", {textContent: "Your pasted text, saved verbatim as evidence"}), toggle),
      el("p", {className: "muted small-note", textContent: "Headers and bullets are display only; the saved text is unchanged."}),
      textBox);
  } else {
    right = el("section", {}, el("p", {className: "note-warn", textContent:
      "Your pasted text was not found in this conversation, so this save will fail. Paste the posting again."}));
  }

  return [duplicatesView(context.duplicates, context, context.onLink), summary, el("div", {className: "evidence-columns posting-columns"},
    el("section", {},
      el("h3", {textContent: "Structured record to save"}), fields,
      ...roleContext,
      el("h3", {textContent: `Requirements (${(args.requirements || []).length})`}), requirements,
      el("h3", {textContent: "Responsibilities"}), responsibilities),
    right)];
}

function proposalView(toolName, args, context) {
  if (toolName === "add_posting") return postingView(args, context);
  if (toolName === "import_resume") return resumeDraftView(args.draft, context.filename);
  if (toolName === "update_experience") {
    return [el("div", {className: "evidence-columns"},
      el("section", {}, el("h3", {textContent: `Current ${args.exp_id}`}),
        context.current ? entryView(context.current) : el("p", {className: "muted", textContent: "Entry not found."})),
      el("section", {}, el("h3", {textContent: "After approval"}), entryView(args)))];
  }
  if (toolName === "propose_analysis_instructions") {
    return [
      el("p", {}, el("strong", {textContent: "Why: "}), args.reason || "—"),
      el("h3", {textContent: "Changes to your analysis instructions (− removed, + added)"}),
      lineDiff(context.before || "", args.new_text || ""),
    ];
  }
  if (toolName === "delete_experience") {
    const children = context.children || [];
    return [
      el("h3", {textContent: `${args.exp_id} will be permanently deleted`}),
      context.current ? entryView(context.current) : el("p", {className: "note-warn", textContent: `${args.exp_id} was not found, so nothing will be deleted.`}),
      children.length ? el("p", {className: "muted", textContent:
        `Its ${children.length} nested detail${children.length > 1 ? "s" : ""} (${children.map(c => c.id).join(", ")}) will be kept as standalone entries.`}) : null,
    ];
  }
  if (toolName === "propose_experience") {
    return [el("h3", {textContent: "Exact entry to save (source: user-added, not on resume)"}), entryView(args)];
  }
  return [el("pre", {textContent: JSON.stringify(args, null, 2)})];
}

function resumeDraftView(draft, filename) {
  if (!draft) return [el("p", {className: "note-warn", textContent: "The parsed resume draft is missing. Upload the resume again."})];
  const items = draft.items || [];
  const skills = draft.skills || [];
  const nodes = [el("p", {className: "muted", textContent:
    `From ${filename || "your uploaded file"}: ${items.length} items, ${skills.length} skills.`})];
  for (const category of ITEM_CATEGORIES) {
    const group = items.filter(item => item.category === category);
    if (!group.length) continue;
    nodes.push(el("h3", {className: "resume-section", textContent: CATEGORY_TITLES[category]}));
    for (const item of group) {
      const meta = [item.organization, item.dates].filter(Boolean).join(" · ");
      const details = el("ul", {className: "resume-details"});
      item.details.forEach(detail => details.append(el("li", {textContent: detail.text})));
      nodes.push(el("div", {className: "resume-item"},
        el("p", {className: "resume-headline", textContent: item.text}),
        meta ? el("p", {className: "entry-meta", textContent: meta}) : null,
        item.details.length ? details : null));
    }
  }
  if (skills.length) {
    nodes.push(el("h3", {className: "resume-section", textContent: "Skills"}),
      el("div", {className: "chips"}, ...skills.map(skill => el("span", {className: "chip", textContent: skill}))));
  }
  return nodes;
}

// ----- Edit form -------------------------------------------------------------

function textInput(label, value, {multiline = false} = {}) {
  const control = multiline ? el("textarea", {rows: 3}) : el("input", {type: "text"});
  control.value = value ?? "";
  return {node: el("label", {className: "form-field"}, el("span", {textContent: label}), control),
          get: () => control.value.trim() || null};
}

function checkInput(label, value) {
  const control = el("input", {type: "checkbox", checked: Boolean(value)});
  return {node: el("label", {className: "form-check"}, control, el("span", {textContent: label})),
          get: () => control.checked};
}

function selectInput(label, value, options) {
  const control = el("select");
  for (const option of options) control.append(el("option", {value: option, textContent: option}));
  control.value = options.includes(value) ? value : options[options.length - 1];
  return {node: el("label", {className: "form-field"}, el("span", {textContent: label}), control),
          get: () => control.value};
}

function linesInput(label, items) {
  const control = el("textarea", {rows: Math.max(3, (items || []).length + 1)});
  control.value = (items || []).join("\n");
  return {node: el("label", {className: "form-field"}, el("span", {textContent: `${label} (one per line)`}), control),
          get: () => control.value.split("\n").map(line => line.trim()).filter(Boolean)};
}

function skillsInput(skills) {
  const input = textInput("Skills (comma-separated)", (skills || []).join(", "));
  return {node: input.node, get: () => (input.get() || "").split(",").map(s => s.trim()).filter(Boolean)};
}

function entryInputs(entry, {withOnResume = false} = {}) {
  entry = entryObject(entry);
  const inputs = {
    category: selectInput("Category", entry.category, CATEGORIES),
    text: textInput("Text (one fact, your wording)", entry.text, {multiline: true}),
    skills: skillsInput(entry.skills),
  };
  for (const [key, label] of ENTRY_FIELDS) inputs[key] = textInput(label, entry[key]);
  if (withOnResume) inputs.on_resume = checkInput("On my resume", entry.on_resume);
  return inputs;
}

function detailsInput(details) {
  // Keep each bullet's skills when its text is unchanged.
  const original = new Map((details || []).map(detail => [detail.text, detail.skills || []]));
  const input = linesInput("Bullet points", (details || []).map(detail => detail.text));
  return {node: input.node, get: () => input.get().map(text => ({text, skills: original.get(text) || []}))};
}

function resumeItemInputs(item) {
  return {
    category: selectInput("Section", item.category, ITEM_CATEGORIES),
    text: textInput("Headline (role, degree, project…)", item.text),
    organization: textInput("Organization", item.organization),
    dates: textInput("Dates", item.dates),
    skills: skillsInput(item.skills),
    details: detailsInput(item.details),
  };
}

function collectInputs(inputs) {
  return Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.get()]));
}

function rowList(title, items, makeRow, blank, addLabel) {
  const rows = [];
  const container = el("div", {className: "row-list"});
  const add = item => {
    const inputs = makeRow(item);
    const row = el("fieldset", {className: "edit-row"}, ...Object.values(inputs).map(input => input.node));
    const record = {inputs, row};
    const remove = el("button", {type: "button", className: "secondary small", textContent: "Remove"});
    remove.addEventListener("click", () => { rows.splice(rows.indexOf(record), 1); row.remove(); });
    row.append(remove);
    rows.push(record);
    container.append(row);
  };
  (items || []).forEach(add);
  const addButton = el("button", {type: "button", className: "secondary small", textContent: addLabel});
  addButton.addEventListener("click", () => add(blank()));
  return {node: el("div", {}, el("h3", {textContent: title}), container, addButton),
          get: () => rows.map(({inputs}) => collectInputs(inputs))};
}

function buildEditor(toolName, args) {
  const parts = {};
  if (toolName === "add_posting") {
    for (const [key, label] of POSTING_FIELDS) {
      parts[key] = key === "date_posted_is_estimate" ? checkInput(label, args[key])
        : key === "date_posted" ? textInput("Date posted (as on the page, e.g. “6 days ago”, or YYYY-MM-DD)", args[key])
        : textInput(label, args[key], {multiline: key === "notes"});
    }
    parts.requirements = rowList("Requirements", args.requirements,
      req => ({text: textInput("Requirement", req.text, {multiline: true}),
               type: selectInput("Type", req.type, ["required", "preferred"])}),
      () => ({text: "", type: "required"}), "Add requirement");
    parts.responsibilities = linesInput("Responsibilities", args.responsibilities);
    parts.about_role = textInput("About the role (team, mission, what the role is for)", args.about_role, {multiline: true});
    parts.products = linesInput("Products and areas to research", args.products);
    parts.partner_teams = linesInput("Works with (teams)", args.partner_teams);
  } else if (toolName === "propose_analysis_instructions") {
    parts.new_text = (() => {
      const control = el("textarea", {rows: 16, className: "draft-editor"});
      control.value = args.new_text || "";
      return {node: el("label", {className: "form-field"}, el("span", {textContent: "New instructions (saved exactly as written)"}), control),
              get: () => control.value};
    })();
  } else if (toolName === "import_resume") {
    const draft = args.draft || {items: [], skills: []};
    const items = rowList("Resume items", draft.items, resumeItemInputs,
      () => ({category: "work", text: "", organization: null, dates: null, skills: [], details: []}), "Add item");
    const skills = skillsInput(draft.skills);
    parts.draft = {
      node: el("div", {}, items.node, el("h3", {textContent: "Skills"}), skills.node),
      get: () => ({items: items.get(), skills: skills.get()}),
    };
  } else {
    Object.assign(parts, entryInputs(args, {withOnResume: toolName === "update_experience"}));
  }
  const note = toolName === "add_posting"
    ? el("p", {className: "muted", textContent: "Your pasted text is kept verbatim as evidence and cannot be edited here."})
    : null;
  return {
    node: el("div", {className: "editor"}, note, ...Object.values(parts).map(part => part.node)),
    collect: () => {
      const edited = {...args};
      for (const [key, part] of Object.entries(parts)) edited[key] = part.get();
      if (toolName === "update_experience" && args.on_resume === undefined && !edited.on_resume) delete edited.on_resume;
      return edited;
    },
  };
}

function renderApproval(pending, force = false) {
  const encoded = pending ? JSON.stringify(pending) : "";
  if (!force && encoded === previousApproval) return;
  previousApproval = encoded;
  approval.replaceChildren();
  approval.classList.remove("approval-fit", "question-card");
  approval.hidden = !pending;
  if (!pending) return;
  const toolName = pending.tool_name || "write_tool";
  if (toolName === "ask_user") {
    renderQuestion(pending.arguments || {}, force);
    return;
  }
  const context = pending.context || {};
  // Show the proposal as code will save it (requirements and resume entries are
  // normalized server-side), so the card matches the saved record exactly.
  const args = {...(pending.arguments || {})};
  if (context.requirements) args.requirements = context.requirements;
  if (context.draft) args.draft = context.draft;
  const labels = {
    add_posting: "Review this posting before saving",
    import_resume: "Review your resume before saving",
    propose_experience: "Review this experience fact before saving",
    delete_experience: "Delete this experience entry?",
    propose_analysis_instructions: "Review this change to how analyses are written",
    update_experience: "Review the experience change before saving",
  };
  const hasDuplicates = toolName === "add_posting" && context.duplicates?.length;
  context.onLink = jobId => decide({decision: "link", arguments: {...args, related_postings: [jobId]}});
  const deleting = toolName === "delete_experience";
  const approve = el("button", {
    className: deleting ? "danger" : "",
    textContent: deleting ? "Delete permanently" : hasDuplicates ? "Save as new posting" : "Approve and save",
  });
  approve.addEventListener("click", () => decide({decision: "approve"}));
  // A call missing a required field cannot be saved as is; the user fills it in via Edit.
  const missing = context.missing || [];
  approve.disabled = missing.length > 0;
  const missingNote = missing.length
    ? el("p", {className: "note-warn", textContent:
        `The assistant left out required fields: ${missing.join(", ")}. Click Edit to fill them in, or Reject.`})
    : null;
  const edit = el("button", {className: "secondary", textContent: "Edit"});
  const reject = el("button", {
    className: deleting ? "secondary" : "danger",
    textContent: deleting ? "Keep it" : hasDuplicates ? "Skip — don't save" : "Reject",
  });
  reject.addEventListener("click", () => decide({decision: "reject"}));
  edit.addEventListener("click", () => {
    const editor = buildEditor(toolName, args);
    const save = el("button", {textContent: "Save my edited version"});
    save.addEventListener("click", () => {
      const edited = editor.collect();
      decide(sameDraft(edited, args) ? {decision: "approve"} : {decision: "edit", arguments: edited});
    });
    const cancel = el("button", {className: "secondary", textContent: "Cancel"});
    cancel.addEventListener("click", () => renderApproval(pending, true));
    approval.classList.remove("approval-fit");
    approval.replaceChildren(
      el("h2", {textContent: "Edit before saving"}),
      el("p", {className: "muted", textContent: "Your edited version is saved exactly as you leave it; the assistant does not rewrite it."}),
      editor.node,
      el("div", {className: "approval-actions"}, save, cancel));
  });
  approval.append(
    el("h2", {textContent: labels[toolName] || `Review ${toolName} before continuing`}),
    ...[missingNote, ...proposalView(toolName, args, context)].filter(Boolean),
    el("div", {className: "approval-actions"}, approve, deleting ? null : edit, reject));
  if (toolName === "add_posting") {
    approval.classList.add("approval-fit");
    // Bring a new card fully into view once; later polls do not move the page.
    // An instant jump: a smooth scroll is cancelled by the refresh that follows.
    if (!force) requestAnimationFrame(() => approval.scrollIntoView({block: "start"}));
  }
}

// Up to three likely next requests, chosen by the server after each turn.
function renderNextSteps(steps) {
  const encoded = JSON.stringify(steps || []);
  if (encoded === previousSteps) return;
  previousSteps = encoded;
  nextSteps.replaceChildren();
  nextSteps.hidden = !steps?.length;
  if (!steps?.length) return;
  nextSteps.append(el("span", {className: "next-steps-label", textContent: "Next:"}));
  for (const step of steps) {
    const button = el("button", {type: "button", className: "secondary small", textContent: step.label});
    button.addEventListener("click", () => {
      if (step.message) {
        askInChat(step.message);
      } else if (step.action === "compose") {
        promptBox.placeholder = "Paste the job posting here, then press Send.";
        promptBox.focus();
        promptBox.scrollIntoView({block: "center"});
      } else if (step.action === "tab") {
        showTab(step.tab);
      } else if (step.action === "upload") {
        resumeForm.scrollIntoView({block: "center"});
        resumeFile.focus();
      }
    });
    nextSteps.append(button);
  }
}

// A clarifying question from the assistant (ask_user). Nothing is saved; the
// answer goes back to the assistant, and skipping is always allowed.
function renderQuestion(args, force) {
  approval.classList.add("question-card");
  const box = el("textarea", {rows: 2, maxLength: 2000, placeholder: "Type your answer, then press Enter"});
  const answer = text => { if (text.trim()) decide({decision: "answer", answer: text.trim()}); };
  const reply = el("button", {type: "button", textContent: "Send answer"});
  reply.addEventListener("click", () => answer(box.value));
  box.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); answer(box.value); }
  });
  const skip = el("button", {type: "button", className: "secondary", textContent: "Skip this question"});
  skip.addEventListener("click", () => decide({decision: "reject"}));
  const options = (args.options || []).map(option => {
    const button = el("button", {type: "button", className: "secondary", textContent: option});
    button.addEventListener("click", () => answer(option));
    return button;
  });
  approval.append(...[
    el("h2", {textContent: "The assistant has a question"}),
    el("p", {className: "question-text", textContent: args.question || ""}),
    args.reason ? el("p", {className: "muted", textContent: `Why it's asking: ${args.reason}`}) : null,
    options.length ? el("div", {className: "question-options"}, ...options) : null,
    box,
    el("div", {className: "approval-actions"}, reply, skip,
      el("span", {className: "muted small-note", textContent: "Nothing is saved from this answer."})),
  ].filter(Boolean));
  if (!force) requestAnimationFrame(() => { approval.scrollIntoView({block: "center"}); box.focus({preventScroll: true}); });
}

async function decide(payload) {
  const asking = approval.classList.contains("question-card");
  try {
    await request("/api/approval", payload);
    renderApproval(null);
    statusLine.textContent = payload.decision === "answer" ? "Answer sent. The assistant is continuing…"
      : payload.decision === "link"
      ? "Saving as a new posting linked to the existing one…"
      : payload.decision === "edit"
      ? "Saving your edited version…"
      : payload.decision === "approve" ? "Approved. Saving…"
      : asking ? "Skipped. The assistant will continue without it." : "Rejected. Nothing was saved.";
    await refresh();
  } catch (error) { statusLine.textContent = error.message; }
}

async function refresh() {
  try {
    const state = await request("/api/state");
    renderTurns(state.turns);
    renderApproval(state.pending);
    renderNextSteps(submitting ? [] : state.suggestions);
    if (typeof notePending === "function") notePending(Boolean(state.pending));
    send.disabled = submitting || state.busy;
    uploadResume.disabled = submitting || state.busy;
    if (!state.pending) statusLine.textContent = state.busy ? "Assistant is working…" : "Ready";
  } catch (error) {
    send.disabled = true;
    statusLine.textContent = `Cannot connect: ${error.message}`;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = promptBox.value;
  if (!message.trim() || send.disabled) return;
  submitting = true;
  send.disabled = true;
  statusLine.textContent = "Sending…";
  request("/api/chat", {message}).catch(error => { statusLine.textContent = error.message; });
  if (promptBox.value === message) promptBox.value = "";
  await refresh();
  submitting = false;
});

resumeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = resumeFile.files[0];
  if (!file || uploadResume.disabled) return;
  submitting = true;
  send.disabled = true;
  uploadResume.disabled = true;
  statusLine.textContent = "Reading your selected resume…";
  try {
    const uploaded = await uploadSelectedResume(file);
    resumeFile.value = "";
    statusLine.textContent = "Resume uploaded. Preparing entries for your review…";
    request("/api/chat", {
      message: `Import the resume I uploaded. Upload ID: ${uploaded.upload_id}`,
    }).catch(error => { statusLine.textContent = error.message; });
    await refresh();
  } catch (error) {
    statusLine.textContent = error.message;
  } finally {
    submitting = false;
  }
});

refresh();
setInterval(refresh, 700);
