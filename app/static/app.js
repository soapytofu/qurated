const extraQScores = [
  ["materials", "Course materials"],
  ["assignments", "Assignments"],
  ["course_feedback", "Feedback on coursework"],
  ["sections", "Section component"],
  ["accessibility", "Instructor accessibility"],
  ["enthusiasm", "Generates enthusiasm"],
  ["discussion", "Encourages discussion and participation"],
  ["instructor_feedback", "Useful instructor feedback"],
  ["timely_returns", "Timely assignment returns"],
  ["open_mindedness", "Students listen with an open mind"],
  ["expressing_views", "Comfort expressing controversial views"],
];
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

// ---------- storage (best effort; the app works without it) ----------
const store = {
  get(k, fallback) { try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch { return fallback; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

let sessionId = null;
let busy = false;
// Saved to the student's account (server); see loadAccount().
let prefs = {};
let favorites = {}; // code -> card
let taken = new Map(); // code -> title
let isAdmin = false; // admins (ADMIN_EMAILS on the server) also see per-answer cost

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 1) => (n == null ? "–" : Number(n).toFixed(d).replace(/\.0$/, ""));
const initials = (name) => (name || "?").split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

function markdown(src) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\W)\*(?!\s)(.+?)\*(?=\W|$)/g, "$1<i>$2</i>");
  return src.trim().split(/\n{2,}/).map((block) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l))) {
      return "<ul>" + lines.map((l) => `<li>${inline(l.replace(/^\s*([-*•]|\d+\.)\s+/, ""))}</li>`).join("") + "</ul>";
    }
    return `<p>${lines.map(inline).join("<br>")}</p>`;
  }).join("");
}

const ICON_HEART = '<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/></svg>';
const ICON_CAL = '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>';

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (res.status === 401) { location.replace("/"); throw new Error("signed out"); }
  return res;
}
async function checkedWrite(path, options) {
  const res = await api(path, options);
  if (!res.ok) throw new Error("Your changes weren't saved. Please try again.");
  return res;
}
function saveError(err) {
  if (err.message !== "signed out") window.alert(err.message || "Your changes weren't saved. Please try again.");
}
const putJSON = (path, body) => checkedWrite(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
const del = (path) => checkedWrite(path, { method: "DELETE" });
const enc = encodeURIComponent;

// ---------- views: home (Apple-style landing) vs. conversation ----------
let browseGeneration = 0;
const browseStates = new Map(), browseControllers = new Set();
function cancelBrowseWork() {
  browseGeneration++;
  browseControllers.forEach(controller => controller.abort());
  browseControllers.clear();
  for (const [block] of browseStates) if (!block || !block.isConnected) browseStates.delete(block);
  $("#f-show").disabled = false;
}
function showHome() {
  cancelBrowseWork();
  document.body.classList.remove("chatting");
  document.body.classList.add("home");
  $(".hero").appendChild($("#composer-wrap"));
}
function showChat() {
  document.body.classList.remove("home");
  document.body.classList.add("chatting");
  document.body.appendChild($("#composer-wrap"));
}
showHome();

// ---------- course cards ----------
function tagsHTML(tags) {
  if (!tags?.length) return "";
  return `<div class="tags">${tags.map((t) => `<span class="tag ${t.sentiment === "positive" ? "pos" : t.sentiment === "negative" ? "neg" : ""}">${esc(t.label)}</span>`).join("")}</div>`;
}

function cardHTML(c) {
  const on = favorites[c.code] ? " on" : "";
  const prof = c.instructors?.[0] || "Staff";
  const more = c.instructors?.length > 1 ? ` +${c.instructors.length - 1}` : "";
  const overallHi = c.overall != null && c.overall_fas_avg != null && c.overall > c.overall_fas_avg ? " hi" : "";
  return `
    <article class="card" data-code="${esc(c.code)}" tabindex="0">
      <button class="fav${on}" aria-label="Favorite ${esc(c.code)}" aria-pressed="${!!on}">${ICON_HEART}</button>
      <div class="code">${esc(c.code)}${c.gen_ed ? ` <span class="gened">${esc(c.gen_ed)}</span>` : ""}${c.grad_level ? ' <span class="gened grad" title="Graduate-level: may need instructor permission">Graduate</span>' : ""}</div>
      <h3>${esc(c.title)}</h3>
      ${c.next_offered ? `<div class="meets">${ICON_CAL}${esc(c.next_offered)}</div>` : ""}
      ${tagsHTML(c.tags)}
      ${c.reason ? `<p class="reason">${esc(c.reason)}</p>` : ""}
      ${c.quote ? `<p class="quote">“${esc(c.quote)}”</p>` : ""}
      ${c.has_q === false ? '<div class="no-q">No Q Guide reviews available in the loaded data</div>' : `
      <div class="stats">
        <div class="stat"><div class="n${overallHi}">${fmt(c.overall)}</div><div class="l">Overall</div></div>
        <div class="stat"><div class="n">${fmt(c.workload_hrs)}<small>hrs</small></div><div class="l">Per week</div></div>
        <div class="stat"><div class="n">${fmt(c.recommend)}</div><div class="l">Recommend</div></div>
      </div>`}
      ${c.instructor_score != null || c.effective_lectures != null ? `<p class="meta">Instructor ${fmt(c.instructor_score)} · Effective lectures ${fmt(c.effective_lectures)}<br>Latest Q report: ${esc(c.q_term || "term unavailable")}; instructors may differ.</p>` : ""}
      <div class="card-foot">
        <div class="who"><span class="avatar">${esc(initials(prof))}</span><span>${esc(prof + more)}</span></div>
        <div class="term">${esc(c.term || "")}</div>
      </div>
      <div class="card-actions">
        <button class="taken-btn${taken.has(c.code) ? " on" : ""}" aria-pressed="${taken.has(c.code)}">${taken.has(c.code) ? "✓ Taken" : "I've taken this"}</button>
        <span class="details-link" aria-hidden="true">Details ›</span>
      </div>
    </article>`;
}

function bindCards(root, cards) {
  const byCode = Object.fromEntries(cards.map((c) => [c.code, c]));
  $$(".card", root).forEach((el) => {
    const c = byCode[el.dataset.code];
    el.addEventListener("click", (e) => {
      if (e.target.closest(".fav")) { toggleFav(c); return; }
      if (e.target.closest(".taken-btn")) { toggleTaken(c.code, c.title); return; }
      openDetail(c.code);
    });
    el.addEventListener("keydown", (e) => { if (e.target === el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDetail(c.code); } });
  });
}

function syncCardButtons(code) {
  $$(`.card[data-code="${CSS.escape(code)}"]`).forEach((el) => {
    const f = $(".fav", el), t = $(".taken-btn", el);
    f.classList.toggle("on", !!favorites[code]); f.setAttribute("aria-pressed", !!favorites[code]);
    t.classList.toggle("on", taken.has(code)); t.setAttribute("aria-pressed", taken.has(code));
    t.textContent = taken.has(code) ? "✓ Taken" : "I've taken this";
    t.classList.toggle("on", taken.has(code));
  });
}

const pendingFavorites = new Set(), pendingTaken = new Set();
async function toggleFav(card) {
  if (pendingFavorites.has(card.code)) return;
  pendingFavorites.add(card.code);
  const previous = favorites[card.code];
  const on = !favorites[card.code];
  if (on) favorites[card.code] = card; else delete favorites[card.code];
  syncCardButtons(card.code);
  renderFavs();
  try {
    await (on ? putJSON(`/api/account/favorites/${enc(card.code)}`, card) : del(`/api/account/favorites/${enc(card.code)}`));
  } catch (err) {
    if (previous) favorites[card.code] = previous; else delete favorites[card.code];
    syncCardButtons(card.code); renderFavs(); saveError(err);
  } finally { pendingFavorites.delete(card.code); }
}

async function toggleTaken(code, title) {
  if (pendingTaken.has(code)) return;
  pendingTaken.add(code);
  const previous = taken.get(code);
  const on = !taken.has(code);
  if (on) taken.set(code, title || ""); else taken.delete(code);
  syncCardButtons(code);
  renderTaken();
  try {
    await (on ? putJSON(`/api/account/taken/${enc(code)}`) : del(`/api/account/taken/${enc(code)}`));
    refreshBrowseResults();
  } catch (err) {
    if (on) taken.delete(code); else taken.set(code, previous);
    syncCardButtons(code); renderTaken(); saveError(err);
  } finally { pendingTaken.delete(code); }
}

// ---------- chat ----------
function addTurn(cls, html = "") {
  const el = document.createElement("div");
  el.className = `turn ${cls}`;
  el.innerHTML = html;
  $("#thread").appendChild(el);
  return el;
}
const scrollToEnd = () => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });

