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
const putJSON = (path, body) => api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
const del = (path) => api(path, { method: "DELETE" });
const enc = encodeURIComponent;

// ---------- views: home (Apple-style landing) vs. conversation ----------
function showHome() {
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
      <div class="code">${esc(c.code)}${c.gen_ed ? ` <span class="gened">${esc(c.gen_ed)}</span>` : ""}</div>
      <h3>${esc(c.title)}</h3>
      ${c.next_offered ? `<div class="meets">${ICON_CAL}${esc(c.next_offered)}</div>` : ""}
      ${tagsHTML(c.tags)}
      ${c.reason ? `<p class="reason">${esc(c.reason)}</p>` : ""}
      ${c.quote ? `<p class="quote">“${esc(c.quote)}”</p>` : ""}
      ${c.has_q === false ? '<div class="no-q">New: no Q reviews yet</div>' : `
      <div class="stats">
        <div class="stat"><div class="n${overallHi}">${fmt(c.overall)}</div><div class="l">Overall</div></div>
        <div class="stat"><div class="n">${fmt(c.workload_hrs)}<small>hrs</small></div><div class="l">Per week</div></div>
        <div class="stat"><div class="n">${fmt(c.recommend)}</div><div class="l">Recommend</div></div>
      </div>`}
      <div class="card-foot">
        <div class="who"><span class="avatar">${esc(initials(prof))}</span><span>${esc(prof + more)}</span></div>
        <div class="term">${esc(c.term || "")}</div>
      </div>
      <button class="taken-btn${taken.has(c.code) ? " on" : ""}" aria-pressed="${taken.has(c.code)}">${taken.has(c.code) ? "✓ Taken" : "I've taken this"}</button>
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
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") openDetail(c.code); });
  });
}

function syncCardButtons(code) {
  $$(`.card[data-code="${CSS.escape(code)}"]`).forEach((el) => {
    const f = $(".fav", el), t = $(".taken-btn", el);
    f.classList.toggle("on", !!favorites[code]); f.setAttribute("aria-pressed", !!favorites[code]);
    t.classList.toggle("on", taken.has(code)); t.setAttribute("aria-pressed", taken.has(code));
    t.textContent = taken.has(code) ? "✓ Taken" : "I've taken this";
  });
}

function toggleFav(card) {
  const on = !favorites[card.code];
  if (on) favorites[card.code] = card; else delete favorites[card.code];
  syncCardButtons(card.code);
  renderFavs();
  (on ? putJSON(`/api/account/favorites/${enc(card.code)}`, card) : del(`/api/account/favorites/${enc(card.code)}`)).catch(() => {});
}

function toggleTaken(code, title) {
  const on = !taken.has(code);
  if (on) taken.set(code, title || ""); else taken.delete(code);
  syncCardButtons(code);
  renderTaken();
  (on ? putJSON(`/api/account/taken/${enc(code)}`) : del(`/api/account/taken/${enc(code)}`)).catch(() => {});
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
  busy = true;
  $("#ask").disabled = true;
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
    const res = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message, preferences: prefs, favorites: Object.keys(favorites) }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      note("error", body.detail || `Request failed (${res.status}).`);
      return;
    }
    sessionId = res.headers.get("X-Session-Id") || sessionId;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
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
        } else if (ev.type === "usage") {
          note("usage", `${ev.searched ? "Searched courses" : "Answered directly"} · ${progress.seconds()}s`);
        } else if (ev.type === "error") {
          note("error", ev.text);
        }
      }
    }
  } catch (e) {
    if (e.message !== "signed out") note("error", "Lost connection to the Qurated server. Is it running?");
  } finally {
    progress.finish();
    busy = false;
    $("#ask").disabled = false;
    $("#input").focus();
    refreshMe();
  }
}

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
$$(".try").forEach((b) => b.addEventListener("click", () => send(b.textContent.replace("›", ""))));
$("#new-chat").addEventListener("click", () => { sessionId = null; $("#thread").innerHTML = ""; showHome(); scrollTo({ top: 0 }); });
$("#nav-ask").addEventListener("click", () => { if (!document.body.classList.contains("chatting")) scrollTo({ top: 0, behavior: "smooth" }); $("#input").focus(); });
$("#cta-ask").addEventListener("click", () => $("#input").focus());
$("#nav-how").addEventListener("click", () => { showHome(); $("#how").scrollIntoView({ behavior: "smooth" }); });

