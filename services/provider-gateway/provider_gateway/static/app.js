(() => {
  const state = {
    apiKey: localStorage.getItem("gateway_api_key") || "demo-key",
    meta: null,
    orgs: [],
    selectedOrgId: null,
    creating: false,
    requestFilter: "PENDING_REVIEW",
  };

  const el = {
    apiKey: document.getElementById("api-key"),
    connect: document.getElementById("btn-connect"),
    sessionStatus: document.getElementById("session-status"),
    providerName: document.getElementById("provider-name"),
    pendingBadge: document.getElementById("pending-badge"),
    requestsList: document.getElementById("requests-list"),
    orgList: document.getElementById("org-list"),
    orgForm: document.getElementById("org-form"),
    orgId: document.getElementById("org-id"),
    orgName: document.getElementById("org-name"),
    orgStatus: document.getElementById("org-status"),
    orgPolicyVersion: document.getElementById("org-policy-version"),
    metaAuto: document.getElementById("meta-auto"),
    dataAuto: document.getElementById("data-auto"),
    metaFields: document.getElementById("meta-fields"),
    dataFields: document.getElementById("data-fields"),
    orgFormStatus: document.getElementById("org-form-status"),
    btnNewOrg: document.getElementById("btn-new-org"),
    btnRevokeOrg: document.getElementById("btn-revoke-org"),
    btnRefreshRequests: document.getElementById("btn-refresh-requests"),
    toast: document.getElementById("toast"),
  };

  el.apiKey.value = state.apiKey;

  function toast(message) {
    el.toast.textContent = message;
    el.toast.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.toast.classList.remove("show"), 2800);
  }

  async function api(path, options = {}) {
    const headers = {
      "X-API-Key": state.apiKey,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    };
    const res = await fetch(path, { ...options, headers });
    let body = null;
    const text = await res.text();
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    if (!res.ok) {
      const detail =
        body && typeof body === "object" && body.detail
          ? typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail)
          : res.statusText;
      throw new Error(detail || `Request failed (${res.status})`);
    }
    return body;
  }

  function setSession(ok, message) {
    el.sessionStatus.textContent = message;
    el.sessionStatus.classList.toggle("ok", ok);
    el.sessionStatus.classList.toggle("err", !ok);
  }

  function renderFieldChips(container, fields, selected) {
    const selectedSet = new Set(selected || []);
    container.innerHTML = fields
      .map(
        (field) => `
      <label class="chip">
        <input type="checkbox" value="${field}" ${selectedSet.has(field) ? "checked" : ""} />
        ${field}
      </label>`
      )
      .join("");
  }

  function selectedChips(container) {
    return [...container.querySelectorAll("input:checked")].map((n) => n.value);
  }

  function renderOrgList() {
    if (!state.orgs.length) {
      el.orgList.innerHTML = `<li class="empty-state" style="padding:1rem;border:0;box-shadow:none">No organisations yet</li>`;
      return;
    }
    el.orgList.innerHTML = state.orgs
      .map(
        (org) => `
      <li>
        <button type="button" data-org="${org.organisation_id}" class="${
          state.selectedOrgId === org.organisation_id ? "is-active" : ""
        }">
          ${escapeHtml(org.display_name)}
          <span class="org-id">${escapeHtml(org.organisation_id)} · ${org.status}</span>
        </button>
      </li>`
      )
      .join("");
  }

  function fillOrgForm(org) {
    state.creating = !org;
    el.orgId.readOnly = Boolean(org);
    el.orgId.value = org?.organisation_id || "";
    el.orgName.value = org?.display_name || "";
    el.orgStatus.value = org?.status || "ACTIVE";
    el.orgPolicyVersion.value = org?.policy_version || "v1";
    el.metaAuto.checked = Boolean(org?.metadata_auto_approval);
    el.dataAuto.checked = Boolean(org?.data_auto_approval);
    renderFieldChips(
      el.metaFields,
      state.meta?.allowed_metadata_fields || [],
      org?.allowed_metadata_fields
    );
    renderFieldChips(
      el.dataFields,
      state.meta?.allowed_data_fields || [],
      org?.allowed_data_fields
    );
    el.orgFormStatus.textContent = org
      ? `Editing ${org.display_name}`
      : "Creating a new organisation allowlist";
    el.btnRevokeOrg.disabled = !org || org.status === "REVOKED";
  }

  function statusChip(status) {
    return `<span class="status-chip status-${status}">${status.replaceAll("_", " ")}</span>`;
  }

  function fieldTags(meta, data) {
    const tags = [
      ...(meta || []).map((f) => `<span class="tag meta">${escapeHtml(f)}</span>`),
      ...(data || []).map((f) => `<span class="tag data">${escapeHtml(f)}</span>`),
    ];
    return tags.join("") || `<span class="tag">No fields requested</span>`;
  }

  function renderRequests(requests) {
    if (!requests.length) {
      el.requestsList.innerHTML = `
        <div class="empty-state">
          <p>Nothing in this queue right now.</p>
          <p>When partners need manual review, their requests appear here.</p>
        </div>`;
      return;
    }

    el.requestsList.innerHTML = requests
      .map((req) => {
        const pending = req.status === "PENDING_REVIEW";
        return `
        <article class="request-card" data-id="${escapeHtml(req.provider_request_id)}">
          <div class="request-head">
            <div>
              <h3>${escapeHtml(req.project_title)}</h3>
              <p class="meta-line">
                ${escapeHtml(req.organisation_id)} · researcher ${escapeHtml(req.researcher_id)}
                · ${escapeHtml(req.provider_request_id)}
              </p>
            </div>
            ${statusChip(req.status)}
          </div>
          <p class="purpose">${escapeHtml(req.purpose)}</p>
          <div class="field-tags">
            ${fieldTags(req.requested_metadata_fields, req.requested_data_fields)}
          </div>
          ${
            req.decision_reason
              ? `<p class="meta-line">Last decision: ${escapeHtml(req.decision_reason)}</p>`
              : ""
          }
          ${
            pending
              ? `<div class="decision-box">
                  <textarea rows="2" placeholder="Optional note for approve or deny…" data-reason></textarea>
                  <div class="decision-actions">
                    <button type="button" class="btn btn-approve" data-action="approve">Approve</button>
                    <button type="button" class="btn btn-deny" data-action="deny">Deny</button>
                  </div>
                </div>`
              : ""
          }
        </article>`;
      })
      .join("");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function loadMeta() {
    state.meta = await api("/admin/meta");
    el.providerName.textContent = state.meta.provider_name;
    const count = state.meta.pending_review_count || 0;
    el.pendingBadge.textContent = String(count);
    el.pendingBadge.classList.toggle("hidden", count === 0);
    document.title = `${state.meta.provider_name} · Access Portal`;
  }

  async function loadOrgs() {
    state.orgs = await api("/admin/organisations");
    renderOrgList();
    if (state.creating) {
      fillOrgForm(null);
      return;
    }
    const selected =
      state.orgs.find((o) => o.organisation_id === state.selectedOrgId) ||
      state.orgs[0] ||
      null;
    state.selectedOrgId = selected?.organisation_id || null;
    fillOrgForm(selected);
  }

  async function loadRequests() {
    const qs = state.requestFilter
      ? `?status=${encodeURIComponent(state.requestFilter)}`
      : "";
    const requests = await api(`/admin/access-requests${qs}`);
    renderRequests(requests);
  }

  async function connect() {
    state.apiKey = el.apiKey.value.trim();
    localStorage.setItem("gateway_api_key", state.apiKey);
    try {
      await loadMeta();
      await Promise.all([loadOrgs(), loadRequests()]);
      setSession(true, `Connected to ${state.meta.provider} gateway`);
      toast("Portal connected");
    } catch (err) {
      setSession(false, err.message);
      toast(err.message);
    }
  }

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => {
        t.classList.toggle("is-active", t === tab);
        t.setAttribute("aria-selected", t === tab ? "true" : "false");
      });
      document.querySelectorAll(".panel").forEach((panel) => {
        const active = panel.id === `panel-${tab.dataset.tab}`;
        panel.classList.toggle("is-active", active);
        panel.hidden = !active;
      });
    });
  });

  document.getElementById("status-filters").addEventListener("click", async (event) => {
    const pill = event.target.closest(".pill");
    if (!pill) return;
    document.querySelectorAll("#status-filters .pill").forEach((p) => {
      p.classList.toggle("is-active", p === pill);
    });
    state.requestFilter = pill.dataset.status;
    try {
      await loadRequests();
    } catch (err) {
      toast(err.message);
    }
  });

  el.orgList.addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-org]");
    if (!btn) return;
    state.creating = false;
    state.selectedOrgId = btn.dataset.org;
    const org = state.orgs.find((o) => o.organisation_id === state.selectedOrgId);
    renderOrgList();
    fillOrgForm(org);
  });

  el.btnNewOrg.addEventListener("click", () => {
    state.creating = true;
    state.selectedOrgId = null;
    renderOrgList();
    fillOrgForm(null);
    el.orgId.focus();
  });

  el.orgForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      organisation_id: el.orgId.value.trim(),
      display_name: el.orgName.value.trim(),
      status: el.orgStatus.value,
      policy_version: el.orgPolicyVersion.value.trim() || "v1",
      metadata_auto_approval: el.metaAuto.checked,
      data_auto_approval: el.dataAuto.checked,
      allowed_metadata_fields: selectedChips(el.metaFields),
      allowed_data_fields: selectedChips(el.dataFields),
    };
    try {
      if (state.creating) {
        const created = await api("/admin/organisations", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        state.selectedOrgId = created.organisation_id;
        state.creating = false;
        toast("Allowlist created");
      } else {
        const { organisation_id, ...update } = payload;
        await api(`/admin/organisations/${encodeURIComponent(organisation_id)}`, {
          method: "PUT",
          body: JSON.stringify(update),
        });
        toast("Allowlist updated");
      }
      await loadMeta();
      await loadOrgs();
    } catch (err) {
      el.orgFormStatus.textContent = err.message;
      toast(err.message);
    }
  });

  el.btnRevokeOrg.addEventListener("click", async () => {
    if (!state.selectedOrgId) return;
    if (!confirm(`Revoke allowlist for ${state.selectedOrgId}?`)) return;
    try {
      await api(`/admin/organisations/${encodeURIComponent(state.selectedOrgId)}`, {
        method: "DELETE",
      });
      toast("Organisation revoked");
      await loadOrgs();
    } catch (err) {
      toast(err.message);
    }
  });

  el.requestsList.addEventListener("click", async (event) => {
    const actionBtn = event.target.closest("[data-action]");
    if (!actionBtn) return;
    const card = actionBtn.closest(".request-card");
    const id = card?.dataset.id;
    if (!id) return;
    const reason = card.querySelector("[data-reason]")?.value?.trim() || null;
    const action = actionBtn.dataset.action;
    actionBtn.disabled = true;
    try {
      await api(`/admin/access-requests/${encodeURIComponent(id)}/${action}`, {
        method: "POST",
        body: JSON.stringify({ reason, actor: "hospital_admin" }),
      });
      toast(action === "approve" ? "Request approved" : "Request denied");
      await loadMeta();
      await loadRequests();
    } catch (err) {
      toast(err.message);
      actionBtn.disabled = false;
    }
  });

  el.connect.addEventListener("click", connect);
  el.btnRefreshRequests.addEventListener("click", async () => {
    try {
      await loadMeta();
      await loadRequests();
      toast("Queue refreshed");
    } catch (err) {
      toast(err.message);
    }
  });

  el.apiKey.addEventListener("keydown", (event) => {
    if (event.key === "Enter") connect();
  });

  connect();
})();