// ---------- progress cues: show work immediately so waiting never feels like nothing is happening ----------
const GENERIC_STEP = "Looking through courses";
function makeProgress(bot) {
  const el = document.createElement("div");
  el.className = "progress";
  el.innerHTML = '<ol class="steps"></ol><span class="elapsed">0s</span>';
  bot.appendChild(el);
  const list = $(".steps", el), clock = $(".elapsed", el), t0 = Date.now();
  const tick = setInterval(() => { clock.textContent = `${Math.round((Date.now() - t0) / 1000)}s`; }, 1000);
  let skeleton = null;
  const api = {
    el,
    step(text) {
      const last = list.lastElementChild;
      if (last && last.dataset.text === text) return;
      // "Looking through courses" just means the model is mid-step: keep the current step pulsing instead.
      if (text === GENERIC_STEP && last) return;
      if (last) last.classList.add("done");
      const li = document.createElement("li");
      li.dataset.text = text;
      li.textContent = text;
      list.appendChild(li);
      if (!skeleton && /^Searching/.test(text)) api.showSkeleton();
    },
    showSkeleton() {
      skeleton = document.createElement("div");
      skeleton.className = "skeleton-wrap";
      skeleton.innerHTML = `<div class="section-head"><h2>Finding your best fits…</h2></div>
        <div class="cards">${'<div class="card skeleton"><i></i><i></i><i></i><i></i></div>'.repeat(3)}</div>`;
      bot.insertBefore(skeleton, el);
    },
    replaceSkeleton(node) { if (skeleton) { skeleton.replaceWith(node); skeleton = null; } else bot.insertBefore(node, el); },
    seconds: () => Math.round((Date.now() - t0) / 1000),
    finish() { clearInterval(tick); skeleton?.remove(); el.remove(); },
  };
  api.step("Understanding your question");
  return api;
}

async function send(message) {
  message = message.trim();
  if (!message || busy) return;
  cancelBrowseWork();
  busy = true;
  $("#ask").disabled = true;
  $("#new-chat").disabled = true;
  $("#input").value = "";
  showChat();
  addTurn("user", `<div class="bubble">${esc(message)}</div>`);
  const bot = addTurn("bot");
  const progress = makeProgress(bot);
  const status = progress.el;
  const note = (cls, text) => { const el = document.createElement("div"); el.className = cls; el.textContent = text; bot.insertBefore(el, status); };
  let textEl = null, text = "", cardGrid = null;
  const newText = () => { textEl = document.createElement("div"); textEl.className = "bot-text"; bot.insertBefore(textEl, status); text = ""; };
  scrollToEnd();

  try {
    await flushPrefs();
    const res = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message, preferences: prefs, favorites: Object.keys(favorites), filters: activeFilters() }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      note("error", typeof body.detail === "string" ? body.detail : "Please enter a question of at most 2,000 characters.");
      return;
    }
    sessionId = res.headers.get("X-Session-Id") || sessionId;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "", completed = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const events = buf.split("\n\n");
      buf = events.pop();
      for (const raw of events) {
        if (!raw.startsWith("data: ")) continue;
        const ev = JSON.parse(raw.slice(6));
        if (ev.type === "text") {
          if (!textEl) newText();
          text += ev.delta;
          textEl.innerHTML = markdown(text);
        } else if (ev.type === "search_plan") {
          const details = document.createElement("details");
          details.className = "search-plan";
          const heading = document.createElement("summary");
          heading.textContent = "Search interpreted as";
          details.appendChild(heading);
          const lines = [ev.criteria, ev.queries?.length ? `Keywords: ${ev.queries.join(" / ")}` : "Browsing by filters",
            ...(ev.notes || []), "Change your question or Filters to adjust this search."];
          for (const line of lines) {
            const paragraph = document.createElement("p");
            paragraph.textContent = line;
            details.appendChild(paragraph);
          }
          bot.insertBefore(details, status);
        } else if (ev.type === "status") {
          if (ev.text !== "Thinking") progress.step(ev.text === "Putting together your picks" ? "Picking your best fits" : ev.text);
        } else if (ev.type === "card") {
          // Fast pipeline: cards arrive one at a time; the first one replaces the shimmering placeholders.
          if (!cardGrid) {
            const wrap = document.createElement("div");
            wrap.innerHTML = '<div class="section-head"><h2>Have you considered?</h2></div><div class="cards"></div>';
            progress.replaceSkeleton(wrap);
            cardGrid = $(".cards", wrap);
            textEl = null;
          }
          const tmp = document.createElement("div");
          tmp.innerHTML = cardHTML(ev.card);
          bindCards(tmp, [ev.card]);  // bind only the new card, then move it into the grid
          cardGrid.appendChild(tmp.firstElementChild);
        } else if (ev.type === "cards" && ev.cards.length) {
          const wrap = document.createElement("div");
          wrap.innerHTML = `<div class="section-head"><h2>Have you considered?</h2></div>
            <div class="cards">${ev.cards.map(cardHTML).join("")}</div>`;
          progress.replaceSkeleton(wrap);
          bindCards(wrap, ev.cards);
          progress.step("Writing advice");
          textEl = null;
        } else if (ev.type === "options" && ev.options.length) {
          // Clarifying question: tappable answers.
          const row = document.createElement("div");
          row.className = "followups clarify";
          row.innerHTML = ev.options.map((o) => `<button class="chip-btn">${esc(o)}</button>`).join("");
          $$("button", row).forEach((b) => b.addEventListener("click", () => send(b.textContent)));
          bot.insertBefore(row, status);
        } else if (ev.type === "memory" && ev.facts.length) {
          addMemoryNote(bot, status, ev.facts);
        } else if (ev.type === "usage") {
          note("usage", `${ev.searched ? "Searched courses" : "Answered directly"} · ${progress.seconds()}s`);
        } else if (ev.type === "done") {
          completed = true;
        } else if (ev.type === "error") {
          note("error", ev.text);
        }
      }
    }
    if (!completed) note("error", "The answer was interrupted. Please try again.");
  } catch (e) {
    if (e.message !== "signed out") note("error", e.message.includes("saved") ? e.message : "Connection interrupted. Please try again.");
  } finally {
    progress.finish();
    busy = false;
    $("#ask").disabled = false;
    $("#new-chat").disabled = false;
    $("#input").focus();
    refreshMe();
  }
}

