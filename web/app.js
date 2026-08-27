"use strict";

const STORAGE_KEYS = Object.freeze({
  token: "nutriliteConverter.v1.accessToken",
  profile: "nutriliteConverter.v1.profile",
  supplements: "nutriliteConverter.v1.supplements",
  results: "nutriliteConverter.v1.results"
});

const DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_ORIGINAL_IMAGE_BYTES = 30 * 1024 * 1024;
const MAX_IMAGE_DIMENSION = 1600;
const MAX_RESULTS = 20;
const MAX_SUPPLEMENTS = 100;
const EXPORT_FORMAT = "nutrilite-converter-local-backup";
const EXPORT_VERSION = 1;

const state = {
  config: {
    analysis_available: false,
    max_image_bytes: DEFAULT_MAX_IMAGE_BYTES,
    app_version: ""
  },
  selectedFile: null,
  previewUrl: "",
  profile: {},
  supplements: [],
  results: [],
  currentResult: null,
  currentResultKind: "analysis",
  toastTimer: null
};

const dom = {};

function cacheDom() {
  const ids = [
    "service-status", "service-status-text", "cabinet-count", "analysis-form",
    "product-photo", "photo-preview-wrap", "photo-preview", "photo-name", "photo-size",
    "remove-photo", "product-url", "product-text", "analysis-consent", "analysis-profile",
    "analyze-button", "analysis-status", "result-panel", "result-title", "result-meta",
    "save-product", "download-report", "product-block", "product-result",
    "recommendations-block", "recommendations-result", "gaps-block", "gaps-result",
    "warnings-block", "warnings-result", "source-block", "source-result",
    "result-disclaimer", "cabinet-empty", "cabinet-content", "supplement-list",
    "compare-consent", "compare-profile", "compare-button", "compare-status",
    "history-list", "history-empty", "clear-history", "profile-form", "profile-age",
    "profile-demographic", "profile-life-stage", "profile-goals", "profile-diet",
    "profile-saved", "access-token", "save-token", "remove-token", "token-status",
    "export-data", "export-data-top", "import-data", "clear-data", "data-status",
    "app-version", "toast"
  ];
  ids.forEach((id) => {
    dom[id] = document.getElementById(id);
  });
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function storageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (_error) {
    return null;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch (_error) {
    showToast("This browser could not save the data. Private browsing or storage limits may be blocking it.");
    return false;
  }
}

function storageRemove(key) {
  try {
    window.localStorage.removeItem(key);
  } catch (_error) {
    // The in-memory state is still cleared even if browser storage is unavailable.
  }
}

function loadJson(key, fallback) {
  const raw = storageGet(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch (_error) {
    storageRemove(key);
    return fallback;
  }
}

function persistState() {
  storageSet(STORAGE_KEYS.profile, JSON.stringify(state.profile));
  storageSet(STORAGE_KEYS.supplements, JSON.stringify(state.supplements));
  storageSet(STORAGE_KEYS.results, JSON.stringify(state.results));
}

function loadState() {
  const profile = loadJson(STORAGE_KEYS.profile, {});
  const supplements = loadJson(STORAGE_KEYS.supplements, []);
  const results = loadJson(STORAGE_KEYS.results, []);
  state.profile = isPlainObject(profile) ? sanitizeForStorage(profile) : {};
  state.supplements = Array.isArray(supplements)
    ? supplements.filter(validSupplementEntry).slice(0, MAX_SUPPLEMENTS).map(sanitizeForStorage)
    : [];
  state.results = Array.isArray(results)
    ? results.filter(validResultEntry).slice(0, MAX_RESULTS).map(sanitizeForStorage)
    : [];
}

function sanitizeForStorage(value, depth = 0, keyName = "") {
  if (depth > 8 || value === undefined || typeof value === "function") return undefined;
  if (/token|password|secret/i.test(keyName)) return undefined;
  if (/image|photo|thumbnail/i.test(keyName)) return undefined;
  if (typeof value === "string") {
    if (value.startsWith("data:image/")) return undefined;
    return value.slice(0, 30000);
  }
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (typeof value === "boolean" || value === null) return value;
  if (Array.isArray(value)) {
    return value.slice(0, 200).map((item) => sanitizeForStorage(item, depth + 1, keyName))
      .filter((item) => item !== undefined);
  }
  if (isPlainObject(value)) {
    const output = {};
    Object.entries(value).slice(0, 100).forEach(([key, item]) => {
      const clean = sanitizeForStorage(item, depth + 1, key);
      if (clean !== undefined) output[key] = clean;
    });
    return output;
  }
  return String(value).slice(0, 30000);
}

function validSupplementEntry(entry) {
  return isPlainObject(entry) && isPlainObject(entry.product);
}

function validResultEntry(entry) {
  return isPlainObject(entry) && isPlainObject(entry.data);
}

function newId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function showToast(message) {
  if (!dom.toast) return;
  window.clearTimeout(state.toastTimer);
  dom.toast.textContent = message;
  dom.toast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    dom.toast.hidden = true;
  }, 4200);
}

