/* Replytic UI behaviours. Vanilla JS, progressive enhancement only. */
(function () {
  "use strict";

  // Show / hide password fields.
  document.querySelectorAll("[data-toggle-password]").forEach(function (button) {
    button.addEventListener("click", function () {
      var input = document.getElementById(button.getAttribute("data-toggle-password"));
      if (!input) return;
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-label", show ? "Hide password" : "Show password");
      var icon = button.querySelector("i");
      if (icon) icon.className = show ? "bi bi-eye-slash" : "bi bi-eye";
    });
  });

  // Auto-dismiss non-error flash messages.
  document.querySelectorAll(".flash[data-autodismiss]").forEach(function (el) {
    setTimeout(function () {
      if (!document.body.contains(el)) return;
      el.style.transition = "opacity .3s, transform .3s";
      el.style.opacity = "0";
      el.style.transform = "translateY(-4px)";
      setTimeout(function () { el.remove(); }, 320);
    }, 6000);
  });
  document.querySelectorAll(".flash .btn-close").forEach(function (btn) {
    btn.addEventListener("click", function () { btn.closest(".flash").remove(); });
  });

  // Disable submit buttons after the first submit to avoid duplicates.
  document.querySelectorAll("form[data-loading-form]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var submit = form.querySelector("button[type=submit]");
      if (!submit) return;
      setTimeout(function () { submit.classList.add("btn-loading"); submit.setAttribute("aria-disabled", "true"); }, 0);
    });
  });

  // Live preview for the business logo picker.
  var logoInput = document.querySelector("[data-logo-input]");
  var logoPreview = document.querySelector("[data-logo-preview]");
  if (logoInput && logoPreview) {
    logoInput.addEventListener("change", function () {
      var file = logoInput.files && logoInput.files[0];
      if (!file || !file.type.startsWith("image/")) return;
      var reader = new FileReader();
      reader.onload = function (event) {
        logoPreview.innerHTML = "";
        var img = document.createElement("img");
        img.alt = "Logo preview";
        img.src = event.target.result;
        logoPreview.appendChild(img);
      };
      reader.readAsDataURL(file);
    });
  }

  // Team page: fill the shared modals with the selected member.
  document.querySelectorAll("[data-member-modal]").forEach(function (trigger) {
    trigger.addEventListener("click", function () {
      var target = document.querySelector(trigger.getAttribute("data-member-modal"));
      if (!target) return;
      var form = target.querySelector("form");
      form.setAttribute("action", trigger.getAttribute("data-action"));
      target.querySelectorAll("[data-fill=name]").forEach(function (node) { node.textContent = trigger.getAttribute("data-name"); });
      var select = target.querySelector("select[name=role]");
      if (select) select.value = trigger.getAttribute("data-role");
    });
  });

  // Tooltips.
  if (window.bootstrap) {
    document.querySelectorAll("[data-bs-toggle=tooltip]").forEach(function (el) { new bootstrap.Tooltip(el); });
  }
})();