// Facts the student mentioned mid-chat: remembered for this chat, with an option to keep them in preferences.
function addMemoryNote(bot, before, facts) {
  const el = document.createElement("div");
  el.className = "memory-note";
  el.innerHTML = `<span>Noted for this chat: ${facts.map(esc).join(" · ")}</span> <button class="linkbtn">Save to my preferences</button>`;
  $("button", el).addEventListener("click", (e) => {
    const notes = (prefs.notes || "").split("\n").filter(Boolean);
    facts.forEach((f) => { if (!notes.includes(f)) notes.push(f); });
    prefs.notes = notes.join("\n").slice(-600);
    fillPrefs();
    savePrefs();
    e.target.replaceWith(Object.assign(document.createElement("span"), { className: "saved", textContent: "Saved ✓" }));
  });
  bot.insertBefore(el, before);
}

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
$$(".try").forEach((b) => b.addEventListener("click", () => send(b.textContent.replace("›", ""))));
$("#new-chat").addEventListener("click", () => { if (busy) return; closeAll(); toggleFilters(false); sessionId = null; $("#thread").innerHTML = ""; showHome(); scrollTo({ top: 0 }); });
$("#nav-ask").addEventListener("click", () => { if (!document.body.classList.contains("chatting")) scrollTo({ top: 0, behavior: "smooth" }); $("#input").focus(); });
$("#cta-ask").addEventListener("click", () => $("#input").focus());
$("#nav-how").addEventListener("click", openGuide);

// ---------- workload preference label (preferences sheet) ----------
const menu = document.createElement("div");  // placeholder: the old workload quick menu was replaced by Filters
function syncWorkload() {
  const w = Number(prefs.max_workload_hrs) || 0;
  $("#wl-val").textContent = w ? `${w} hrs/week` : "no limit";
  $('[name="max_workload_hrs"]').value = w;
}

// ---------- sheets ----------
let activeOverlay = null, overlayReturn = null, inertElements = [];
const focusable = (root) => $$('button:not([disabled]), input, select, textarea, a[href], [tabindex="0"]', root)
  .filter(el => !el.hidden && el.getClientRects().length);
function openOverlay(id, scrim = false) {
  const trigger = document.activeElement;
  closeAll(false);
  toggleFilters(false);
  acctMenu.hidden = true;
  activeOverlay = $(id); overlayReturn = trigger;
  activeOverlay.hidden = false;
  activeOverlay.setAttribute("role", "dialog");
  activeOverlay.setAttribute("aria-modal", "true");
  $("#scrim").hidden = !scrim;
  inertElements = [...document.body.children].filter(el => el !== activeOverlay && el.id !== "scrim" && !el.inert);
  inertElements.forEach(el => el.inert = true);
  document.body.classList.add("overlay-open");
  focusable(activeOverlay)[0]?.focus();
}
function openSheet(id) { openOverlay(id, true); }
function closeAll(restore = true) {
  ["#prefs", "#favs", "#detail", "#feedback", "#taken"].forEach(id => $(id).hidden = true);
  $("#scrim").hidden = true;
  inertElements.forEach(el => el.inert = false); inertElements = [];
  document.body.classList.remove("overlay-open");
  activeOverlay = null;
  if (restore && overlayReturn?.isConnected) overlayReturn.focus();
  overlayReturn = null;
}
$$("[data-close]").forEach(b => b.addEventListener("click", () => closeAll()));
$("#scrim").addEventListener("click", () => closeAll());
["#detail", "#feedback"].forEach(id => $(id).addEventListener("click", e => { if (e.target === $(id)) closeAll(); }));
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeAll(); toggleFilters(false); acctMenu.hidden = true; }
  if (e.key === "Tab" && activeOverlay) {
    const items = focusable(activeOverlay), first = items[0], last = items.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
  }
});
document.addEventListener("click", (e) => {
  if (!menu.contains(e.target)) menu.hidden = true;
  if (!acctMenu.contains(e.target) && !e.target.closest("#account")) acctMenu.hidden = true;
});

// preferences
$("#open-prefs").addEventListener("click", () => openSheet("#prefs"));
$("#cta-prefs").addEventListener("click", () => openSheet("#prefs"));
let prefsTimer = null, prefsSave = Promise.resolve(), prefsDirty = false;
function savePrefs() {
  prefsDirty = true;
  $("#prefs-status").textContent = "Unsaved changes";
  clearTimeout(prefsTimer);
  prefsTimer = setTimeout(() => flushPrefs().catch(() => {}), 500);
}
async function flushPrefs() {
  clearTimeout(prefsTimer);
  if (prefsDirty) {
    const snapshot = { ...prefs };
    prefsDirty = false;
    $("#prefs-status").textContent = "Saving…";
    prefsSave = prefsSave.catch(() => {}).then(() => putJSON("/api/account/prefs", snapshot)).then(() => {
      if (!prefsDirty) $("#prefs-status").textContent = "Saved to your account";
    }).catch(err => {
      prefsDirty = true;
      $("#prefs-status").textContent = "Couldn't save. Use Save preferences to retry.";
      throw err;
    });
  }
  return prefsSave;
}
$("#prefs-save").addEventListener("click", async () => {
  const button = $("#prefs-save"); button.disabled = true;
  try { await flushPrefs(); closeAll(); } catch {} finally { button.disabled = false; }
});
function fillPrefs() {
  $$("#prefs [name]").forEach((el) => { if (prefs[el.name] != null && el.type !== "range") el.value = prefs[el.name]; });
  syncWorkload();
}
$$("#prefs [name]").forEach((el) => {
  el.addEventListener("input", () => {
    prefs[el.name] = el.type === "range" ? Number(el.value) : el.value;
    savePrefs();
    if (el.type === "range") syncWorkload();
  });
});
syncWorkload();