function setFormStatus(element, message, status = "") {
  element.textContent = message;
  if (status) element.dataset.state = status;
  else delete element.dataset.state;
}

function consumeInviteToken() {
  if (!window.location.hash) return false;
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = (params.get("invite") || "").trim();
  if (!token) return false;
  if (token.length <= 4096) storageSet(STORAGE_KEYS.token, token);
  window.history.replaceState(null, document.title, `${window.location.pathname}${window.location.search}`);
  return token.length <= 4096;
}

function initializeTablists() {
  document.querySelectorAll('[role="tablist"]').forEach((tablist) => {
    const tabs = Array.from(tablist.querySelectorAll(':scope > [role="tab"]'));
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activateTab(tab));
      tab.addEventListener("keydown", (event) => {
        let targetIndex = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") targetIndex = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") targetIndex = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") targetIndex = 0;
        if (event.key === "End") targetIndex = tabs.length - 1;
        if (targetIndex === null) return;
        event.preventDefault();
        activateTab(tabs[targetIndex], true);
      });
    });
  });

  document.querySelectorAll("[data-open-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = document.getElementById(button.dataset.openTab);
      if (tab) activateTab(tab, true);
    });
  });
}

function activateTab(tab, moveFocus = false) {
  const tablist = tab.closest('[role="tablist"]');
  if (!tablist) return;
  tablist.querySelectorAll(':scope > [role="tab"]').forEach((candidate) => {
    const active = candidate === tab;
    candidate.setAttribute("aria-selected", String(active));
    candidate.tabIndex = active ? 0 : -1;
    const panel = document.getElementById(candidate.getAttribute("aria-controls"));
    if (panel) panel.hidden = !active;
  });
  if (moveFocus) tab.focus();
}

function selectedSourceType() {
  const selected = document.querySelector('.source-tabs [role="tab"][aria-selected="true"]');
  if (!selected) return "image";
  const controls = selected.getAttribute("aria-controls");
  return controls === "url-panel" ? "url" : controls === "text-panel" ? "text" : "image";
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 1) return "0 bytes";
  const units = ["bytes", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / (1024 ** index);
  return `${value.toFixed(index === 0 ? 0 : value >= 10 ? 1 : 2)} ${units[index]}`;
}

function handlePhotoSelection() {
  const file = dom["product-photo"].files && dom["product-photo"].files[0];
  clearSelectedPhoto();
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    setFormStatus(dom["analysis-status"], "Please select an image file.", "error");
    return;
  }
  if (file.size > MAX_ORIGINAL_IMAGE_BYTES) {
    setFormStatus(dom["analysis-status"], `That original image is larger than ${formatBytes(MAX_ORIGINAL_IMAGE_BYTES)}. Choose a smaller photo.`, "error");
    return;
  }
  state.selectedFile = file;
  state.previewUrl = URL.createObjectURL(file);
  dom["photo-preview"].src = state.previewUrl;
  dom["photo-name"].textContent = file.name || "Selected photo";
  dom["photo-size"].textContent = formatBytes(file.size);
  dom["photo-preview-wrap"].hidden = false;
  setFormStatus(dom["analysis-status"], "Photo selected. It will be resized before it is sent.");
}

function clearSelectedPhoto() {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = "";
  state.selectedFile = null;
  if (dom["product-photo"]) dom["product-photo"].value = "";
  if (dom["photo-preview"]) dom["photo-preview"].removeAttribute("src");
  if (dom["photo-preview-wrap"]) dom["photo-preview-wrap"].hidden = true;
}

async function decodeImage(file) {
  if (typeof window.createImageBitmap === "function") {
    try {
      return await window.createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (_error) {
      // Fall through for browsers or image formats that need the HTML image decoder.
    }
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("This browser could not read the selected image. Try a JPEG or PNG photo."));
    };
    image.src = url;
  });
}

function canvasToBlob(canvas, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("The photo could not be prepared for analysis."));
    }, "image/jpeg", quality);
  });
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("The prepared photo could not be read."));
    reader.readAsDataURL(blob);
  });
}

