// Courtroom RAG frontend. Plain JS, no framework, no build step.

// where the backend lives. empty = same server. only set in config.js if the UI is hosted elsewhere
const API = (window.COURTROOM_API || "").replace(/\/$/, "");

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = {
  cases: [],
  caseKey: null,
  rounds: 2,
  exhibits: {},      // id -> chunk seen in this trial
  citedBy: {},       // id -> Set('for' | 'against' | 'judge')
  retrievedBy: {},   // id -> Set('for' | 'against')
  admitted: new Set(),
  lastAdmitted: null,
  transcript: null,
  es: null,
  claim: "",
  allExhibits: null,
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const shortTitle = (t) => String(t || "").split("—").pop().split(":")[0].trim();

const VERDICT_TEXT = {
  SUPPORTED: "Supported",
  NOT_SUPPORTED: "Not supported",
  MIXED: "Partly supported",
  INSUFFICIENT: "Insufficient evidence",
};

/* ---------------- theme ---------------- */
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch (_) {}
  document.documentElement.dataset.theme = saved || "light";
  $("#themeBtn").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (_) {}
  });
}

/* ---------------- status + cases ---------------- */
async function loadStatus() {
  // free hosting sleeps after 15 min idle; the first request can take about a minute
  const slow = setTimeout(() => flash("Starting the server. On free hosting the first load after a quiet period can take up to a minute."), 3000);
  try {
    const s = await (await fetch(API + "/api/status")).json();
    clearTimeout(slow); $("#notice").hidden = true;
    if (s.uploads === false) $("#uploadForm").hidden = true;
    const el = $("#mode");
    el.textContent = s.mode === "llm" ? s.label : "Offline mode (rule-based)";
    el.className = "mode " + s.mode;
    el.title = s.mode === "llm" ? "Agents are using this model" : "Set LLM_PROVIDER and LLM_API_KEY to use a language model";
  } catch (_) {
    clearTimeout(slow);
    $("#mode").textContent = "Server unavailable";
    flash("The server could not be reached. Check that it is running, then reload the page.");
  }
}

async function loadCases(selectKey) {
  try { state.cases = await (await fetch(API + "/api/cases")).json(); } catch (_) { return; }
  $("#caseList").innerHTML = state.cases.map((c) => `
    <li><button class="case" role="radio" aria-checked="false" data-key="${esc(c.key)}">
      <span class="case-name">${esc(c.name)}</span>
      <span class="case-meta">${c.docs.length} document${c.docs.length === 1 ? "" : "s"}${c.uploaded ? ", uploaded" : ""}</span>
    </button></li>`).join("");
  $$(".case").forEach((b) => b.addEventListener("click", () => selectCase(b.dataset.key)));
  selectCase(selectKey || state.caseKey || state.cases[0]?.key);
}

function selectCase(key) {
  if (!key) return;
  state.caseKey = key;
  state.allExhibits = null;
  $$(".case").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.key === key)));
  const c = state.cases.find((x) => x.key === key);
  $("#docList").innerHTML = (c?.docs || []).map((d) => `<li title="${esc(d)}">${esc(d.replace(/^\d+_/, "").replace(/\.\w+$/, "").replace(/_/g, " "))}</li>`).join("");
  $("#samples").innerHTML = (c?.claims || []).map((s) => `<button type="button" class="example">${esc(s)}</button>`).join("") || `<span class="hint">None for this case file</span>`;
  $$("#samples .example").forEach((b) => b.addEventListener("click", () => { $("#claim").value = b.textContent; $("#claim").focus(); }));
  $("#caseHint").textContent = c ? `Tested against: ${c.name}` : "";
}

/* ---------------- rounds toggle ---------------- */
$$(".seg-btn").forEach((b) => b.addEventListener("click", () => {
  state.rounds = Number(b.dataset.rounds);
  $$(".seg-btn").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", String(x === b)); });
}));