// favorites
function renderFavs() {
  const items = Object.values(favorites);
  const badge = $("#fav-count");
  badge.hidden = !items.length;
  badge.textContent = items.length;
  $("#compare-favs").disabled = items.length < 2;
  $("#fav-list").innerHTML = items.length
    ? items.map((c) => `
        <div class="fav-item" data-code="${esc(c.code)}">
          <div class="t"><b>${esc(c.code)} · ${esc(c.title)}</b>
            <span>Overall ${fmt(c.overall)} · ${fmt(c.workload_hrs)} hrs/wk · Recommend ${fmt(c.recommend)}</span></div>
          <button class="iconbtn plain" aria-label="Remove">✕</button>
        </div>`).join("")
    : '<p class="empty">Tap the heart on any course to save it here.</p>';
  $$("#fav-list .fav-item").forEach((el) => {
    const c = favorites[el.dataset.code];
    $(".t", el).addEventListener("click", () => openDetail(c.code));
    $("button", el).addEventListener("click", () => toggleFav(c));
  });
}
["#open-favs", "#nav-favs", "#tile-favs"].forEach((id) => $(id).addEventListener("click", () => openSheet("#favs")));
$("#compare-favs").addEventListener("click", () => {
  closeAll();
  send(`Compare my favorites and help me choose: ${Object.keys(favorites).join(", ")}`);
});
renderFavs();

// ---------- courses taken ----------
function renderTaken() {
  $("#taken-list").innerHTML = taken.size
    ? [...taken].map(([code, title]) => `
        <span class="chip-x" data-code="${esc(code)}"><b>${esc(code)}</b>${title ? " " + esc(title) : ""}
          <button aria-label="Remove ${esc(code)}">✕</button></span>`).join("")
    : '<p class="empty">Add the courses you\'ve already taken and Qurated will skip them.</p>';
  $$("#taken-list .chip-x button").forEach((b) => b.addEventListener("click", () => toggleTaken(b.parentElement.dataset.code)));
}
const tIn = $("#taken-input"), tSug = $("#taken-suggest");
let sugTimer = null, suggestionVersion = 0;
tIn.addEventListener("input", () => {
  clearTimeout(sugTimer);
  const version = ++suggestionVersion, q = tIn.value.trim();
  if (q.length < 2) { tSug.hidden = true; return; }
  sugTimer = setTimeout(async () => {
    try {
      const response = await api(`/api/courses/suggest?q=${enc(q)}`);
      if (!response.ok) throw new Error("suggest failed");
      const items = await response.json();
      if (version !== suggestionVersion) return;
      tSug.innerHTML = items.length
        ? items.map(i => `<button data-code="${esc(i.code)}" data-title="${esc(i.title)}"${taken.has(i.code) ? " disabled" : ""}><b>${esc(i.code)}</b> ${esc(i.title)}${taken.has(i.code) ? " ✓" : ""}</button>`).join("")
        : '<div class="none">No matching course</div>';
      tSug.hidden = false;
      $$("button", tSug).forEach(b => b.addEventListener("click", () => {
        toggleTaken(b.dataset.code, b.dataset.title);
        suggestionVersion++; tIn.value = ""; tSug.hidden = true; tIn.focus();
      }));
    } catch {
      if (version !== suggestionVersion) return;
      tSug.innerHTML = '<div class="none">Couldn’t search. Change your search to retry.</div>'; tSug.hidden = false;
    }
  }, 180);
});
tIn.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("button:not([disabled])", tSug)?.click(); }
});
function openTaken() { closeAll(); acctMenu.hidden = true; openSheet("#taken"); tIn.focus(); }
$("#prefs-taken").addEventListener("click", openTaken);

// ---------- load the student's saved account (with one-time move from this browser) ----------
async function loadAccount() {
  const acct = await api("/api/account").then(r => r.ok ? r.json() : null).catch(() => null);
  if (!acct) return;
  favorites = Object.fromEntries(acct.favorites.map((c) => [c.code, c]));
  taken = new Map(acct.taken.map((t) => [t.code, t.title]));
  prefs = acct.prefs || {};
  // Older versions saved favorites/preferences only in this browser; move them to the account once.
  const oldFavs = store.get("hua.favorites", null), oldPrefs = store.get("hua.prefs", null);
  try {
    if (oldFavs) {
      for (const [code, card] of Object.entries(oldFavs)) {
        if (!favorites[code]) {
          await putJSON(`/api/account/favorites/${enc(code)}`, card);
          favorites[code] = card;
        }
      }
      localStorage.removeItem("hua.favorites");
    }
    if (oldPrefs && !Object.keys(prefs).length) {
      await putJSON("/api/account/prefs", oldPrefs);
      prefs = oldPrefs;
    }
    localStorage.removeItem("hua.prefs");
  } catch (err) { saveError(err); }
  renderFavs(); renderTaken(); fillPrefs();
  Object.keys(favorites).forEach(syncCardButtons);
}
loadAccount();