async function resizeImage(file, byteLimit) {
  const image = await decodeImage(file);
  const originalWidth = image.naturalWidth || image.width;
  const originalHeight = image.naturalHeight || image.height;
  if (!originalWidth || !originalHeight) throw new Error("The selected image has no readable dimensions.");

  const initialScale = Math.min(1, MAX_IMAGE_DIMENSION / Math.max(originalWidth, originalHeight));
  let width = Math.max(1, Math.round(originalWidth * initialScale));
  let height = Math.max(1, Math.round(originalHeight * initialScale));
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("This browser cannot prepare photos for analysis.");

  let blob = null;
  const qualities = [0.84, 0.74, 0.64, 0.54, 0.44];
  for (let scaleAttempt = 0; scaleAttempt < 4; scaleAttempt += 1) {
    canvas.width = width;
    canvas.height = height;
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
    for (const quality of qualities) {
      blob = await canvasToBlob(canvas, quality);
      if (blob.size <= byteLimit) break;
    }
    if (blob && blob.size <= byteLimit) break;
    const reduction = Math.min(0.82, Math.sqrt(byteLimit / blob.size) * 0.9);
    width = Math.max(1, Math.round(width * reduction));
    height = Math.max(1, Math.round(height * reduction));
  }

  if (typeof image.close === "function") image.close();
  canvas.width = 1;
  canvas.height = 1;
  if (!blob || blob.size > byteLimit) {
    throw new Error(`The photo is still larger than the app limit of ${formatBytes(byteLimit)} after resizing.`);
  }
  return blobToDataUrl(blob);
}

function accessToken() {
  return (storageGet(STORAGE_KEYS.token) || "").trim();
}

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  const token = accessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body) headers.set("Content-Type", "application/json");

  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120000);
  let response;
  try {
    response = await fetch(path, { ...options, headers, signal: controller.signal });
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The request took too long. Please try again.");
    throw new Error(navigator.onLine ? "The service could not be reached. Please try again." : "You are offline. Reconnect to run an analysis.");
  } finally {
    window.clearTimeout(timeout);
  }

  let payload = null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
  }
  if (!response.ok) {
    let detail = isPlainObject(payload) ? payload.message || payload.detail || payload.error : "";
    if (isPlainObject(detail)) detail = detail.message || detail.code || "";
    if (response.status === 401 || response.status === 403) {
      throw new Error("This invite token is missing, expired, or not authorized.");
    }
    throw new Error(typeof detail === "string" && detail ? detail : `The service returned an error (${response.status}).`);
  }
  if (!isPlainObject(payload)) throw new Error("The service returned an unreadable response.");
  return payload;
}

async function loadConfig() {
  dom["service-status"].dataset.state = "loading";
  dom["service-status-text"].textContent = "Connecting…";
  try {
    const config = await apiRequest("/api/config");
    const maxBytes = Number(config.max_image_bytes);
    state.config = {
      analysis_available: config.analysis_available === true,
      max_image_bytes: Number.isFinite(maxBytes) && maxBytes >= 100000 && maxBytes <= 30 * 1024 * 1024
        ? Math.floor(maxBytes)
        : DEFAULT_MAX_IMAGE_BYTES,
      app_version: typeof config.app_version === "string" ? config.app_version : ""
    };
    setServiceState(
      state.config.analysis_available
        ? (accessToken() ? "ready" : "locked")
        : "paused"
    );
  } catch (_error) {
    state.config.analysis_available = false;
    setServiceState(navigator.onLine ? "paused" : "offline");
  }
  updateAnalysisAvailability();
  dom["app-version"].textContent = state.config.app_version
    ? `App version ${state.config.app_version}`
    : "Version unavailable";
}

function setServiceState(serviceState) {
  dom["service-status"].dataset.state = serviceState;
  const labels = {
    ready: "Ready",
    locked: "Invite needed",
    paused: "Analysis paused",
    offline: "Offline",
    loading: "Connecting…"
  };
  dom["service-status-text"].textContent = labels[serviceState] || labels.loading;
}

function updateAnalysisAvailability() {
  const available = state.config.analysis_available && Boolean(accessToken());
  dom["analyze-button"].disabled = !available;
  dom["compare-button"].disabled = !available || state.supplements.length === 0;
  if (!available && !dom["analysis-status"].textContent) {
    let message = "Analysis is temporarily unavailable. Your locally saved data remains accessible.";
    if (!navigator.onLine) message = "The app shell is available offline. Reconnect to analyze a product.";
    else if (state.config.analysis_available && !accessToken()) message = "Open your private invite link or add an access token under Profile & data.";
    setFormStatus(dom["analysis-status"], message);
  }
}

function compactProfile() {
  const clean = sanitizeForStorage(state.profile);
  return isPlainObject(clean) ? clean : {};
}

function profileHasData() {
  return Object.values(compactProfile()).some((value) => value !== "" && value !== null && value !== undefined);
}

function updateProfileOptions() {
  const available = profileHasData();
  [dom["analysis-profile"], dom["compare-profile"]].forEach((checkbox) => {
    checkbox.disabled = !available;
    if (!available) checkbox.checked = false;
  });
}

