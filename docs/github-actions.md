# GitHub 自动构建与发布

工作流：`.github/workflows/windows-build.yml`，在标准 `windows-2022` 运行器上使用 Python 3.11、Nuitka 和 MSVC 编译，再由 Inno Setup 生成 Lite / Full 安装包。

## 合并后自动构建

每次向 `main` 推送提交或合并 PR 都会自动构建 Lite / Full 安装包。进入 **Actions → Windows installers** 查看进度；成功后从该次运行的 **Artifacts** 下载。`main` 的构建只上传产物，不创建 GitHub Release。

## 手动生成安装包

1. 打开仓库的 **Actions → Windows installers → Run workflow**。
2. 选择要编译的分支或标签，点击 **Run workflow**。已合并的最新功能请选择 `main`。
3. 完成后，在运行详情的 **Artifacts** 下载 `SkyAutoMusic-v版本-windows`。

手动选择分支只生成构建产物，不创建 GitHub Release。产物包含两个安装器、SHA-256 校验和、构建信息及发布说明，保留 7 天。工作流不会启动桌面程序或操作游戏。

## 标签自动发布

更新 `app_updater.py`（如果存在）、`installer.iss` 的两个版本字段，以及 `RELEASE_NOTES.md`。三处版本必须一致，例如 `1.0.14`；然后在该源码提交创建并推送 `v1.0.14` 标签：

```powershell
git tag v1.0.14
git push github v1.0.14
```

推送 `v*` 标签会自动编译。标签号与源码版本不符时会在编译前失败。构建成功后，流程先创建 Release 草稿并上传两个安装器、校验和及构建信息，再发布为正式版本。

已发布的版本不会被重新运行的流程覆盖；未完成的草稿可重新运行恢复上传。所有构建步骤成功后才会执行发布任务。仅发布任务获得 `contents: write`，使用 GitHub 自动提供的 `GITHUB_TOKEN`，无需添加个人访问令牌。

## 曲库与默认配置

Lite 固定内置《最后一吻》《Call of Silence》，这两份 JSON 已随源码保存。Full 下载 `site/downloads.json` 中指定的曲库版本，核对大小、SHA-256 和曲目数量后，再合入仓库曲谱。更新曲库基线时同步修改该清单的 `sheets` 字段。

安装包会生成干净的默认配置和空收藏，不复制仓库内的个人 `config.json`、`favorites.json`、缓存与配对令牌。当前源码包含的音色、图标和手机同步页面会随程序打包；Chromium 仍由用户在应用内下载。

## 本地复用

```powershell
python -m pip install -r requirements-build.txt
./tools/build_desktop.ps1 -InnoCompiler 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

输出位于 `output/windows/installers`。每次构建使用新输出目录；已有目录时可传入 `-OutputDir output/windows-next`。构建失败可在 Actions 下载 `windows-build-diagnostics` 查看编译日志。

## 费用

本仓库为公开仓库，使用 GitHub 标准托管运行器的 Actions 免费。私有仓库有按套餐提供的免费额度，超出额度收费；大型运行器有单独费用。详见 [GitHub Actions 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。
