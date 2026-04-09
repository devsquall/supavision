/* Supavision — Product interactions */

// ── Global functions (accessible from onclick) ────────
function toggleTheme() {
  var html = document.documentElement;
  var next = (html.dataset.theme || "light") === "light" ? "dark" : "light";
  html.dataset.theme = next;
  localStorage.setItem("supavision-theme", next);
}

function toggleSidebar() {
  document.body.classList.toggle("sidebar-collapsed");
  localStorage.setItem("supavision-sidebar",
    document.body.classList.contains("sidebar-collapsed") ? "collapsed" : "expanded");
}

// ── xterm.js Terminal Functions ──────────────────────

var _activeTermSSE = null;

function initLiveTerminal(resourceId, runId) {
  var container = document.getElementById("terminal-container");
  if (!container || typeof Terminal === "undefined") return;

  var term = new Terminal({
    cols: 120, rows: 20,
    cursorBlink: false,
    scrollback: 5000,
    convertEol: true,
    theme: { background: '#0e1117', foreground: '#e6edf3', cursor: '#e6edf3' }
  });

  if (typeof FitAddon !== "undefined") {
    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);
    fitAddon.fit();
    window.addEventListener("resize", function() { fitAddon.fit(); });
  } else {
    term.open(container);
  }

  term.write("\x1b[2m Connecting...\x1b[0m\r\n");

  // Close any existing SSE
  if (_activeTermSSE) { _activeTermSSE.close(); _activeTermSSE = null; }

  var source = new EventSource("/resources/" + resourceId + "/runs/" + runId + "/stream");
  _activeTermSSE = source;

  source.onmessage = function(e) {
    try {
      var data = JSON.parse(e.data);
      if (data.d) term.write(data.d + "\r\n");
    } catch (err) {
      term.write(e.data + "\r\n");
    }
  };

  source.addEventListener("done", function() {
    source.close();
    _activeTermSSE = null;
    term.write("\r\n\x1b[32m✓ Run completed\x1b[0m\r\n");
    setTimeout(function() { location.reload(); }, 2000);
  });

  source.onerror = function() {
    source.close();
    _activeTermSSE = null;
    term.write("\r\n\x1b[33m⚠ Connection lost — refresh to reconnect\x1b[0m\r\n");
  };
}

function initReplayTerminal(events) {
  var container = document.getElementById("terminal-container");
  if (!container || typeof Terminal === "undefined") return;

  var term = new Terminal({
    cols: 120, rows: 20,
    cursorBlink: false,
    scrollback: 5000,
    convertEol: true,
    theme: { background: '#0e1117', foreground: '#e6edf3', cursor: '#e6edf3' }
  });

  if (typeof FitAddon !== "undefined") {
    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);
    fitAddon.fit();
  } else {
    term.open(container);
  }

  // Write all output (instant replay)
  if (events && events.length > 0) {
    events.forEach(function(evt) {
      term.write(evt[1] + "\r\n");
    });
  } else {
    term.write("\x1b[2mNo output recorded.\x1b[0m\r\n");
  }
}

function initJobTerminal(itemId, jobId) {
  var container = document.getElementById("terminal-container");
  if (!container || typeof Terminal === "undefined") return;

  var term = new Terminal({
    cols: 120, rows: 20,
    cursorBlink: false,
    scrollback: 5000,
    convertEol: true,
    theme: { background: '#0e1117', foreground: '#e6edf3', cursor: '#e6edf3' }
  });

  if (typeof FitAddon !== "undefined") {
    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);
    fitAddon.fit();
    window.addEventListener("resize", function() { fitAddon.fit(); });
  } else {
    term.open(container);
  }

  term.write("\x1b[2m Agent starting...\x1b[0m\r\n");

  var source = new EventSource("/findings/" + itemId + "/jobs/" + jobId + "/stream");

  source.onmessage = function(e) {
    try {
      var data = JSON.parse(e.data);
      if (data.d) term.write(data.d + "\r\n");
    } catch (err) {
      term.write(e.data + "\r\n");
    }
  };

  source.addEventListener("done", function() {
    source.close();
    term.write("\r\n\x1b[32m✓ Job completed\x1b[0m\r\n");
    setTimeout(function() { location.reload(); }, 2000);
  });

  source.onerror = function() {
    source.close();
    term.write("\r\n\x1b[33m⚠ Connection lost — refresh to reconnect\x1b[0m\r\n");
  };
}

