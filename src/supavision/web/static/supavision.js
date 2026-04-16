/* Supavision — Product interactions */

// ── Global functions (accessible from onclick) ────────
function toggleTheme() {
  var html = document.documentElement;
  var next = (html.dataset.theme || "light") === "light" ? "dark" : "light";
  html.dataset.theme = next;
  localStorage.setItem("supavision-theme", next);
}

function toggleSidebar() {
  // matchMedia aligns with the CSS breakpoint exactly (including scrollbar width)
  var isMobile = window.matchMedia("(max-width: 768px)").matches;
  if (isMobile) {
    document.body.classList.toggle("sidebar-open");
    document.body.classList.remove("sidebar-collapsed");
  } else {
    document.body.classList.toggle("sidebar-collapsed");
    document.body.classList.remove("sidebar-open");
    localStorage.setItem("supavision-sidebar",
      document.body.classList.contains("sidebar-collapsed") ? "collapsed" : "expanded");
  }
}

// ── Command Palette ──────────────────────────────
var _paletteActive = false;
var _paletteIdx = -1;
var _searchTimer = null;

var _navItems = [
  {type: "nav", name: "Dashboard", link: "/", badge: ""},
  {type: "nav", name: "Resources", link: "/resources", badge: ""},
  {type: "nav", name: "Reports", link: "/reports", badge: ""},
  {type: "nav", name: "Alerts", link: "/alerts", badge: ""},
  {type: "nav", name: "Sessions", link: "/sessions", badge: ""},
  {type: "nav", name: "Activity", link: "/activity", badge: ""},
  {type: "nav", name: "Live", link: "/activity/live", badge: ""},
  {type: "nav", name: "Metrics", link: "/metrics", badge: ""},
  {type: "nav", name: "Schedules", link: "/schedules", badge: ""},
  {type: "nav", name: "Command Center", link: "/command-center", badge: ""},
  {type: "nav", name: "Ask Supavision", link: "/ask", badge: ""},
  {type: "nav", name: "Settings", link: "/settings", badge: ""},
  {type: "nav", name: "Profile", link: "/profile", badge: ""},
];

function openPalette() {
  var overlay = document.getElementById("cmd-palette-overlay");
  if (!overlay) return;
  overlay.style.display = "flex";
  _paletteActive = true;
  _paletteIdx = -1;
  var input = document.getElementById("cmd-palette-input");
  input.value = "";
  input.focus();
  renderPaletteResults(_navItems.slice(0, 8));
}

function closePalette() {
  var overlay = document.getElementById("cmd-palette-overlay");
  if (overlay) overlay.style.display = "none";
  _paletteActive = false;
  _paletteIdx = -1;
}

function renderPaletteResults(items) {
  var container = document.getElementById("cmd-palette-results");
  if (!container) return;
  if (items.length === 0) {
    container.innerHTML = '<div class="cmd-palette-empty">No results</div>';
    return;
  }
  container.innerHTML = items.map(function(item, i) {
    var icon = item.type === "nav" ? "\u2192" : item.type === "resource" ? "\u25C6" : "\u25C7";
    var badge = item.badge ? '<span class="badge badge--type" style="margin-left:auto">' + item.badge + '</span>' : '';
    return '<a href="' + item.link + '" class="cmd-palette-item' +
           (i === _paletteIdx ? ' cmd-palette-item--active' : '') +
           '" data-idx="' + i + '">' +
           '<span class="cmd-palette-icon">' + icon + '</span>' +
           '<span>' + item.name + '</span>' +
           badge + '</a>';
  }).join("");
}

function onPaletteInput(query) {
  _paletteIdx = -1;
  if (!query || query.length < 1) {
    renderPaletteResults(_navItems.slice(0, 8));
    return;
  }
  // Filter nav items
  var q = query.toLowerCase();
  var filtered = _navItems.filter(function(item) {
    return item.name.toLowerCase().indexOf(q) !== -1;
  });
  renderPaletteResults(filtered);

  // Server search (debounced)
  if (query.length >= 2) {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(function() {
      fetch("/api/v1/search?q=" + encodeURIComponent(query))
        .then(function(r) { return r.json(); })
        .then(function(data) {
          if (!data.ok || !_paletteActive) return;
          var combined = filtered.concat(data.results);
          renderPaletteResults(combined.slice(0, 12));
        })
        .catch(function() {});
    }, 200);
  }
}

