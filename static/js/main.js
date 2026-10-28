(function () {
  "use strict";

  var SDS = (window.SDS = window.SDS || {});

  SDS.getCookie = function (name) {
    var match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[2]) : "";
  };

  SDS.csrfToken = function () {
    var token = SDS.getCookie("csrftoken");
    if (!token) {
      var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
      if (input) token = input.value;
    }
    return token || "";
  };

  SDS.debounce = function (fn, waitMs) {
    var timer = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, waitMs);
    };
  };

  SDS.formatBytes = function (bytes) {
    if (!bytes && bytes !== 0) return "—";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  };

  SDS.EXTS = ["jpg", "jpeg", "png", "webp"];

  SDS.validateFile = function (file, maxBytes) {
    if (!file) return "No file selected.";
    var parts = file.name.toLowerCase().split(".");
    var ext = parts.length > 1 ? parts.pop() : "";
    if (SDS.EXTS.indexOf(ext) === -1) {
      return "Unsupported format. Please choose a JPG, PNG or WebP image.";
    }
    if (maxBytes && file.size > maxBytes) {
      return "Image is " + SDS.formatBytes(file.size) + " — the maximum is " + SDS.formatBytes(maxBytes) + ".";
    }
    return null;
  };

  function initTheme() {
    var button = document.getElementById("theme-toggle");
    if (!button) return;
    button.addEventListener("click", function () {
      var root = document.documentElement;
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("sds-theme", next); } catch (e) {}
    });
  }

  function initNav() {
    var toggle = document.getElementById("nav-toggle");
    var menu = document.getElementById("nav-menu");
    if (!toggle || !menu) return;
    toggle.addEventListener("click", function () {
      var open = menu.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.classList.contains("is-open")) {
        menu.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus();
      }
    });
  }

  function initAlerts() {
    document.querySelectorAll(".alert-close").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var alert = btn.closest(".alert");
        if (alert) alert.remove();
      });
    });
  }

  SDS.initDropzone = function (zone, input, opts) {
    opts = opts || {};
    if (!zone || !input) return;

    function openPicker() { input.click(); }

    zone.addEventListener("click", function (event) {
      if (event.target.closest("button:not(.dropzone-browse)")) return;
      openPicker();
    });
    zone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openPicker();
      }
    });
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.remove("is-dragover");
      });
    });
    zone.addEventListener("drop", function (event) {
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) {
        input.files = files;
        if (opts.onFile) opts.onFile(files[0]);
      }
    });
    input.addEventListener("change", function () {
      if (input.files && input.files.length && opts.onFile) {
        opts.onFile(input.files[0]);
      }
    });

    return {
      clear: function () {
        input.value = "";
        if (opts.onClear) opts.onClear();
      }
    };
  };

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initNav();
    initAlerts();
  });
})();