// ---------- course detail ----------
function safeExternalURL(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
function catalogHTML(cat) {
  if (!cat) return '<p class="meta">Not in the Fall 2026 / Spring 2027 catalog.</p>';
  const syllabus = safeExternalURL(cat.syllabus_url);
  const facts = [["Gen Ed", cat.gen_ed], ["Divisional", cat.divisional], ["QRD", cat.qrd], ["Level", cat.level],
    ["Format", cat.component], ["Grading", cat.grading], ["Consent", cat.consent], ["Cap", cat.enrollment_cap]]
    .filter(([, v]) => v != null && v !== "");
  const offered = (cat.offered || []).map((o) => `
    <div class="offer"><b>${esc(o.term)}</b>${o.sections.slice(0, 4).map((s) => {
      const meets = (s.meetings || []).filter((m) => m.days?.length && m.time).map((m) => `${m.days.join("/")} ${m.time}`).join("; ") || "Time TBA";
      return `<div>${s.topic ? `<i>${esc(s.topic)}</i> · ` : ""}${esc(meets)} · ${esc((s.instructors || []).join(", ") || "Staff")}</div>`;
    }).join("")}${o.sections.length > 4 ? `<div class="meta">+${o.sections.length - 4} more sections</div>` : ""}</div>`).join("");
  return `
    ${cat.description ? `<p class="desc">${esc(cat.description)}</p>` : ""}
    <div class="offers">${offered}</div>
    ${facts.length ? `<dl class="facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` : ""}
    ${syllabus ? `<p><a class="link" href="${esc(syllabus)}" target="_blank" rel="noopener">View syllabus ›</a></p>` : ""}`;
}
let detailVersion = 0;
async function openDetail(code) {
  const version = ++detailVersion;
  $("#detail-body").innerHTML = '<p class="meta">Loading…</p>';
  openOverlay("#detail");
  let c;
  try {
    const res = await api(`/api/course/${encodeURIComponent(code)}`);
    if (!res.ok) throw new Error("detail failed");
    c = await res.json();
  } catch {
    if (version === detailVersion) $("#detail-body").innerHTML = '<p class="meta">Couldn’t load this course. Close this panel and open the card to retry.</p>';
    return;
  }
  if (version !== detailVersion) return;
  $("#detail-body").innerHTML = `
    <div class="code">${esc(c.code)}${c.also_listed_as.length ? " · " + esc(c.also_listed_as.join(" · ")) : ""}</div>
    <h2>${esc(c.title)}</h2>
    <p class="meta">${esc(c.dept)}</p>
    <div class="detail-actions">
      <button class="pill pill-soft" id="d-fav">${favorites[c.code] ? "♥ Favorited" : "♡ Favorite"}</button>
      <button class="pill pill-soft" id="d-taken">${taken.has(c.code) ? "✓ Taken" : "I've taken this"}</button>
    </div>
    ${tagsHTML(c.tags)}
    ${catalogHTML(c.catalog)}
    ${c.offerings.length ? `<h3 class="sub">Q report history</h3>` : '<p class="no-q">No Q Guide reviews available in the loaded data</p>'}
    <table class="offerings"${c.offerings.length ? "" : " hidden"}>
      <thead><tr><th>Term</th><th>Instructor</th><th>Overall</th><th>Hrs/wk</th><th>Recommend</th><th>Instructor rating</th><th>Effective lectures</th><th>Responses</th></tr></thead>
      <tbody>${c.offerings.map((o) => `
        <tr><td>${esc(o.term)}</td><td>${esc(o.instructors.join(", "))}</td><td>${fmt(o.course_overall, 2)}</td>
        <td>${fmt(o.workload_hrs_mean)}</td><td>${fmt(o.recommend_mean, 2)}</td><td>${fmt(o.instructor_score, 2)}</td><td>${fmt(o.effective_lectures, 2)}</td><td>${fmt(o.responded, 0)}/${fmt(o.invited, 0)}</td></tr>`).join("")}
      </tbody>
    </table>
    ${c.offerings.length ? `<details><summary>More Q scores · ${esc(c.offerings[0].term)}</summary><p class="hint">Latest loaded report, out of 5. Instructor scores average that report’s instructors; current instructors may differ. — means unavailable.</p><table class="offerings"><tbody>${extraQScores.map(([field, label]) => `<tr><th>${esc(label)}</th><td>${fmt(c.offerings[0][field], 2)}</td></tr>`).join("")}</tbody></table></details>` : ""}
    <div class="comments">
      <h3>What students said <span class="meta">(${c.total_comments})</span></h3>
      ${c.comments.map((x) => `<div class="comment">${esc(x.text)}<div class="ct">${esc(x.term)}</div></div>`).join("") || '<p class="meta">No written comments.</p>'}
    </div>`;
  $("#d-fav").addEventListener("click", async () => {
    const card = favorites[c.code] || (await api("/api/cards", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify([c.code]) }).then((r) => r.json()))[0];
    if (card) await toggleFav(card);
    $("#d-fav").textContent = favorites[c.code] ? "♥ Favorited" : "♡ Favorite";
  });
  $("#d-taken").addEventListener("click", async () => {
    await toggleTaken(c.code, c.title);
    $("#d-taken").textContent = taken.has(c.code) ? "✓ Taken" : "I've taken this";
  });
}

// ---------- account ----------
const acctMenu = $("#account-menu");
$("#account").addEventListener("click", (e) => {
  e.stopPropagation();
  const open = acctMenu.hidden;
  closeAll();  // a drawer or course page may be open; the menu should still work in one click
  acctMenu.hidden = !open;
});
$("#open-taken").addEventListener("click", openTaken);
$("#logout").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  try { google.accounts.id.disableAutoSelect(); } catch {}
  location.replace("/");
});
async function refreshMe() {
  const me = await api("/api/me").then((r) => r.json()).catch(() => null);
  if (!me) return;
  isAdmin = !!me.is_admin;
  $("#me-name").textContent = me.name;
  $("#me-email").textContent = me.email;
  $("#me-quota").textContent = `${me.questions_left} of ${me.daily_limit} questions left today`;
  const btn = $("#account");
  if (me.picture) { btn.style.backgroundImage = `url("${me.picture}")`; $("#me-initials").textContent = ""; }
  else $("#me-initials").textContent = initials(me.name);
}
refreshMe();

// ---------- home stats (count up, Apple-style) ----------
function countUp(el, to) {
  const start = performance.now(), dur = 1100;
  const step = (t) => {
    const p = Math.min((t - start) / dur, 1), eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(to * eased).toLocaleString();
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function dataCoverageText(m) {
  if (!m.courses) return "No course data has been loaded on this installation. Ask the person running Qurated to import the public catalog.";
  if (!m.catalog_courses) return "Historical Q reviews are loaded, but the current course catalog is missing. Import the catalog to search upcoming schedules and requirements.";
  if (!m.reviewed_courses) return "The course catalog is loaded. No Q Guide reviews are loaded yet; ratings and workload filters will exclude courses with unknown scores.";
  const terms = m.terms.join(", ");
  return `${m.catalog_courses.toLocaleString()} courses in the catalog · ${m.reviewed_courses.toLocaleString()} courses with Q Guide reports. Q terms: ${terms}. Coverage varies by course.`;
}
api("/api/meta").then((r) => {
  if (!r.ok) throw new Error("coverage unavailable");
  return r.json();
}).then((m) => {
  $("#data-coverage").textContent = dataCoverageText(m);
  $("#hero-sub").textContent = m.courses
    ? `Explore ${m.courses.toLocaleString()} courses using the catalog and reviews available here.`
    : "Course search will be available after the catalog is imported.";
  const obs = new IntersectionObserver((entries) => {
    if (!entries[0].isIntersecting) return;
    countUp($("#st-courses"), m.courses);
    countUp($("#st-comments"), m.comments);
    countUp($("#st-terms"), m.terms.length);
    obs.disconnect();
  }, { threshold: 0.4 });
  obs.observe($("#stats-tile"));
}).catch(() => {
  $("#data-coverage").textContent = "Couldn't check data coverage. Refresh to try again.";
});

// ---------- feedback ----------
const fbStatus = $("#fb-status");
$("#open-feedback").addEventListener("click", () => {
  openOverlay("#feedback");
  fbStatus.hidden = true;
  $("#fb-message").focus();
});
$("#feedback-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const send = $("#fb-send");
  send.disabled = true;
  try {
    const res = await api("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: $('#feedback-form [name="kind"]:checked').value,
        message: $("#fb-message").value,
        include_email: $("#fb-include-email").checked,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || "Couldn't send. Please try again.");
    $("#fb-message").value = "";
    fbStatus.className = "fb-status";
    fbStatus.textContent = "Thanks! We read every message.";
    fbStatus.hidden = false;
    setTimeout(closeAll, 1400);
  } catch (err) {
    if (err.message === "signed out") return;
    fbStatus.className = "fb-status err";
    fbStatus.textContent = err.message;
    fbStatus.hidden = false;
  } finally {
    send.disabled = false;
  }
});

// ---------- grading-policy notice (hide per browser) ----------
if (store.get("qurated.hideGradingNotice", false)) $("#grading-notice").hidden = true;
$("#grading-notice-close").addEventListener("click", () => {
  $("#grading-notice").hidden = true;
  store.set("qurated.hideGradingNotice", true);
});

// ---------- filters: instant structured search, also hard constraints for the chatbot ----------
let filters = store.get("qurated.filters", {});
const fPanel = $("#filters");
function activeFilters() {
  return Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== "" && v !== null && v !== false && !(Array.isArray(v) && !v.length)));
}
function filterSummary() {
  const n = Object.keys(activeFilters()).length;
  $("#filter-count").hidden = !n;
  $("#filter-count").textContent = n;
}
function renderFilterControls() {
  $$(".segctl", fPanel).forEach((seg) => $$("button", seg).forEach((b) =>
    { const on = String(filters[seg.dataset.key] ?? "") === b.dataset.v; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); }));
  $$("#f-days button").forEach(b => { const on = (filters.avoid_days || []).includes(b.dataset.v); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
  $("#f-subject").value = filters.subject || "";
  $("#f-gen_ed").value = filters.gen_ed || "";
  $("#f-divisional").value = filters.divisional || "";
  $("#f-earliest").value = filters.earliest_start || "";
  $("#f-workload").value = filters.max_workload || 0;
  $("#f-wl-val").textContent = filters.max_workload ? `≤ ${filters.max_workload} hrs/wk` : "any";
  ["qrd", "small_class", "has_reviews", "include_grad"].forEach((k) => ($(`#f-${k}`).checked = !!filters[k]));
  filterSummary();
}
let countTimer = null, countVersion = 0;
async function browseRequest(state, signal) {
  const response = await api("/api/browse", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(state), signal });
  if (!response.ok) throw new Error("Couldn't load courses. Please try again.");
  return response.json();
}
function filtersChanged() {
  store.set("qurated.filters", filters);
  renderFilterControls();
  clearTimeout(countTimer);
  const version = ++countVersion;
  $("#f-show").textContent = "Checking courses…";
  $("#f-show").disabled = true;
  $("#browse-status").textContent = "";
  countTimer = setTimeout(async () => {
    try {
      const r = await browseRequest({ filters:activeFilters(), query:$("#f-query").value });
      if (version !== countVersion) return;
      $("#f-show").textContent = r.total ? `Show ${r.total.toLocaleString()} course${r.total === 1 ? "" : "s"}` : "Show empty results";
      $("#f-show").disabled = false;
      $("#browse-status").textContent = r.total ? "" : "No matches. Try removing a filter or changing your keywords.";
    } catch (err) {
      if (version !== countVersion) return;
      $("#f-show").textContent = "Retry search"; $("#f-show").disabled = false;
      $("#browse-status").textContent = "Couldn't check courses. Try again.";
    }
  }, 150);
}

