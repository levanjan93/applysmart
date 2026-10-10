// Evidence review for a saved fit analysis (plan sections 5.3 and 8).
// Uses el(), entryView(), and request() from chat.js.

const analysisView = document.querySelector("#analysis-view");
const RATING_LABELS = {
  supported: "Supported", stretched: "Stretched", unsupported: "Unsupported",
  invalid: "Invalid citation", unchecked: "Not checked",
};

function claimRating(claim) {
  if (!claim.valid) return "invalid";
  return claim.check?.rating || "unchecked";
}

function ratingBadge(rating) {
  return el("span", {className: `badge badge-${rating}`, textContent: RATING_LABELS[rating]});
}

function postingExcerpt(lines, highlight) {
  const box = el("div", {className: "raw-posting"});
  if (!lines.length) return el("p", {className: "muted", textContent: "No saved posting text."});
  lines.forEach((line, index) => {
    box.append(el("div", {className: index === highlight ? "raw-line highlight" : "raw-line", textContent: line || " "}));
  });
  return box;
}

function citedEntries(claim, experience) {
  const list = el("div", {className: "cited"});
  for (const expId of claim.evidence) {
    const entry = experience[expId];
    list.append(entry
      ? el("div", {},
          el("p", {className: "cited-id", textContent: `${expId} · ${entry.source}${entry.on_resume ? " · on resume" : " · not on resume"}`}),
          entryView(entry))
      : el("p", {className: "note-warn", textContent: `${expId} was cited but has since been deleted.`}));
  }
  for (const expId of claim.invalid_evidence || []) {
    list.append(el("p", {className: "invalid-cite", textContent: `${expId} — cited by the model, but no such entry exists.`}));
  }
  if (!list.children.length) list.append(el("p", {className: "muted", textContent: "No experience was cited."}));
  return list;
}

function reviewButtons(analysisId, claim, openedAt) {
  const status = el("span", {className: "muted", textContent: claim.review ? `You ${claim.review.decision}d.` : ""});
  const make = (decision, label) => {
    const button = el("button", {
      type: "button", textContent: label,
      className: `small ${claim.review?.decision === decision ? "" : "secondary"}`,
    });
    button.addEventListener("click", async () => {
      try {
        const seconds = (performance.now() - openedAt.value) / 1000;
        const result = await request("/api/claim-review", {
          analysis_id: analysisId, requirement_id: claim.requirement_id, decision, seconds,
        });
        claim.review = result.claim.review;
        agree.className = `small ${decision === "agree" ? "" : "secondary"}`;
        disagree.className = `small ${decision === "disagree" ? "" : "secondary"}`;
        status.textContent = `You ${decision}d. Logged.`;
        const summaryStatus = button.closest("details")?.querySelector(".review-status");
        if (summaryStatus) summaryStatus.textContent = decision === "agree" ? "✓ agreed" : "✗ disagreed";
      } catch (error) { status.textContent = error.message; }
    });
    return button;
  };
  const agree = make("agree", "I agree");
  const disagree = make("disagree", "I disagree");
  return el("div", {className: "approval-actions"}, agree, disagree, status);
}

function claimCard(analysisId, claim, view) {
  const rating = claimRating(claim);
  const openedAt = {value: performance.now()};
  const details = el("details", {className: `claim claim-${rating}`});
  details.append(el("summary", {},
    ratingBadge(rating),
    el("span", {className: "tag", textContent: claim.verdict}),
    el("span", {className: "req-id", textContent: claim.requirement_id}),
    el("span", {className: "claim-text", textContent: claim.requirement_text}),
    el("span", {className: "review-status", textContent: claim.review ? (claim.review.decision === "agree" ? "✓ agreed" : "✗ disagreed") : ""})));
  const body = el("div", {className: "claim-body"});
  details.append(body);
  details.addEventListener("toggle", () => {
    if (!details.open || body.children.length) return;
    openedAt.value = performance.now();
    const excerpt = postingExcerpt(view.raw_lines, claim.raw_line);
    body.append(
      el("p", {}, el("strong", {textContent: "Analysis: "}), claim.explanation || "—"),
      claim.check ? el("p", {}, el("strong", {textContent: `Checker (${claim.check.by === "code" ? "code rule" : "independent model"}): `}), claim.check.reason) : null,
      ...(claim.issues || []).map(issue => el("p", {className: "invalid-cite", textContent: issue})),
      el("div", {className: "evidence-columns"},
        el("section", {},
          el("h3", {textContent: `${claim.requirement_id} in the posting (${claim.requirement_type})`}),
          el("p", {className: "req-quote", textContent: claim.requirement_text}),
          claim.raw_line === null || claim.raw_line === undefined
            ? el("p", {className: "muted", textContent: "The matching line could not be located in your pasted text."}) : null,
          excerpt),
        el("section", {},
          el("h3", {textContent: "Cited experience"}),
          citedEntries(claim, view.experience))),
      reviewButtons(analysisId, claim, openedAt));
    const line = excerpt.querySelector?.(".highlight");
    if (line) excerpt.scrollTop = line.offsetTop - excerpt.offsetTop - 40;
  });
  return details;
}