async function handleAnalysisSubmit(event) {
  event.preventDefault();
  if (!state.config.analysis_available) {
    setFormStatus(dom["analysis-status"], "Analysis is not available right now.", "error");
    return;
  }
  if (!dom["analysis-consent"].checked) {
    setFormStatus(dom["analysis-status"], "Please review and accept the analysis consent first.", "error");
    dom["analysis-consent"].focus();
    return;
  }

  const sourceType = selectedSourceType();
  const payload = {
    source_type: sourceType,
    include_profile: dom["analysis-profile"].checked,
    profile: dom["analysis-profile"].checked ? compactProfile() : {}
  };

  try {
    setBusy(dom["analyze-button"], true, sourceType === "image" ? "Preparing photo…" : "Analyzing…");
    setFormStatus(dom["analysis-status"], sourceType === "image" ? "Preparing the photo securely in your browser…" : "Reviewing product information…");
    if (sourceType === "image") {
      if (!state.selectedFile) throw new Error("Take or choose a label photo first.");
      payload.image_data_url = await resizeImage(state.selectedFile, state.config.max_image_bytes);
      setFormStatus(dom["analysis-status"], "Photo prepared. Analyzing the label…");
    } else if (sourceType === "url") {
      const rawUrl = dom["product-url"].value.trim();
      let parsed;
      try {
        parsed = new URL(rawUrl);
      } catch (_error) {
        throw new Error("Enter a complete public product URL, including https://.");
      }
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error("Only HTTP or HTTPS product URLs can be analyzed.");
      payload.product_url = parsed.toString();
    } else {
      const productText = dom["product-text"].value.trim();
      if (productText.length < 3) throw new Error("Enter a product name or label details first.");
      payload.product_text = productText.slice(0, 30000);
    }

    setBusy(dom["analyze-button"], true, "Analyzing…");
    const result = await apiRequest("/api/analyze", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    state.currentResult = sanitizeForStorage(result);
    state.currentResultKind = "analysis";
    recordResult("analysis", result);
    renderCurrentResult();
    setFormStatus(dom["analysis-status"], "Analysis complete. Review the result below.", "success");
    dom["analysis-consent"].checked = false;
    if (sourceType === "image") clearSelectedPhoto();
    dom["result-panel"].scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setFormStatus(dom["analysis-status"], error.message || "The product could not be analyzed.", "error");
  } finally {
    delete payload.image_data_url;
    setBusy(dom["analyze-button"], false, "Analyze supplement");
    updateAnalysisAvailability();
  }
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
  button.textContent = label;
}

function humanizeKey(key) {
  return String(key)
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isSafeHttpUrl(value) {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch (_error) {
    return false;
  }
}

function hasContent(value) {
  if (value === null || value === undefined || value === "") return false;
  if (Array.isArray(value)) return value.some(hasContent);
  if (isPlainObject(value)) return Object.values(value).some(hasContent);
  return true;
}

function scalarNode(value) {
  if (isSafeHttpUrl(value)) {
    const link = document.createElement("a");
    link.href = value;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = value;
    return link;
  }
  const span = document.createElement("span");
  span.className = "text-value";
  if (typeof value === "boolean") span.textContent = value ? "Yes" : "No";
  else span.textContent = String(value);
  return span;
}

function appendStructured(container, value, depth = 0) {
  container.classList.add("structured-content");
  if (!hasContent(value)) return;
  if (depth > 5) {
    const paragraph = document.createElement("p");
    paragraph.textContent = "Additional details available in the downloaded report.";
    container.append(paragraph);
    return;
  }
  if (Array.isArray(value)) {
    if (value.every((item) => !isPlainObject(item) && !Array.isArray(item))) {
      const list = document.createElement("ul");
      value.slice(0, 50).forEach((item) => {
        const li = document.createElement("li");
        li.append(scalarNode(item));
        list.append(li);
      });
      container.append(list);
      return;
    }
    value.slice(0, 50).forEach((item, index) => {
      const card = document.createElement("article");
      card.className = "data-card";
      const title = document.createElement("h4");
      title.textContent = productName(isPlainObject(item) && isPlainObject(item.product) ? item.product : item) || `Item ${index + 1}`;
      card.append(title);
      appendStructured(card, item, depth + 1);
      container.append(card);
    });
    return;
  }
  if (isPlainObject(value)) {
    const list = document.createElement("dl");
    list.className = "data-list";
    Object.entries(value).slice(0, 60).forEach(([key, item]) => {
      if (!hasContent(item) || /image|photo|thumbnail/i.test(key)) return;
      const term = document.createElement("dt");
      term.textContent = humanizeKey(key);
      const detail = document.createElement("dd");
      if (isPlainObject(item) || Array.isArray(item)) appendStructured(detail, item, depth + 1);
      else detail.append(scalarNode(item));
      list.append(term, detail);
    });
    container.append(list);
    return;
  }
  const paragraph = document.createElement("p");
  paragraph.append(scalarNode(value));
  container.append(paragraph);
}

function renderBlock(block, container, value) {
  container.replaceChildren();
  block.hidden = !hasContent(value);
  if (hasContent(value)) appendStructured(container, value);
}

function resultProduct(result) {
  if (!isPlainObject(result)) return null;
  return isPlainObject(result.product)
    ? result.product
    : isPlainObject(result.subject) ? result.subject : null;
}

function resultWarnings(result) {
  if (!isPlainObject(result)) return [];
  return hasContent(result.warnings) ? result.warnings : result.limitations;
}

function resultEvidence(result) {
  if (!isPlainObject(result)) return null;
  const details = {};
  if (hasContent(result.source)) details.input = result.source;
  if (hasContent(result.confidence)) details.confidence = result.confidence;
  if (hasContent(result.evidence)) details.evidence = result.evidence;
  return details;
}

function renderCurrentResult() {
  const result = state.currentResult;
  if (!isPlainObject(result)) {
    dom["result-panel"].hidden = true;
    return;
  }
  const comparison = state.currentResultKind === "comparison";
  dom["result-title"].textContent = comparison ? "Consolidated Nutrilite conversion" : "Supplement comparison";
  const resultId = result.analysis_id || result.comparison_id || "";
  dom["result-meta"].textContent = resultId ? `Reference ${resultId}` : "Generated from the information supplied";
  const understoodProduct = resultProduct(result);
  renderBlock(dom["product-block"], dom["product-result"], understoodProduct);
  renderBlock(dom["recommendations-block"], dom["recommendations-result"], result.recommendations);
  renderBlock(dom["gaps-block"], dom["gaps-result"], result.gaps);
  renderBlock(dom["warnings-block"], dom["warnings-result"], resultWarnings(result));
  renderBlock(dom["source-block"], dom["source-result"], resultEvidence(result));
  dom["result-disclaimer"].textContent = typeof result.disclaimer === "string" && result.disclaimer.trim()
    ? result.disclaimer
    : "For informational comparison only. Verify labels and consult a qualified healthcare professional before changing supplements.";
  dom["save-product"].hidden = comparison || !isPlainObject(understoodProduct);
  updateSaveProductButton();
  dom["result-panel"].hidden = false;
}

function productName(product) {
  if (!isPlainObject(product)) return "";
  return String(product.name || product.product_name || product.title || product.display_name || "").trim();
}

function productBrand(product) {
  if (!isPlainObject(product)) return "";
  return String(product.brand || product.manufacturer || "").trim();
}

function productKey(product) {
  if (!isPlainObject(product)) return "";
  const strongId = product.sku || product.id || product.upc || product.gtin || product.product_url || product.url;
  if (strongId) return String(strongId).trim().toLowerCase();
  return [productBrand(product), productName(product), product.serving_size || product.form || ""]
    .map((item) => String(item).trim().toLowerCase()).join("|");
}

function updateSaveProductButton() {
  const product = resultProduct(state.currentResult);
  if (!isPlainObject(product)) return;
  const key = productKey(product);
  const saved = state.supplements.some((entry) => productKey(entry.product) === key);
  dom["save-product"].disabled = saved;
  dom["save-product"].textContent = saved ? "Saved to cabinet" : "Save to cabinet";
}

function saveCurrentProduct() {
  const product = sanitizeForStorage(resultProduct(state.currentResult));
  if (!isPlainObject(product)) return;
  const key = productKey(product);
  if (!key) {
    showToast("This result does not contain enough product detail to save.");
    return;
  }
  if (state.supplements.some((entry) => productKey(entry.product) === key)) {
    showToast("That product is already in your cabinet.");
    return;
  }
  state.supplements.unshift({ id: newId(), saved_at: new Date().toISOString(), product });
  state.supplements = state.supplements.slice(0, MAX_SUPPLEMENTS);
  storageSet(STORAGE_KEYS.supplements, JSON.stringify(state.supplements));
  renderCabinet();
  updateSaveProductButton();
  showToast("Product saved to your cabinet on this device.");
}

function renderCabinet() {
  const hasSupplements = state.supplements.length > 0;
  dom["cabinet-count"].textContent = String(state.supplements.length);
  dom["cabinet-empty"].hidden = hasSupplements;
  dom["cabinet-content"].hidden = !hasSupplements;
  dom["supplement-list"].replaceChildren();
  state.supplements.forEach((entry) => {
    const item = document.createElement("article");
    item.className = "saved-item";
    const copy = document.createElement("div");
    const heading = document.createElement("h3");
    heading.textContent = productName(entry.product) || "Saved supplement";
    const detail = document.createElement("p");
    const brand = productBrand(entry.product);
    const serving = entry.product.serving_size || entry.product.form || "";
    detail.textContent = [brand, serving].filter(Boolean).join(" · ") || "Product details saved locally";
    copy.append(heading, detail);
    const actions = document.createElement("div");
    actions.className = "item-actions";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button danger-text";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeSupplement(entry.id));
    actions.append(remove);
    item.append(copy, actions);
    dom["supplement-list"].append(item);
  });
  updateAnalysisAvailability();
  updateSaveProductButton();
}

function removeSupplement(id) {
  state.supplements = state.supplements.filter((entry) => entry.id !== id);
  storageSet(STORAGE_KEYS.supplements, JSON.stringify(state.supplements));
  renderCabinet();
  showToast("Product removed from this device.");
}

async function compareCabinet() {
  if (!state.supplements.length) return;
  if (!dom["compare-consent"].checked) {
    setFormStatus(dom["compare-status"], "Please review and accept the comparison consent first.", "error");
    dom["compare-consent"].focus();
    return;
  }
  const payload = {
    supplements: state.supplements.map((entry) => sanitizeForStorage(entry.product)),
    include_profile: dom["compare-profile"].checked,
    profile: dom["compare-profile"].checked ? compactProfile() : {}
  };
  try {
    setBusy(dom["compare-button"], true, "Comparing…");
    setFormStatus(dom["compare-status"], "Building a consolidated conversion…");
    const result = await apiRequest("/api/compare", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    state.currentResult = sanitizeForStorage(result);
    state.currentResultKind = "comparison";
    recordResult("comparison", result);
    renderCurrentResult();
    activateTab(document.getElementById("analyze-tab"));
    dom["compare-consent"].checked = false;
    setFormStatus(dom["compare-status"], "Comparison complete.", "success");
    window.setTimeout(() => dom["result-panel"].scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  } catch (error) {
    setFormStatus(dom["compare-status"], error.message || "The cabinet could not be compared.", "error");
  } finally {
    setBusy(dom["compare-button"], false, "Compare my cabinet");
    updateAnalysisAvailability();
  }
}

function recordResult(kind, data) {
  const clean = sanitizeForStorage(data);
  if (!isPlainObject(clean)) return;
  state.results.unshift({ id: newId(), created_at: new Date().toISOString(), kind, data: clean });
  state.results = state.results.slice(0, MAX_RESULTS);
  storageSet(STORAGE_KEYS.results, JSON.stringify(state.results));
  renderHistory();
}

function resultSummary(entry) {
  if (entry.kind === "comparison") return "Consolidated cabinet conversion";
  return productName(resultProduct(entry.data)) || "Supplement analysis";
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Saved locally";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function renderHistory() {
  dom["history-list"].replaceChildren();
  dom["history-empty"].hidden = state.results.length > 0;
  dom["clear-history"].hidden = state.results.length === 0;
  state.results.forEach((entry) => {
    const item = document.createElement("article");
    item.className = "history-item";
    const copy = document.createElement("div");
    const heading = document.createElement("h4");
    heading.textContent = resultSummary(entry);
    const detail = document.createElement("p");
    detail.textContent = `${entry.kind === "comparison" ? "Combined conversion" : "Product analysis"} · ${formatDate(entry.created_at)}`;
    copy.append(heading, detail);
    const actions = document.createElement("div");
    actions.className = "item-actions";
    const view = document.createElement("button");
    view.type = "button";
    view.className = "text-button";
    view.textContent = "View";
    view.addEventListener("click", () => viewSavedResult(entry));
    const download = document.createElement("button");
    download.type = "button";
    download.className = "text-button";
    download.textContent = "Report";
    download.addEventListener("click", () => downloadMarkdown(entry.data, entry.kind));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button danger-text";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteResult(entry.id));
    actions.append(view, download, remove);
    item.append(copy, actions);
    dom["history-list"].append(item);
  });
}

function viewSavedResult(entry) {
  state.currentResult = entry.data;
  state.currentResultKind = entry.kind;
  renderCurrentResult();
  activateTab(document.getElementById("analyze-tab"));
  window.setTimeout(() => dom["result-panel"].scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}

function deleteResult(id) {
  state.results = state.results.filter((entry) => entry.id !== id);
  storageSet(STORAGE_KEYS.results, JSON.stringify(state.results));
  renderHistory();
}

function clearHistory() {
  if (!window.confirm("Delete all locally saved comparison results? Saved supplements and your profile will remain.")) return;
  state.results = [];
  storageSet(STORAGE_KEYS.results, "[]");
  renderHistory();
  showToast("Result history cleared.");
}

function readProfileForm() {
  const ageValue = dom["profile-age"].value.trim();
  const age = ageValue ? Number.parseInt(ageValue, 10) : null;
  if (ageValue && (!Number.isInteger(age) || age < 13 || age > 120)) {
    throw new Error("Enter an age between 13 and 120, or leave it blank.");
  }
  const profile = {
    age,
    demographic: dom["profile-demographic"].value,
    life_stage: dom["profile-life-stage"].value,
    goals: dom["profile-goals"].value.trim(),
    dietary_considerations: dom["profile-diet"].value.trim()
  };
  Object.keys(profile).forEach((key) => {
    if (profile[key] === "" || profile[key] === null) delete profile[key];
  });
  return profile;
}

function saveProfile(event) {
  event.preventDefault();
  try {
    state.profile = readProfileForm();
    storageSet(STORAGE_KEYS.profile, JSON.stringify(state.profile));
    updateProfileOptions();
    dom["profile-saved"].textContent = "Saved on this device";
    window.setTimeout(() => { dom["profile-saved"].textContent = ""; }, 3000);
  } catch (error) {
    dom["profile-saved"].textContent = error.message;
    dom["profile-age"].focus();
  }
}

function renderProfile() {
  dom["profile-age"].value = state.profile.age || "";
  dom["profile-demographic"].value = state.profile.demographic || "";
  dom["profile-life-stage"].value = state.profile.life_stage || "";
  dom["profile-goals"].value = state.profile.goals || "";
  dom["profile-diet"].value = state.profile.dietary_considerations || "";
  updateProfileOptions();
}

function updateTokenStatus() {
  const hasToken = Boolean(accessToken());
  dom["token-status"].textContent = hasToken
    ? "An access token is saved on this device."
    : "No access token is saved on this device.";
  dom["remove-token"].hidden = !hasToken;
  dom["access-token"].value = "";
}

function saveToken() {
  const token = dom["access-token"].value.trim();
  if (!token) {
    showToast("Paste an access token first.");
    dom["access-token"].focus();
    return;
  }
  if (token.length > 4096) {
    showToast("That access token is too long.");
    return;
  }
  storageSet(STORAGE_KEYS.token, token);
  updateTokenStatus();
  setFormStatus(dom["analysis-status"], "");
  showToast("Access token saved on this device.");
  loadConfig();
}

function removeToken() {
  storageRemove(STORAGE_KEYS.token);
  updateTokenStatus();
  showToast("Access token removed.");
  loadConfig();
}

function exportLocalData() {
  const backup = {
    export_type: EXPORT_FORMAT,
    format_version: EXPORT_VERSION,
    exported_at: new Date().toISOString(),
    profile: sanitizeForStorage(state.profile),
    supplements: sanitizeForStorage(state.supplements),
    results: sanitizeForStorage(state.results)
  };
  downloadBlob(
    JSON.stringify(backup, null, 2),
    `nutrilite-converter-backup-${dateStamp()}.json`,
    "application/json"
  );
  setFormStatus(dom["data-status"], "Backup downloaded. It does not contain photos or your access token.", "success");
}

async function importLocalData() {
  const file = dom["import-data"].files && dom["import-data"].files[0];
  dom["import-data"].value = "";
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    setFormStatus(dom["data-status"], "That backup is larger than the 2 MB import limit.", "error");
    return;
  }
  try {
    const imported = JSON.parse(await file.text());
    if (!isPlainObject(imported) || imported.export_type !== EXPORT_FORMAT || imported.format_version !== EXPORT_VERSION) {
      throw new Error("This is not a supported Nutrilite Converter backup.");
    }
    const profile = isPlainObject(imported.profile) ? sanitizeForStorage(imported.profile) : {};
    const supplements = Array.isArray(imported.supplements)
      ? imported.supplements.map(sanitizeForStorage).filter(validSupplementEntry).slice(0, MAX_SUPPLEMENTS)
      : [];
    const results = Array.isArray(imported.results)
      ? imported.results.map(sanitizeForStorage).filter(validResultEntry).slice(0, MAX_RESULTS)
      : [];
    if (!window.confirm(`Replace this device's local profile, ${state.supplements.length} saved products, and result history with the selected backup?`)) return;
    state.profile = profile;
    state.supplements = supplements;
    state.results = results;
    persistState();
    renderProfile();
    renderCabinet();
    renderHistory();
    setFormStatus(dom["data-status"], "Backup imported. Your existing access token was unchanged.", "success");
  } catch (error) {
    setFormStatus(dom["data-status"], error.message || "The backup could not be imported.", "error");
  }
}

function clearLocalData() {
  if (!window.confirm("Clear the optional profile, saved supplements, results, and access token from this device? This cannot be undone unless you exported a backup.")) return;
  Object.values(STORAGE_KEYS).forEach(storageRemove);
  state.profile = {};
  state.supplements = [];
  state.results = [];
  state.currentResult = null;
  clearSelectedPhoto();
  renderProfile();
  renderCabinet();
  renderHistory();
  updateTokenStatus();
  dom["result-panel"].hidden = true;
  setFormStatus(dom["data-status"], "All Nutrilite Converter data was cleared from this browser.", "success");
  loadConfig();
}

function dateStamp() {
  return new Date().toISOString().slice(0, 10);
}

function downloadBlob(content, filename, mimeType) {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function markdownEscape(value) {
  return String(value).replace(/([\\`*_{}\[\]<>#+.!|])/g, "\\$1").replace(/\r?\n/g, " ");
}

function markdownScalar(value) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return markdownEscape(value);
}

function structuredMarkdown(value, depth = 0) {
  if (!hasContent(value) || depth > 6) return "";
  if (Array.isArray(value)) {
    return value.slice(0, 100).map((item, index) => {
      if (isPlainObject(item)) {
        const title = productName(isPlainObject(item.product) ? item.product : item) || `Item ${index + 1}`;
        return `### ${markdownEscape(title)}\n\n${structuredMarkdown(item, depth + 1)}`;
      }
      return `- ${markdownScalar(item)}`;
    }).join("\n\n");
  }
  if (isPlainObject(value)) {
    return Object.entries(value).filter(([key, item]) => hasContent(item) && !/image|photo|thumbnail/i.test(key)).map(([key, item]) => {
      if (isPlainObject(item) || Array.isArray(item)) {
        return `**${markdownEscape(humanizeKey(key))}**\n\n${structuredMarkdown(item, depth + 1)}`;
      }
      return `- **${markdownEscape(humanizeKey(key))}:** ${markdownScalar(item)}`;
    }).join("\n\n");
  }
  return markdownScalar(value);
}

function buildMarkdown(result, kind) {
  const title = kind === "comparison" ? "Nutrilite Cabinet Conversion" : "Supplement Comparison";
  const sections = [
    `# ${title}`,
    `Generated: ${new Date().toLocaleString()}`
  ];
  const reference = result.analysis_id || result.comparison_id;
  if (reference) sections.push(`Reference: ${markdownEscape(reference)}`);
  const sectionMap = [
    ["Product understood", resultProduct(result)],
    ["Nutrilite matches", result.recommendations],
    ["Potential gaps or considerations", result.gaps],
    ["Important cautions", resultWarnings(result)],
    ["Sources and method", resultEvidence(result)]
  ];
  sectionMap.forEach(([heading, value]) => {
    if (hasContent(value)) sections.push(`## ${heading}\n\n${structuredMarkdown(value)}`);
  });
  const disclaimer = typeof result.disclaimer === "string" && result.disclaimer.trim()
    ? result.disclaimer
    : "For informational comparison only. Verify labels and consult a qualified healthcare professional before changing supplements.";
  sections.push(`## Important notice\n\n> ${markdownEscape(disclaimer)}`);
  return `${sections.join("\n\n")}\n`;
}

function downloadMarkdown(result = state.currentResult, kind = state.currentResultKind) {
  if (!isPlainObject(result)) return;
  const baseName = kind === "comparison"
    ? "nutrilite-cabinet-conversion"
    : slugify(productName(resultProduct(result))) || "supplement-comparison";
  downloadBlob(buildMarkdown(result, kind), `${baseName}-${dateStamp()}.md`, "text/markdown");
}

function slugify(value) {
  return String(value || "").toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 70);
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {
      // Installation support is optional; the connected application remains usable.
    });
  });
}