$$(".segctl", fPanel).forEach((seg) => $$("button", seg).forEach((b) => b.addEventListener("click", () => {
  const v = b.dataset.v;
  filters[seg.dataset.key] = ["min_overall", "min_instructor", "min_lectures", "min_recommend", ...extraQScores.map(([field]) => `min_${field}`)].includes(seg.dataset.key) && v ? Number(v) : v;
  filtersChanged();
})));
$$("#f-days button").forEach((b) => b.addEventListener("click", () => {
  const set = new Set(filters.avoid_days || []);
  set.has(b.dataset.v) ? set.delete(b.dataset.v) : set.add(b.dataset.v);
  filters.avoid_days = [...set];
  filtersChanged();
}));
[["#f-subject", "subject"], ["#f-gen_ed", "gen_ed"], ["#f-divisional", "divisional"]].forEach(([id, k]) =>
  $(id).addEventListener("change", (e) => { filters[k] = e.target.value; filtersChanged(); }));
$("#f-earliest").addEventListener("change", (e) => { filters.earliest_start = e.target.value ? Number(e.target.value) : ""; filtersChanged(); });
$("#f-workload").addEventListener("input", (e) => { filters.max_workload = Number(e.target.value) || ""; filtersChanged(); });
["qrd", "small_class", "has_reviews", "include_grad"].forEach((k) => $(`#f-${k}`).addEventListener("change", (e) => { filters[k] = e.target.checked; filtersChanged(); }));
$("#f-query").addEventListener("input", filtersChanged);
$("#f-reset").addEventListener("click", () => { filters = {}; $("#f-query").value = ""; filtersChanged(); });

function toggleFilters(open = fPanel.hidden) {
  fPanel.hidden = !open;
  document.body.classList.toggle("filters-open", open);
  $("#open-filters").setAttribute("aria-expanded", open);
  if (open) { if (!document.body.classList.contains("chatting")) showChat(); filtersChanged(); }
}
$("#open-filters").addEventListener("click", () => toggleFilters());
$("#f-close").addEventListener("click", () => toggleFilters(false));