// ---------- workload quick menu (mirrors the preference slider) ----------
const menu = $("#workload-menu");
function syncWorkload() {
  const w = Number(prefs.max_workload_hrs) || 0;
  $("#seg-workload-label").textContent = w ? `Under ${w} hrs/wk` : "Any workload";
  $("#wl-val").textContent = w ? `${w} hrs/week` : "no limit";
  $('[name="max_workload_hrs"]').value = w;
}
$("#seg-workload").addEventListener("click", (e) => {
  e.stopPropagation();
  const r = e.currentTarget.getBoundingClientRect();
  const above = document.body.classList.contains("chatting");
  menu.style.position = "fixed";
  menu.style.left = `${r.left}px`;
  menu.style.top = above ? `${r.top - 186}px` : `${r.bottom + 8}px`;
  menu.hidden = !menu.hidden;
});
$$("button", menu).forEach((b) => b.addEventListener("click", () => {
  prefs.max_workload_hrs = b.dataset.w ? Number(b.dataset.w) : 0;
  savePrefs();
  syncWorkload();
  menu.hidden = true;
}));

// ---------- sheets ----------
function openSheet(id) { $("#scrim").hidden = false; $(id).hidden = false; }
function closeAll() { ["#prefs", "#favs", "#detail", "#feedback", "#taken"].forEach((id) => ($(id).hidden = true)); $("#scrim").hidden = true; }
$$("[data-close]").forEach((b) => b.addEventListener("click", closeAll));
$("#scrim").addEventListener("click", closeAll);
$("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") closeAll(); });
$("#feedback").addEventListener("click", (e) => { if (e.target.id === "feedback") closeAll(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeAll(); acctMenu.hidden = true; } });
document.addEventListener("click", (e) => {
  if (!menu.contains(e.target)) menu.hidden = true;
  if (!acctMenu.contains(e.target) && !e.target.closest("#account")) acctMenu.hidden = true;
});

// preferences
$("#open-prefs").addEventListener("click", () => openSheet("#prefs"));
$("#cta-prefs").addEventListener("click", () => openSheet("#prefs"));
let prefsTimer = null;
function savePrefs() {  // debounced save to the account
  clearTimeout(prefsTimer);
  prefsTimer = setTimeout(() => putJSON("/api/account/prefs", prefs).catch(() => {}), 500);
}
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
let sugTimer = null;
tIn.addEventListener("input", () => {
  clearTimeout(sugTimer);
  const q = tIn.value.trim();
  if (q.length < 2) { tSug.hidden = true; return; }
  sugTimer = setTimeout(async () => {
    const items = await api(`/api/courses/suggest?q=${enc(q)}`).then((r) => r.json()).catch(() => []);
    tSug.innerHTML = items.length
      ? items.map((i) => `<button data-code="${esc(i.code)}" data-title="${esc(i.title)}"${taken.has(i.code) ? " disabled" : ""}><b>${esc(i.code)}</b> ${esc(i.title)}${taken.has(i.code) ? " ✓" : ""}</button>`).join("")
      : '<div class="none">No matching course</div>';
    tSug.hidden = false;
    $$("button", tSug).forEach((b) => b.addEventListener("click", () => {
      toggleTaken(b.dataset.code, b.dataset.title);
      tIn.value = ""; tSug.hidden = true; tIn.focus();
    }));
  }, 180);
});
tIn.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("button:not([disabled])", tSug)?.click(); }
});
function openTaken() { closeAll(); acctMenu.hidden = true; openSheet("#taken"); tIn.focus(); }
$("#prefs-taken").addEventListener("click", openTaken);

