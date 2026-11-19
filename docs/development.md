# 开发与验证

## 运行源码

在仓库根目录打开 PowerShell，使用含 Tkinter 的 64 位 Python 3.10：

```powershell
py -3.10 -m venv .venv
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -r requirements.txt
& $py -m unittest discover -s tests -v
& $py viewer_gui.py
```

`NEKOCONNE_GAME` 和 `NEKOCONNE_CACHE` 分别设置初始游戏目录和缓存目录。源码版渲染 Cubism 动作还需执行 `& $py -m pip install -r requirements-render.txt`。便携版走另一条路径，直接调用游戏目录内的 Cubism Core。

`tests/` 是开发和 CI 用的回归测试，仅随源码提供。`wallpaper/index.html` 和 `wallpaper/project.json` 是生成互动壁纸时使用的模板，便携版把它们放在 `_internal/wallpaper/`。

## 导出与索引

`tools/export_images.py` 的 `--match` 按名称正则筛图；`tools/export_audio.py` 的 `--select` 匹配 cue sheet 名称，`--cli` 指定 vgmstream；`tools/render_spine.py` 的 `--list` 列出指定骨架的动作和皮肤。各脚本都可加 `--help` 查看参数。

已有模型和精选背景图时，可以在命令行生成壁纸。下面的命令将缺少的动作先渲染，再把项目写入 `exports/projects`；如要换背景，加 `--background .\background.png`。

```powershell
& $py tools/build_scene.py exports/builtin/cubism/10106_01_S01/10106_01_S01.model3.json --dest exports/projects --render-missing
```

在导出缓存中，`builtin/inventory.csv` 记录 Unity 对象路径与编号，`images/index.csv` 记录图片来源及导出结果。用浏览器打开 `video/index.html` 看视频缩略图，或打开 `audio/listen.html` 按 cue 名称、编号试听。外部导入的资源单独记在 `external/<目录标识>/manifest.json`。

## 实测覆盖

以下是同一份 Steam 安装（Unity 6000.2.15f1）的导出结果。

| 类别 | 结果 |
| --- | --- |
| 图像 | 2,392 个 Sprite 或 Texture2D 对象导出为 PNG，另存 14 张精选图。Sprite 裁片和原图集有重复，不能按此统计独立立绘。另有 12 个零尺寸字体纹理没有像素。 |
| Cubism | 1 套模型，2 页纹理、5 段已渲染动作、10 个表情文件。使用包内 MOC3 和从 Unity 播放数据重建的 JSON，未还原全部物理与游戏逻辑。 |
| Spine | 6 套 4.1.11 骨架，10 页纹理，共 100 个动作条目。每套均已导出合成 PNG 和示例动作视频。另导出 48 张皮肤／方向视图；其中一个 NPC 的 32 个标签在同一姿态下只有 16 种不同画面。 |
| 语音 | 20,426 条波形记录中有 20,423 条导出为 WAV，另外 3 条缺少完整音库。总时长约 22 小时 27 分，占约 7.8 GB。 |
| 视频 | 78 个 VP9 WebM 已逐个完整解码检查。61 个保留音轨（其中一条源轨静音），其余 17 个源视频没有音轨。 |

## 构建 Windows 便携包

构建机需要 Visual Studio 2022 的 C++ x64 可再分发组件，以及 MSYS2 UCRT64。以下命令在仓库根目录运行；第一步沿用前面的 PowerShell 和 `$py`：

```powershell
& $py -m pip install PyInstaller==6.22.3 moderngl==5.12.0 glcontext==3.0.0 astc-encoder-py==0.1.12 etcpak==0.9.15 texture2ddecoder==1.0.6
& $py packaging/media.py --prepare
```

在 **MSYS2 UCRT64** 终端进入同一仓库，安装工具链并编译媒体工具：

```bash
pacman -S --needed make perl diffutils mingw-w64-ucrt-x86_64-{gcc,binutils,cmake,ninja,nasm,pkgconf}
bash packaging/build_ffmpeg.sh
.venv/Scripts/python.exe packaging/build_vgmstream.py --jobs 4
```

回到 PowerShell 打包。将便携包解压到临时目录，再运行启动和导出测试：

```powershell
& $py packaging/build_windows.py
$check = Join-Path $env:TEMP ("neko-viewer-" + [guid]::NewGuid())
Expand-Archive dist/NekoConneViewer-windows-x64.zip $check
& $py packaging/smoke_windows.py (Join-Path $check NekoConneViewer) --gui
```

`dist/` 还会生成对应的媒体工具源码包。若只需查看器源码，在 Git 工作副本运行 `& $py tools/package_source.py`，得到当前提交的 `dist/NekoConneViewer-source.zip`。完整发布流程见 [Windows 构建工作流](../.github/workflows/release.yml)：手动运行只生成构建产物，推送 `v*` 标签才会发布 Release。
