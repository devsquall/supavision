/* Supervisor — Toast, button feedback, run polling, relative timestamps */
(function () {
  "use strict";

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

  // ── Button feedback ────────────────────────────────
  document.addEventListener("htmx:beforeRequest", function (e) {
    var el = e.detail.elt;
    if (el.tagName === "BUTTON" && el.dataset.action) {
      el.disabled = true;
      el._text = el.textContent;
      el.textContent = "Running\u2026";
    }
  });

  document.addEventListener("htmx:afterRequest", function (e) {
    var el = e.detail.elt;
    if (!el.dataset || !el.dataset.action) return;
    var ok = e.detail.xhr && e.detail.xhr.status < 400;
    showToast(
      ok ? (el.dataset.successMsg || "Action started") : (el.dataset.errorMsg || "Action failed"),
      ok ? "success" : "error"
    );

    // For run triggers, poll for completion
    if (ok && (el.dataset.action === "check" || el.dataset.action === "discover")) {
      pollRunStatus(el);
    } else {
      reEnableButton(el, 2000);
    }
  });

  function reEnableButton(el, delay) {
    setTimeout(function () {
      el.disabled = false;
      if (el._text) el.textContent = el._text;
    }, delay);
  }

  // ── Run status polling ─────────────────────────────
  function pollRunStatus(btn) {
    var resourceId = btn.closest("[data-resource-id]");
    if (!resourceId) { reEnableButton(btn, 3000); return; }
    var rid = resourceId.dataset.resourceId;

    btn.textContent = "Running\u2026";
    var dots = 0;
    var interval = setInterval(function () {
      dots = (dots + 1) % 4;
      btn.textContent = "Running" + ".".repeat(dots);
    }, 500);

    var attempts = 0;
    var maxAttempts = 90; // 3 minutes at 2s intervals

    function check() {
      attempts++;
      if (attempts > maxAttempts) {
        clearInterval(interval);
        btn.textContent = "Timed out";
        showToast("Run is taking longer than expected. Check back later.", "error");
        reEnableButton(btn, 3000);
        return;
      }

      // Use the dashboard resources endpoint to check if severity changed
      fetch("/dashboard/resources-status/" + rid)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.running) {
            setTimeout(check, 2000);
          } else {
            clearInterval(interval);
            if (data.status === "failed") {
              btn.textContent = "Failed";
              showToast(data.error || "Run failed. Check the resource page for details.", "error");
            } else {
              btn.textContent = "Done!";
              showToast(data.severity ? "Completed: " + data.severity.toUpperCase() : "Completed", "success");
            }
            reEnableButton(btn, 3000);
            htmx.trigger(document.body, "refreshResources");
          }
        })
        .catch(function () {
          setTimeout(check, 2000);
        });
    }

    setTimeout(check, 3000); // First check after 3s
  }

  // ── Relative timestamps ────────────────────────────
  function timeAgo(dateStr) {
    if (!dateStr || dateStr === "-") return dateStr;
    var date = new Date(dateStr);
    if (isNaN(date.getTime())) return dateStr;
    var now = new Date();
    var seconds = Math.floor((now - date) / 1000);

    if (seconds < 60) return "just now";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
    if (seconds < 604800) return Math.floor(seconds / 86400) + "d ago";
    return dateStr; // Fall back to original for old dates
  }

  function updateTimestamps() {
    document.querySelectorAll("[data-timestamp]").forEach(function (el) {
      el.textContent = timeAgo(el.dataset.timestamp);
      el.title = el.dataset.timestamp; // Show full date on hover
    });
  }

  // Run on load and after HTMX swaps
  document.addEventListener("DOMContentLoaded", updateTimestamps);
  document.addEventListener("htmx:afterSwap", updateTimestamps);

  // Refresh timestamps every 60s
  setInterval(updateTimestamps, 60000);
})();