// ── xterm.js Terminal Functions ──────────────────────

var _activeTermSSE = null;
var _activeTerm = null;

function copyTerminalOutput() {
  if (!_activeTerm) return;
  var buffer = _activeTerm.buffer.active;
  var lines = [];
  for (var i = 0; i < buffer.length; i++) {
    var line = buffer.getLine(i);
    if (line) lines.push(line.translateToString(true));
  }
  var text = lines.join("\n").trimEnd();
  navigator.clipboard.writeText(text).then(function() {
    showToast("Output copied to clipboard", "success");
  }).catch(function() {
    showToast("Failed to copy", "error");
  });
}

function initLiveTerminal(resourceId, runId) {
  var container = document.getElementById("terminal-container");
  if (!container || typeof Terminal === "undefined") return;

  var term = new Terminal({
    cols: 80, rows: 20,
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
  _activeTerm = term;

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
    term.write("\r\n\x1b[32m\u2713 Run completed\x1b[0m\r\n");
    var header = document.querySelector(".session-header .badge:last-of-type, .page-header .badge");
    if (header) { header.className = "badge badge--healthy"; header.textContent = "completed"; }
    if (window.sv && sv.toast) {
      sv.toast.show({ title: "Run complete", type: "success", duration: 3000 });
    }
    // Prefer in-place HTMX refresh, fall back to subtle inline banner.
    var refreshTarget = document.querySelector("[hx-trigger*='refreshOnComplete']");
    if (refreshTarget && window.htmx) {
      htmx.trigger(refreshTarget, "refreshOnComplete");
    } else {
      var banner = document.createElement("div");
      banner.className = "terminal-complete-banner";
      banner.innerHTML = 'Run completed \u2014 <a href="javascript:window.location.reload()">Refresh to see results</a>';
      container.parentElement.appendChild(banner);
      if (window.sv && sv.fx) sv.fx.fadeSwap(banner);
    }
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
    cols: 80, rows: 20,
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
  _activeTerm = term;

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
    cols: 80, rows: 20,
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
  _activeTerm = term;

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
    term.write("\r\n\x1b[32m\u2713 Job completed\x1b[0m\r\n");
    var header = document.querySelector(".session-header .badge:last-of-type, .page-header .badge");
    if (header) { header.className = "badge badge--healthy"; header.textContent = "completed"; }
    if (window.sv && sv.toast) {
      sv.toast.show({ title: "Job complete", type: "success", duration: 3000 });
    }
    var refreshTarget = document.querySelector("[hx-trigger*='refreshOnComplete']");
    if (refreshTarget && window.htmx) {
      htmx.trigger(refreshTarget, "refreshOnComplete");
    } else {
      var banner = document.createElement("div");
      banner.className = "terminal-complete-banner";
      banner.innerHTML = 'Job completed \u2014 <a href="javascript:window.location.reload()">Refresh to see results</a>';
      container.parentElement.appendChild(banner);
      if (window.sv && sv.fx) sv.fx.fadeSwap(banner);
    }
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
        indicator.className = "live-indicator live-indicator--completed";
        indicator.innerHTML = '<span class="live-dot"></span> Completed';
        if (window.sv && sv.fx) sv.fx.pulse(indicator);
      }
      if (window.sv && sv.toast) {
        sv.toast.show({ title: "Run complete", type: "success", duration: 3000 });
      }
      // Refresh the page content via HTMX instead of full reload.
      var refreshTarget = document.querySelector("[hx-trigger*='refreshOnComplete']");
      if (refreshTarget) {
        htmx.trigger(refreshTarget, "refreshOnComplete");
      } else {
        // Legacy fallback — brief delay so user sees "Completed" before reload.
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
              // SSE takes over from here — stop polling and release the button.
              btn.disabled = false;
              btn.classList.remove("btn--loading");
              return;
            }
            // Still discovering — keep polling briefly until run_id appears.
            if (content && attempts % 5 === 0) {
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
              // Prefer an in-place HTMX refresh over full reload.
              var refreshTarget = document.querySelector("[hx-trigger*='refreshOnComplete']");
              if (refreshTarget && window.htmx) {
                htmx.trigger(refreshTarget, "refreshOnComplete");
              } else {
                setTimeout(function () { window.location.reload(); }, 1500);
              }
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

  // ── HTMX error handling ──────────────────────────
  document.addEventListener("htmx:responseError", function(e) {
    showToast("Connection error \u2014 retrying...", "error");
  });
  document.addEventListener("htmx:sendError", function(e) {
    showToast("Network error \u2014 check your connection", "error");
  });

  // ── Keyboard shortcuts ───────────────────────────
  var _gPressed = false;
  document.addEventListener("keydown", function(e) {
    // Don't trigger when typing in inputs
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable) {
      if (e.key === "Escape" && _paletteActive) { closePalette(); e.preventDefault(); }
      return;
    }

    // Cmd+K / Ctrl+K — open palette
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      _paletteActive ? closePalette() : openPalette();
      return;
    }

    // Escape — close palette or help
    if (e.key === "Escape") {
      if (_paletteActive) closePalette();
      var help = document.getElementById("keyboard-help");
      if (help && help.style.display !== "none") help.style.display = "none";
      return;
    }

    // Arrow keys in palette
    if (_paletteActive) {
      var items = document.querySelectorAll(".cmd-palette-item");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        _paletteIdx = Math.min(_paletteIdx + 1, items.length - 1);
        items.forEach(function(el, i) { el.classList.toggle("cmd-palette-item--active", i === _paletteIdx); });
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        _paletteIdx = Math.max(_paletteIdx - 1, 0);
        items.forEach(function(el, i) { el.classList.toggle("cmd-palette-item--active", i === _paletteIdx); });
      } else if (e.key === "Enter" && _paletteIdx >= 0 && items[_paletteIdx]) {
        e.preventDefault();
        window.location.href = items[_paletteIdx].href;
      }
      return;
    }

    // ? — show keyboard help
    if (e.key === "?" && !e.ctrlKey && !e.metaKey) {
      var help = document.getElementById("keyboard-help");
      if (help) help.style.display = help.style.display === "none" ? "flex" : "none";
      return;
    }

    // g + key shortcuts
    if (_gPressed) {
      _gPressed = false;
      var routes = { d: "/", r: "/resources", s: "/settings", a: "/activity", m: "/metrics" };
      if (routes[e.key]) { window.location.href = routes[e.key]; return; }
    }
    if (e.key === "g") { _gPressed = true; setTimeout(function() { _gPressed = false; }, 1000); }
  });

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