/* ---------------- steps ---------------- */
const ORDER = ["retrieval", "opening", "rebuttal", "admitted", "verdict"];
function setStep(name) {
  const idx = ORDER.indexOf(name);
  $$("#steps li").forEach((li, i) => {
    li.classList.toggle("done", i < idx);
    li.classList.toggle("active", i === idx);
    li.classList.toggle("skip", state.rounds === 1 && li.dataset.step === "rebuttal");
  });
}
function finishSteps() { $$("#steps li").forEach((li) => { li.classList.remove("active"); if (!li.classList.contains("skip")) li.classList.add("done"); }); }
function resetSteps() { $$("#steps li").forEach((li) => li.classList.remove("done", "active", "skip")); }

/* ---------------- helpers ---------------- */
function tag(id, cls = "", withDoc = false, bad = false) {
  const e = state.exhibits[id];
  if (bad) return `<span class="tag bad" title="Not in this side's retrieved evidence, so the point was rejected">${esc(id)}</span>`;
  const doc = withDoc && e ? `<span class="doc">${esc(shortTitle(e.title))}</span>` : "";
  return `<button type="button" class="tag ${cls}" data-ex="${esc(id)}" title="Open exhibit ${esc(id)}">${esc(id)}${doc}</button>`;
}
function mark(set, id, who) { (set[id] ||= new Set()).add(who); }

function updateCounts() {
  for (const side of ["for", "against"]) {
    const retrieved = Object.values(state.retrievedBy).filter((s) => s.has(side)).length;
    const cited = Object.values(state.citedBy).filter((s) => s.has(side)).length;
    $(side === "for" ? "#countFor" : "#countAgainst").textContent = retrieved ? `${retrieved} retrieved, ${cited} cited` : "";
  }
}

function flash(msg) { const n = $("#notice"); n.textContent = msg; n.hidden = false; }

/* ---------------- trial ---------------- */
function resetTrial() {
  Object.assign(state, { exhibits: {}, citedBy: {}, retrievedBy: {}, admitted: new Set(), lastAdmitted: null });
  state.transcript = { claim: state.claim, case: state.caseKey, events: [] };
  $("#empty").hidden = true;
  $("#floor").hidden = false;
  $("#bench").hidden = true;
  $("#evidence").hidden = true;
  $("#notice").hidden = true;
  for (const side of ["for", "against"]) {
    $(`#disc-${side}`).innerHTML = `<p class="disc-label">Searching documents…</p>`;
    $(`#args-${side}`).innerHTML = "";
  }
  resetSteps();
  updateCounts();
}

function typing(side) {
  const el = $(`#args-${side}`);
  $(".typing", el)?.remove();
  el.insertAdjacentHTML("beforeend", `<div class="typing"><span class="spinner" aria-hidden="true"></span>Preparing argument</div>`);
}
function stopTyping(side) { $(`#args-${side} .typing`)?.remove(); }

function startTrial() {
  const claim = $("#claim").value.trim();
  if (claim.length < 5) { $("#claim").focus(); flash("Enter a claim of at least a few words."); return; }
  if (!state.caseKey) return;
  state.claim = claim;
  state.es?.close();
  resetTrial();
  const btn = $("#startBtn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner" aria-hidden="true"></span><span>Running</span>`;

  const url = API + `/api/trial?case=${encodeURIComponent(state.caseKey)}&claim=${encodeURIComponent(claim)}&rounds=${state.rounds}`;
  const es = new EventSource(url);
  state.es = es;
  setStep("retrieval");

  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    state.transcript.events.push(ev);
    handle(ev);
    if (ev.type === "done" || ev.type === "error") { es.close(); endTrial(); }
  };
  es.onerror = () => {
    es.close();
    if (!$("#bench").hidden) return;
    flash("The connection to the server was lost. Run the trial again.");
    endTrial();
  };
}

function endTrial() {
  const btn = $("#startBtn");
  btn.disabled = false;
  btn.innerHTML = `<span>Run trial</span>`;
  stopTyping("for"); stopTyping("against");
}

function handle(ev) {
  switch (ev.type) {
    case "retrieval": {
      ev.exhibits.forEach((e) => { state.exhibits[e.id] = e; mark(state.retrievedBy, e.id, ev.side); });
      $(`#disc-${ev.side}`).innerHTML = ev.exhibits.length
        ? `<p class="disc-label">Retrieved in discovery</p><div class="tag-row">${ev.exhibits.map((e) => tag(e.id, ev.side, true)).join("")}</div>`
        : `<p class="disc-label">No relevant passages found.</p>`;
      updateCounts();
      if (ev.side === "against") { setStep("opening"); typing("for"); }
      break;
    }
    case "argument": {
      stopTyping(ev.side);
      renderRound(ev);
      ev.points.filter((p) => !p.struck).forEach((p) => p.evidence.forEach((id) => mark(state.citedBy, id, ev.side)));
      updateCounts();
      if (ev.round === "opening" && ev.side === "for") typing("against");
      else if (ev.round === "opening" && ev.side === "against" && state.rounds === 2) { setStep("rebuttal"); typing("for"); }
      else if (ev.round === "rebuttal" && ev.side === "for") typing("against");
      break;
    }
    case "admitted":
      setStep("admitted");
      ev.ids.forEach((id) => state.admitted.add(id));
      state.lastAdmitted = ev;
      break;
    case "verdict":
      setStep("verdict");
      renderVerdict(ev);
      renderEvidence();
      break;
    case "notice":
    case "error":
      flash(ev.message);
      break;
    case "done":
      finishSteps();
      $("#elapsed").textContent = `Completed in ${ev.seconds} s`;
      break;
  }
}