function gapList(claims, type) {
  const items = claims.filter(claim => claim.verdict === "gap" && claim.requirement_type === type);
  if (!items.length) return el("p", {className: "muted", textContent: `No ${type} gaps.`});
  const list = el("ul", {className: "plain-list"});
  for (const claim of items) {
    list.append(el("li", {},
      el("span", {className: "req-id", textContent: claim.requirement_id}),
      el("span", {textContent: claim.requirement_text}),
      claim.issues?.length ? el("span", {className: "muted", textContent: ` (${claim.issues.join(" ")})`}) : null));
  }
  return list;
}

function renderAnalysis(view) {
  const analysis = view.analysis;
  const matches = analysis.claims.filter(claim => claim.verdict !== "gap" || !claim.valid);
  const counts = {};
  for (const claim of matches) counts[claimRating(claim)] = (counts[claimRating(claim)] || 0) + 1;
  const close = el("button", {type: "button", className: "secondary small", textContent: "Close"});
  close.addEventListener("click", () => { analysisView.hidden = true; analysisView.replaceChildren(); });

  const fits = el("div", {className: "claims"});
  matches.forEach(claim => fits.append(claimCard(analysis.analysis_id, claim, view)));

  const resume = el("ul", {className: "plain-list"});
  for (const item of analysis.add_to_resume) {
    resume.append(el("li", {},
      el("span", {className: "req-id", textContent: item.exp_id}),
      el("span", {textContent: `${item.text} — supports ${item.requirement_ids.join(", ")}`})));
  }
  const questions = el("ul", {className: "plain-list"});
  for (const item of analysis.questions) {
    questions.append(el("li", {}, el("span", {className: "req-id", textContent: item.requirement_id}), el("span", {textContent: item.question})));
  }

  analysisView.replaceChildren(
    el("div", {className: "analysis-head"},
      el("h2", {textContent: `Evidence review — ${analysis.job_id} (${analysis.job_title}, ${analysis.company})`}),
      close),
    el("div", {className: "summary-badges"},
      ...["invalid", "unsupported", "stretched", "unchecked", "supported"]
        .filter(rating => counts[rating])
        .map(rating => el("span", {className: `badge badge-${rating}`, textContent: `${counts[rating]} ${RATING_LABELS[rating].toLowerCase()}`}))),
    el("p", {className: "muted", textContent: "Claims flagged by the independent checker come first. Open a claim to compare the requirement with the cited experience, then record whether you agree."}),
    el("h3", {textContent: "What fits — claims to verify"}),
    matches.length ? fits : el("p", {className: "muted", textContent: "No matches were claimed."}),
    el("h3", {textContent: "What doesn't fit"}),
    el("p", {className: "subhead", textContent: "Required"}), gapList(analysis.claims, "required"),
    el("p", {className: "subhead", textContent: "Preferred"}), gapList(analysis.claims, "preferred"),
    el("h3", {textContent: "Add to your resume"}),
    analysis.add_to_resume.length ? resume : el("p", {className: "muted", textContent: "No saved experience outside your resume supports these requirements."}),
    el("h3", {textContent: "Questions for you"}),
    analysis.questions.length ? questions : el("p", {className: "muted", textContent: "No questions."}),
    el("p", {className: "muted small-print", textContent:
      `Analysis: ${analysis.models?.analysis || "unknown model"} · Claim check: ${analysis.models?.check || "unknown model"}` +
      (analysis.check_status && analysis.check_status !== "done" ? ` (check ${analysis.check_status})` : "")}),
  );
  if (analysis.invalid_claims?.length) {
    analysisView.append(el("p", {className: "invalid-cite", textContent:
      "Discarded model claims: " + analysis.invalid_claims.map(item => item.issue).join(" ")}));
  }
}

async function openAnalysis(analysisId) {
  try {
    const view = await request(`/api/analysis?id=${encodeURIComponent(analysisId)}`);
    analysisView.hidden = false;
    renderAnalysis(view);
    analysisView.scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) { statusLine.textContent = error.message; }
}