/* ════════════════════════════════════════════════════════════════════
   UX ELEVATION FOUNDATION — sv.* namespace
   WAAPI motion, form validation, toast stack, nprogress, focus trap.
   Progressive enhancement: every helper early-returns to no-op / instant
   when JS is disabled, WAAPI is missing, or prefers-reduced-motion is set.
   ════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  var sv = (window.sv = window.sv || {});

  var RM = window.matchMedia ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  var canAnim = function () {
    return !RM.matches && typeof Element !== "undefined" && "animate" in Element.prototype;
  };
  var EASE = "cubic-bezier(0.16, 1, 0.3, 1)";
  var SPRING = "cubic-bezier(0.22, 1.2, 0.36, 1)";

  // ── sv.fx — native WAAPI motion primitives ────────────────────────
  sv.fx = {
    fadeSwap: function (el) {
      if (!el || !canAnim()) return;
      try {
        el.animate(
          [{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }],
          { duration: 120, easing: EASE }
        );
      } catch (e) {}
    },
    scaleIn: function (el) {
      if (!el || !canAnim()) return;
      try {
        el.animate(
          [{ opacity: 0, transform: "scale(.96)" }, { opacity: 1, transform: "scale(1)" }],
          { duration: 140, easing: EASE }
        );
      } catch (e) {}
    },
    slideInRight: function (el) {
      if (!el || !canAnim()) return;
      try {
        el.animate(
          [{ opacity: 0, transform: "translateX(16px)" }, { opacity: 1, transform: "none" }],
          { duration: 180, easing: EASE }
        );
      } catch (e) {}
    },
    stagger: function (selector, step) {
      if (!canAnim()) return;
      step = step || 30;
      var items = typeof selector === "string"
        ? document.querySelectorAll(selector)
        : selector;
      for (var i = 0; i < items.length; i++) {
        try {
          items[i].animate(
            [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }],
            { duration: 180, delay: i * step, easing: EASE, fill: "both" }
          );
        } catch (e) {}
      }
    },
    drawCheck: function (path) {
      if (!path || !canAnim()) return;
      try {
        path.animate(
          [{ strokeDashoffset: 24 }, { strokeDashoffset: 0 }],
          { duration: 320, easing: SPRING, fill: "forwards" }
        );
      } catch (e) {}
    },
    countUp: function (el, to, opts) {
      if (!el) return;
      opts = opts || {};
      var from = typeof opts.from === "number" ? opts.from : 0;
      var dur = opts.duration || 600;
      var format = opts.format || function (n) { return Math.round(n).toLocaleString(); };
      if (!canAnim()) { el.textContent = format(to); return; }
      var start = performance.now();
      function tick(now) {
        var t = Math.min(1, (now - start) / dur);
        var eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
        el.textContent = format(from + (to - from) * eased);
        if (t < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    },
    pulse: function (el) {
      if (!el || !canAnim()) return;
      try {
        el.animate(
          [{ transform: "scale(1)" }, { transform: "scale(1.06)" }, { transform: "scale(1)" }],
          { duration: 500, easing: EASE }
        );
      } catch (e) {}
    }
  };

  // ── sv.focusTrap — minimal focus trap for modals/palettes/sheets ──
  sv.focusTrap = function (el) {
    if (!el) return function () {};
    var prev = document.activeElement;
    var focusable = function () {
      return el.querySelectorAll(
        'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
      );
    };
    function onKey(e) {
      if (e.key !== "Tab") return;
      var items = focusable();
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    }
    el.addEventListener("keydown", onKey);
    var items = focusable();
    if (items.length) items[0].focus();
    return function release() {
      el.removeEventListener("keydown", onKey);
      if (prev && prev.focus) prev.focus();
    };
  };

  // ── sv.toast — structured toast stack with undoable pattern ───────
  function toastContainer() {
    var c = document.getElementById("toast-container");
    if (c && !c.classList.contains("toast-container--stacked")) {
      c.classList.add("toast-container--stacked");
    }
    return c;
  }
  sv.toast = {
    show: function (opts) {
      var c = toastContainer();
      if (!c) return null;
      opts = typeof opts === "string" ? { body: opts } : (opts || {});
      var type = opts.type || "success";
      var duration = typeof opts.duration === "number" ? opts.duration : 3200;

      var t = document.createElement("div");
      t.className = "toast toast--" + type + (opts.action ? " toast--with-action" : "");
      var body = document.createElement("div");
      body.className = "toast__body";
      if (opts.title) {
        var h = document.createElement("strong");
        h.textContent = opts.title;
        h.style.display = "block";
        body.appendChild(h);
      }
      if (opts.body) {
        var b = document.createElement("span");
        b.textContent = opts.body;
        body.appendChild(b);
      }
      if (!opts.title && !opts.body && typeof opts === "string") {
        body.textContent = opts;
      }
      t.appendChild(body);

      var timer = null;
      var dismiss = function () {
        if (timer) { clearTimeout(timer); timer = null; }
        t.classList.add("hiding");
        var fallback = setTimeout(function () { if (t.parentNode) t.remove(); }, 400);
        t.addEventListener("animationend", function () { clearTimeout(fallback); if (t.parentNode) t.remove(); }, { once: true });
      };

      if (opts.action) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "toast__action";
        btn.textContent = opts.action.label || "Undo";
        btn.addEventListener("click", function () {
          if (typeof opts.action.onClick === "function") opts.action.onClick();
          dismiss();
        });
        t.appendChild(btn);
      }

      c.appendChild(t);
      sv.fx.scaleIn(t);
      if (duration > 0) timer = setTimeout(dismiss, duration);
      return { dismiss: dismiss, el: t };
    },
    undoable: function (message, onUndo, commitFn, opts) {
      opts = opts || {};
      var timeout = typeof opts.timeout === "number" ? opts.timeout : 5000;
      var undone = false;
      var toast = sv.toast.show({
        body: message,
        type: opts.type || "success",
        duration: timeout,
        action: {
          label: opts.actionLabel || "Undo",
          onClick: function () {
            undone = true;
            if (typeof onUndo === "function") onUndo();
          }
        }
      });
      setTimeout(function () {
        if (!undone && typeof commitFn === "function") commitFn();
      }, timeout + 50);
      return toast;
    }
  };

  // Backward-compat shim for existing showToast callers (keep existing behaviour).
  if (!window._svToastShimmed) {
    window._svToastShimmed = true;
    var legacyShowToast = window.showToast;
    window.showToast = function (message, type) {
      // Prefer the richer stack; fall back to legacy if container missing.
      if (document.getElementById("toast-container")) {
        sv.toast.show({ body: message, type: type || "success" });
      } else if (typeof legacyShowToast === "function") {
        legacyShowToast(message, type);
      }
    };
  }

  // ── sv.nprog — page-level top loading bar, scoped & debounced ─────
  sv.nprog = (function () {
    var bar, showTimer = null, active = false;
    function ensure() {
      if (bar) return bar;
      bar = document.getElementById("nprogress");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "nprogress";
        document.body.insertBefore(bar, document.body.firstChild);
      }
      return bar;
    }
    return {
      start: function () {
        ensure();
        if (showTimer) clearTimeout(showTimer);
        showTimer = setTimeout(function () {
          active = true;
          bar.classList.remove("done");
          bar.classList.add("active");
          bar.style.transform = "scaleX(0)";
          // reflow
          void bar.offsetWidth;
          bar.style.transform = "scaleX(0.7)";
        }, 120);
      },
      done: function () {
        if (showTimer) { clearTimeout(showTimer); showTimer = null; }
        if (!active) { if (bar) { bar.classList.remove("active"); bar.style.transform = "scaleX(0)"; } return; }
        active = false;
        ensure();
        bar.classList.add("done");
        setTimeout(function () {
          bar.classList.remove("active", "done");
          bar.style.transform = "scaleX(0)";
        }, 360);
      }
    };
  })();

  // ── sv.form — validation, autosave, unsaved-guard ─────────────────
  var SENSITIVE_TYPES = { password: 1, hidden: 1, file: 1 };
  var SENSITIVE_NAMES = /(password|secret|token|apikey|api_key|ssh_key|private_key|credential)/i;
  function isSensitive(input) {
    if (!input || !input.type) return false;
    if (SENSITIVE_TYPES[input.type]) return true;
    if (input.hasAttribute("data-sensitive")) return true;
    if (input.name && SENSITIVE_NAMES.test(input.name)) return true;
    if (input.id && SENSITIVE_NAMES.test(input.id)) return true;
    return false;
  }

  sv.form = {
    validateField: function (input) {
      if (!input || !input.checkValidity) return true;
      var field = input.closest(".form-field");
      var ok = input.checkValidity();
      var customMsg = input.dataset ? input.dataset.error : null;
      if (!ok && customMsg) input.setCustomValidity(customMsg);
      if (field) {
        field.classList.toggle("form-field--error", !ok);
        field.classList.toggle("form-field--success", ok && input.value && input.type !== "submit");
        var msgEl = field.querySelector(".form-field__error-text");
        if (msgEl) msgEl.textContent = ok ? "" : (customMsg || input.validationMessage || "Invalid input");
      }
      return ok;
    },
    enableAutosave: function (form, key) {
      if (!form || !key || !window.localStorage) return;
      var storageKey = "sv:draft:" + key;
      // Restore
      try {
        var raw = localStorage.getItem(storageKey);
        if (raw) {
          var saved = JSON.parse(raw);
          Object.keys(saved).forEach(function (name) {
            var input = form.elements[name];
            if (input && !isSensitive(input)) input.value = saved[name];
          });
        }
      } catch (e) {}
      // Persist (debounced)
      var t = null;
      var persist = function () {
        var data = {};
        for (var i = 0; i < form.elements.length; i++) {
          var input = form.elements[i];
          if (!input.name || isSensitive(input)) continue;
          if (input.type === "submit" || input.type === "button") continue;
          data[input.name] = input.value;
        }
        try { localStorage.setItem(storageKey, JSON.stringify(data)); } catch (e) {}
      };
      form.addEventListener("input", function () {
        if (t) clearTimeout(t);
        t = setTimeout(persist, 500);
      });
      form.addEventListener("submit", function () {
        try { localStorage.removeItem(storageKey); } catch (e) {}
      });
    },
    guardUnsaved: function (form, message) {
      if (!form) return;
      message = message || "You have unsaved changes. Leave anyway?";
      var dirty = false;
      form.addEventListener("input", function () { dirty = true; });
      form.addEventListener("submit", function () { dirty = false; });
      // HTMX navigation is intentional — don't block it.
      document.addEventListener("htmx:beforeRequest", function () { dirty = false; });
      window.addEventListener("beforeunload", function (e) {
        if (!dirty) return undefined;
        e.preventDefault();
        e.returnValue = message;
        return message;
      });
    }
  };

  // Auto-wire forms that opt in via data attributes.
  function initForms() {
    document.querySelectorAll("form[data-autosave]").forEach(function (f) {
      sv.form.enableAutosave(f, f.dataset.autosave);
    });
    document.querySelectorAll("form[data-guard-unsaved]").forEach(function (f) {
      sv.form.guardUnsaved(f, f.dataset.guardUnsaved);
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initForms);
  } else {
    initForms();
  }

  // Delegated validation listener (opt-in via data-validate on the field input).
  document.addEventListener("input", function (e) {
    if (!e.target.matches || !e.target.matches("[data-validate]")) return;
    sv.form.validateField(e.target);
  });
  document.addEventListener("blur", function (e) {
    if (!e.target.matches || !e.target.matches("[data-validate]")) return;
    sv.form.validateField(e.target);
  }, true);

  // ── HTMX swap interceptors (opt-in via [data-anim] on target) ─────
  document.addEventListener("htmx:beforeSwap", function (evt) {
    var t = evt.detail.target;
    if (!t || !t.matches) return;
    if (!(t.matches("[data-anim], [data-anim] *") || t.closest("[data-anim]"))) return;
    // Remember height so we can clamp shrink jumps.
    t.dataset.svPrevH = t.offsetHeight;
    if (canAnim()) {
      try { t.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 80, fill: "forwards" }); } catch (e) {}
    }
  });
  document.addEventListener("htmx:afterSwap", function (evt) {
    var t = evt.detail.target;
    if (!t || !t.matches) return;
    if (!(t.matches("[data-anim], [data-anim] *") || t.closest("[data-anim]"))) return;
    var prevH = parseInt(t.dataset.svPrevH, 10);
    var newH = t.offsetHeight;
    if (!isNaN(prevH) && newH > prevH) {
      t.style.minHeight = prevH + "px";
      requestAnimationFrame(function () { t.style.minHeight = ""; });
    }
    delete t.dataset.svPrevH;
    sv.fx.fadeSwap(t);
    // Re-wire any newly rendered opt-in forms.
    t.querySelectorAll && t.querySelectorAll("form[data-autosave]").forEach(function (f) {
      if (!f.dataset.svAutosaveBound) {
        sv.form.enableAutosave(f, f.dataset.autosave);
        f.dataset.svAutosaveBound = "1";
      }
    });
  });
  document.addEventListener("htmx:oobAfterSwap", function (evt) {
    var t = evt.detail.target;
    if (t && t.matches && !t.hasAttribute("data-no-anim")) sv.fx.scaleIn(t);
  });

  // ── nprogress on page-level HTMX requests ─────────────────────────
  document.addEventListener("htmx:beforeRequest", function (evt) {
    var t = evt.detail.target;
    if (!t) return;
    if (t.matches && (t.matches("[data-anim-root]") || t === document.body || t.tagName === "MAIN" || t.closest("[data-anim-root]"))) {
      sv.nprog.start();
    }
  });
  document.addEventListener("htmx:afterRequest", function () { sv.nprog.done(); });
  document.addEventListener("htmx:responseError", function () { sv.nprog.done(); });
  document.addEventListener("htmx:sendError", function () { sv.nprog.done(); });

  // ── Close palette/help overlays with focus trap on open ───────────
  // (Extends existing openPalette without rewriting it.)
  var _origOpen = window.openPalette;
  if (typeof _origOpen === "function") {
    var _releaseTrap = null;
    window.openPalette = function () {
      _origOpen.apply(this, arguments);
      var panel = document.querySelector("#cmd-palette-overlay .cmd-palette");
      if (panel) {
        sv.fx.scaleIn(panel);
        if (_releaseTrap) _releaseTrap();
        _releaseTrap = sv.focusTrap(panel);
      }
    };
    var _origClose = window.closePalette;
    window.closePalette = function () {
      if (_releaseTrap) { _releaseTrap(); _releaseTrap = null; }
      if (typeof _origClose === "function") _origClose.apply(this, arguments);
    };
  }
})();

// ── Copy-link button (W3) ───────────────────────────────────────────
// Clicking any [data-copy-link] button copies the current page URL. Falls
// back to a hidden textarea + execCommand('copy') on insecure contexts
// where navigator.clipboard isn't available.
(function () {
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    // Fallback for HTTP/non-secure contexts
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        resolve();
      } catch (e) { reject(e); }
      finally { document.body.removeChild(ta); }
    });
  }
  function showToast(msg) {
    if (window.sv && sv.toast) { sv.toast(msg); return; }
    // Minimal fallback toast
    var t = document.createElement("div");
    t.textContent = msg;
    t.style.cssText = "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1a1a2e;color:#fff;padding:10px 16px;border-radius:6px;font-size:14px;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.2)";
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2000);
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy-link]");
    if (!btn) return;
    e.preventDefault();
    copyText(window.location.href).then(
      function () { showToast("Link copied to clipboard"); },
      function () { showToast("Could not copy — press ⌘C"); }
    );
  });
})();

// ── Animated stat counters (W1) ─────────────────────────────────────
// Targets elements with [data-count-to]. Animates ONCE on the first time
// the dashboard overview is rendered (whether initial load or first HTMX
// swap). Subsequent polls (every 30s) skip animation and just update text,
// so the numbers don't "fly up" every refresh.
// Respects prefers-reduced-motion via sv.fx.countUp's own canAnim() check.
(function () {
  var _hasAnimated = false;
  function animateCounters(scope) {
    if (!window.sv || !sv.fx || !sv.fx.countUp) return;
    scope = scope || document;
    var els = scope.querySelectorAll(".cc-status-count[data-count-to]");
    if (!els.length) return;
    if (_hasAnimated) {
      // Subsequent renders: just set final value, no animation
      els.forEach(function (el) {
        el.textContent = el.dataset.countTo;
      });
      return;
    }
    els.forEach(function (el) {
      var to = parseInt(el.dataset.countTo, 10) || 0;
      el.textContent = "0";
      sv.fx.countUp(el, to, { duration: 700 });
    });
    _hasAnimated = true;
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { animateCounters(); });
  } else {
    animateCounters();
  }
  // Catch HTMX-injected status strip (first swap animates, later polls don't)
  document.body.addEventListener("htmx:afterSwap", function (e) {
    animateCounters(e.target);
  });
})();