function renderRound(ev) {
  const pts = ev.points.map((p) => `
    <li class="point ${p.struck ? "struck" : ""}">
      <p>${esc(p.text)}</p>
      <div class="tag-row">
        ${p.evidence.map((id) => tag(id, ev.side)).join("")}
        ${(p.invalid || []).map((id) => tag(id, ev.side, false, true)).join("")}
      </div>
      ${p.struck ? `<span class="objection">Struck: no valid exhibit cited</span>` : ""}
    </li>`).join("");
  $(`#args-${ev.side}`).insertAdjacentHTML("beforeend", `
    <section class="round">
      <h3 class="round-name">${ev.round === "opening" ? "Opening argument" : "Rebuttal"}</h3>
      ${ev.summary ? `<p class="round-summary">${esc(ev.summary)}</p>` : ""}
      <ol class="points">${pts || `<li class="point"><p>No points made.</p></li>`}</ol>
    </section>`);
}

function renderVerdict(v) {
  const bench = $("#bench");
  bench.hidden = false;
  bench.classList.remove("reveal");
  bench.dataset.v = v.verdict;
  $("#verdictWord").textContent = VERDICT_TEXT[v.verdict] || v.verdict;
  $("#headline").textContent = v.headline || "";
  $("#confNum").textContent = `${v.confidence}%`;
  $("#confBar").style.width = "0";

  (v.reasoning || []).forEach((r) => r.evidence.forEach((id) => mark(state.citedBy, id, "judge")));
  $("#reasoning").innerHTML = (v.reasoning || []).map((r) => `
    <li><p>${esc(r.text)}</p><div class="tag-row">${r.evidence.map((id) => tag(id, "judge")).join("")}</div></li>`).join("")
    || `<li><p>No reasons citing admitted exhibits were given.</p></li>`;
  const weak = [...(v.weak_points || []), ...(v.struck_reasoning || []).map((r) => `Removed from the ruling for citing evidence that was not admitted: ${r.text}`)];
  $("#weak").innerHTML = weak.length ? weak.map((w) => `<li>${esc(w)}</li>`).join("") : "<li>Nothing set aside.</li>";

  const a = state.lastAdmitted || { ids: [], unused: [], struck_points: 0 };
  $("#benchStats").innerHTML = `
    <dt>Exhibits admitted</dt><dd>${a.ids.length}</dd>
    <dt>Retrieved, not cited</dt><dd>${a.unused.length}</dd>
    <dt>Points struck</dt><dd>${a.struck_points}</dd>`;

  requestAnimationFrame(() => requestAnimationFrame(() => {
    bench.classList.add("reveal");
    $("#confBar").style.width = `${v.confidence}%`;
    bench.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }));
}

