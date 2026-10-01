const state = {
  manifest: null,
  edition: "lite",
  theme: localStorage.getItem("skyautomusic-theme") || "carbon",
};

const elements = {
  editions: [...document.querySelectorAll('input[name="edition"]')],
  cards: [...document.querySelectorAll(".edition-card")],
  mirror: document.querySelector("#mirror-select"),
  download: document.querySelector("#download-button"),
  checksum: document.querySelector("#checksum"),
  copy: document.querySelector("#copy-checksum"),
  note: document.querySelector("#download-note"),
  count: document.querySelector("#sheet-count"),
  version: document.querySelector("#release-version"),
  liteSize: document.querySelector("#lite-size"),
  fullSize: document.querySelector("#full-size"),
  themes: [...document.querySelectorAll("[data-theme-value]")],
  requestForm: document.querySelector("#request-form"),
  requestTitle: document.querySelector("#request-title-input"),
  requestSource: document.querySelector("#request-source"),
  requestNotes: document.querySelector("#request-notes"),
  authorQq: document.querySelector("#author-qq"),
  copyQq: document.querySelector("#copy-qq"),
};

const availableThemes = new Set(["carbon", "warm", "editorial"]);

function applyTheme(theme) {
  state.theme = availableThemes.has(theme) ? theme : "carbon";
  document.documentElement.dataset.theme = state.theme;
  localStorage.setItem("skyautomusic-theme", state.theme);
  elements.themes.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeValue === state.theme));
  });
}

function formatBytes(bytes) {
  return `${(Number(bytes) / 1024 / 1024).toFixed(1)} MB`;
}

function mirroredUrl(url, prefix) {
  return prefix && url.startsWith("https://github.com/")
    ? `${prefix.replace(/\/$/, "")}/${url}`
    : url;
}

function render() {
  if (!state.manifest) return;
  const asset = state.manifest.app[state.edition];
  const isLite = state.edition === "lite";
  elements.download.textContent = `下载 ${isLite ? "Lite 精简版" : "Full 完整版"}`;
  elements.download.href = mirroredUrl(asset.asset_url, elements.mirror.value);
  elements.checksum.textContent = asset.sha256;
  elements.version.textContent = `v${state.manifest.app.version}`;
  elements.count.textContent = Number(state.manifest.sheets.count).toLocaleString("zh-CN");
  elements.liteSize.textContent = formatBytes(state.manifest.app.lite.size);
  elements.fullSize.textContent = formatBytes(state.manifest.app.full.size);
  elements.note.textContent = elements.mirror.value
    ? "当前通过国内镜像转发 GitHub Release；若线路不可用，请切换备用镜像或直连。"
    : "当前使用 GitHub 直连下载。";
  elements.cards.forEach((card) => {
    card.classList.toggle("is-selected", card.querySelector("input").checked);
  });
}

elements.editions.forEach((input) => {
  input.addEventListener("change", () => {
    state.edition = input.value;
    render();
  });
});

elements.mirror.addEventListener("change", render);

elements.themes.forEach((button) => {
  button.addEventListener("click", () => applyTheme(button.dataset.themeValue));
});

elements.copy.addEventListener("click", async () => {
  if (!state.manifest) return;
  try {
    await navigator.clipboard.writeText(elements.checksum.textContent);
    elements.copy.textContent = "已复制";
    window.setTimeout(() => { elements.copy.textContent = "复制"; }, 1600);
  } catch {
    elements.copy.textContent = "复制失败";
  }
});

elements.copyQq.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(elements.authorQq.textContent.trim());
    elements.copyQq.textContent = "QQ 已复制";
    window.setTimeout(() => { elements.copyQq.textContent = "复制作者 QQ"; }, 1600);
  } catch {
    elements.copyQq.textContent = "请手动复制号码";
  }
});

elements.requestForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const title = elements.requestTitle.value.trim();
  if (!title) {
    elements.requestTitle.focus();
    return;
  }
  const source = elements.requestSource.value.trim() || "未提供";
  const notes = elements.requestNotes.value.trim() || "无";
  const params = new URLSearchParams({
    title: `求谱：${title}`,
    body: `### 歌曲名称\n${title}\n\n### 音源链接\n${source}\n\n### 补充说明\n${notes}\n\n---\n来自 sky.xxlab.dev 的扒谱请求`,
  });
  window.location.href = `https://github.com/Aknices-QWQ/SkyAutoMusic/issues/new?${params}`;
});

const requestParams = new URLSearchParams(window.location.search);
if (requestParams.get("request") === "1") {
  elements.requestTitle.value = requestParams.get("title") || "";
  window.setTimeout(() => elements.requestTitle.focus(), 0);
}

applyTheme(state.theme);

fetch("/downloads.json", { cache: "no-store" })
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then((manifest) => {
    state.manifest = manifest;
    render();
  })
  .catch(() => {
    elements.download.removeAttribute("href");
    elements.download.textContent = "下载信息暂时不可用";
    elements.note.textContent = "请稍后刷新，或前往 GitHub Releases 下载。";
  });
