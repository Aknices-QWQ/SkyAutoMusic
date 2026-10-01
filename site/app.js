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
  heroRelease: document.querySelector("#hero-release-version"),
  heroSource: document.querySelector("#hero-source-version"),
  featureSource: document.querySelector("#feature-source-version"),
  featureAvailability: document.querySelector("#feature-availability"),
  sourceNotice: document.querySelector("#source-notice"),
  sourceStatus: document.querySelector("#source-status"),
  buildLink: document.querySelector("#build-link"),
  releaseNotes: document.querySelector("#release-notes-link"),
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

function compareVersions(left, right) {
  const a = left.split(".").map(Number);
  const b = right.split(".").map(Number);
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
  }
  return 0;
}

function appFromRelease(release) {
  if (release.draft || release.prerelease || !/^v\d+\.\d+\.\d+$/.test(release.tag_name)) return null;
  const version = release.tag_name.slice(1);
  const repository = "https://github.com/Aknices-QWQ/SkyAutoMusic";
  const app = { version, repository, release_url: `${repository}/releases/tag/${release.tag_name}` };
  for (const edition of ["lite", "full"]) {
    const name = `SkyAutoMusic-${release.tag_name}-${edition === "lite" ? "Lite" : "Full"}-Setup.exe`;
    const expectedUrl = `${repository}/releases/download/${release.tag_name}/${name}`;
    const asset = (release.assets || []).find((entry) => entry.name === name && entry.state === "uploaded");
    if (!asset || asset.browser_download_url !== expectedUrl ||
        !Number.isSafeInteger(asset.size) || asset.size <= 0 ||
        !/^sha256:[a-f0-9]{64}$/i.test(asset.digest || "")) return null;
    app[edition] = { name, asset_url: expectedUrl, size: asset.size, sha256: asset.digest.slice(7).toLowerCase() };
  }
  return app;
}

async function refreshLatestRelease() {
  // The deployed manifest remains available if GitHub is blocked or rate limited.
  try {
    const response = await fetch("https://api.github.com/repos/Aknices-QWQ/SkyAutoMusic/releases/latest", {
      headers: { Accept: "application/vnd.github+json" },
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return;
    const app = appFromRelease(await response.json());
    if (!app || compareVersions(app.version, state.manifest.app.version) < 0) return;
    state.manifest.app = { ...state.manifest.app, ...app };
    render();
  } catch {
    // Keep the validated, published download from downloads.json.
  }
}

function render() {
  if (!state.manifest) return;
  const asset = state.manifest.app[state.edition];
  const isLite = state.edition === "lite";
  elements.download.textContent = `下载 ${isLite ? "Lite 精简版" : "Full 完整版"}`;
  elements.download.href = mirroredUrl(asset.asset_url, elements.mirror.value);
  elements.checksum.textContent = asset.sha256;
  elements.version.textContent = `v${state.manifest.app.version}`;
  elements.heroRelease.textContent = elements.version.textContent;
  elements.releaseNotes.href = state.manifest.app.release_url || `${state.manifest.app.repository}/releases/latest`;
  const source = state.manifest.source;
  if (source) {
    elements.heroSource.textContent = `v${source.version}`;
    elements.featureSource.textContent = `v${source.version}`;
    const unreleased = compareVersions(source.version, state.manifest.app.version) > 0;
    elements.sourceNotice.hidden = !unreleased;
    elements.sourceStatus.textContent = `v${source.version} 已合并到源码，尚未发布正式安装包；下方下载的是 v${state.manifest.app.version}。最新构建完成后，可在 Actions 的 Artifacts 下载（需登录 GitHub）。`;
    elements.buildLink.href = source.build_url;
    elements.featureAvailability.textContent = unreleased
      ? "以下功能已合并到最新源码，正式安装包以下载区的版本为准。"
      : "以下功能已包含在当前正式版，可在下载区获取安装包。";
  }
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
    window.setTimeout(() => { elements.copyQq.textContent = "复制 QQ 群号"; }, 1600);
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
    void refreshLatestRelease();
  })
  .catch(() => {
    elements.download.removeAttribute("href");
    elements.download.textContent = "下载信息暂时不可用";
    elements.note.textContent = "请稍后刷新，或前往 GitHub Releases 下载。";
  });
