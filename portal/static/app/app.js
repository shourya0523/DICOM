const TOKEN_KEY = "platform_token";

const loginGate = document.getElementById("login-gate");
const appShell = document.getElementById("app-shell");
const loginEmail = document.getElementById("login-email");
const loginOrg = document.getElementById("login-org");
const loginError = document.getElementById("login-error");
const loginPresets = document.getElementById("login-presets");
const sessionLabel = document.getElementById("session-label");
const aggregateEl = document.getElementById("aggregate");
const nodeResultsEl = document.getElementById("node-results");
const detailPanel = document.getElementById("detail-panel");
const detailTitle = document.getElementById("detail-title");
const detailBody = document.getElementById("detail-body");
const detailEyebrow = document.getElementById("detail-eyebrow");

let profiles = {};
let platformToken = localStorage.getItem(TOKEN_KEY) || "";
let sessionResearcher = {
  researcher_id: "",
  org: "",
  irb_approved: false,
};
/** @type {Record<string, any>} */
let lastByNode = {};
let selectedNode = null;

function authHeaders(json = true) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  if (platformToken) h["Authorization"] = `Bearer ${platformToken}`;
  return h;
}

function researcher() {
  return { ...sessionResearcher };
}

function optionalNumber(id) {
  const raw = document.getElementById(id).value;
  if (raw === "" || raw == null) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function conceptCode(raw) {
  const cleaned = String(raw || "")
    .trim()
    .replace(/[^A-Za-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return cleaned ? cleaned.toUpperCase() : "";
}

function readFilters() {
  const body = document.getElementById("body-part").value;
  const filters = {
    patient_age_min: optionalNumber("age-min"),
    patient_age_max: optionalNumber("age-max"),
    gestational_age_min_weeks: optionalNumber("ga-min"),
    gestational_age_max_weeks: optionalNumber("ga-max"),
    modality: document.getElementById("modality").value || null,
    body_parts: body ? [body] : [],
    concepts: [],
  };
  const concept = document.getElementById("concept").value.trim();
  if (concept) {
    filters.concepts = [{ code: conceptCode(concept), assertion: "PRESENT" }];
  }
  return filters;
}

function setOptionalNumber(id, value) {
  document.getElementById(id).value = value == null || value === "" ? "" : value;
}

function applyFiltersToForm(filters) {
  if (!filters) return;
  setOptionalNumber("age-min", filters.patient_age_min ?? filters.age_min);
  setOptionalNumber("age-max", filters.patient_age_max ?? filters.age_max);
  setOptionalNumber("ga-min", filters.gestational_age_min_weeks);
  setOptionalNumber("ga-max", filters.gestational_age_max_weeks);
  document.getElementById("modality").value = filters.modality || "";
  const parts = filters.body_parts || (filters.body_part ? [filters.body_part] : []);
  document.getElementById("body-part").value = parts[0] || "";
  const concepts = filters.concepts;
  if (Array.isArray(concepts) && concepts.length && concepts[0].code) {
    document.getElementById("concept").value = concepts[0].code;
  } else if (filters.concept) {
    document.getElementById("concept").value = filters.concept;
  } else {
    document.getElementById("concept").value = "";
  }
}

function resolveSessionProfile(email, org) {
  const normalized = (email || "").trim().toLowerCase();
  const match = Object.values(profiles).find(
    (p) => (p.researcher_id || "").toLowerCase() === normalized
  );
  sessionResearcher = {
    researcher_id: normalized,
    org: org || match?.org || "",
    irb_approved: !!match?.irb_approved,
  };
}

async function loadProfiles() {
  const res = await fetch("/profiles");
  profiles = await res.json();
  loginPresets.innerHTML = "";
  const labels = {
    harvard_irb: "Harvard + IRB",
    mit_partner: "MIT",
    neu: "Northeastern",
    bu: "BU",
  };
  for (const [key, p] of Object.entries(profiles)) {
    if (key === "guest") continue;
    const lb = document.createElement("button");
    lb.type = "button";
    lb.textContent = labels[key] || key;
    lb.title = `Sign in as ${p.researcher_id}`;
    lb.addEventListener("click", () => {
      loginEmail.value = p.researcher_id;
      loginOrg.value = p.org;
      [...loginPresets.querySelectorAll("button")].forEach((b) => {
        const on = b === lb;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    });
    loginPresets.appendChild(lb);
  }
}

function showApp(email) {
  loginGate.hidden = true;
  appShell.hidden = false;
  sessionLabel.textContent = email;
}

function showLogin(msg) {
  platformToken = "";
  localStorage.removeItem(TOKEN_KEY);
  loginGate.hidden = false;
  appShell.hidden = true;
  sessionResearcher = { researcher_id: "", org: "", irb_approved: false };
  hideDetail();
  if (msg) {
    loginError.hidden = false;
    loginError.textContent = typeof msg === "string" ? msg : JSON.stringify(msg);
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
  resolveSessionProfile(data.email, data.org || "");
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
  resolveSessionProfile(me.email, me.org || "");
  showApp(me.email);
}

async function suggestFromNL() {
  const q = document.getElementById("nl-query").value.trim();
  if (!q) return;
  const res = await fetch("/gateway/preview", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ q }),
  });
  if (res.status === 401) {
    showLogin("Platform session required");
    return;
  }
  const data = await res.json();
  applyFiltersToForm((data.gateway_request || {}).filters || {});
}

function trustIcon(kind) {
  const cls = trustClass(kind);
  if (cls === "granted") {
    return `<span class="trust-icon granted" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
        <path d="M9 8H7a4 4 0 0 0 0 8h2"/><path d="M15 8h2a4 4 0 0 1 0 8h-2"/><path d="M8 12h8"/>
      </svg>
    </span>`;
  }
  if (cls === "suppressed") {
    return `<span class="trust-icon suppressed" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-dasharray="3 2.5">
        <path d="M9 8H7a4 4 0 0 0 0 8h2"/><path d="M15 8h2a4 4 0 0 1 0 8h-2"/><path d="M8 12h8"/>
      </svg>
    </span>`;
  }
  return `<span class="trust-icon denied" aria-hidden="true">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
      <path d="M9 8H7a4 4 0 0 0 0 8h2"/><path d="M15 8h2a4 4 0 0 1 0 8h-2"/>
      <path d="M8 12h3"/><path d="M13 12h3"/><path d="M12 10.5v3"/>
    </svg>
  </span>`;
}

function trustClass(status) {
  if (status === "ok" || status === "complete" || status === "allow" || status === "granted") {
    return "granted";
  }
  if (status === "suppressed" || status === "suppress") return "suppressed";
  return "denied";
}

function badge(status) {
  const cls = trustClass(status);
  const label =
    cls === "granted" ? "has data" : cls === "suppressed" ? "protected" : "no access";
  return `<span class="badge ${cls}">${label}</span>`;
}

function emptyResultsHtml() {
  return `<div class="empty-state" id="results-empty">
    <p class="empty-title">No hospitals yet</p>
    <p class="empty-copy">Ask the coordinator with a plain-language query. Matching hospitals will appear here — click one for details.</p>
  </div>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function hospitalLabel(node) {
  const names = {
    BCH: "Boston Children's",
    MGH: "Mass General",
    BWH: "Brigham & Women's",
  };
  return names[node] || node;
}

function hideDetail() {
  selectedNode = null;
  detailPanel.hidden = true;
  detailBody.innerHTML = "";
  [...nodeResultsEl.querySelectorAll(".card")].forEach((c) => {
    c.classList.remove("selected");
    c.setAttribute("aria-pressed", "false");
  });
}

function openHospitalDetail(nodeKey) {
  const entry = lastByNode[nodeKey];
  if (!entry) return;
  selectedNode = nodeKey;
  const { node, gw, status } = entry;
  const tClass = trustClass(status);
  const reason = node.reason || gw.reason || "—";
  const summary = gw.sample_summary || {};
  const mods = (summary.modalities || []).join(", ") || "—";
  const parts = (summary.body_parts || []).join(", ") || "—";
  const studies = node.studies || gw._studies || [];

  detailPanel.hidden = false;
  detailEyebrow.textContent = "Hospital detail";
  detailTitle.textContent = `${nodeKey} · ${hospitalLabel(nodeKey)}`;

  [...nodeResultsEl.querySelectorAll(".card")].forEach((c) => {
    const on = c.dataset.node === nodeKey;
    c.classList.toggle("selected", on);
    c.setAttribute("aria-pressed", on ? "true" : "false");
  });

    const studyRows = studies.length
    ? studies
        .slice(0, 8)
        .map((s) => {
          const studyId = s.StudyID || s.study_id || "";
          const uid = s.StudyInstanceUID || "";
          const display = studyId || (uid ? `${String(uid).slice(0, 18)}…` : "—");
          return `<tr>
            <td class="mono">${escapeHtml(display)}</td>
            <td class="mono">${escapeHtml(s.Modality || "—")}</td>
            <td class="mono">${escapeHtml(s.BodyPartExamined || "—")}</td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="3" class="meta">No study rows returned for this site.</td></tr>`;

  detailBody.innerHTML = `
    <div class="status-row detail-status">
      ${trustIcon(status)}
      ${badge(status)}
      ${gw.count_band ? `<span class="badge band">${escapeHtml(gw.count_band)}</span>` : ""}
    </div>
    <p class="reason">${escapeHtml(reason)}</p>
    <table class="data-table">
      <tr><th>Matches</th><td>${escapeHtml(String(gw.match_count ?? node.count ?? "—"))}</td></tr>
      <tr><th>Access available</th><td>${escapeHtml(String(gw.access_available ?? "—"))}</td></tr>
      <tr><th>Modality · body part</th><td>${escapeHtml(`${mods} · ${parts}`)}</td></tr>
      <tr><th>Tier</th><td class="mono">${escapeHtml(String(node.tier || "—"))}</td></tr>
    </table>
    <h3 class="section-title detail-section">Studies for this query</h3>
    <div class="study-wrap">
      <table class="data-table studies-table">
        <thead><tr><th>Study</th><th>Modality</th><th>Body part</th></tr></thead>
        <tbody>${studyRows}</tbody>
      </table>
    </div>
    <h3 class="section-title detail-section">Access audit</h3>
    <div id="detail-audit" class="audit-log">
      <p class="meta">Loading audit…</p>
    </div>
  `;

  loadHospitalAudit(nodeKey);
}

async function loadHospitalAudit(node) {
  const host = document.getElementById("detail-audit");
  if (!host) return;
  try {
    const res = await fetch(`/audit/${node}`, { headers: authHeaders(false) });
    const data = await res.json();
    const events = Array.isArray(data?.events) ? data.events : Array.isArray(data) ? data : [];
    if (!events.length) {
      host.innerHTML = `<div class="empty-state compact"><p class="empty-copy">No audit events yet for ${escapeHtml(node)}.</p></div>`;
      return;
    }
    host.innerHTML = "";
    for (const ev of events.slice().reverse().slice(0, 12)) {
      const decision = String(ev.decision || "deny").toLowerCase();
      const cls =
        decision === "allow" ? "allow" : decision === "suppress" ? "suppress" : "deny";
      const row = document.createElement("article");
      row.className = `audit-row ${cls}`;
      row.innerHTML = `
        ${trustIcon(cls)}
        <div>
          <div class="audit-meta">${escapeHtml(ev.ts || "—")} · ${escapeHtml(node)}</div>
          <div class="audit-decision">${escapeHtml(decision)}</div>
        </div>
        <p class="audit-reason">${escapeHtml(ev.reason || "—")}</p>
      `;
      host.appendChild(row);
    }
  } catch {
    host.innerHTML = `<p class="error">Could not load audit for ${escapeHtml(node)}.</p>`;
  }
}

function renderResults(data) {
  if (data.gateway_request?.filters) {
    applyFiltersToForm(data.gateway_request.filters);
  }

  const gatewayResponses = data.gateway_responses || [];
  const byProvider = Object.fromEntries(
    gatewayResponses.map((g) => [g.provider, g])
  );

  lastByNode = {};
  hideDetail();
  nodeResultsEl.innerHTML = "";

  const nodes = data.nodes || [];
  if (!nodes.length) {
    aggregateEl.className = "sub";
    aggregateEl.textContent = "No hospital responses.";
    nodeResultsEl.innerHTML = emptyResultsHtml();
    return;
  }

  const withData = nodes.filter((n) => {
    const gw = byProvider[n.node] || n.gateway || {};
    const st = trustClass(gw.status || n.status);
    return st === "granted" || st === "suppressed";
  });
  const denied = nodes.filter((n) => {
    const gw = byProvider[n.node] || n.gateway || {};
    return trustClass(gw.status || n.status) === "denied";
  });

  if (data.portal_suppressed) {
    aggregateEl.className = "aggregate-notice";
    aggregateEl.textContent = `Counts protected — ${data.portal_reason}`;
  } else {
    aggregateEl.className = "sub";
    const n = withData.length;
    aggregateEl.textContent =
      n === 0
        ? "No hospitals reported matching data for this query."
        : `${n} hospital${n === 1 ? "" : "s"} reported matching data` +
          (data.aggregate_count != null ? ` · aggregate ${data.aggregate_count}` : "") +
          (denied.length ? ` · ${denied.length} denied access` : "");
  }

  const ordered = [...withData, ...denied];
  for (const node of ordered) {
    const gw = byProvider[node.node] || node.gateway || {};
    const status = gw.status || node.status || "denied";
    const tClass = trustClass(status);
    const nodeKey = node.node || gw.provider;
    lastByNode[nodeKey] = { node, gw, status };

    const card = document.createElement("button");
    card.type = "button";
    card.className = `card card-button ${tClass}`;
    card.dataset.node = nodeKey;
    card.setAttribute("aria-pressed", "false");
    const countLabel =
      tClass === "denied"
        ? "No access"
        : gw.count_band ||
          (gw.match_count != null ? `${gw.match_count} matches` : "Matches protected");
    card.innerHTML = `
      <div class="card-head">
        <div>
          <h3>${escapeHtml(nodeKey)}</h3>
          <p class="card-sub">${escapeHtml(hospitalLabel(nodeKey))}</p>
        </div>
        <div class="status-row">
          ${trustIcon(status)}
          ${badge(status)}
        </div>
      </div>
      <p class="card-count mono">${escapeHtml(countLabel)}</p>
      <p class="card-cta meta">View details →</p>
    `;
    card.addEventListener("click", () => openHospitalDetail(nodeKey));
    nodeResultsEl.appendChild(card);
  }
}

async function doSearch() {
  const q = document.getElementById("nl-query").value.trim();
  const body = {
    researcher: researcher(),
    filters: readFilters(),
    q: q || undefined,
  };
  if (!body.filters.modality && !body.filters.body_parts.length && !body.filters.concepts.length && !q) {
    aggregateEl.className = "error";
    aggregateEl.textContent = "Enter a query or decode filters first.";
    return;
  }
  const res = await fetch("/search", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    showLogin("Platform session required");
    return;
  }
  const data = await res.json();
  if (!res.ok) {
    aggregateEl.className = "error";
    aggregateEl.textContent = data.detail || "Search failed";
    return;
  }
  renderResults(data);
}

document.getElementById("login-btn").addEventListener("click", doPlatformLogin);
document.getElementById("logout-btn").addEventListener("click", () => showLogin());
document.getElementById("suggest-btn").addEventListener("click", suggestFromNL);
document.getElementById("nl-query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") suggestFromNL();
});
document.getElementById("search-btn").addEventListener("click", doSearch);
document.getElementById("detail-close").addEventListener("click", hideDetail);

loginEmail.addEventListener("keydown", (e) => {
  if (e.key === "Enter") doPlatformLogin();
});
loginOrg.addEventListener("keydown", (e) => {
  if (e.key === "Enter") doPlatformLogin();
});

loadProfiles().then(restoreSession);