// ── Confirmation Modal (replaces browser confirm()) ──
function showConfirmModal(message, onConfirm) {
  // Remove existing modal
  var existing = document.getElementById("confirm-modal");
  if (existing) existing.remove();

  var backdrop = document.createElement("div");
  backdrop.id = "confirm-modal";
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML =
    '<div class="modal">' +
    '  <div class="modal-body">' +
    '    <p style="margin-bottom:var(--sp-4);">' + message + '</p>' +
    '    <div style="display:flex;gap:var(--sp-2);justify-content:flex-end;">' +
    '      <button class="btn btn-outline" id="confirm-cancel">Cancel</button>' +
    '      <button class="btn btn-danger" id="confirm-ok">Confirm</button>' +
    '    </div>' +
    '  </div>' +
    '</div>';
  document.body.appendChild(backdrop);

  document.getElementById("confirm-cancel").onclick = function() {
    backdrop.remove();
  };
  document.getElementById("confirm-ok").onclick = function() {
    backdrop.remove();
    if (onConfirm) onConfirm();
  };
  backdrop.addEventListener("click", function(e) {
    if (e.target === backdrop) backdrop.remove();
  });
}

// ── Main IIFE ─────────────────────────────────────────
(function () {
  "use strict";

  // ── Custom confirm for dangerous actions ─────────────
  // Elements with data-confirm="message" will show our modal instead of browser confirm()
  document.addEventListener("click", function(e) {
    var btn = e.target.closest("[data-confirm]");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    showConfirmModal(btn.dataset.confirm, function() {
      // Remove the data-confirm temporarily so the click goes through
      var msg = btn.dataset.confirm;
      delete btn.dataset.confirm;
      btn.click();
      btn.dataset.confirm = msg;
    });
  }, true); // capture phase to intercept before HTMX

  // ── CSRF token injection for HTMX ──────────────────
  document.addEventListener("htmx:configRequest", function (e) {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) {
      e.detail.headers["X-CSRF-Token"] = meta.content;
    }
  });

  // ── Toast ──────────────────────────────────────────
  function showToast(message, type) {
    var c = document.getElementById("toast-container");
    if (!c) return;
    var t = document.createElement("div");
    t.className = "toast toast--" + (type || "success");
    t.textContent = message;
    c.appendChild(t);
    setTimeout(function () {
      t.classList.add("hiding");
      t.addEventListener("animationend", function () { t.remove(); });
    }, 3000);
  }
  window.showToast = showToast;

  // ── Button loading (spinner, not "Running...") ─────
  document.addEventListener("htmx:beforeRequest", function (e) {
    var el = e.detail.elt;
    if (el.tagName === "BUTTON") {
      el.classList.add("btn--loading");
      el.disabled = true;
    }
  });

  document.addEventListener("htmx:afterRequest", function (e) {
    var el = e.detail.elt;
    if (el.tagName !== "BUTTON") return;

    el.classList.remove("btn--loading");
    var ok = e.detail.xhr && e.detail.xhr.status < 400;

    // Show toast if data attributes present
    if (el.dataset.successMsg || el.dataset.errorMsg) {
      showToast(
        ok ? (el.dataset.successMsg || "Done") : (el.dataset.errorMsg || "Action failed"),
        ok ? "success" : "error"
      );
    }

    // For infra run triggers, start polling
    if (ok && (el.dataset.action === "check" || el.dataset.action === "discover")) {
      pollRunStatus(el);
    } else {
      // Re-enable after short delay (let HTMX swap finish)
      setTimeout(function () { el.disabled = false; }, 500);
    }
  });

  // ── Live output streaming (SSE) ─────────────────────
  var activeSSE = null;

  function connectSSE(resourceId, runId) {
    var output = document.getElementById("live-output");
    var content = document.getElementById("live-output-content");
    if (!output || !content) return;

    output.classList.remove("hidden");
    content.textContent = "";

    if (activeSSE) { activeSSE.close(); activeSSE = null; }

    var source = new EventSource("/resources/" + resourceId + "/runs/" + runId + "/stream");
    activeSSE = source;

    // Show live indicator
    var indicator = output.querySelector(".live-indicator");
    if (indicator) {
      indicator.className = "live-indicator";
      indicator.innerHTML = '<span class="live-dot"></span> Live';
    }

    source.onmessage = function (e) {
      content.textContent += e.data + "\n";
      content.scrollTop = content.scrollHeight;
    };

    source.addEventListener("done", function () {
      source.close();
      activeSSE = null;
      if (indicator) {
        indicator.className = "live-indicator";
        indicator.innerHTML = "Completed";
      }
      // Refresh the page content via HTMX instead of full reload
      var refreshTarget = document.querySelector("[hx-trigger*='refreshOnComplete']");
      if (refreshTarget) {
        htmx.trigger(refreshTarget, "refreshOnComplete");
      } else {
        // Fallback: reload after brief delay to show "Completed"
        setTimeout(function () { window.location.reload(); }, 1500);
      }
    });

    source.onerror = function () {
      source.close();
      activeSSE = null;
      if (indicator) {
        indicator.className = "live-indicator live-indicator--disconnected";
        indicator.innerHTML = '<span class="live-dot"></span> Disconnected';
      }
    };
  }

  // ── Run status polling ─────────────────────────────
  function pollRunStatus(btn) {
    // Guard: if xterm SSE is already active, don't interfere
    if (_activeTermSSE) {
      btn.disabled = false;
      btn.classList.remove("btn--loading");
      return;
    }
    var resourceEl = btn.closest("[data-resource-id]");
    if (!resourceEl) {
      btn.disabled = false;
      btn.classList.remove("btn--loading");
      return;
    }
    var rid = resourceEl.dataset.resourceId;
    var sseConnected = false;
    var attempts = 0;

    // Show immediate feedback in live output area
    var output = document.getElementById("live-output");
    var content = document.getElementById("live-output-content");
    if (output && content) {
      output.classList.remove("hidden");
      content.textContent = "Connecting to resource...\n";
      var indicator = output.querySelector(".live-indicator");
      if (indicator) {
        indicator.className = "live-indicator";
        indicator.innerHTML = '<span class="live-dot"></span> Starting';
      }
    }

    function check() {
      attempts++;

      // After 3 min, release button but keep polling in background
      if (attempts === 90) {
        btn.disabled = false;
        btn.classList.remove("btn--loading");
        showToast("Still running — output will appear when ready.", "success");
      }

      // Give up after 30 min (900 attempts x 2s)
      if (attempts > 900) {
        if (content) content.textContent += "\nTimed out waiting for response.\n";
        return;
      }

      fetch("/dashboard/resources-status/" + rid)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.running) {
            if (!sseConnected && data.run_id) {
              connectSSE(rid, data.run_id);
              sseConnected = true;
            }
            // Update live output with waiting message if no SSE yet
            if (!sseConnected && content && attempts % 5 === 0) {
              content.textContent = "Waiting for output... (" + (attempts * 2) + "s)\n";
            }
            setTimeout(check, 2000);
          } else {
            btn.disabled = false;
            btn.classList.remove("btn--loading");
            if (data.status === "failed") {
              showToast(data.error || "Run failed.", "error");
              if (content) content.textContent += "\nFailed: " + (data.error || "Unknown error") + "\n";
              if (output) {
                var ind = output.querySelector(".live-indicator");
                if (ind) ind.innerHTML = "Failed";
              }
            } else {
              showToast(
                data.severity ? "Completed: " + data.severity : "Completed",
                "success"
              );
              // Reload page to show updated results
              setTimeout(function () { window.location.reload(); }, 1500);
            }
          }
        })
        .catch(function () { setTimeout(check, 2000); });
    }

    setTimeout(check, 2000);
  }

  // ── Tab switching ──────────────────────────────────
  document.addEventListener("click", function (e) {
    var tab = e.target.closest(".tab");
    if (!tab) return;
    var tabGroup = tab.closest(".tabs");
    if (!tabGroup) return;

    // Deactivate all tabs
    tabGroup.querySelectorAll(".tab").forEach(function (t) {
      t.classList.remove("tab--active");
    });
    tab.classList.add("tab--active");

    // Show matching panel
    var panelId = tab.dataset.panel;
    if (panelId) {
      var container = tabGroup.parentElement;
      container.querySelectorAll(".tab-panel").forEach(function (p) {
        p.classList.toggle("tab-panel--active", p.id === panelId);
      });
    }
  });

  // ── Relative timestamps ────────────────────────────
  function timeAgo(dateStr) {
    if (!dateStr || dateStr === "-") return dateStr;
    var date = new Date(dateStr);
    if (isNaN(date.getTime())) return dateStr;
    var seconds = Math.floor((new Date() - date) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
    if (seconds < 604800) return Math.floor(seconds / 86400) + "d ago";
    return dateStr;
  }

  function updateTimestamps() {
    document.querySelectorAll("[data-timestamp]").forEach(function (el) {
      el.textContent = timeAgo(el.dataset.timestamp);
      el.title = el.dataset.timestamp;
    });
  }

  document.addEventListener("DOMContentLoaded", updateTimestamps);
  document.addEventListener("htmx:afterSwap", updateTimestamps);
  setInterval(updateTimestamps, 60000);

  // ── Elapsed timer (for live output headers) ──────────
  setInterval(function() {
    document.querySelectorAll("[data-elapsed-since]").forEach(function(el) {
      var started = new Date(el.dataset.elapsedSince);
      var secs = Math.floor((Date.now() - started.getTime()) / 1000);
      if (isNaN(secs) || secs < 0) return;
      el.textContent = secs < 60 ? secs + "s" : Math.floor(secs / 60) + "m " + (secs % 60) + "s";
    });
  }, 1000);

  // ── Close dropdowns on outside click ────────────────
  document.addEventListener("click", function (e) {
    document.querySelectorAll(".topbar-user-menu.open").forEach(function (menu) {
      if (!menu.contains(e.target)) menu.classList.remove("open");
    });
    document.querySelectorAll(".btn-dropdown.open").forEach(function (dd) {
      if (!dd.contains(e.target)) dd.classList.remove("open");
    });
  });

  // Close dropdown when an item is clicked
  document.addEventListener("click", function (e) {
    var item = e.target.closest(".btn-dropdown-item");
    if (item) {
      var dd = item.closest(".btn-dropdown");
      if (dd) dd.classList.remove("open");
    }
  });
})();