// Results of a direct search render as a block in the thread (no AI involved).
const SORTS = [["rating", "Best rated"], ["workload", "Lightest workload"], ["recommend", "Most recommended"], ["reviews", "Most reviewed"], ["relevance", "Keyword match"]];
async function showResults(append = null) {
  const query = $("#f-query").value.trim();
  const state = { ...(append || { sort:query ? "relevance" : "rating", offset:0, query, filters:activeFilters() }) };
  let block = append?.block;
  if (block?.dataset.loading === "true" && !state.refresh) return;
  const previous = browseStates.get(block || null);
  previous?.controller.abort();
  state.controller = new AbortController();
  state.generation = browseGeneration;
  browseControllers.add(state.controller);
  browseStates.set(block || null, state);
  const current = () => state.generation === browseGeneration && browseStates.get(block || null) === state && (!block || block.isConnected);
  if (block) {
    block.dataset.loading = "true";
    $(".more", block).disabled = true;
    $("select", block).disabled = true;
  }
  $("#f-show").disabled = true;
  try {
    const res = await browseRequest({ filters:state.filters, query:state.query, sort:state.sort, offset:state.offset }, state.controller.signal);
    if (!current()) return null;
    if (!block) {
      showChat(); block = addTurn("bot results");
      browseStates.delete(null);
      browseStates.set(block, state);
      state.block = block;
      block.innerHTML = `<div class="results-head"><h2>${res.total.toLocaleString()} matching course${res.total === 1 ? "" : "s"}</h2>
        <select aria-label="Sort">${SORTS.filter(([k]) => k !== "relevance" || state.query).map(([k,l]) => `<option value="${k}"${k === state.sort ? " selected" : ""}>${l}</option>`).join("")}</select></div>
        <p class="results-sub">${esc(describeFilters(state))}</p><div class="cards"></div><button class="pill pill-soft more" hidden>Show more</button><p class="hint result-status" role="status"></p>`;
    }
    $("h2", block).textContent = `${res.total.toLocaleString()} matching course${res.total === 1 ? "" : "s"}`;
    $(".result-status", block).textContent = res.total ? "" : "No courses match. Open Filters to remove a constraint or try different keywords.";
    const select = $("select", block);
    select.value = state.sort;
    select.onchange = () => showResults({ ...state, sort:select.value, offset:0, block, refresh:true });
    const grid = $(".cards", block), tmp = document.createElement("div");
    if (state.refresh) grid.replaceChildren();
    tmp.innerHTML = res.cards.map(cardHTML).join(""); bindCards(tmp, res.cards);
    while (tmp.firstElementChild) grid.appendChild(tmp.firstElementChild);
    const more = $(".more", block), shown = state.offset + res.cards.length;
    more.hidden = shown >= res.total;
    more.textContent = "Show more";
    more.onclick = () => showResults({ ...state, offset:shown, block, refresh:false });
    if (!append) block.scrollIntoView({ behavior:"smooth", block:"start" });
    $("#browse-status").textContent = "";
    return block;
  } catch (err) {
    if (!current() || err.name === "AbortError") return null;
    const message = "Couldn't load courses. Your results are still here; try again.";
    if (block) {
      $(".result-status", block).textContent = message;
      if (state.refresh) {
        const retry = $(".more", block);
        retry.hidden = false; retry.textContent = "Retry results";
        retry.onclick = () => showResults({ ...state, offset:0, refresh:true });
      }
      $("select", block).value = previous?.sort || state.sort;
    }
    if (fPanel.hidden) { fPanel.hidden = false; document.body.classList.add("filters-open"); $("#open-filters").setAttribute("aria-expanded", "true"); }
    $("#browse-status").textContent = message;
    return null;
  } finally {
    browseControllers.delete(state.controller);
    if (browseStates.get(block || null) === state) {
      if (block) { block.dataset.loading = "false"; $(".more", block).disabled = false; $("select", block).disabled = false; }
      else browseStates.delete(null);
      $("#f-show").disabled = false;
    }
  }
}

function refreshBrowseResults() {
  for (const [block, state] of [...browseStates]) {
    if (block ? !block.isConnected : state.generation !== browseGeneration) {
      browseStates.delete(block); continue;
    }
    // The server excludes taken courses, so an old numeric offset is no longer valid.
    showResults({ ...state, offset:0, block, refresh:true });
  }
  if (!fPanel.hidden) filtersChanged();
}

