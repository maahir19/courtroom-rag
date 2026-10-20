// Courtroom RAG frontend. Plain JS, no framework — didn't want a build step for a demo.

// where the backend lives. empty = same server (local). on netlify, config.js sets it to the render url
const API = (window.COURTROOM_API || "").replace(/\/$/, "");

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = {
  cases: [],
  caseKey: null,
  rounds: 2,
  exhibits: {},      // id -> chunk (everything seen in this trial)
  citedBy: {},       // id -> Set('for' | 'against' | 'judge')
  retrievedBy: {},   // id -> Set('for' | 'against')
  coins: { for: [], against: [] },
  admitted: new Set(),
  transcript: null,
  es: null,
  claim: "",
  allExhibits: null,
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const VERDICT_TEXT = {
  SUPPORTED: "Supported",
  NOT_SUPPORTED: "Not supported",
  MIXED: "Partly supported",
  INSUFFICIENT: "Unproven",
};

const CASE_ICONS = {
  // building = policies, flask = research, briefcase = job, folder = anything uploaded
  policies: `<path d="M3 21h18M5 21V9l7-5 7 5v12M9 21v-6h6v6M9 11h.01M15 11h.01"/>`,
  research: `<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3M7.5 14h9"/>`,
  job: `<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/>`,
  other: `<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>`,
};
function caseIcon(key) {
  if (/polic/.test(key)) return CASE_ICONS.policies;
  if (/research|paper/.test(key)) return CASE_ICONS.research;
  if (/job|offer/.test(key)) return CASE_ICONS.job;
  return CASE_ICONS.other;
}

/* ---------------- theme (dark by default) ---------------- */
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch (_) {}
  document.documentElement.dataset.theme = saved || "dark";
  $("#themeBtn").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (_) {}
  });
}

/* ---------------- status + cases ---------------- */
async function loadStatus() {
  // free hosting sleeps after 15 min idle; first request can take ~1 min
  const slow = setTimeout(() => flash("Waking up the courtroom server. Free hosting sleeps when idle, so this first load can take up to a minute."), 3000);
  try {
    const s = await (await fetch(API + "/api/status")).json();
    clearTimeout(slow); $("#notice").hidden = true;
    if (s.uploads === false) $("#uploadForm").hidden = true;
    const el = $("#mode");
    el.textContent = s.mode === "llm" ? s.label : "Offline mode: rule-based agents";
    el.className = "mode " + s.mode;
    el.title = s.mode === "llm" ? "Agents are using this model" : "Set LLM_PROVIDER and LLM_API_KEY in .env to use a real model";
  } catch (_) {
    clearTimeout(slow);
    $("#mode").textContent = "Server not reachable";
    flash("Can't reach the backend server. If this is the deployed site, check COURTROOM_API on Netlify and that the Render service is running.");
  }
}

async function loadCases(selectKey) {
  try { state.cases = await (await fetch(API + "/api/cases")).json(); } catch (_) { return; }
  const list = $("#caseList");
  list.innerHTML = state.cases.map((c, i) => `
    <li><button class="case" role="radio" aria-checked="false" data-key="${esc(c.key)}" data-i="${i % 4}">
      <span class="case-ico"><svg viewBox="0 0 24 24" aria-hidden="true">${caseIcon(c.key)}</svg></span>
      <span class="case-name">${esc(c.name)}</span>
      <span class="case-meta">${c.docs.length} document${c.docs.length === 1 ? "" : "s"}${c.uploaded ? ", uploaded" : ""}</span>
    </button></li>`).join("");
  $$(".case", list).forEach((b) => b.addEventListener("click", () => selectCase(b.dataset.key)));
  selectCase(selectKey || state.caseKey || state.cases[0]?.key);
}

function selectCase(key) {
  if (!key) return;
  state.caseKey = key;
  state.allExhibits = null;
  $$(".case").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.key === key)));
  const c = state.cases.find((x) => x.key === key);
  $("#docList").innerHTML = (c?.docs || []).map((d) => `<li title="${esc(d)}">${esc(d.replace(/^\d+_/, "").replace(/\.\w+$/, "").replace(/_/g, " "))}</li>`).join("");
  $("#samples").innerHTML = (c?.claims || []).map((s) => `<button type="button" class="chip">${esc(s)}</button>`).join("");
  $$("#samples .chip").forEach((b) => b.addEventListener("click", () => { $("#claim").value = b.textContent; $("#claim").focus(); }));
}