// ---------- load the student's saved account (with one-time move from this browser) ----------
async function loadAccount() {
  const acct = await api("/api/account").then((r) => r.json()).catch(() => null);
  if (!acct) return;
  favorites = Object.fromEntries(acct.favorites.map((c) => [c.code, c]));
  taken = new Map(acct.taken.map((t) => [t.code, t.title]));
  prefs = acct.prefs || {};
  // Older versions saved favorites/preferences only in this browser; move them to the account once.
  const oldFavs = store.get("hua.favorites", null), oldPrefs = store.get("hua.prefs", null);
  if (oldFavs) {
    for (const [code, card] of Object.entries(oldFavs)) {
      if (!favorites[code]) { favorites[code] = card; putJSON(`/api/account/favorites/${enc(code)}`, card).catch(() => {}); }
    }
  }
  if (oldPrefs && !Object.keys(prefs).length) { prefs = oldPrefs; putJSON("/api/account/prefs", prefs).catch(() => {}); }
  try { localStorage.removeItem("hua.favorites"); localStorage.removeItem("hua.prefs"); } catch {}
  renderFavs(); renderTaken(); fillPrefs();
  Object.keys(favorites).forEach(syncCardButtons);
}
loadAccount();

// ---------- course detail ----------
function catalogHTML(cat) {
  if (!cat) return '<p class="meta">Not in the Fall 2026 / Spring 2027 catalog.</p>';
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
    ${cat.syllabus_url ? `<p><a class="link" href="${esc(cat.syllabus_url)}" target="_blank" rel="noopener">View syllabus ›</a></p>` : ""}`;
}
async function openDetail(code) {
  $("#detail-body").innerHTML = '<p class="meta">Loading…</p>';
  $("#scrim").hidden = true;
  $("#detail").hidden = false;
  const res = await api(`/api/course/${encodeURIComponent(code)}`);
  if (!res.ok) { $("#detail-body").innerHTML = '<p class="meta">No Q data found.</p>'; return; }
  const c = await res.json();
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
    ${c.offerings.length ? `<h3 class="sub">Q report history</h3>` : '<p class="no-q">New: no Q reviews yet</p>'}
    <table class="offerings"${c.offerings.length ? "" : " hidden"}>
      <thead><tr><th>Term</th><th>Instructor</th><th>Overall</th><th>Hrs/wk</th><th>Recommend</th><th>Responses</th></tr></thead>
      <tbody>${c.offerings.map((o) => `
        <tr><td>${esc(o.term)}</td><td>${esc(o.instructors.join(", "))}</td><td>${fmt(o.course_overall, 2)}</td>
        <td>${fmt(o.workload_hrs_mean)}</td><td>${fmt(o.recommend_mean, 2)}</td><td>${fmt(o.responded, 0)}/${fmt(o.invited, 0)}</td></tr>`).join("")}
      </tbody>
    </table>
    <div class="comments">
      <h3>What students said <span class="meta">(${c.total_comments})</span></h3>
      ${c.comments.map((x) => `<div class="comment">${esc(x.text)}<div class="ct">${esc(x.term)}</div></div>`).join("") || '<p class="meta">No written comments.</p>'}
    </div>`;
  $("#d-fav").addEventListener("click", async () => {
    const card = favorites[c.code] || (await api("/api/cards", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify([c.code]) }).then((r) => r.json()))[0];
    if (card) toggleFav(card);
    $("#d-fav").textContent = favorites[c.code] ? "♥ Favorited" : "♡ Favorite";
  });
  $("#d-taken").addEventListener("click", () => {
    toggleTaken(c.code, c.title);
    $("#d-taken").textContent = taken.has(c.code) ? "✓ Taken" : "I've taken this";
  });
}

// ---------- account ----------
const acctMenu = $("#account-menu");
$("#account").addEventListener("click", (e) => { e.stopPropagation(); acctMenu.hidden = !acctMenu.hidden; });
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
api("/api/meta").then((r) => r.json()).then((m) => {
  if (!m.courses) return;
  const span = m.terms.length > 1 ? `${m.terms[0]} – ${m.terms[m.terms.length - 1]}` : m.terms[0];
  $("#hero-sub").textContent = `Ask in plain English. Answers come straight from Q reports for ${m.courses.toLocaleString()} courses (${span}).`;
  const obs = new IntersectionObserver((entries) => {
    if (!entries[0].isIntersecting) return;
    countUp($("#st-courses"), m.courses);
    countUp($("#st-comments"), m.comments);
    countUp($("#st-terms"), m.terms.length);
    obs.disconnect();
  }, { threshold: 0.4 });
  obs.observe($("#stats-tile"));
}).catch(() => {});

// ---------- feedback ----------
const fbStatus = $("#fb-status");
$("#open-feedback").addEventListener("click", () => {
  fbStatus.hidden = true;
  $("#feedback").hidden = false;
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