function bindEvents() {
  dom["product-photo"].addEventListener("change", handlePhotoSelection);
  dom["remove-photo"].addEventListener("click", clearSelectedPhoto);
  dom["analysis-form"].addEventListener("submit", handleAnalysisSubmit);
  dom["save-product"].addEventListener("click", saveCurrentProduct);
  dom["download-report"].addEventListener("click", () => downloadMarkdown());
  dom["compare-button"].addEventListener("click", compareCabinet);
  dom["clear-history"].addEventListener("click", clearHistory);
  dom["profile-form"].addEventListener("submit", saveProfile);
  dom["save-token"].addEventListener("click", saveToken);
  dom["remove-token"].addEventListener("click", removeToken);
  dom["export-data"].addEventListener("click", exportLocalData);
  dom["export-data-top"].addEventListener("click", exportLocalData);
  dom["import-data"].addEventListener("change", importLocalData);
  dom["clear-data"].addEventListener("click", clearLocalData);
  window.addEventListener("online", loadConfig);
  window.addEventListener("offline", () => {
    state.config.analysis_available = false;
    setServiceState("offline");
    updateAnalysisAvailability();
  });
}

function initialize() {
  cacheDom();
  loadState();
  initializeTablists();
  bindEvents();
  renderProfile();
  renderCabinet();
  renderHistory();
  updateTokenStatus();
  const inviteAccepted = consumeInviteToken();
  if (inviteAccepted) {
    updateTokenStatus();
    showToast("Invite accepted. This device is ready to connect.");
  }
  loadConfig();
  registerServiceWorker();
}

initialize();