function describeFilters(state) {
  const f = state.filters, parts = [];
  if (state.query) parts.push(`“${state.query}”`);
  if (f.term) parts.push(f.term);
  if (f.subject) parts.push(f.subject);
  if (f.min_overall) parts.push(`rated ${f.min_overall}+`);
  for (const [field, label] of extraQScores) {
    if (f[`min_${field}`]) parts.push(`${label.toLowerCase()} ${f[`min_${field}`]}+`);
  }
  if (f.min_instructor) parts.push(`instructor ${f.min_instructor}+`);
  if (f.min_lectures) parts.push(`effective lectures ${f.min_lectures}+`);
  if (f.min_recommend) parts.push(`recommend ${f.min_recommend}+`);
  if (f.max_workload) parts.push(`≤ ${f.max_workload} hrs/wk`);
  if (f.gen_ed) parts.push(`Gen Ed: ${f.gen_ed}`);
  if (f.divisional) parts.push(f.divisional);
  if (f.format) parts.push(f.format);
  if (f.grading) parts.push(f.grading === "letter" ? "letter graded" : "Sat/Unsat");
  if (f.avoid_days?.length) parts.push(`no ${f.avoid_days.join("/")}`);
  if (f.earliest_start) parts.push(`starts ≥ ${$(`#f-earliest option[value="${f.earliest_start}"]`)?.textContent || ""}`);
  if (f.qrd) parts.push("QRD");
  if (f.small_class) parts.push("small classes");
  if (f.has_reviews) parts.push("has Q reviews");
  if (f.include_grad) parts.push("incl. graduate courses");
  return (parts.length ? parts.join(" · ") : "All undergrad courses") + " · direct search, no AI";
}
$("#f-show").addEventListener("click", () => { showResults(); if (window.innerWidth < 1100) toggleFilters(false); });

// Populate dropdowns from the data.
api("/api/facets").then((r) => r.json()).then((fx) => {
  $("#f-subject").insertAdjacentHTML("beforeend", fx.subjects.map((s) => `<option value="${esc(s.code)}">${esc(s.code)} · ${esc(s.name)} (${s.count})</option>`).join(""));
  $("#f-gen_ed").insertAdjacentHTML("beforeend", fx.gen_eds.map((g) => `<option>${esc(g)}</option>`).join(""));
  $("#f-divisional").insertAdjacentHTML("beforeend", fx.divisions.map((g) => `<option>${esc(g)}</option>`).join(""));
  renderFilterControls();
}).catch(() => {});
filterSummary();

// ---------- navigation helpers ----------
function setActiveNav(id) { $$(".gn-link").forEach((b) => b.classList.toggle("active", b.id === id)); }
setActiveNav("nav-ask");
$("#nav-ask").addEventListener("click", () => { closeAll(); toggleFilters(false); showHome(); scrollTo({ top: 0, behavior: "smooth" }); setActiveNav("nav-ask"); });
$("#nav-browse").addEventListener("click", () => { closeAll(); toggleFilters(true); setActiveNav("nav-browse"); });
$("#cta-browse").addEventListener("click", () => { toggleFilters(true); setActiveNav("nav-browse"); });
$("#menu-prefs").addEventListener("click", () => openSheet("#prefs"));
$("#menu-favs").addEventListener("click", () => openSheet("#favs"));
$("#nav-favs").addEventListener("click", () => setActiveNav("nav-favs"));
document.addEventListener("click", (e) => { if (e.target.closest("#composer") && !document.body.classList.contains("home")) setActiveNav(""); });

// ---------- follow-up suggestions after an answer with cards ----------
const FOLLOW_UPS = ["Which of these has the lightest workload?", "Compare the top two"];
function addFollowUps(turnEl) {
  if (!turnEl.querySelector(".card") || turnEl.querySelector(".followups:not(.clarify)")) return;
  const row = document.createElement("div");
  row.className = "followups";
  row.innerHTML = '<span>Try next:</span><button class="chip-btn" data-more="1">Show more like these</button>' +
    FOLLOW_UPS.map((q) => `<button class="chip-btn">${esc(q)}</button>`).join("") +
    '<button class="chip-btn ghost" data-browse="1">Browse with filters ›</button>';
  $$("button", row).forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.browse) { toggleFilters(true); setActiveNav("nav-browse"); }
    else if (b.dataset.more) showMore(turnEl, b);
    else send(b.textContent);
  }));
  turnEl.appendChild(row);
}
// Next matches for the same search, straight from the index: instant and no AI cost.
async function showMore(turnEl, btn) {
  const generation = browseGeneration, requestSession = sessionId;
  btn.disabled = true;
  btn.textContent = "Loading…";
  let status = $(".more-status", turnEl);
  if (!status) {
    status = document.createElement("p"); status.className = "hint more-status";
    status.setAttribute("role", "status"); turnEl.appendChild(status);
  }
  status.textContent = "";
  const current = () => generation === browseGeneration && requestSession === sessionId && turnEl.isConnected;
  try {
    const response = await api("/api/more", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: requestSession }) });
    if (!response.ok) throw new Error("more failed");
    const r = await response.json();
    if (!current()) return;
    const grid = $(".cards", turnEl);
    if (!grid) return;
    if (!r.cards?.length) { btn.textContent = "No more matches"; return; }
    const shown = new Set($$(".card", turnEl).map(card => card.dataset.code));
    const cards = r.cards.filter(card => !shown.has(card.code) && !taken.has(card.code));
    const tmp = document.createElement("div");
    tmp.innerHTML = cards.map(cardHTML).join("");
    bindCards(tmp, cards);
    [...tmp.children].forEach(el => grid.appendChild(el));
    btn.disabled = false;
    btn.textContent = "Show more like these";
  } catch (err) {
    if (!current()) return;
    status.textContent = "Couldn't load more courses. Please try again.";
    btn.disabled = false; btn.textContent = "Retry more courses";
  } finally {
    if (!current() && turnEl.isConnected) { btn.disabled = false; btn.textContent = "Show more like these"; }
  }
}
new MutationObserver(() => {
  if (busy) return;
  const turns = $$(".turn.bot:not(.results)");
  const last = turns[turns.length - 1];
  if (last) addFollowUps(last);
}).observe($("#thread"), { childList: true, subtree: true });


// ---------- Replayable first-visit guide (never sends a question automatically) ----------
const guideSteps = [
  ["Start with what matters to you", "Tell Qurated your interests, the term you're planning for, and how much time you have. Follow up to compare its suggestions.", "Try: Find a Spring 2027 Gen Ed about ethics with a manageable workload."],
  ["Set your must-haves", "Use Filters for firm limits such as term, meeting days or workload. They apply to both browsing and chat. Preferences are softer guidance saved to your account.", "Browse with filters doesn't use your daily question allowance. Reset filters if too few courses match."],
  ["Read the evidence", "Open a course card for meeting times, requirements and student reviews. Ratings and workloads describe past offerings; a course with no reviews isn't necessarily a bad fit.", "Always confirm availability and degree requirements in my.harvard or with your adviser. Qurated cannot guarantee grades or enrollment."],
  ["Build your shortlist", "Tap a heart to save a course. With two or more favorites, you can ask Qurated to compare them. Mark courses you've taken so they won't be recommended again.", "New chat clears the conversation on this page. Your saved preferences, favorites, taken courses and filters stay in place."]
];
const guide = $("#quick-guide");
let guideStep = 0;
function renderGuide() {
  const [title, copy, example] = guideSteps[guideStep];
  $("#guide-progress").textContent = `Step ${guideStep + 1} of ${guideSteps.length}`;
  $("#guide-title").textContent = title;
  $("#guide-copy").textContent = copy;
  $("#guide-example").textContent = example;
  $("#guide-back").disabled = guideStep === 0;
  $("#guide-next").textContent = guideStep === guideSteps.length - 1 ? "Try a question" : "Next";
  $("#guide-title").focus();
}
function openGuide() {
  closeAll(); toggleFilters(false); acctMenu.hidden = true;
  guideStep = 0; guide.showModal(); renderGuide();
}
function dismissGuideInvitation() {
  store.set("qurated.guideSeen.v1", true);
  $("#guide-welcome").hidden = true;
}
function closeGuide() {
  // Persist before closing: the browser queues the native close event asynchronously.
  // A reload/navigation can happen after the dialog disappears but before that event runs.
  dismissGuideInvitation();
  guide.close();
}
["#start-guide", "#open-guide", "#menu-guide"].forEach(id => $(id).addEventListener("click", openGuide));
$("#dismiss-guide").addEventListener("click", dismissGuideInvitation);
["#guide-close", "#guide-skip"].forEach(id => $(id).addEventListener("click", closeGuide));
guide.addEventListener("cancel", event => { event.preventDefault(); closeGuide(); });
guide.addEventListener("close", dismissGuideInvitation);
$("#guide-back").addEventListener("click", () => { guideStep--; renderGuide(); });
$("#guide-next").addEventListener("click", () => {
  if (guideStep < guideSteps.length - 1) { guideStep++; renderGuide(); return; }
  closeGuide();
  $("#input").value = "Find a Spring 2027 Gen Ed about ethics with a manageable workload.";
  $("#input").focus(); $("#input").scrollIntoView({ block:"center", behavior:"smooth" });
});
$("#guide-welcome").hidden = !!store.get("qurated.guideSeen.v1", false);
