// MILO page script (SPEC section 16, D47, D138, D139): vanilla, no inline script, text only (textContent and createElement).
// Polls turn events every 500 ms and builds the reply details drawer, the spec panel and the estimate from them.
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : "";
  var POLL_MS = 500;
  var READY = { assessment_ready: true, quote_ready: true };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function toast(text) {
    var box = document.getElementById("toasts");
    if (!box) { return; }
    var t = el("div", "toast", text);
    box.appendChild(t);
    window.setTimeout(function () { t.remove(); }, 5000);
  }

  function api(url, options) {
    var opts = options || {};
    var headers = { "X-CSRF-Token": csrf, "X-Requested-With": "fetch" };
    var body = opts.body;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    return fetch(url, {
      method: opts.method || "GET",
      headers: headers,
      body: body,
      credentials: "same-origin"
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { status: r.status, data: data };
      });
    });
  }

  function shown(value) {
    if (value === null || value === undefined) { return ""; }
    if (value === true) { return "yes"; }
    if (value === false) { return "no"; }
    if (Array.isArray(value)) { return value.map(shown).join(", "); }
    return String(value);
  }

  function main() { return document.querySelector("main[data-events-url]"); }

  function sessionId() { var m = main(); return m ? m.dataset.session : ""; }

  function setSession(id) {
    var m = main();
    if (!m || !id || m.dataset.session === id) { return; }
    m.dataset.session = id;
    document.querySelectorAll('input[name="session_id"]').forEach(function (i) { i.value = id; });
    if (window.history && window.history.replaceState) { window.history.replaceState(null, "", "/sessions/" + encodeURIComponent(id)); }
  }

  function thread() { return document.getElementById("thread"); }

  function addBubble(role, text) {
    var t = thread();
    var empty = t.querySelector(".empty-state");
    if (empty) { empty.remove(); }
    var row = el("article", "msg-row msg-" + role);
    var bubble = el("div", "bubble plain-text", text); // plain text only (D139)
    row.appendChild(bubble);
    t.appendChild(row);
    row.scrollIntoView({ block: "end" });
    return row;
  }

  function addDrawer(row) {
    var d = el("details", "drawer");
    d.appendChild(el("summary", null, "Reply details"));
    var parts = {};
    [["progress", "Progress"], ["sources", "Sources"], ["tools", "Tool calls"]].forEach(function (p) {
      d.appendChild(el("h3", null, p[1]));
      var list = el("ul", p[0] === "progress" ? "progress-list" : null);
      d.appendChild(list);
      parts[p[0]] = list;
    });
    parts.cost = el("p", "small muted", "Model cost: pending");
    d.appendChild(parts.cost);
    parts.estimate = el("p", "small muted", "The indicative estimate is shown separately in the panel.");
    d.appendChild(parts.estimate);
    row.appendChild(d);
    return parts;
  }

  function li(list, text) { list.appendChild(el("li", null, text)); }

  // ---- spec panel ----

  function updateField(p) {
    var item = document.querySelector('#field-list [data-field="' + CSS.escape(p.field_id || "") + '"]');
    if (!item) { return; }
    item.classList.remove("field-missing");
    var chip = item.querySelector("[data-status]");
    if (chip) {
      chip.className = "chip chip-" + p.status;
      chip.querySelector(".chip-text").textContent = p.status;
    }
    item.querySelector(".field-value").textContent = p.status === "conflicting" ? "" : shown(p.value);
    var quote = item.querySelector(".quote");
    if (quote && p.evidence_text) { quote.textContent = p.evidence_text; quote.hidden = false; }
    var saved = document.getElementById("save-indicator");
    if (saved) { saved.hidden = false; }
  }

  function updateProgress(view) {
    var count = 0;
    document.querySelectorAll("#field-list [data-status]").forEach(function (chip) {
      var s = chip.querySelector(".chip-text").textContent;
      if (s === "reported" || s === "confirmed" || s === "estimated") { count += 1; }
    });
    var bar = document.getElementById("progress-bar");
    var label = document.getElementById("progress-count");
    if (bar && label && view) {
      var covered = Math.max(0, Number(bar.max) - (view.missing || []).length);
      bar.value = covered;
      label.textContent = String(covered);
    } else if (label) {
      label.textContent = String(count);
    }
    if (view) {
      var state = document.getElementById("protocol-state");
      if (state) { state.textContent = String(view.protocol_state || "").replace(/_/g, " "); }
      var btn = document.getElementById("submit-open");
      if (btn) { btn.disabled = !READY[view.protocol_state]; }
      var hint = document.getElementById("submit-hint");
      if (hint) { hint.hidden = !!READY[view.protocol_state]; }
      var missing = document.getElementById("missing-list");
      if (missing && (view.missing || []).length === 0) { missing.hidden = true; if (missing.previousElementSibling) { missing.previousElementSibling.hidden = true; } }
    }
  }

  function money(n) { return Math.round(Number(n)).toString(); }

  function updateEstimate(p) {
    var card = document.getElementById("estimate-card");
    if (!card) { return; }
    card.hidden = false;
    document.getElementById("estimate-status").textContent = p.status || "";
    var cur = p.currency || "USD";
    document.getElementById("estimate-monthly").textContent = p.monthly
      ? "Monthly " + money(p.monthly.low) + " to " + money(p.monthly.high) + " " + cur + " (base " + money(p.monthly.base) + ")"
      : "No figure yet: " + (p.status || "none") + ".";
    document.getElementById("estimate-setup").textContent = p.setup ? "Setup " + money(p.setup.base) + " " + cur + " one time" : "";
  }

  // ---- turns ----

  var pendingApproval = null;

  function showApproval(p) {
    pendingApproval = p.approval_id;
    var banner = document.getElementById("approval-banner");
    document.getElementById("approval-text").textContent =
      "MILO wants to run " + (p.tool || "an action") + ". This needs your approval. Nothing has changed yet.";
    banner.hidden = false;
    var first = banner.querySelector("button");
    if (first) { first.focus(); }
  }

  function poll(turnId, after, row, parts) {
    var base = main().dataset.eventsUrl;
    api(base + encodeURIComponent(turnId) + "/events?after=" + after).then(function (res) {
      if (res.status !== 200 || !Array.isArray(res.data)) {
        toast("Could not read the reply.");
        return;
      }
      var finished = false;
      var next = after;
      var bubble = row.querySelector(".bubble");
      res.data.forEach(function (ev) {
        var p = ev.payload || {};
        next = Math.max(next, ev.seq || 0);
        switch (ev.type) {
          case "node_started": li(parts.progress, (p.node || "step") + " started"); break;
          case "node_finished": li(parts.progress, (p.node || "step") + " finished in " + (p.duration_ms || 0) + " ms"); break;
          case "source": li(parts.sources, (p.chunk_id || "") + " · " + (p.doc_id || "") + " · score " + p.score); break;
          case "policy":
            if (p.stage === "tool") { li(parts.tools, (p.tool || "") + " · policy " + (p.decision || "")); }
            else { li(parts.progress, "input taint " + (p.taint || "")); }
            break;
          case "tool_call": li(parts.tools, (p.name || "") + " · arguments " + JSON.stringify(p.args || {}) + " · result " + (p.result || "") + (p.ok ? "" : " · failed")); break;
          case "fact_recorded": updateField(p); break;
          case "fact_rejected": li(parts.progress, "fact not recorded: " + (p.field_id || "") + " (" + (p.reason || "") + ")"); break;
          case "document_truncated": toast("The document was long; only the first part was read."); break;
          case "estimate_updated": updateEstimate(p); break;
          case "approval_required": showApproval(p); li(parts.tools, (p.tool || "") + " · needs approval"); break;
          case "answer": bubble.textContent = p.text || ""; break;
          case "error": if (!bubble.textContent || bubble.textContent === "...") { bubble.textContent = p.message || "The assistant could not answer."; } toast("The assistant could not answer. Please try again."); break;
          case "done":
            finished = true;
            parts.cost.textContent = "Model cost: " + Number(p.cost_usd || 0).toFixed(4) + " USD (" + (p.model_calls || 0) + " model calls)";
            updateProgress(p.view);
            break;
          default: break;
        }
      });
      if (!finished) { window.setTimeout(function () { poll(turnId, next, row, parts); }, POLL_MS); }
    });
  }

  function startTurn(data) {
    setSession(data.session_id);
    var row = addBubble("assistant", "...");
    poll(data.turn_id, 0, row, addDrawer(row));
  }

  function refused(res) {
    if (res.status === 409) { toast("A reply is still being written. Wait for it to finish."); }
    else if (res.status === 429) { toast("Please wait a moment before sending more (" + (res.data.retry_after_s || 60) + " s)."); }
    else if (res.status === 403 && res.data.error === "consent_required") { window.location.href = "/consent"; }
    else if (res.status === 400) { toast("That could not be read: " + String(res.data.error || "invalid input").split(":").pop().replace(/_/g, " ") + "."); }
    else if (res.status === 413) { toast("That file is larger than 5 MB."); }
    else { toast("Could not send that."); }
  }

  var composer = document.getElementById("composer");
  if (composer) {
    composer.addEventListener("submit", function (e) {
      e.preventDefault();
      var input = document.getElementById("composer-input");
      var text = input.value.trim();
      if (!text) { return; }
      api(composer.action, { method: "POST", json: { text: text, session_id: sessionId() } }).then(function (res) {
        if (res.status === 200 && res.data.turn_id) {
          addBubble("user", text);
          input.value = "";
          startTurn(res.data);
        } else { refused(res); }
      });
    });
    composer.querySelector("textarea").addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { composer.requestSubmit(); }
    });
  }

  document.querySelectorAll("[data-suggestion]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById("composer-input");
      if (input) { input.value = btn.querySelector("span:nth-child(2)").textContent; input.focus(); }
    });
  });

  document.querySelectorAll("[data-approval]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!pendingApproval) { return; }
      api("/approvals/" + encodeURIComponent(pendingApproval), { method: "POST", json: { decision: btn.dataset.approval } }).then(function (res) {
        if (res.status === 200) {
          document.getElementById("approval-banner").hidden = true;
          pendingApproval = null;
          if (res.data.answer) { addBubble("assistant", res.data.answer); }
        } else { toast("Could not record your decision."); }
      });
    });
  });

  var upload = document.getElementById("upload-input");
  if (upload) {
    upload.addEventListener("change", function () {
      if (!upload.files.length) { return; }
      var file = upload.files[0];
      var body = new FormData();
      body.append("file", file);
      body.append("session_id", sessionId());
      var input = document.getElementById("composer-input");
      if (input && input.value.trim()) { body.append("text", input.value.trim()); }
      api(upload.dataset.uploadUrl, { method: "POST", body: body }).then(function (res) {
        if (res.status === 200 && res.data.turn_id) {
          addBubble("user", "Document: " + file.name);
          if (input) { input.value = ""; }
          startTurn(res.data);
        } else { refused(res); }
        upload.value = "";
      });
    });
  }

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) { e.preventDefault(); }
    });
  });

  document.querySelectorAll("[data-open]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var dlg = document.querySelector(btn.dataset.open);
      if (dlg && dlg.showModal) { dlg.showModal(); }
    });
  });
  document.querySelectorAll("[data-close]").forEach(function (btn) {
    btn.addEventListener("click", function () { btn.closest("dialog").close(); });
  });

  var filter = document.querySelector("[data-filter]");
  if (filter) {
    filter.addEventListener("input", function () {
      var q = filter.value.toLowerCase();
      document.querySelectorAll(filter.dataset.filter + " .session-row").forEach(function (row) {
        row.hidden = row.dataset.title.toLowerCase().indexOf(q) === -1;
      });
    });
  }
})();