/* ---------------- rounds toggle ---------------- */
$$(".seg-btn").forEach((b) => b.addEventListener("click", () => {
  state.rounds = Number(b.dataset.rounds);
  $$(".seg-btn").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", String(x === b)); });
}));

/* ---------------- steps ---------------- */
function setStep(name) {
  const order = ["retrieval", "opening", "rebuttal", "admitted", "verdict"];
  const idx = order.indexOf(name);
  $$("#steps li").forEach((li, i) => {
    li.classList.toggle("done", i < idx);
    li.classList.toggle("active", i === idx);
    li.classList.toggle("skip", state.rounds === 1 && li.dataset.step === "rebuttal");
  });
}
function finishSteps() { $$("#steps li").forEach((li) => { li.classList.remove("active"); if (!li.classList.contains("skip")) li.classList.add("done"); }); }
function resetSteps() { $$("#steps li").forEach((li) => li.classList.remove("done", "active", "skip")); }

/* ---------------- the scales ----------------
   every exhibit a side cites drops a coin in their pan.
   the heavier pan sinks. on the verdict the scales tip to the winner. */
const BEAM_HALF = 150;

function tilt(deg) {
  deg = Math.max(-15, Math.min(15, deg));
  const dy = BEAM_HALF * Math.sin((deg * Math.PI) / 180);
  $("#beam").style.transform = `rotate(${deg}deg)`;
  $("#panL").style.transform = `translateY(${-dy}px)`;
  $("#panR").style.transform = `translateY(${dy}px)`;
}

function weighScales() {
  const f = state.coins.for.length, a = state.coins.against.length;
  if (f + a === 0) return tilt(0);
  tilt(((a - f) / (a + f)) * 13);   // positive = right pan (defense) goes down
}

function addCoin(side, id) {
  const list = state.coins[side];
  if (list.includes(id)) return;
  list.push(id);
  const i = list.length - 1;
  if (i >= 8) return; // pan's full, still counts for weight though
  const cx = side === "for" ? 50 : 350;
  const col = i % 4, row = Math.floor(i / 4);
  const x = cx - 27 + col * 18 + (row % 2) * 9;
  const y = 163 - row * 16;
  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  g.setAttribute("class", `coin ${side}`);
  g.style.animationDelay = `${(i % 4) * 0.08}s`;
  g.innerHTML = `<circle cx="${x}" cy="${y}" r="9"/><text x="${x}" y="${y}">${esc(id)}</text>`;
  $(side === "for" ? "#coinsL" : "#coinsR").appendChild(g);
  updateCounts();
}

function updateCounts() {
  const fmt = (n) => (n ? `${n} exhibit${n === 1 ? "" : "s"} on the scale` : "No exhibits yet");
  $("#countFor").textContent = fmt(state.coins.for.length);
  $("#countAgainst").textContent = fmt(state.coins.against.length);
}

function resetScales() {
  state.coins = { for: [], against: [] };
  $("#coinsL").innerHTML = ""; $("#coinsR").innerHTML = "";
  tilt(0); updateCounts();
}

function speaking(side) {
  $$(".avatar").forEach((a) => a.classList.remove("speaking"));
  if (side) $(`.avatar.${side}`)?.classList.add("speaking");
}

/* ---------------- tags ---------------- */
function tag(id, cls = "", withDoc = false, bad = false) {
  const e = state.exhibits[id];
  const doc = withDoc && e ? `<span class="doc">${esc(e.title.split("—").pop().split(":")[0].trim())}</span>` : "";
  if (bad) return `<span class="tag bad ${cls}" title="Not in this advocate's evidence pool, so it was rejected">${esc(id)}</span>`;
  return `<button type="button" class="tag ${cls}" data-ex="${esc(id)}" title="Open exhibit ${esc(id)}">${esc(id)}${doc}</button>`;
}
function mark(set, id, who) { (set[id] ||= new Set()).add(who); }