function renderEvidence() {
  const ids = Object.keys(state.exhibits).sort((x, y) => Number(x.slice(1)) - Number(y.slice(1)));
  if (!ids.length) return;
  const who = (set, map) => [...(set || [])].map((k) => map[k]).filter(Boolean).join("");
  const retrievedMap = { for: `<span class="p">Prosecution</span>`, against: `<span class="d">Defense</span>` };
  const citedMap = { ...retrievedMap, judge: `<span class="j">Judge</span>` };
  $("#evidenceRows").innerHTML = ids.map((id) => {
    const e = state.exhibits[id];
    const adm = state.admitted.has(id);
    return `<tr data-ex="${esc(id)}">
      <td class="ev-id">${esc(id)}</td>
      <td><span class="ev-src">${esc(shortTitle(e.title))}</span>${e.section ? `<span class="ev-sec">${esc(e.section)}</span>` : ""}</td>
      <td><span class="who">${who(state.retrievedBy[id], retrievedMap) || "—"}</span></td>
      <td><span class="who">${who(state.citedBy[id], citedMap) || "—"}</span></td>
      <td><span class="status ${adm ? "adm" : "not"}">${adm ? "Admitted" : "Not cited"}</span></td>
    </tr>`;
  }).join("");
  $("#evidenceHint").textContent = `${state.admitted.size} of ${ids.length} exhibits admitted`;
  $("#evidence").hidden = false;
}

/* ---------------- drawer ---------------- */
function openDrawer(title, html) {
  $("#drawerTitle").textContent = title;
  $("#drawerBody").innerHTML = html;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  $("#closeDrawer").focus();
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
}

function highlight(text, claim) {
  const words = [...new Set((claim || "").toLowerCase().match(/[a-z0-9₹]{4,}/g) || [])]
    .filter((w) => !["that", "this", "with", "from", "their", "they", "will", "have", "should", "every", "more"].includes(w));
  const out = esc(text);
  if (!words.length) return out;
  const re = new RegExp(`\\b(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").slice(0, Math.max(4, w.length - 2))).join("|")})\\w*`, "gi");
  return out.replace(re, "<mark>$&</mark>");
}

function showExhibit(id) {
  const e = state.exhibits[id] || (state.allExhibits || []).find((x) => x.id === id);
  if (!e) return;
  const who = [];
  const r = state.retrievedBy[id], c = state.citedBy[id];
  if (r?.has("for")) who.push(`<span class="badge for">Retrieved by prosecution</span>`);
  if (r?.has("against")) who.push(`<span class="badge against">Retrieved by defense</span>`);
  if (c?.has("for")) who.push(`<span class="badge for">Cited by prosecution</span>`);
  if (c?.has("against")) who.push(`<span class="badge against">Cited by defense</span>`);
  if (c?.has("judge")) who.push(`<span class="badge adm">Relied on by the judge</span>`);
  if (state.admitted.has(id)) who.push(`<span class="badge adm">Admitted</span>`);
  else if (r) who.push(`<span class="badge">Not admitted</span>`);
  openDrawer(`Exhibit ${id}`, `
    <p class="ex-meta">${esc(e.doc)}${e.section ? " / " + esc(e.section) : ""}</p>
    <p class="ex-doc">${esc(e.title)}</p>
    <div class="ex-text">${highlight(e.text, state.claim || $("#claim").value)}</div>
    <div class="ex-who">${who.join("")}</div>
    ${e.score != null ? `<p class="ex-score">Retrieval score ${e.score} (relevance ${e.relevance} before stance weighting)</p>` : ""}`);
}

