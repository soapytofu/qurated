const error = document.getElementById("login-error");
const status = document.getElementById("login-status");
const retry = document.getElementById("login-retry");
const slot = document.getElementById("gsi-button");
let generation = 0;
let signingIn = false;

function showError(message) {
  error.textContent = message;
  error.hidden = false;
  status.textContent = "";
  retry.hidden = false;
}

async function request(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    // fetch resolves at the response headers; keep the deadline through body consumption.
    const body = await response.json().catch(err => {
      if (controller.signal.aborted) throw err;
      return {};
    });
    return { ok: response.ok, body };
  } finally { clearTimeout(timer); }
}

function loadGoogle() {
  if (window.google?.accounts?.id) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    const finish = (failed) => {
      clearTimeout(timer);
      script.onload = script.onerror = null;
      if (failed) { script.remove(); reject(new Error("Sign-in provider unavailable")); }
      else resolve();
    };
    const timer = setTimeout(() => finish(true), 15000);
    script.onload = () => finish(!window.google?.accounts?.id);
    script.onerror = () => finish(true);
    document.head.appendChild(script);
  });
}

async function onCredential(response, version) {
  if (version !== generation || signingIn) return;
  if (!response?.credential) {
    showError("Google did not complete sign-in. Please try again.");
    return;
  }
  signingIn = true;
  error.hidden = retry.hidden = true;
  status.textContent = "Signing in…";
  try {
    const result = await request("/api/auth/google", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential: response.credential }),
    });
    if (version !== generation) return;
    if (result.ok) { location.replace("/"); return; }
    const body = result.body || {};
    showError(typeof body.detail === "string" ? body.detail : "Sign-in failed. Please try again.");
  } catch {
    if (version === generation) showError("Couldn't reach sign-in. Check your connection and try again.");
  } finally { if (version === generation) signingIn = false; }
}

async function init() {
  const version = ++generation;
  signingIn = false;
  error.hidden = retry.hidden = true;
  slot.replaceChildren();
  status.textContent = "Loading sign-in…";
  try {
    const result = await request("/api/auth/config");
    if (!result.ok) throw new Error("Configuration unavailable");
    const config = result.body;
    if (version !== generation) return;
    if (!config || typeof config !== "object" || !config.google_client_id || !Array.isArray(config.allowed_domains) ||
        !config.allowed_domains.length || config.allowed_domains.some(d => typeof d !== "string" || !d)) {
      showError("Sign-in is temporarily unavailable. Please try again later.");
      return;
    }
    const domains = config.allowed_domains.map(d => "@" + d).join(" or ");
    document.getElementById("domain-note").textContent = `Sign in with your ${domains} Google account.`;
    await loadGoogle();
    if (version !== generation) return;
    google.accounts.id.initialize({
      client_id: config.google_client_id,
      callback: response => onCredential(response, version),
      hd: config.allowed_domains.length === 1 ? config.allowed_domains[0] : undefined,
      ux_mode: "popup", auto_select: false,
    });
    google.accounts.id.renderButton(slot, {
      theme: "outline", size: "large", shape: "pill", text: "continue_with", width: 300,
    });
    status.textContent = "";
  } catch {
    if (version === generation) showError("Couldn't load sign-in. Check your connection and try again.");
  }
}

retry.addEventListener("click", init);
init();
