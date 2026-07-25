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

let profiles = {};
let activeKey = "harvard_irb";

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
  const labels = {
    harvard_irb: "Harvard + IRB",
    mit_partner: "MIT (no IRB)",
    neu: "Northeastern",
    bu: "BU",
    guest: "Guest",
  };
  for (const key of Object.keys(profiles)) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.key = key;
    btn.textContent = labels[key] || key;
    btn.addEventListener("click", () => applyProfile(key));
    profileButtons.appendChild(btn);
  }
  applyProfile(activeKey);
}

function badge(status) {
  const cls = status === "ok" ? "ok" : status === "suppressed" ? "suppressed" : "denied";
  return `<span class="badge ${cls}">${status}</span>`;
}

function renderResults(data) {
  resultsPanel.hidden = false;
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
    const studies = (node.studies || [])
      .slice(0, 5)
      .map(
        (s) =>
          `<div><button class="linkish" data-node="${node.node}" data-id="${s.StudyID}" type="button">${s.StudyID}</button> · ${s.BodyPartExamined} · ${s.Modality}</div>`
      )
      .join("");
    card.innerHTML = `
      <h3>${node.node}</h3>
      <div>${badge(node.status)} <span class="badge ok">${node.tier || ""}</span></div>
      <p class="meta">SSO: ${node.sso || "—"} · count: ${node.count ?? "—"}
      ${node.reason ? `<br/>${node.reason}` : ""}
      ${node.scope ? `<br/>scopes: ${node.scope.join(", ")}` : ""}</p>
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ q: queryEl.value, researcher: researcher() }),
  });
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  retrieveOut.textContent = JSON.stringify(data, null, 2);
}

async function doAudit() {
  const node = document.getElementById("audit-node").value;
  const res = await fetch(`/audit/${node}`);
  const data = await res.json();
  auditOut.textContent = JSON.stringify(data, null, 2);
}

document.getElementById("search-btn").addEventListener("click", doSearch);
document.getElementById("retrieve-btn").addEventListener("click", doRetrieve);
document.getElementById("audit-btn").addEventListener("click", doAudit);

loadProfiles();
