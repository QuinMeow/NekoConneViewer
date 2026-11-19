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

`NEKOCONNE_GAME` 和 `NEKOCONNE_CACHE` 分别设置初始游戏目录和缓存目录。源码版与便携版都用游戏目录内的 Cubism Core。源码版构建媒体工具后，可让 `NEKOCONNE_TOOLS` 指向便携包内的 `.tools/`。

`tests/` 是开发和 CI 用的回归测试，仅随源码提供。`wallpaper/index.html` 和 `wallpaper/project.json` 是生成互动壁纸时使用的模板，便携版把它们放在 `_internal/wallpaper/`。

## 导出与索引

`tools/export_audio.py` 的 `--select` 匹配 cue sheet 名称，`--cli` 指定 vgmstream；`tools/render_spine.py` 的 `--list` 列出指定骨架的动作和姿势（文件中的 `skins` 字段）。各脚本都可加 `--help` 查看参数。

在命令行按类别扫描和导出；已有模型时可生成壁纸。如要换背景，加 `--background .\background.png`。

```powershell
& $py -m tools.scan_assets --game $env:NEKOCONNE_GAME --out exports-v2 --catalog
& $py -m tools.scan_assets --game $env:NEKOCONNE_GAME --out exports-v2 images spine live2d audio video
& $py tools/build_scene.py exports-v2/live2d/10106_01_S01/10106_01_S01.model3.json --cache exports-v2 --dest exports-v2/wallpapers --render-missing
```

先将 `NEKOCONNE_GAME` 设为自己的游戏安装目录。`--catalog` 只建立头像目录，不导出素材。导出时可重复传入 `--character ID`，角色及其形态统一按五位 ID 归组。默认不导出非 `1` 开头的已归属角色，使用 `--include-other-roles` 可取消该限制。

在缓存中，`index/assets.json` 记录对象来源，`index/catalog.json` 记录预览头像与编号，`index/report.json` 列出无法识别或导出的文件。音频和影片的索引分别在 `index/audio/`、`index/video/`。

## 实测覆盖

以下是同一份 Steam 安装（Unity 6000.2.15f1）的实测结果。游戏更新后数量可能变化，具体以扫描报告为准。

| 类别 | 结果 |
| --- | --- |
| 图像 | 11,755 张外部 DDS 通过像素解码，其中 2,600 张为角色头像和缩略图。内置包另有 2,392 个可导出的图像对象。 |
| Cubism | 全量扫描导出 218 份模型。其中 1 份来自内置包，12 份技能包模型与对应角色包中的模型逐文件一致。205 个外部角色包含 1,131 段动作和 1,564 个表情。 |
| Spine | 支持已识别内外部包中的骨架、图集和贴图。动作可批量渲染，按全段动画的固定边界裁切透明 WebM。 |
| 音频 | 不限定角色和音频类别的全量测试导出 20,854 条 WAV，包含语音、音乐和音效；5 个音库解码失败。 |
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
