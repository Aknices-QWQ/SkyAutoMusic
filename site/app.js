const state = {
  manifest: null,
  edition: "lite",
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
};

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
