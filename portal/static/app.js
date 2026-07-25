const TOKEN_KEY = "platform_token";

const loginGate = document.getElementById("login-gate");
const appShell = document.getElementById("app-shell");
const loginEmail = document.getElementById("login-email");
const loginOrg = document.getElementById("login-org");
const loginError = document.getElementById("login-error");
const loginPresets = document.getElementById("login-presets");
const sessionLabel = document.getElementById("session-label");

const profileButtons = document.getElementById("profile-buttons");
const emailEl = document.getElementById("email");
const orgEl = document.getElementById("org");
const irbEl = document.getElementById("irb");
const queryEl = document.getElementById("query");
const resultsPanel = document.getElementById("results-panel");
const aggregateEl = document.getElementById("aggregate");
const nodeResultsEl = document.getElementById("node-results");
const retrieveOut = document.getElementById("retrieve-out");
const auditOut = document.getElementById("audit-out");
const gatewayReqOut = document.getElementById("gateway-req-out");

let profiles = {};
let activeKey = "harvard_irb";
let platformToken = localStorage.getItem(TOKEN_KEY) || "";

function authHeaders(json = true) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  if (platformToken) h["Authorization"] = `Bearer ${platformToken}`;
  return h;
}

function researcher() {
  return {
    researcher_id: emailEl.value.trim(),
    org: orgEl.value.trim(),
    irb_approved: irbEl.checked,
  };
}

function applyProfile(key) {
  activeKey = key;
  const p = profiles[key];
  if (!p) return;
  emailEl.value = p.researcher_id;
  orgEl.value = p.org;
  irbEl.checked = !!p.irb_approved;
  [...profileButtons.querySelectorAll("button")].forEach((b) => {
    b.classList.toggle("active", b.dataset.key === key);
  });
}

async function loadProfiles() {
  const res = await fetch("/profiles");
  profiles = await res.json();
  profileButtons.innerHTML = "";
  loginPresets.innerHTML = "";
  const labels = {
    harvard_irb: "Harvard + IRB",
    mit_partner: "MIT (no IRB)",
    neu: "Northeastern",
    bu: "BU",
    guest: "Guest",
  };
  for (const key of Object.keys(profiles)) {
    const p = profiles[key];
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.key = key;
    btn.textContent = labels[key] || key;
    btn.addEventListener("click", () => applyProfile(key));
    profileButtons.appendChild(btn);

    if (key === "guest") continue; // cannot enter platform
    const lb = document.createElement("button");
    lb.type = "button";
    lb.textContent = labels[key] || key;
    lb.addEventListener("click", () => {
      loginEmail.value = p.researcher_id;
      loginOrg.value = p.org;
    });
    loginPresets.appendChild(lb);
  }
  applyProfile(activeKey);
}

function showApp(email) {
  loginGate.hidden = true;
  appShell.hidden = false;
  sessionLabel.textContent = `Signed in as ${email}`;
}

function showLogin(msg) {
  platformToken = "";
  localStorage.removeItem(TOKEN_KEY);
  loginGate.hidden = false;
  appShell.hidden = true;
  if (msg) {
    loginError.hidden = false;
    loginError.textContent = msg;
  } else {
    loginError.hidden = true;
  }
}

async function doPlatformLogin() {
  loginError.hidden = true;
  const res = await fetch("/platform/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: loginEmail.value.trim(),
      org: loginOrg.value.trim(),
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    showLogin(data.detail || "Platform SSO denied");
    return;
  }
  platformToken = data.token;
  localStorage.setItem(TOKEN_KEY, platformToken);
  // Align hospital identity with platform user by default
  emailEl.value = data.email;
  orgEl.value = data.org || "";
  showApp(data.email);
}

async function restoreSession() {
  if (!platformToken) {
    showLogin();
    return;
  }
  const res = await fetch("/platform/me", { headers: authHeaders(false) });
  if (!res.ok) {
    showLogin("Session expired — sign in again");
    return;
  }
  const me = await res.json();
  showApp(me.email);
}

function badge(status) {
  const cls = status === "ok" ? "ok" : status === "suppressed" ? "suppressed" : "denied";
  return `<span class="badge ${cls}">${status}</span>`;
}

function renderResults(data) {
  resultsPanel.hidden = false;
  if (data.gateway_request) {
    gatewayReqOut.hidden = false;
    gatewayReqOut.textContent = "Gateway request → " + JSON.stringify(data.gateway_request);
  }
  if (data.portal_suppressed) {
    aggregateEl.innerHTML = `<strong>Aggregate:</strong> suppressed — ${data.portal_reason}`;
  } else {
    aggregateEl.innerHTML = `<strong>Aggregate count:</strong> ${data.aggregate_count ?? "—"}
      <span class="meta"> · expanded: ${(data.expanded_terms || []).slice(0, 8).join(", ")}</span>`;
  }

  nodeResultsEl.innerHTML = "";
  for (const node of data.nodes || []) {
    const card = document.createElement("article");
    card.className = "card";
    const gw = node.gateway || {};
    const studies = (node.studies || [])
      .slice(0, 5)
      .map(
        (s) =>
          `<div><button class="linkish" data-node="${node.node}" data-id="${s.StudyID}" type="button">${s.StudyID}</button> · ${s.BodyPartExamined} · ${s.Modality}</div>`
      )
      .join("");
    card.innerHTML = `
      <h3>${node.node}</h3>
      <div>${badge(node.status)} <span class="badge ok">${node.tier || ""}</span>
        ${gw.count_band ? `<span class="badge ok">band ${gw.count_band}</span>` : ""}
      </div>
      <p class="meta">Gateway SSO: ${node.sso || "—"} · count: ${node.count ?? "—"}
      ${gw.access_available != null ? `<br/>access_available: ${gw.access_available}` : ""}
      ${node.reason ? `<br/>${node.reason}` : ""}
      ${node.scope && node.scope.length ? `<br/>scopes: ${node.scope.join(", ")}` : ""}</p>
      ${studies ? `<div class="studies">${studies}</div>` : ""}
    `;
    nodeResultsEl.appendChild(card);
  }

  nodeResultsEl.querySelectorAll("button.linkish").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("retrieve-node").value = btn.dataset.node;
      document.getElementById("study-id").value = btn.dataset.id;
      doRetrieve();
    });
  });
}

async function doSearch() {
  const res = await fetch("/search", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ q: queryEl.value, researcher: researcher() }),
  });
  if (res.status === 401) {
    showLogin("Platform session required");
    return;
  }
  const data = await res.json();
  renderResults(data);
}

async function doRetrieve() {
  const body = {
    node: document.getElementById("retrieve-node").value,
    study_id: document.getElementById("study-id").value.trim(),
    researcher: researcher(),
  };
  const res = await fetch("/retrieve", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  const data = await res.json();
  retrieveOut.textContent = JSON.stringify(data, null, 2);
}

async function doAudit() {
  const node = document.getElementById("audit-node").value;
  const res = await fetch(`/audit/${node}`, { headers: authHeaders(false) });
  const data = await res.json();
  auditOut.textContent = JSON.stringify(data, null, 2);
}

document.getElementById("login-btn").addEventListener("click", doPlatformLogin);
document.getElementById("logout-btn").addEventListener("click", () => showLogin());
document.getElementById("search-btn").addEventListener("click", doSearch);
document.getElementById("retrieve-btn").addEventListener("click", doRetrieve);
document.getElementById("audit-btn").addEventListener("click", doAudit);

loadProfiles().then(restoreSession);
