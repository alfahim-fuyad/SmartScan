(function () {
  "use strict";

  var UPLOAD_TIMEOUT_MS = 45000;

  document.addEventListener("DOMContentLoaded", function () {
    var card = document.getElementById("home-upload-card");
    if (!card) return;

    var SDS = window.SDS;
    var maxBytes = parseInt(card.getAttribute("data-max-bytes"), 10) || 12 * 1024 * 1024;
    var uploadUrl = card.getAttribute("data-upload-url") || "/scanner/upload/";

    var zone = document.getElementById("home-dropzone");
    var picker = document.getElementById("home-file-input");
    var previewWrap = document.getElementById("home-upload-preview");
    var previewImg = document.getElementById("home-preview-img");
    var nameEl = document.getElementById("home-preview-name");
    var sizeEl = document.getElementById("home-preview-size");
    var errorEl = document.getElementById("home-upload-error");
    var scanBtn = document.getElementById("home-scan-btn");
    var replaceBtn = document.getElementById("home-replace-btn");
    var removeBtn = document.getElementById("home-remove-btn");
    var overlay = document.getElementById("home-upload-overlay");
    var overlayText = document.getElementById("home-upload-overlay-text");
    var btnLabel = scanBtn.querySelector(".btn-label");
    var btnIcon = scanBtn.querySelector(".btn-icon");
    var btnSpinner = scanBtn.querySelector(".btn-spinner");

    var selectedFile = null;
    var uploading = false;
    var currentController = null;

    function showError(message) {
      errorEl.textContent = message;
      errorEl.hidden = false;
    }

    function clearError() { errorEl.hidden = true; }

    function showPreview(file) {
      if (URL.createObjectURL) {
        if (previewImg.dataset.blobUrl) URL.revokeObjectURL(previewImg.dataset.blobUrl);
        previewImg.src = URL.createObjectURL(file);
        previewImg.dataset.blobUrl = previewImg.src;
      }
      nameEl.textContent = file.name;
      sizeEl.textContent = SDS.formatBytes(file.size);
      previewWrap.hidden = false;
      zone.hidden = true;
      scanBtn.disabled = false;
    }

    function reset() {
      if (previewImg.dataset.blobUrl) URL.revokeObjectURL(previewImg.dataset.blobUrl);
      delete previewImg.dataset.blobUrl;
      previewImg.removeAttribute("src");
      previewWrap.hidden = true;
      zone.hidden = false;
      picker.value = "";
      scanBtn.disabled = true;
      selectedFile = null;
      clearError();
    }

    function handleFile(file) {
      if (uploading) return;
      clearError();
      var error = SDS.validateFile(file, maxBytes);
      if (error) { showError(error); reset(); return; }

      selectedFile = file;
      showPreview(file);
    }

    function setBusy(busy, label) {
      uploading = busy;
      scanBtn.disabled = busy;
      if (btnLabel) btnLabel.textContent = label || "Scan This Image";
      if (btnIcon) btnIcon.hidden = !!busy;
      if (btnSpinner) btnSpinner.hidden = !busy;
      if (overlay) {
        overlay.hidden = !busy;
        if (overlayText && label) overlayText.textContent = label;
      }
    }

    function uploadAjax(file) {
      clearError();
      setBusy(true, "Uploading image…");

      currentController = new AbortController();
      var timer = setTimeout(function () {
        try { currentController.abort(); } catch (e) {}
      }, UPLOAD_TIMEOUT_MS);

      var form = new FormData();
      form.append("image", file);

      fetch(uploadUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": SDS.csrfToken(),
          "X-Requested-With": "XMLHttpRequest"
        },
        credentials: "same-origin",
        body: form,
        signal: currentController.signal
      })
        .then(function (response) {
          return response.text().then(function (text) {
            var data = null;
            if (text) {
              try { data = JSON.parse(text); }
              catch (e) { throw new Error("Unexpected response from the server."); }
            }
            if (!response.ok || !data || data.ok === false) {
              var msg = (data && data.errors && data.errors.join(" ")) || ("Upload failed (" + response.status + ").");
              throw new Error(msg);
            }
            return data;
          });
        })
        .then(function (data) {
          clearTimeout(timer);
          setBusy(true, "Opening scanner…");
          var scanUrl = "/scanner/?scan=" + data.id;
          window.location.href = scanUrl;
        })
        .catch(function (error) {
          clearTimeout(timer);
          setBusy(false);
          if (error.name === "AbortError") {
            showError("Upload timed out. Please try again or use a smaller image.");
          } else {
            showError(error.message || "Upload failed. Please try again.");
          }
        });
    }

    window.SDS.initDropzone(zone, picker, { maxBytes: maxBytes, onFile: handleFile });

    replaceBtn.addEventListener("click", function () {
      if (uploading) return;
      picker.click();
    });
    removeBtn.addEventListener("click", function () {
      if (uploading) return;
      reset();
    });

    scanBtn.addEventListener("click", function (event) {
      event.preventDefault();
      if (uploading) return;
      if (!selectedFile) {
        showError("Please choose an image first.");
        return;
      }
      uploadAjax(selectedFile);
    });

    document.addEventListener("paste", function (event) {
      if (uploading) return;
      var items = event.clipboardData && event.clipboardData.files;
      if (items && items.length) handleFile(items[0]);
    });
  });
})();
