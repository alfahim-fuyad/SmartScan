(function () {
  "use strict";

  var SDS = window.SDS;
  var $ = function (id) { return document.getElementById(id); };

  var FETCH_TIMEOUT_MS = 45000;

  var state = {
    scanId: null,
    filename: "",
    origW: 0,
    origH: 0,
    corners: null,
    draftCorners: null,
    manualMode: false,
    perspective: true,
    filter: "original",
    params: { brightness: 1, contrast: 1, sharpness: 0, denoise: false, clahe: true },
    exportFormat: "jpg",
    pages: [],
    restorePreset: "natural",
    restoreStrength: 0.5,
    restoreFormat: "jpg",
    activateToken: 0,
    processToken: 0
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

  function showMessages(list, isInfo) {
    var box = $("scan-messages");
    if (!box) return;
    if (!list || !list.length) { box.hidden = true; box.innerHTML = ""; return; }
    box.textContent = list.join(" ");
    box.classList.toggle("is-info", !!isInfo);
    box.hidden = false;
  }

  function clearMessages() {
    var box = $("scan-messages");
    if (box) { box.hidden = true; box.innerHTML = ""; }
  }

  var loading = { image: false, detect: false, process: false, enhance: false, upload: false };

  function updateSpinners() {
    var sourceSpinner = $("source-spinner");
    var afterSpinner = $("after-spinner");
    var enhanceSpinner = $("enhance-spinner");
    var anyBusy = loading.image || loading.detect || loading.process || loading.upload;
    if (sourceSpinner) sourceSpinner.hidden = !anyBusy;
    if (afterSpinner) afterSpinner.hidden = !loading.process;
    if (enhanceSpinner) enhanceSpinner.hidden = !loading.enhance;
  }

  function setStatusChip(text, level) {
    var chip = $("scan-status-chip");
    if (!chip) return;
    chip.hidden = !text;
    chip.textContent = text || "";
    chip.className = "chip" + (level === "ok" ? " chip-ok" : level === "warn" ? " chip-warn" : level === "bad" ? " chip-bad" : "");
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
      controller: controller,
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

  function apiPost(url, body, isForm) {
    var req = newRequest();
    var options = {
      method: "POST",
      headers: { "X-CSRFToken": SDS.csrfToken(), "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
      signal: req.signal
    };
    if (isForm) {
      options.body = body;
    } else {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body || {});
    }
    return fetch(url, options).then(function (response) {
      return parseJsonSafe(response).then(function (data) {
        if (!response.ok || !data || data.ok === false) {
          var msg = (data && data.errors && data.errors.join(" ")) || ("Request failed (" + response.status + ").");
          throw new Error(msg);
        }
        return data;
      });
    }).finally(function () { req.done(); });
  }

  function apiPostBlob(url, body) {
    var req = newRequest();
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": SDS.csrfToken(),
        "X-Requested-With": "XMLHttpRequest"
      },
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
      signal: req.signal
    }).then(function (response) {
      if (!response.ok) {
        return parseJsonSafe(response).then(function (data) {
          throw new Error((data && data.errors && data.errors.join(" ")) || ("Export failed (" + response.status + ")."));
        });
      }
      var header = response.headers.get("Content-Disposition");
      return response.blob().then(function (blob) { return { blob: blob, header: header }; });
    }).finally(function () { req.done(); });
  }

  function downloadBlob(blob, fallbackName) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = fallbackName || "smart-scan";
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

  function sourceImage() { return $("source-image"); }

  function displayScale() {
    var img = sourceImage();
    if (!img || !img.clientWidth || !state.origW) return 1;
    return img.clientWidth / state.origW;
  }

  function toDisplay(corners) {
    var s = displayScale();
    return corners.map(function (pt) { return [pt[0] * s, pt[1] * s]; });
  }

  function renderOverlay() {
    var overlay = $("quad-overlay");
    var polygon = $("quad-polygon");
    var img = sourceImage();
    if (!overlay || !polygon || !img) return;

    var pts = state.manualMode ? state.draftCorners : state.corners;
    var handles = document.querySelectorAll(".corner-handle");
    if (!pts) {
      polygon.setAttribute("points", "");
      handles.forEach(function (h) { h.style.left = "-100px"; h.style.top = "-100px"; });
      return;
    }

    var disp = toDisplay(pts);
    polygon.setAttribute("points", disp.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" "));
    handles.forEach(function (handle) {
      var idx = parseInt(handle.getAttribute("data-corner"), 10);
      handle.style.left = disp[idx][0].toFixed(1) + "px";
      handle.style.top = disp[idx][1].toFixed(1) + "px";
    });
  }

  function clampDraft(point) {
    var img = sourceImage();
    if (!img || !img.clientWidth) return point;
    var maxX = img.clientWidth - 2, maxY = img.clientHeight - 2;
    return [
      Math.min(Math.max(point[0], 2), maxX),
      Math.min(Math.max(point[1], 2), maxY)
    ];
  }

  function initHandles() {
    document.querySelectorAll(".corner-handle").forEach(function (handle) {
      var idx = parseInt(handle.getAttribute("data-corner"), 10);

      handle.addEventListener("pointerdown", function (event) {
        if (!state.manualMode || !state.draftCorners) return;
        event.preventDefault();
        handle.setPointerCapture(event.pointerId);
        handle.classList.add("is-dragging");
      });

      handle.addEventListener("pointermove", function (event) {
        if (!handle.classList.contains("is-dragging") || !state.draftCorners) return;
        var frame = $("source-frame").getBoundingClientRect();
        var displayPoint = [event.clientX - frame.left, event.clientY - frame.top];
        state.draftCorners[idx] = clampDraft(displayPoint);
        renderOverlay();
        markDraftDirty();
      });

      function release(event) {
        if (!handle.classList.contains("is-dragging")) return;
        try { handle.releasePointerCapture(event.pointerId); } catch (e) {}
        handle.classList.remove("is-dragging");
      }
      handle.addEventListener("pointerup", release);
      handle.addEventListener("pointercancel", release);

      handle.addEventListener("keydown", function (event) {
        if (!state.manualMode || !state.draftCorners) return;
        var step = event.shiftKey ? 10 : 2;
        var delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
        if (!delta) return;
        event.preventDefault();
        var cur = state.draftCorners[idx];
        state.draftCorners[idx] = clampDraft([cur[0] + delta[0], cur[1] + delta[1]]);
        renderOverlay();
        markDraftDirty();
      });
    });
  }

  var markDraftDirty = function () {
    var apply = $("btn-apply-crop");
    if (apply) apply.disabled = false;
  };

  function renderSourceMeta() {
    var meta = $("source-meta");
    if (!meta) return;
    if (!state.scanId) { meta.textContent = "No image loaded"; return; }
    meta.textContent = state.origW + " × " + state.origH + " px";
  }

  function loadImageSrc(url, onLoad) {
    var img = sourceImage();
    if (!img) { if (onLoad) onLoad(false); return; }
    loading.image = true; updateSpinners();
    imageLoaded = false;
    img.onload = function () {
      loading.image = false; updateSpinners();
      imageLoaded = true;
      renderOverlay();
      renderSourceMeta();
      var before = $("before-image");
      if (before) { before.src = img.src; var bd = $("before-dims"); if (bd) bd.textContent = state.origW + "×" + state.origH; }
      if (onLoad) onLoad(true);
    };
    img.onerror = function () {
      loading.image = false; updateSpinners();
      showMessages(["Could not display the image. Please try uploading again."]);
      if (onLoad) onLoad(false);
    };
    img.src = url;
  }

  function setDetectFeedback(message, level) {
    var el = $("detect-feedback");
    if (!el) return;
    el.textContent = message || "";
    el.className = "panel-note" + (level === "error" ? " is-error" : level === "ok" ? " is-ok" : "");
  }

  function renderQuality(report) {
    var list = $("quality-list");
    if (!list || !report) return;
    list.innerHTML = "";
    var items = (report.assessments || []).map(function (item) {
      var li = document.createElement("li");
      li.className = "q-" + (item.level || "warn");
      li.textContent = item.message;
      return li;
    });
    var m = report.metrics || {};
    if (m.width) {
      var li = document.createElement("li");
      li.className = "";
      li.textContent = m.width + "×" + m.height + " px";
      items.push(li);
    }
    items.forEach(function (li) { list.appendChild(li); });
  }

  function collectParams() {
    state.params.brightness = parseFloat($("inp-brightness").value);
    state.params.contrast = parseFloat($("inp-contrast").value);
    state.params.sharpness = parseFloat($("inp-sharpness").value);
    state.params.denoise = $("inp-denoise").checked;
    state.params.clahe = $("inp-clahe").checked;
    return state.params;
  }

  function renderParamOutputs() {
    $("out-brightness").textContent = parseFloat($("inp-brightness").value).toFixed(2);
    $("out-contrast").textContent = parseFloat($("inp-contrast").value).toFixed(2);
    $("out-sharpness").textContent = parseFloat($("inp-sharpness").value).toFixed(2);
  }

  function syncFilterChips() {
    document.querySelectorAll(".filter-chip").forEach(function (chip) {
      var selected = chip.getAttribute("data-filter") === state.filter;
      chip.classList.toggle("is-selected", selected);
      chip.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }

  function syncRestoreChips() {
    document.querySelectorAll(".restore-chip").forEach(function (chip) {
      var selected = chip.getAttribute("data-preset") === state.restorePreset;
      chip.classList.toggle("is-selected", selected);
      chip.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    var out = $("out-restore-strength");
    if (out) out.textContent = Math.round(state.restoreStrength * 100) + "%";
  }

  function process() {
    if (!state.scanId) return Promise.resolve();
    if (loading.process) return Promise.resolve();
    var token = ++state.processToken;
    loading.process = true; updateSpinners();
    setStatusChip("Enhancing image…");
    clearMessages();

    var payload = {
      corners: state.perspective && state.corners ? state.corners : null,
      perspective: state.perspective && !!state.corners,
      filter: state.filter,
      params: state.params
    };

    return apiPost(apiUrl(config.processUrlTemplate, state.scanId), payload)
      .then(function (data) {
        if (token !== state.processToken) return;
        var after = $("after-image");
        if (after) {
          after.onload = function () {
            setStatusChip("Scan completed", "ok");
            var status = $("compare-status");
            if (status) { status.hidden = false; status.textContent = "Scan completed"; status.className = "chip chip-ok"; }
          };
          after.onerror = function () {
            showMessages(["The processed image could not be loaded. Please try again."]);
            setStatusChip("Could not load result", "bad");
          };
          after.src = data.processed_url;
        }
        var ad = $("after-dims"); if (ad) ad.textContent = data.width + "×" + data.height;
        refreshPages();
      })
      .catch(function (error) {
        if (token !== state.processToken) return;
        if (error.name === "AbortError") {
          setStatusChip("Processing timed out", "bad");
          showMessages(["Processing took too long. Please try a smaller image or simpler settings."]);
        } else {
          setStatusChip("Processing failed", "bad");
          showMessages([error.message || "Could not process the image. Please try again."]);
        }
      })
      .finally(function () {
        if (token !== state.processToken) return;
        loading.process = false; updateSpinners();
      });
  }

  var debouncedProcess = SDS.debounce(function () { process(); }, 420);

  function autoDetect(thenProcess) {
    if (!state.scanId) return Promise.resolve();
    if (loading.detect) return Promise.resolve();
    loading.detect = true; updateSpinners();
    setDetectFeedback("Detecting document…");
    clearMessages();

    return apiPost(apiUrl(config.detectUrlTemplate, state.scanId), {})
      .then(function (data) {
        loading.detect = false; updateSpinners();
        if (data.detected && data.corners) {
          state.corners = data.corners;
          state.draftCorners = data.corners.map(function (p) { return p.slice(); });
          state.perspective = true;
          setDetectFeedback("Document detected.", "ok");
          setStatusChip("Document detected", "ok");
        } else {
          fullImageCorners();
          setDetectFeedback("No document boundary found — using the full image. Adjust corners manually for a tighter crop.", "error");
          setStatusChip("Adjust corners manually", "warn");
        }
        renderOverlay();
        if (thenProcess !== false) return process();
      })
      .catch(function (error) {
        loading.detect = false; updateSpinners();
        fullImageCorners();
        renderOverlay();
        if (error.name === "AbortError") {
          setDetectFeedback("Detection timed out. Using the full image.", "error");
        } else {
          setDetectFeedback("Could not detect the document — using the full image.", "error");
        }
        if (thenProcess !== false) return process();
      })
      .finally(function () {
        loading.detect = false; updateSpinners();
      });
  }

  function fullImageCorners() {
    state.corners = [
      [0, 0],
      [state.origW - 1, 0],
      [state.origW - 1, state.origH - 1],
      [0, state.origH - 1]
    ];
    state.draftCorners = state.corners.map(function (p) { return p.slice(); });
    state.perspective = false;
  }

  function setManualMode(enabled) {
    state.manualMode = enabled;
    var frame = $("source-frame");
    var button = $("btn-manual");
    var hint = $("stage-hint");
    if (!frame || !button) return;
    frame.classList.toggle("manual-mode", enabled);
    button.setAttribute("aria-pressed", enabled ? "true" : "false");
    button.classList.toggle("is-selected", enabled);
    button.textContent = enabled ? "Done Adjusting" : "Adjust Corners";
    if (hint) hint.hidden = !enabled;
    if (enabled) {
      if (!state.corners) fullImageCorners();
      state.draftCorners = state.corners.map(function (p) { return p.slice(); });
      state.perspective = true;
      $("btn-apply-crop").disabled = true;
      renderOverlay();
    } else {
      state.draftCorners = state.corners ? state.corners.map(function (p) { return p.slice(); }) : null;
      renderOverlay();
    }
  }

  function applyCrop() {
    if (!state.draftCorners) return;
    state.corners = state.draftCorners.map(function (p) { return p.slice(); });
    state.perspective = true;
    $("btn-apply-crop").disabled = true;
    $("btn-reset-crop").disabled = false;
    setManualMode(false);
    process();
  }

  function resetCrop() {
    $("btn-reset-crop").disabled = true;
    autoDetect(true);
  }

  function activateScan(payload) {
    if (!payload || !payload.id) return;
    var token = ++state.activateToken;
    abortActive();
    loading.image = false; loading.detect = false; loading.process = false; loading.enhance = false;
    updateSpinners();

    state.scanId = payload.id;
    state.filename = payload.filename || "";
    state.origW = payload.width;
    state.origH = payload.height;
    state.filter = payload.filter || "original";
    state.corners = payload.corners || null;
    state.draftCorners = state.corners ? state.corners.map(function (p) { return p.slice(); }) : null;
    state.perspective = !!state.corners;
    state.manualMode = false;

    var stored = payload.params || {};
    $("inp-brightness").value = stored.brightness != null ? stored.brightness : 1;
    $("inp-contrast").value = stored.contrast != null ? stored.contrast : 1;
    $("inp-sharpness").value = stored.sharpness != null ? stored.sharpness : 0;
    $("inp-denoise").checked = !!stored.denoise;
    $("inp-clahe").checked = stored.clahe === undefined ? true : !!stored.clahe;
    renderParamOutputs();
    syncFilterChips();
    setManualMode(false);
    clearMessages();
    setDetectFeedback("");

    $("workspace").hidden = false;
    $("empty-state").hidden = true;
    var enhancePanel = $("enhance-panel");
    if (enhancePanel) enhancePanel.hidden = false;
    var enhanceBefore = $("enhance-before");
    if (enhanceBefore) enhanceBefore.removeAttribute("src");
    var enhanceAfter = $("enhance-after");
    if (enhanceAfter) enhanceAfter.removeAttribute("src");
    var enhanceStatus = $("enhance-status");
    if (enhanceStatus) { enhanceStatus.hidden = true; enhanceStatus.textContent = ""; }

    if (payload.has_processed && payload.processed_url) {
      var after = $("after-image");
      if (after) {
        after.src = payload.processed_url;
        after.onload = function () {
          if (token !== state.activateToken) return;
          setStatusChip("Scan completed", "ok");
          var status = $("compare-status");
          if (status) { status.hidden = false; status.textContent = "Scan completed"; status.className = "chip chip-ok"; }
        };
      }
      var ad = $("after-dims");
      if (ad) ad.textContent = (payload.processed_width || "") + (payload.processed_width ? "×" + payload.processed_height : "");
      var cs = $("compare-status");
      if (cs) { cs.hidden = false; cs.textContent = "Previously processed"; cs.className = "chip"; }
      setStatusChip("Loaded from history", "ok");
    } else {
      var a2 = $("after-image");
      if (a2) a2.removeAttribute("src");
      var ad2 = $("after-dims");
      if (ad2) ad2.textContent = "";
      var cs2 = $("compare-status");
      if (cs2) cs2.hidden = true;
    }

    renderSourceMeta();
    if (payload.quality) renderQuality(payload.quality);
    refreshPages();

    loadImageSrc(payload.original_url, function (ok) {
      if (token !== state.activateToken) return;
      if (!ok) return;
      if (payload.corners && payload.corners.length === 4) {
        renderOverlay();
        if (!payload.has_processed) process();
        else setStatusChip("Scan completed", "ok");
      } else {
        autoDetect(!payload.has_processed);
      }
    });
  }

  function uploadFile(file) {
    if (loading.upload) return;
    var errorEl = $("scanner-upload-error");
    function fail(message) {
      if (errorEl) { errorEl.textContent = message; errorEl.hidden = false; }
      else showMessages([message]);
    }
    var error = SDS.validateFile(file, config.maxUploadBytes);
    if (error) { fail(error); return; }
    if (errorEl) errorEl.hidden = true;

    loading.upload = true; updateSpinners();
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
        loading.upload = false; updateSpinners();
        renderQuality(data.quality);
        activateScan(data);
      })
      .catch(function (error) {
        loading.upload = false; updateSpinners();
        setStatusChip("", "");
        if (error.name === "AbortError") fail("Upload timed out. Please try again.");
        else fail(error.message || "Upload failed. Please try again.");
      })
      .finally(function () { req.done(); });
  }

  function deleteCurrentScan() {
    if (!state.scanId || !window.confirm("Remove this page and its files?")) return;
    apiPost(apiUrl(config.deleteUrlTemplate, state.scanId), {})
      .then(function () {
        var remaining = state.pages.filter(function (p) { return p.id !== state.scanId; });
        state.scanId = null;
        if (remaining.length) {
          activateScan(remaining[0]);
        } else {
          $("workspace").hidden = true;
          $("empty-state").hidden = false;
          var enhancePanel = $("enhance-panel");
          if (enhancePanel) enhancePanel.hidden = true;
          setDetectFeedback("");
          setStatusChip("", "");
        }
      })
      .catch(function (error) { showMessages([error.message]); });
  }

  function refreshPages() {
    return fetch(config.pagesUrl, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin"
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        state.pages = data.pages;
        renderPages();
      })
      .catch(function () {});
  }

  function renderPages() {
    var strip = $("pages-strip");
    if (!strip) return;
    strip.innerHTML = "";
    $("pages-count").textContent = state.pages.length ? "· " + state.pages.length : "";

    state.pages.forEach(function (page, index) {
      var li = document.createElement("li");
      li.className = "page-thumb" + (page.id === state.scanId ? " is-active" : "");
      li.setAttribute("aria-label", "Page " + (index + 1) + ": " + page.filename);

      var main = document.createElement("button");
      main.type = "button";
      main.className = "page-thumb-main";
      main.setAttribute("aria-label", "Open page " + (index + 1));
      main.innerHTML = '<span class="page-order-badge">' + (index + 1) + "</span>";
      var img = document.createElement("img");
      img.src = page.thumb_url;
      img.alt = "";
      img.loading = "lazy";
      main.appendChild(img);
      main.addEventListener("click", function () { activateScan(page); });
      li.appendChild(main);

      var actions = document.createElement("div");
      actions.className = "page-thumb-actions";

      var left = document.createElement("button");
      left.type = "button";
      left.textContent = "←";
      left.title = "Move earlier";
      left.setAttribute("aria-label", "Move page " + (index + 1) + " earlier");
      left.disabled = index === 0;
      left.addEventListener("click", function () { movePage(index, index - 1); });

      var right = document.createElement("button");
      right.type = "button";
      right.textContent = "→";
      right.title = "Move later";
      right.setAttribute("aria-label", "Move page " + (index + 1) + " later");
      right.disabled = index === state.pages.length - 1;
      right.addEventListener("click", function () { movePage(index, index + 1); });

      var del = document.createElement("button");
      del.type = "button";
      del.textContent = "✕";
      del.className = "danger";
      del.title = "Delete page";
      del.setAttribute("aria-label", "Delete page " + (index + 1));
      del.addEventListener("click", function () {
        if (!window.confirm("Delete page " + (index + 1) + "?")) return;
        apiPost(apiUrl(config.deleteUrlTemplate, page.id), {})
          .then(function () {
            if (page.id === state.scanId) {
              var remaining = state.pages.filter(function (p) { return p.id !== page.id; });
              state.scanId = null;
              if (remaining.length) activateScan(remaining[0]);
              else {
                $("workspace").hidden = true;
                $("empty-state").hidden = false;
              }
            }
            refreshPages();
          })
          .catch(function (error) { showMessages([error.message]); });
      });

      actions.appendChild(left);
      actions.appendChild(right);
      actions.appendChild(del);
      li.appendChild(actions);
      strip.appendChild(li);
    });
  }

  function movePage(from, to) {
    if (to < 0 || to >= state.pages.length) return;
    var order = state.pages.map(function (p) { return p.id; });
    var moved = order.splice(from, 1)[0];
    order.splice(to, 0, moved);
    apiPost(config.reorderUrl, { order: order })
      .then(refreshPages)
      .catch(function (error) { showMessages([error.message]); });
  }

  function exportScan() {
    if (!state.scanId || loading.process) return;
    var button = $("btn-download");
    button.disabled = true;
    var originalLabel = button.textContent;
    button.textContent = "Preparing…";

    var payload = {
      format: state.exportFormat,
      corners: state.perspective && state.corners ? state.corners : null,
      perspective: state.perspective && !!state.corners,
      filter: state.filter,
      params: state.params
    };

    apiPostBlob(apiUrl(config.exportUrlTemplate, state.scanId), payload)
      .then(function (result) {
        var name = filenameFromDisposition(result.header, "smart-scan." + state.exportFormat);
        downloadBlob(result.blob, name);
        refreshPages();
      })
      .catch(function (error) {
        if (error.name === "AbortError") showMessages(["Export timed out. Please try a smaller image."]);
        else showMessages([error.message || "Export failed. Please try again."]);
      })
      .finally(function () {
        button.disabled = false;
        button.textContent = originalLabel;
      });
  }

  function exportPdfAll() {
    if (!state.pages.length) {
      showMessages(["Upload at least one page first."], true);
      return;
    }
    var button = $("btn-pdf-all");
    button.disabled = true;
    var originalLabel = button.textContent;
    button.textContent = "Building PDF…";

    var ids = state.pages.map(function (p) { return p.id; });
    apiPostBlob(config.pdfMultiUrl, { scan_ids: ids })
      .then(function (result) {
        downloadBlob(result.blob, filenameFromDisposition(result.header, "smart-scan-pages.pdf"));
      })
      .catch(function (error) {
        if (error.name === "AbortError") showMessages(["PDF export timed out. Try fewer pages."]);
        else showMessages([error.message || "PDF export failed. Please try again."]);
      })
      .finally(function () {
        button.disabled = false;
        button.textContent = originalLabel;
      });
  }

  function enhanceOldImage() {
    if (!state.scanId || loading.enhance) return;
    if (!imageLoaded) {
      showMessages(["Please wait for the image to load before enhancing."], true);
      return;
    }
    var button = $("btn-enhance");
    var dlButton = $("btn-enhance-download");
    var originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = "Enhancing…";
    loading.enhance = true; updateSpinners();
    clearMessages();
    var status = $("enhance-status");
    if (status) { status.hidden = false; status.textContent = "Enhancing image…"; status.className = "chip"; }

    var compare = $("enhance-compare");
    var before = $("enhance-before");
    var after = $("enhance-after");
    if (before) before.src = sourceImage().src;
    if (after) after.removeAttribute("src");
    if (compare) compare.hidden = false;

    var payload = {
      preset: state.restorePreset,
      strength: state.restoreStrength,
      format: state.restoreFormat
    };

    apiPostBlob(apiUrl(config.enhanceUrlTemplate, state.scanId), payload)
      .then(function (result) {
        var url = URL.createObjectURL(result.blob);
        if (after) {
          after.onload = function () {
            if (status) { status.textContent = "Enhancement completed"; status.className = "chip chip-ok"; }
          };
          after.onerror = function () {
            if (status) { status.textContent = "Could not display result"; status.className = "chip chip-bad"; }
          };
          after.src = url;
        }
        if (dlButton) {
          dlButton.disabled = false;
          dlButton.onclick = function () {
            var name = filenameFromDisposition(result.header, "enhanced-" + state.restorePreset + "." + state.restoreFormat);
            downloadBlob(result.blob, name);
          };
        }
      })
      .catch(function (error) {
        if (after) after.removeAttribute("src");
        if (status) { status.textContent = "Enhancement failed"; status.className = "chip chip-bad"; }
        if (dlButton) dlButton.disabled = true;
        if (error.name === "AbortError") showMessages(["Enhancement timed out. Try a smaller image or lower strength."]);
        else showMessages([error.message || "Enhancement failed. Please try again."]);
      })
      .finally(function () {
        loading.enhance = false; updateSpinners();
        button.disabled = false;
        button.textContent = originalLabel;
      });
  }

  function bindControls() {

    ["inp-brightness", "inp-contrast", "inp-sharpness"].forEach(function (id) {
      $(id).addEventListener("input", function () {
        renderParamOutputs();
        collectParams();
        debouncedProcess();
      });
    });
    ["inp-denoise", "inp-clahe"].forEach(function (id) {
      $(id).addEventListener("change", function () {
        collectParams();
        debouncedProcess();
      });
    });

    $("btn-reset-enh").addEventListener("click", function () {
      $("inp-brightness").value = 1;
      $("inp-contrast").value = 1;
      $("inp-sharpness").value = 0;
      $("inp-denoise").checked = false;
      $("inp-clahe").checked = true;
      renderParamOutputs();
      collectParams();
      process();
    });

    document.querySelectorAll(".filter-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        state.filter = chip.getAttribute("data-filter");
        syncFilterChips();
        process();
      });
    });

    $("btn-detect").addEventListener("click", function () { autoDetect(true); });
    $("btn-manual").addEventListener("click", function () { setManualMode(!state.manualMode); });
    $("btn-apply-crop").addEventListener("click", applyCrop);
    $("btn-reset-crop").addEventListener("click", resetCrop);
    $("btn-full-image").addEventListener("click", function () {
      fullImageCorners();
      renderOverlay();
      process();
    });
    $("btn-remove").addEventListener("click", deleteCurrentScan);

    document.querySelectorAll(".format-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.exportFormat = btn.getAttribute("data-format");
        document.querySelectorAll(".format-btn").forEach(function (b) {
          var selected = b === btn;
          b.classList.toggle("is-selected", selected);
          b.setAttribute("aria-pressed", selected ? "true" : "false");
        });
      });
    });
    $("btn-download").addEventListener("click", exportScan);
    $("btn-pdf-all").addEventListener("click", exportPdfAll);

    document.querySelectorAll(".restore-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        state.restorePreset = chip.getAttribute("data-preset");
        syncRestoreChips();
      });
    });
    var strength = $("inp-restore-strength");
    if (strength) {
      strength.addEventListener("input", function () {
        state.restoreStrength = parseFloat(strength.value);
        syncRestoreChips();
      });
    }
    document.querySelectorAll(".restore-format-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.restoreFormat = btn.getAttribute("data-format");
        document.querySelectorAll(".restore-format-btn").forEach(function (b) {
          var selected = b === btn;
          b.classList.toggle("is-selected", selected);
          b.setAttribute("aria-pressed", selected ? "true" : "false");
        });
      });
    });
    $("btn-enhance").addEventListener("click", enhanceOldImage);

    var pageInput = $("page-file-input");
    $("btn-add-page").addEventListener("click", function () { pageInput.click(); });
    pageInput.addEventListener("change", function () {
      if (pageInput.files && pageInput.files.length) uploadFile(pageInput.files[0]);
      pageInput.value = "";
    });

    window.SDS.initDropzone($("scanner-dropzone"), $("scanner-file-input"), {
      maxBytes: config.maxUploadBytes,
      onFile: uploadFile
    });

    document.addEventListener("paste", function (event) {
      var items = event.clipboardData && event.clipboardData.files;
      if (items && items.length && !state.scanId) uploadFile(items[0]);
    });

    window.addEventListener("resize", SDS.debounce(renderOverlay, 120));
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!$("scanner-root")) return;
    config = jsonEl("scanner-config", {});
    var pages = jsonEl("initial-pages", []);
    var initial = jsonEl("initial-scan", null);

    state.pages = pages || [];
    bindControls();
    initHandles();
    renderParamOutputs();
    syncRestoreChips();
    renderPages();

    if (initial && initial.id) {
      activateScan(initial);
    } else {
      $("workspace").hidden = true;
      $("empty-state").hidden = false;
      var enhancePanel = $("enhance-panel");
      if (enhancePanel) enhancePanel.hidden = true;
    }
  });
})();