/* ---------------- trial ---------------- */
function resetTrial() {
  Object.assign(state, { exhibits: {}, citedBy: {}, retrievedBy: {}, admitted: new Set() });
  state.transcript = { claim: state.claim, case: state.caseKey, events: [] };
  $("#empty").hidden = true;
  $("#floor").hidden = false;
  $("#bench").hidden = true;
  $("#bench").classList.remove("struck-in");
  $("#notice").hidden = true;
  for (const side of ["for", "against"]) {
    $(`#disc-${side}`).innerHTML = `<p class="disc-label">Searching the documents…</p>`;
    $(`#args-${side}`).innerHTML = "";
  }
  resetSteps();
  resetScales();
}

function typing(side) {
  const el = $(`#args-${side}`);
  $(".typing", el)?.remove();
  el.insertAdjacentHTML("beforeend", `<div class="typing"><i></i><i></i><i></i><span>${side === "for" ? "Prosecution" : "Defense"} is preparing</span></div>`);
  speaking(side);
}
function stopTyping(side) { $(`#args-${side} .typing`)?.remove(); }

function startTrial() {
  const claim = $("#claim").value.trim();
  if (claim.length < 5) { $("#claim").focus(); flash("Write a claim first, at least a few words."); return; }
  if (!state.caseKey) return;
  state.claim = claim;
  state.es?.close();
  resetTrial();
  const btn = $("#startBtn");
  btn.disabled = true; $("span", btn).textContent = "Court in session…";

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
    flash("Lost connection to the server. Check that python app.py is still running, then start the trial again.");
    endTrial();
  };
}

function endTrial() {
  const btn = $("#startBtn");
  btn.disabled = false; $("span", btn).textContent = "Start the trial";
  stopTyping("for"); stopTyping("against"); speaking(null);
}

function flash(msg) { const n = $("#notice"); n.textContent = msg; n.hidden = false; }

