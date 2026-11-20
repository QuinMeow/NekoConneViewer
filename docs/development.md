# 开发与验证

## 运行源码

在仓库根目录打开 PowerShell，使用含 Tkinter 的 64 位 Python 3.10：

```powershell
py -3.10 -m venv .venv
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -r requirements.txt
& $py -m unittest discover -s tests -v
node --test tests/test_duo_scene.cjs tests/test_duo_renderer.cjs
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

## 双角色壁纸原型

这是用源码工具生成的独立网页壁纸，不是 Viewer 里的完整场景编辑器。成品目录须保留全部文件，不能只发送 HTML。

- **Wallpaper Engine**：要保留随包的属性面板，将整个成品文件夹放入 `wallpaper_engine/projects/myprojects/`，在 Wallpaper Engine 中选择该本地项目。也可以从编辑器“创建壁纸”导入 `index.html`；若编辑器重新生成了属性配置，使用成品自带的 `project.json`。
- **调整与交互**：在壁纸属性中切换花园或标题背景，分别调整两人的横向位置、脚底位置和缩放。点击角色播放她自己的语音，点击透明区域不会触发；视线和音量可以单独开关或调节。
- **浏览器预览**：直接打开成品中的 `index.html`；在地址末尾加 `?controls` 可显示临时调节面板，无需启动本地服务器。

使用前述开发环境、自己的游戏安装和已导出的图片、语音和两套角色模型，从同一份缓存生成：

```powershell
& $py -m tools.build_duo_wallpaper --game $env:NEKOCONNE_GAME --cache exports-v2 --dest exports-v2/wallpapers/NekoConne-Duo-10102-10106
```

如果模型来自另一份缓存，可用 `--models` 指定它的 `live2d/` 目录。`--dest` 必须是尚不存在的目录。生成需要游戏自带的 Cubism Core 和带 libvpx-vp9 的 FFmpeg；网页播放不需要游戏、Python、FFmpeg、网络或额外插件。

网页播放两人的原始待机动作（24 FPS 透明 WebM），按每个角色实际显示的帧混合由模型生成的眼部姿势，并用该帧的透明轮廓判断点击。它不是可以任意更换模型或动作的 Cubism 编辑器。游戏资源和生成物不随源码分发。

## 原生 Spine 卡面壁纸

在 Viewer 的素材库选中只有 `default / animation` 的 Spine 4.1 卡面模型，直接点「生成壁纸」，不需要先渲染动作。命令行也可直接组装已导出的模型：

```powershell
& $py -m tools.build_spine_wallpaper .\exports\builtin\spine\1010101_02\1010101_02.skel --dest .\exports\projects\NekoConne-1010101_02-Spine
```

首次生成下载并校验固定版本的 Spine WebGL 运行库，成品可离线打开。原始骨架和图集贴图保存在 `model-data.js`，没有视频依赖。Wallpaper Engine 属性中的「保留完整卡面」只改变取景；「隐藏卡面背景」隐藏 `BG` 槽，保留角色和特效，供同一网页内透明叠加。普通浏览器可用 `?transparent&whole` 预览。公开分发前须确认游戏素材和随包 Spine 运行库的授权条件。

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
