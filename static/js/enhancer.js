(function () {
  "use strict";

  var SDS = window.SDS;
  var $ = function (id) { return document.getElementById(id); };
  var FETCH_TIMEOUT_MS = 45000;

  var DEFAULT_MANUAL = {
    brightness: 0, contrast: 0, sharpness: 0, noise: 0,
    saturation: 0, strength: 0.5, color_correction: false, clahe: true
  };

  var state = {
    scanId: null,
    filename: "",
    origW: 0,
    origH: 0,
    mode: "auto",
    preset: "auto",
    autoStrength: 0.5,
    outputStyle: "colorful",
    manual: Object.assign({}, DEFAULT_MANUAL),
    format: "jpg",
    lastBlob: null,
    lastName: "",
    requestToken: 0,
    restoredOriginal: false
  };
  var config = {};
  var activeController = null;
  var imageLoaded = false;

  function jsonEl(id, fallback) {
    var el = $(id);
    if (!el) return fallback;
    try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
  }

  function apiUrl(template, scanId) {
    return template.replace("{0}", scanId);
  }

  function showMessages(list) {
    var box = $("enhancer-messages");
    if (!box) return;
    if (!list || !list.length) { box.hidden = true; box.innerHTML = ""; return; }
    box.textContent = list.join(" ");
    box.hidden = false;
  }

  function clearMessages() {
    var box = $("enhancer-messages");
    if (box) { box.hidden = true; box.innerHTML = ""; }
  }

  function setStatusChip(text, level) {
    var chip = $("enhance-status");
    if (!chip) return;
    chip.hidden = !text;
    chip.textContent = text || "";
    chip.className = "chip" + (level === "ok" ? " chip-ok" : level === "bad" ? " chip-bad" : "");
  }

  function abortActive() {
    if (activeController) {
      try { activeController.abort(); } catch (e) {}
      activeController = null;
    }
  }

  function newRequest() {
    abortActive();
    var controller = new AbortController();
    activeController = controller;
    var timer = setTimeout(function () {
      try { controller.abort(); } catch (e) {}
    }, FETCH_TIMEOUT_MS);
    return {
      signal: controller.signal,
      done: function () { clearTimeout(timer); }
    };
  }

  function parseJsonSafe(response) {
    return response.text().then(function (text) {
      if (!text) return null;
      try { return JSON.parse(text); } catch (e) {
        throw new Error("Unexpected response from the server.");
      }
    });
  }

  function syncModeTabs() {
    document.querySelectorAll('.mode-tab[data-mode]').forEach(function (tab) {
      var selected = tab.getAttribute("data-mode") === state.mode;
      tab.classList.toggle("is-selected", selected);
      tab.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    var autoC = $("auto-controls");
    var manC = $("manual-controls");
    if (autoC) autoC.hidden = state.mode !== "auto";
    if (manC) manC.hidden = state.mode !== "manual";
  }

  function syncStyleTabs() {
    document.querySelectorAll('.mode-tab[data-style]').forEach(function (tab) {
      var selected = tab.getAttribute("data-style") === state.outputStyle;
      tab.classList.toggle("is-selected", selected);
      tab.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }

  function syncPresetCards() {
    document.querySelectorAll(".enhance-feature-card").forEach(function (card) {
      var selected = card.getAttribute("data-preset") === state.preset;
      card.classList.toggle("is-selected", selected);
      card.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }

  function syncFormatBtns() {
    document.querySelectorAll(".format-btn").forEach(function (btn) {
      var selected = btn.getAttribute("data-format") === state.format;
      btn.classList.toggle("is-selected", selected);
      btn.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }

  function renderOutputs() {
    var obs = [
      ["out-brightness", state.manual.brightness, 2],
      ["out-contrast", state.manual.contrast, 2],
      ["out-sharpness", state.manual.sharpness, "pct"],
      ["out-noise", state.manual.noise, "pct"],
      ["out-saturation", state.manual.saturation, 2],
      ["out-manual-strength", state.manual.strength, "pct"]
    ];
    obs.forEach(function (item) {
      var el = $(item[0]);
      if (!el) return;
      if (item[2] === "pct") el.textContent = Math.round(item[1] * 100) + "%";
      else el.textContent = item[1].toFixed(item[2]);
    });
    var as = $("out-auto-strength");
    if (as) as.textContent = Math.round(state.autoStrength * 100) + "%";
  }

  function uploadFile(file) {
    if (state.scanId && $("enhancer-upload-error")) {
      return;
    }
    var errorEl = $("enhancer-upload-error");
    function fail(message) {
      if (errorEl) { errorEl.textContent = message; errorEl.hidden = false; }
      else showMessages([message]);
    }
    var error = SDS.validateFile(file, config.maxUploadBytes);
    if (error) { fail(error); return; }
    if (errorEl) errorEl.hidden = true;

    setStatusChip("Uploading image…");
    clearMessages();
    var form = new FormData();
    form.append("image", file);

    var req = newRequest();
    fetch(config.uploadUrl, {
      method: "POST",
      headers: { "X-CSRFToken": SDS.csrfToken(), "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
      body: form,
      signal: req.signal
    })
      .then(function (response) {
        return parseJsonSafe(response).then(function (data) {
          if (!response.ok || !data || data.ok === false) {
            throw new Error((data && data.errors && data.errors.join(" ")) || ("Upload failed (" + response.status + ")."));
          }
          return data;
        });
      })
      .then(function (data) {
        activateScan(data);
      })
      .catch(function (error) {
        setStatusChip("", "");
        if (error.name === "AbortError") fail("Upload timed out. Please try again.");
        else fail(error.message || "Upload failed. Please try again.");
      })
      .finally(function () { req.done(); });
  }

  function activateScan(payload) {
    if (!payload || !payload.id) return;
    state.scanId = payload.id;
    state.filename = payload.filename || "";
    state.origW = payload.width;
    state.origH = payload.height;
    state.lastBlob = null;
    state.restoredOriginal = false;

    $("enhancer-empty").hidden = true;
    $("enhancer-workspace").hidden = false;

    var before = $("enhance-before");
    var after = $("enhance-after");
    imageLoaded = false;
    before.onload = function () { imageLoaded = true; runEnhance(); };
    before.onerror = function () {
      showMessages(["Could not display the image. Please try uploading again."]);
    };
    before.src = payload.original_url;
    if (after) after.removeAttribute("src");

    var dl = $("btn-enhance-download");
    if (dl) dl.disabled = true;
  }

  function buildPayload() {
    var payload = {
      mode: state.mode,
      output_style: state.outputStyle,
      format: state.format
    };
    if (state.mode === "auto") {
      payload.preset = state.preset;
      payload.strength = state.autoStrength;
    } else {
      payload.brightness = state.manual.brightness;
      payload.contrast = state.manual.contrast;
      payload.sharpness = state.manual.sharpness;
      payload.noise = state.manual.noise;
      payload.saturation = state.manual.saturation;
      payload.strength = state.manual.strength;
      payload.color_correction = state.manual.color_correction;
      payload.clahe = state.manual.clahe;
    }
    return payload;
  }

  function runEnhance() {
    if (!state.scanId) return;
    if (!imageLoaded) return;
    if (state.restoredOriginal) return;

    var token = ++state.requestToken;
    var spinner = $("enhance-spinner");
    var dl = $("btn-enhance-download");
    var after = $("enhance-after");

    if (spinner) spinner.hidden = false;
    if (dl) dl.disabled = true;
    state.lastBlob = null;
    setStatusChip("Enhancing image…");
    clearMessages();

    var payload = buildPayload();
    var req = newRequest();
    fetch(apiUrl(config.enhanceUrlTemplate, state.scanId), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": SDS.csrfToken(),
        "X-Requested-With": "XMLHttpRequest"
      },
      credentials: "same-origin",
      body: JSON.stringify(payload),
      signal: req.signal
    })
      .then(function (response) {
        if (!response.ok) {
          return parseJsonSafe(response).then(function (data) {
            throw new Error((data && data.errors && data.errors.join(" ")) || ("Enhancement failed (" + response.status + ")."));
          });
        }
        var header = response.headers.get("Content-Disposition");
        return response.blob().then(function (blob) { return { blob: blob, header: header }; });
      })
      .then(function (result) {
        if (token !== state.requestToken) return;
        var url = URL.createObjectURL(result.blob);
        if (after) {
          after.onload = function () { if (token === state.requestToken) setStatusChip("Enhancement completed", "ok"); };
          after.onerror = function () {
            if (token === state.requestToken) {
              setStatusChip("Could not display result", "bad");
              showMessages(["The enhanced image could not be displayed. Please try again."]);
            }
          };
          after.src = url;
        }
        state.lastBlob = result.blob;
        state.lastName = filenameFromDisposition(result.header, "enhanced-" + state.mode + "." + state.format);
        if (dl) dl.disabled = false;
      })
      .catch(function (error) {
        if (token !== state.requestToken) return;
        if (after) after.removeAttribute("src");
        if (error.name === "AbortError") {
          setStatusChip("Enhancement timed out", "bad");
          showMessages(["Enhancement took too long. Try a smaller image or lower settings."]);
        } else {
          setStatusChip("Enhancement failed", "bad");
          showMessages([error.message || "Enhancement failed. Please try again."]);
        }
      })
      .finally(function () {
        if (token === state.requestToken && spinner) spinner.hidden = true;
        req.done();
      });
  }

  var debouncedEnhance = SDS.debounce(function () { runEnhance(); }, 350);

  function downloadBlob(blob, fallbackName) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = fallbackName || "enhanced";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  function filenameFromDisposition(header, fallback) {
    if (!header) return fallback;
    var match = header.match(/filename="?([^";]+)"?/);
    return match ? match[1] : fallback;
  }

  function resetManual() {
    state.manual = Object.assign({}, DEFAULT_MANUAL);
    var ids = ["inp-brightness", "inp-contrast", "inp-sharpness", "inp-noise", "inp-saturation", "inp-manual-strength"];
    var vals = [0, 0, 0, 0, 0, 0.5];
    ids.forEach(function (id, i) { var el = $(id); if (el) el.value = vals[i]; });
    var cc = $("inp-color-correction"); if (cc) cc.checked = false;
    var cl = $("inp-clahe"); if (cl) cl.checked = true;
    renderOutputs();
  }

  function restoreOriginal() {
    state.restoredOriginal = true;
    var after = $("enhance-after");
    var before = $("enhance-before");
    var dl = $("btn-enhance-download");
    var spinner = $("enhance-spinner");
    if (spinner) spinner.hidden = true;
    if (after) after.src = before.src;
    state.lastBlob = null;
    if (dl) dl.disabled = true;
    setStatusChip("Showing original image", "");
  }

  function bindControls() {
    document.querySelectorAll('.mode-tab[data-mode]').forEach(function (tab) {
      tab.addEventListener("click", function () {
        if (state.mode === tab.getAttribute("data-mode")) return;
        state.mode = tab.getAttribute("data-mode");
        state.restoredOriginal = false;
        syncModeTabs();
        runEnhance();
      });
    });

    document.querySelectorAll('.mode-tab[data-style]').forEach(function (tab) {
      tab.addEventListener("click", function () {
        if (state.outputStyle === tab.getAttribute("data-style")) return;
        state.outputStyle = tab.getAttribute("data-style");
        state.restoredOriginal = false;
        syncStyleTabs();
        runEnhance();
      });
    });

    document.querySelectorAll(".enhance-feature-card").forEach(function (card) {
      card.addEventListener("click", function () {
        if (state.preset === card.getAttribute("data-preset")) return;
        state.preset = card.getAttribute("data-preset");
        state.restoredOriginal = false;
        syncPresetCards();
        if (state.mode === "auto") runEnhance();
      });
    });

    var as = $("inp-auto-strength");
    if (as) {
      as.addEventListener("input", function () {
        state.autoStrength = parseFloat(as.value);
        state.restoredOriginal = false;
        renderOutputs();
        if (state.mode === "auto") debouncedEnhance();
      });
    }

    var manualIds = ["inp-brightness", "inp-contrast", "inp-sharpness", "inp-noise", "inp-saturation", "inp-manual-strength"];
    var manualKeys = ["brightness", "contrast", "sharpness", "noise", "saturation", "strength"];
    manualIds.forEach(function (id, i) {
      var el = $(id);
      if (!el) return;
      el.addEventListener("input", function () {
        state.manual[manualKeys[i]] = parseFloat(el.value);
        state.restoredOriginal = false;
        renderOutputs();
        if (state.mode === "manual") debouncedEnhance();
      });
    });

    var cc = $("inp-color-correction");
    if (cc) cc.addEventListener("change", function () {
      state.manual.color_correction = cc.checked;
      state.restoredOriginal = false;
      if (state.mode === "manual") debouncedEnhance();
    });
    var cl = $("inp-clahe");
    if (cl) cl.addEventListener("change", function () {
      state.manual.clahe = cl.checked;
      state.restoredOriginal = false;
      if (state.mode === "manual") debouncedEnhance();
    });

    document.querySelectorAll(".format-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.format = btn.getAttribute("data-format");
        syncFormatBtns();
      });
    });

    var dl = $("btn-enhance-download");
    if (dl) {
      dl.addEventListener("click", function () {
        if (state.lastBlob) downloadBlob(state.lastBlob, state.lastName);
      });
    }

    var autoBtn = $("btn-auto-enhance");
    if (autoBtn) autoBtn.addEventListener("click", function () {
      state.mode = "auto";
      state.preset = "auto";
      state.restoredOriginal = false;
      syncModeTabs();
      syncPresetCards();
      runEnhance();
    });

    var resetBtn = $("btn-reset");
    if (resetBtn) resetBtn.addEventListener("click", function () {
      resetManual();
      state.autoStrength = 0.5;
      var asEl = $("inp-auto-strength"); if (asEl) asEl.value = 0.5;
      state.preset = "auto";
      state.restoredOriginal = false;
      syncPresetCards();
      renderOutputs();
      runEnhance();
    });

    var restoreBtn = $("btn-restore-original");
    if (restoreBtn) restoreBtn.addEventListener("click", restoreOriginal);

    window.SDS.initDropzone($("enhancer-dropzone"), $("enhancer-file-input"), {
      maxBytes: config.maxUploadBytes,
      onFile: uploadFile
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("enhancer-root")) return;
    config = jsonEl("enhancer-config", {});
    var initial = jsonEl("enhancer-initial", null);

    bindControls();
    syncModeTabs();
    syncStyleTabs();
    syncPresetCards();
    syncFormatBtns();
    renderOutputs();

    if (initial && initial.id) {
      activateScan(initial);
    } else {
      $("enhancer-workspace").hidden = true;
      $("enhancer-empty").hidden = false;
    }
  });
})();