function handle(ev) {
  switch (ev.type) {
    case "retrieval": {
      ev.exhibits.forEach((e) => { state.exhibits[e.id] = e; mark(state.retrievedBy, e.id, ev.side); });
      $(`#disc-${ev.side}`).innerHTML = ev.exhibits.length
        ? `<p class="disc-label">Pulled ${ev.exhibits.length} exhibits in discovery</p>
           <div class="tag-row">${ev.exhibits.map((e) => tag(e.id, ev.side, true)).join("")}</div>`
        : `<p class="disc-label">Found nothing relevant in these documents.</p>`;
      if (ev.side === "against") { setStep("opening"); typing("for"); }
      break;
    }
    case "argument": {
      stopTyping(ev.side);
      renderRound(ev);
      ev.points.filter((p) => !p.struck).forEach((p) => p.evidence.forEach((id) => { mark(state.citedBy, id, ev.side); addCoin(ev.side, id); }));
      weighScales();
      if (ev.round === "opening" && ev.side === "for") typing("against");
      else if (ev.round === "opening" && ev.side === "against" && state.rounds === 2) { setStep("rebuttal"); typing("for"); }
      else if (ev.round === "rebuttal" && ev.side === "for") typing("against");
      else speaking(null);
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
      break;
    case "notice":
    case "error":
      flash(ev.message);
      break;
    case "done":
      finishSteps();
      $("#elapsed").textContent = `Trial took ${ev.seconds}s`;
      break;
  }
}

function renderRound(ev) {
  const el = $(`#args-${ev.side}`);
  const pts = ev.points.map((p) => `
    <li class="point ${p.struck ? "struck" : ""}">
      <p>${esc(p.text)}</p>
      <div class="tag-row">
        ${p.evidence.map((id) => tag(id, ev.side)).join("")}
        ${(p.invalid || []).map((id) => tag(id, ev.side, false, true)).join("")}
      </div>
      ${p.struck ? `<span class="objection">Objection sustained: no valid exhibit</span>` : ""}
    </li>`).join("");
  el.insertAdjacentHTML("beforeend", `
    <section class="round">
      <h3 class="round-name">${ev.round === "opening" ? "Opening argument" : "Rebuttal"}</h3>
      ${ev.summary ? `<p class="round-summary">${esc(ev.summary)}</p>` : ""}
      <ol class="points">${pts || `<li class="point"><p>No points made.</p></li>`}</ol>
    </section>`);
}

function renderVerdict(v) {
  const bench = $("#bench");
  bench.hidden = false;
  bench.classList.remove("struck-in");
  bench.dataset.v = v.verdict;
  const word = VERDICT_TEXT[v.verdict] || v.verdict;
  $("#verdictWord").textContent = word;
  $("#stampText").textContent = word;
  $("#headline").textContent = v.headline || "";
  $("#confNum").textContent = `${v.confidence}%`;
  $("#gaugeFill").style.strokeDashoffset = "100";

  (v.reasoning || []).forEach((r) => r.evidence.forEach((id) => mark(state.citedBy, id, "judge")));
  $("#reasoning").innerHTML = (v.reasoning || []).map((r) => `
    <li><div><p>${esc(r.text)}</p><div class="tag-row">${r.evidence.map((id) => tag(id, "judge")).join("")}</div></div></li>`).join("")
    || `<li><div><p>The court gave no reasons that cite admitted exhibits.</p></div></li>`;
  const weak = [...(v.weak_points || []), ...(v.struck_reasoning || []).map((r) => `Judge's own point removed for citing non-admitted evidence: ${r.text}`)];
  $("#weak").innerHTML = weak.length ? weak.map((w) => `<li>${esc(w)}</li>`).join("") : "<li>None</li>";
  $("#admittedTags").innerHTML = [...state.admitted].map((id) => tag(id, "judge", true)).join("") || "None";
  const a = state.lastAdmitted || { ids: [], unused: [], struck_points: 0 };
  $("#benchStats").innerHTML = `
    <span><b>${a.ids.length}</b> exhibits admitted</span>
    <span><b>${a.unused.length}</b> retrieved but never cited</span>
    <span><b>${a.struck_points}</b> point${a.struck_points === 1 ? "" : "s"} struck for missing evidence</span>`;

  // tip the scales to the winner
  const finalTilt = { SUPPORTED: -15, NOT_SUPPORTED: 15, INSUFFICIENT: 0 }[v.verdict];
  speaking(null);
  if (finalTilt !== undefined) tilt(finalTilt);
  else weighScales();

  // the one big moment: gavel, stamp, gauge
  requestAnimationFrame(() => requestAnimationFrame(() => {
    bench.classList.add("struck-in");
    $("#gaugeFill").style.strokeDashoffset = String(100 - v.confidence);
    bench.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
  }));
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
    ${e.score != null ? `<p class="ex-score">Retrieval score ${e.score} (relevance ${e.relevance}, before stance bias)</p>` : ""}`);
}

async function browseExhibits() {
  if (!state.caseKey) return;
  if (!state.allExhibits) state.allExhibits = await (await fetch(API + `/api/cases/${state.caseKey}/exhibits`)).json();
  const render = (q = "") => {
    const ql = q.toLowerCase();
    const items = state.allExhibits.filter((e) => !ql || (e.text + e.title + e.section).toLowerCase().includes(ql));
    $("#browseList").innerHTML = items.map((e) => `
      <li><button type="button" class="browse-item" data-ex="${e.id}">
        <strong>${e.id}  ${esc(e.title.split("—").pop().trim())}${e.section ? " / " + esc(e.section) : ""}</strong>
        <span>${esc(e.text)}</span></button></li>`).join("") || "<li>No exhibits match.</li>";
  };
  const c = state.cases.find((x) => x.key === state.caseKey);
  openDrawer(`All exhibits in ${c?.name || "case"}`, `
    <input id="browseQ" class="text-input browse-search" type="search" placeholder="Filter by word, e.g. intern">
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
    $(".drop-main", drop).textContent = n ? `${n} file${n === 1 ? "" : "s"} selected` : "Drop PDFs, .md or .txt here";
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
      if (!r.ok) throw new Error(data.detail || "Upload failed");
      msg.textContent = `Added ${data.saved.length} document${data.saved.length === 1 ? "" : "s"}. Case file selected.`;
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
$("#docketToggle").addEventListener("click", (e) => {
  const open = $(".docket").classList.toggle("open");
  e.target.setAttribute("aria-expanded", String(open));
  e.target.textContent = open ? "Hide documents and upload" : "Show documents and upload";
});
$("#browseBtn").addEventListener("click", browseExhibits);
$("#downloadBtn").addEventListener("click", downloadTranscript);

initTheme();
initUpload();
loadStatus().then(() => loadCases());
