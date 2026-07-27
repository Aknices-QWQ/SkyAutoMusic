# SkyAutoMusic

SkyAutoMusic 是面向 Windows 的《Sky 光·遇》自动演奏工具，提供现代化 PySide6 界面、悬浮控制、曲目预览、进度跳转和在线曲库更新。

## 下载

请从 [sky.xxlab.dev](https://sky.xxlab.dev) 下载：

- **Lite 精简版**：体积更小，内置《最后一吻》和《Call of Silence》，首次启动后可从 GitHub 更新完整曲库。
- **Full 完整版**：附带全部曲目，解压后即可离线使用。
- 国内网络可以在网站或应用内选择预设 GitHub 镜像线路。

下载安装器后按提示完成安装即可。默认安装到当前用户目录，程序和曲库更新都使用普通用户权限，不需要管理员权限。

## 功能

- 搜索、收藏和快速切换 JSON 曲谱
- 主窗口与悬浮窗同步播放控制
- `F3` 全局开启或关闭悬浮窗
- 本机音色预览，不向游戏发送按键
- 演奏或预览中拖动进度并立即跳转
- 速度按原曲 BPM 倍率缩放，`1.00x` 即原速
- 随机延迟与错键效果
- `Esc` 随时停止并释放全部按键
- 从 GitHub Release 在线更新曲库，支持 SHA-256 校验和国内镜像
- 已安装曲库后只下载版本间增量包，不重复下载完整曲库

## 从源码运行

需要 Windows、Python 3.11 或更高版本。

```powershell
python -m pip install -r requirements.txt
python play_music_qt.py
```

## 构建

程序使用 Nuitka standalone 构建，再由 Inno Setup 封装成普通用户权限安装器；未使用 PyInstaller：

```powershell
python -m pip install nuitka ordered-set zstandard
python -m nuitka --standalone --enable-plugin=pyside6 --windows-console-mode=disable --include-package=keyboard play_music_qt.py
```

准备 Lite 或 Full 文件目录后，可使用 `installer.iss` 生成安装器。默认安装到当前用户的 LocalAppData，因此应用内曲库更新不需要管理员权限。

## 曲谱格式

将 JSON 文件放入 `Sheet Music` 文件夹。乐谱需要包含 `songNotes` 数组；同一 `time` 下的多个 `key` 会同时按下。

## 署名

《最后一吻》和《Call of Silence》制谱署名：`Aknices&&BA4KQS`。

项目基于 [Tloml-Starry/SkyAutoMusic](https://github.com/Tloml-Starry/SkyAutoMusic) 继续开发；Fork 保留完整提交历史与原始来源。

## 许可

本项目以 GNU General Public License v3.0 发布，详见 [LICENSE](LICENSE)。曲谱内容的权利归各自创作者及权利人所有。
