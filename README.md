# NekoConne Viewer

Windows 本地素材查看器。选择自己的 Steam 安装目录，按类型提取素材，按名称或编号查找，导出图片、音频、视频或整套模型，再生成离线 Wallpaper Engine 项目。默认不运行游戏，不连接游戏服务，不修改安装目录，也不应用桌面壁纸。工作流参考 [UmaViewer](https://github.com/katboi01/UmaViewer)，实现不依赖 UmaViewer 或 Unity 编辑器。

## 快速开始

从 [GitHub Releases](https://github.com/QuinMeow/NekoConneViewer/releases/latest) 下载 **NekoConneViewer-windows-x64.zip**，完整解压到可写目录，双击 **NekoConneViewer.exe**。无需安装 Python、FFmpeg 或 .NET，也不用配置 PATH。首次打开时选择自己的游戏目录；如果已有解码结果，把“本地导出缓存”指向已有的 `exports`，不必重新解码。

仅发布 Windows 10/11 x64。解包、图片、语音和视频均可离线处理；Cubism 渲染使用所选游戏安装中的 Core 和显卡的 OpenGL 驱动。**仅 Spine 渲染首次需要联网**：查看器从上游下载固定版本的自包含组件并校验 SHA-256，之后使用本地缓存。下载失败不会阻止浏览素材或导出骨架。无需下载 Release 中另列的媒体源码包来运行程序。

如使用纯源码包，建议以 **Windows x64、Python 3.10（含 Tkinter）** 开始：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe viewer_gui.py
```

源码目录也可双击 **Start-Viewer.pyw**（需要 Windows 已关联 Python）。如果 CMD 在任何批处理执行前就提示 AutoRun 解析错误，直接用 EXE 或 PYW 即可，无需修改注册表；要临时验证批处理，可在 PowerShell 中运行 `cmd.exe /d /c .\Start-Viewer.cmd`。

下表区分便携版已集成的组件与开发时的源码依赖。游戏资源、游戏的 DLL 和第三方 Spine 程序本体不随此项目再分发。

| 功能 | 便携版如何提供 |
| --- | --- |
| 内置包、PNG、模型导出 | 内置 [UnityPy](https://github.com/K0lb3/UnityPy)、Pillow 和 Python 运行时 |
| USM 视频与游戏语音 | 内置从固定源码编译的 [FFmpeg](https://ffmpeg.org/)、libvpx、Opus 和 [vgmstream r2168](https://github.com/vgmstream/vgmstream/releases/tag/r2168)；对应源码及配方与 ZIP 一起发布 |
| Cubism 动作 | 内置渲染器，直接调用游戏随附的 Cubism Core，不加载外部 Python 环境。另有可选的源码版 live2d-py 路线，依赖见 `requirements-render.txt` |
| Spine 动作／皮肤 | 首次从 [SpineViewerCLI v0.16.34](https://github.com/ww-rm/SpineViewer/releases/tag/v0.16.34) 自动获取带 .NET 的 Windows 自包含组件，渲染调用已随包提供的 FFmpeg |

## 使用

1. 选游戏根目录与独立缓存目录。默认只勾选精选图片、Spine 和 Live2D；全语音解码会写入约 7.8 GB。可通过 `NEKOCONNE_GAME`、`NEKOCONNE_CACHE` 设置初值。
2. 用“运行所选任务”读取游戏，或用“导入外部素材目录”读取已有标准 UnityFS / DDS。目录没有已知格式时，不会将文件改扩展名冒充导出成功。每次解码的细节都在“任务日志”页。
3. 按名称、角色编号或类别搜索。图片显示缩略图；音视频通过系统播放器打开。选择模型后，可在下拉框选择动作（Spine 同时选择皮肤标签），渲染为透明 PNG 和 WebM。导出模型时会复制整套文件，包括配套贴图；不会覆盖同名导出。
4. 选择图片、已渲染的动作视频或原始视频，点击“生成壁纸”。选中 Cubism 模型时，若其动作均已渲染，可直接生成带背景、待机、点击动作的完整工程。
5. 在浏览器中打开生成目录内的 `index.html` 即可离线预览。按 [Wallpaper Engine 官方导入说明](https://docs.wallpaperengine.io/en/web/first/gettingstarted.html) 将该文件拖入编辑器“创建壁纸”。编辑器会复制整个项目目录，所以不要直接拖入素材缓存。

不经 GUI 也能重建示例，或替换背景。例如，在已导出内置模型和场景图的情况下：

```powershell
python tools/build_scene.py exports/builtin/cubism/10106_01_S01/10106_01_S01.model3.json --dest exports/projects --render-missing
python tools/build_scene.py exports/builtin/cubism/10106_01_S01/10106_01_S01.model3.json --dest my-projects --background my-background.png
```

直接使用各导出器的 `--help` 查看更多选项。例如 `export_images.py --match` 只匹配指定名称，`export_audio.py --select` 按 cue sheet 筛选，`render_spine.py --list` 查看骨架中实际存在的皮肤与动作。

## 已验证的范围

本次在 Steam 安装的 Unity 6000.2.15f1 数据上检验，数量是这一份安装的结果，不是对其他版本的保证。

| 资源 | 实际输出与限制 |
| --- | --- |
| 内置图像 | 2,392 个有效 Sprite / Texture2D 对象导出 PNG。另有 12 个零尺寸字体纹理没有像素。Sprite 裁片与其原图集可能显示相同内容，因此这不是独立立绘数。另存 14 张精选图 |
| Cubism | 1 套模型，2 页纹理、5 段实际加载并渲染的动作、10 个表情文件。模型使用包内 MOC3；JSON 从 Unity 播放数据重建，不是美术源工程。没有还原全部物理或游戏逻辑 |
| Spine | 6 套 4.1.11 骨架，10 页配套纹理、合计 100 个模型动作条目。6 套均已导出合成 PNG 和示例动作视频。另导出 48 张皮肤／方向视图；例如 NPC 的 32 个标签在同一姿态下只形成 16 种不同图像，不是 32 套衣服 |
| CRI 语音 | 20,423 条 WAV / 20,426 条语音波形记录。3 条缺少匹配的完整音库；保留失败清单而非输出静音占位。输出量约 22 小时 27 分，抽样确认日语可懂，未人工逐条试听 |
| CRI 视频 | 78 个 VP9 WebM，逐个完整解码检查。保留 61 条原本存在的音轨（其中一条源音轨为静音）；其他 17 个原本没有音轨。预渲染视频不是可编辑模型 |

在缓存内，`builtin/inventory.csv` 对应 Unity 的对象路径与编号；`images/index.csv` 对应逐张图片的来源和例外；`video/index.html` 可凭缩略图选择影片；`audio/listen.html` 可按 cue 或编号试听。外部导入结果的 manifest 独立于内置包。

**覆盖边界：** 安装中还有当前查看器不能自动识别的外部文件。本工具没有建立全游戏的资源名映射，不声称已提取所有卡面、皮肤或剧情。没有实现进程读写、远程清单抓取或自动搜集凭据。

## 源码与测试

```powershell
python -m unittest discover -s tests -v
python tools/package_source.py
```

在 Git 工作副本中，`package_source.py` 从当前提交生成 `dist/NekoConneViewer-source.zip`，不读取未提交的文件。源码包和 Windows 发行包均不含游戏图片、音视频或预生成素材库。

构建由 [GitHub Actions](.github/workflows/release.yml) 在 Windows x64 原生 runner 上执行。手动运行 **Build Windows portable** 只生成可下载的构建产物；推送 `v*` 标签会在测试、原生媒体工具编译和解压冒烟测试成功后发布 Release，上传便携 ZIP、对应的媒体源码 ZIP 与 SHA-256 校验清单。要在本地复现，可按同一工作流运行 MSYS2 UCRT64 工具链、`packaging/media.py --prepare`、两份媒体构建脚本和 `packaging/build_windows.py`。图形程序与后台解码程序共享同目录运行库，请整体分发。

本项目原创工具源码采用 MIT 许可证（见 `LICENSE`）。游戏素材不属于该许可证；第三方程序与运行库各自适用其上游条款。本项目未代表游戏官方，也不自动发布到 GitHub 或创意工坊。
