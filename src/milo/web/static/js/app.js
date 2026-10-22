// MILO shell script (SPEC section 16, D138): vanilla, no inline script, polls turn events (D47).
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : "";

  function toast(text) {
    var box = document.getElementById("toasts");
    if (!box) { return; }
    var el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    box.appendChild(el);
    window.setTimeout(function () { el.remove(); }, 4000);
  }

  function api(url, options) {
    var opts = options || {};
    var headers = { "X-CSRF-Token": csrf, "X-Requested-With": "fetch" };
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
    }
    return fetch(url, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body,
      credentials: "same-origin"
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { status: r.status, data: data };
      });
    });
  }

  function addBubble(role, text) {
    var thread = document.getElementById("thread");
    var empty = thread.querySelector(".empty-state");
    if (empty) { empty.remove(); }
    var row = document.createElement("article");
    row.className = "msg-row msg-" + role;
    var bubble = document.createElement("div");
    bubble.className = "bubble plain-text";
    bubble.textContent = text; // plain text only (D139)
    row.appendChild(bubble);
    thread.appendChild(row);
    return bubble;
  }

  var pendingApproval = null;

  function poll(turnId, after, bubble) {
    var base = document.querySelector("main[data-events-url]").dataset.eventsUrl;
    api(base + encodeURIComponent(turnId) + "/events?after=" + after).then(function (res) {
      if (res.status !== 200 || !Array.isArray(res.data)) {
        toast("Could not read the reply.");
        return;
      }
      var done = false;
      var next = after;
      res.data.forEach(function (ev) {
        next = Math.max(next, ev.seq || 0);
        if (ev.type === "answer") { bubble.textContent = (ev.payload && ev.payload.text) || ""; }
        if (ev.type === "approval_required") {
          pendingApproval = ev.payload && ev.payload.approval_id;
          var banner = document.getElementById("approval-banner");
          document.getElementById("approval-text").textContent = (ev.payload && ev.payload.summary) || "This action needs your approval.";
          banner.hidden = false;
        }
        if (ev.type === "error") { toast("The assistant could not answer. Please try again."); done = true; }
        if (ev.type === "done") { done = true; }
      });
      if (!done) { window.setTimeout(function () { poll(turnId, next, bubble); }, 1000); }
    });
  }

  var composer = document.getElementById("composer");
  if (composer) {
    composer.addEventListener("submit", function (e) {
      e.preventDefault();
      var input = document.getElementById("composer-input");
      var text = input.value.trim();
      if (!text) { return; }
      api(composer.action, { method: "POST", json: { text: text, session_id: document.querySelector("main").dataset.session } }).then(function (res) {
        if (res.status === 200 && res.data.turn_id) {
          addBubble("user", text);
          input.value = "";
          poll(res.data.turn_id, 0, addBubble("assistant", "..."));
        } else if (res.status === 409) {
          toast("A reply is still being written. Wait for it to finish.");
        } else if (res.status === 403 && res.data.error === "consent_required") {
          window.location.href = "/consent";
        } else if (res.status === 501) {
          toast("Messages are not connected in this build.");
        } else {
          toast("Could not send that message.");
        }
      });
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
        if (res.status === 200) { document.getElementById("approval-banner").hidden = true; pendingApproval = null; }
        else { toast("Could not record your decision."); }
      });
    });
  });

  var upload = document.getElementById("upload-input");
  if (upload) {
    upload.addEventListener("change", function () {
      if (!upload.files.length) { return; }
      var body = new FormData();
      body.append("file", upload.files[0]);
      api(upload.dataset.uploadUrl, { method: "POST", body: body }).then(function (res) {
        toast(res.status === 200 ? "Document added." : "Could not upload that document.");
      });
    });
  }

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
