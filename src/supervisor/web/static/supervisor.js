/* Supervisor — Toast notifications + HTMX button feedback */
(function () {
  "use strict";

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

  // Button feedback: disable + "Running..." on click
  document.addEventListener("htmx:beforeRequest", function (e) {
    var el = e.detail.elt;
    if (el.tagName === "BUTTON" && el.dataset.action) {
      el.disabled = true;
      el._text = el.textContent;
      el.textContent = "Running\u2026";
    }
  });

  // Toast on completion, re-enable button
  document.addEventListener("htmx:afterRequest", function (e) {
    var el = e.detail.elt;
    if (!el.dataset || !el.dataset.action) return;
    var ok = e.detail.xhr && e.detail.xhr.status < 400;
    showToast(
      ok ? (el.dataset.successMsg || "Action started") : (el.dataset.errorMsg || "Action failed"),
      ok ? "success" : "error"
    );
    setTimeout(function () {
      el.disabled = false;
      if (el._text) el.textContent = el._text;
    }, 2000);
  });
})();
