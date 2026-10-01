"""Read stable application releases without downloading or running installers."""

import json
import re
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

APP_VERSION = "1.0.12"
APP_REPOSITORY = "Aknices-QWQ/SkyAutoMusic"
RELEASES_URL = f"https://github.com/{APP_REPOSITORY}/releases/latest"
RELEASE_API = f"https://api.github.com/repos/{APP_REPOSITORY}/releases/latest"
MANIFEST_URL = "https://sky.xxlab.dev/downloads.json"


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", str(value).strip(), re.IGNORECASE)
    if not match:
        raise ValueError("无法识别的正式版本号")
    parts = tuple(int(part) for part in match[1].split("."))
    return parts + (0,) * (4 - len(parts))


def compare_versions(left, right):
    a, b = version_tuple(left), version_tuple(right)
    return (a > b) - (a < b)


def _read_json(url):
    request = Request(url, headers={
        "Accept": "application/json", "User-Agent": f"SkyAutoMusic/{APP_VERSION}",
    })
    # Checking for updates should not silently consume a configured VPN/proxy.
    with build_opener(ProxyHandler({})).open(request, timeout=10) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("更新信息过大")
    return json.loads(raw.decode("utf-8"))


def fetch_latest_release():
    """Use GitHub, falling back to the website when GitHub is unreachable."""
    for url in (RELEASE_API, MANIFEST_URL):
        try:
            payload = _read_json(url)
            if url == RELEASE_API:
                if payload.get("draft") or payload.get("prerelease"):
                    raise ValueError("没有可用的正式版本")
                version = str(payload["tag_name"]).strip().removeprefix("v").removeprefix("V")
                release_url = payload.get("html_url") or RELEASES_URL
                parsed = urlparse(release_url)
                if (parsed.scheme != "https" or parsed.netloc != "github.com"
                        or not parsed.path.startswith(f"/{APP_REPOSITORY}/releases/")):
                    raise ValueError("更新页面地址无效")
            else:
                version = str(payload["app"]["version"]).strip()
                release_url = "https://sky.xxlab.dev/#download"
            version_tuple(version)
            return {"version": version, "url": release_url, "source": url}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    raise RuntimeError("暂时无法连接 GitHub 和下载网站，请检查网络后重试。")