async function browseExhibits() {
  if (!state.caseKey) return;
  if (!state.allExhibits) {
    try { state.allExhibits = await (await fetch(API + `/api/cases/${state.caseKey}/exhibits`)).json(); } catch (_) { return; }
  }
  const render = (q = "") => {
    const ql = q.toLowerCase();
    const items = state.allExhibits.filter((e) => !ql || (e.text + e.title + e.section).toLowerCase().includes(ql));
    $("#browseList").innerHTML = items.map((e) => `
      <li><button type="button" class="browse-item" data-ex="${e.id}">
        <strong>${e.id}  ${esc(shortTitle(e.title))}${e.section ? " / " + esc(e.section) : ""}</strong>
        <span>${esc(e.text)}</span></button></li>`).join("") || `<li class="browse-item">No exhibits match.</li>`;
  };
  const c = state.cases.find((x) => x.key === state.caseKey);
  openDrawer(`Exhibits in ${c?.name || "case file"}`, `
    <input id="browseQ" class="input browse-search" type="search" placeholder="Filter exhibits">
    <ul id="browseList" class="browse-list"></ul>`);
  render();
  $("#browseQ").addEventListener("input", (e) => render(e.target.value));
}

/* ---------------- upload ---------------- */
function initUpload() {
  const input = $("#fileInput"), drop = $("#drop"), btn = $("#uploadBtn"), msg = $("#uploadMsg");
  const refresh = () => {
    const n = input.files.length;
    btn.disabled = n === 0;
    $(".drop-main", drop).textContent = n ? `${n} file${n === 1 ? "" : "s"} selected` : "Drop files or click to browse";
  };
  input.addEventListener("change", refresh);
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); input.files = e.dataTransfer.files; refresh(); });

  $("#uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("name", $("#caseName").value);
    [...input.files].forEach((f) => fd.append("files", f));
    btn.disabled = true; msg.textContent = "Indexing documents…";
    try {
      const r = await fetch(API + "/api/upload", { method: "POST", body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Upload failed.");
      msg.textContent = `Added ${data.saved.length} document${data.saved.length === 1 ? "" : "s"}.`;
      input.value = ""; $("#caseName").value = ""; refresh();
      await loadCases(data.key);
    } catch (err) {
      msg.textContent = err.message;
      btn.disabled = false;
    }
  });
}

/* ---------------- transcript download ---------------- */
function downloadTranscript() {
  const t = state.transcript;
  if (!t) return;
  const lines = [`# Courtroom RAG transcript`, ``, `**Claim:** ${t.claim}`, `**Case file:** ${t.case}`, ``];
  for (const ev of t.events) {
    if (ev.type === "retrieval") lines.push(`## Discovery (${ev.side === "for" ? "Prosecution" : "Defense"})`, ev.exhibits.map((e) => `- ${e.id}: ${e.doc} / ${e.section || "-"} (score ${e.score})`).join("\n"), "");
    if (ev.type === "argument") {
      lines.push(`## ${ev.side === "for" ? "Prosecution" : "Defense"}: ${ev.round}`, ev.summary ? `_${ev.summary}_` : "");
      ev.points.forEach((p) => lines.push(`- ${p.struck ? "~~" : ""}${p.text}${p.struck ? "~~ (struck)" : ""} [${p.evidence.join(", ")}]`));
      lines.push("");
    }
    if (ev.type === "verdict") {
      lines.push(`## Ruling: ${VERDICT_TEXT[ev.verdict]} (${ev.confidence}% confidence)`, ev.headline, "");
      ev.reasoning.forEach((r, i) => lines.push(`${i + 1}. ${r.text} [${r.evidence.join(", ")}]`));
      lines.push("", "## Admitted exhibits");
      [...state.admitted].forEach((id) => { const e = state.exhibits[id]; if (e) lines.push(`**${id}** (${e.doc}): ${e.text.replace(/\n/g, " ")}`, ""); });
    }
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "courtroom-transcript.md" });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ---------------- wire up ---------------- */
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-ex]");
  if (t) showExhibit(t.dataset.ex);
});
$("#closeDrawer").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && document.activeElement === $("#claim")) startTrial();
});
$("#startBtn").addEventListener("click", startTrial);
$("#browseBtn").addEventListener("click", browseExhibits);
$("#downloadBtn").addEventListener("click", downloadTranscript);
$("#docketToggle").addEventListener("click", (e) => {
  const open = $(".sidebar").classList.toggle("open");
  e.target.setAttribute("aria-expanded", String(open));
  e.target.textContent = open ? "Hide documents" : "Show documents";
});

initTheme();
initUpload();
loadStatus().then(() => loadCases());
