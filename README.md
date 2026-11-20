# NekoConne Viewer

## 功能

- **图片**：浏览角色立绘、皮肤和场景图，按名称或编号查找，导出为 PNG。也可以导入 UnityFS 素材包和 DDS 贴图。
- **模型与动作**：导出 Live2D 和 Spine 模型及贴图。素材库可预览首帧，也可多选模型后批量渲染。Live2D 支持按屏幕尺寸输出，Spine 会裁去透明留白。
- **角色筛选**：启动后显示本机游戏头像网格，角色和变体统一按五位 ID 归组。默认显示 `1` 开头的角色，勾选后可纳入其他角色。导出前可多选，导出后也可点头像筛选。
- **音频与影片**：将识别到的 CRI 语音、音乐和音效导出为 WAV；将游戏影片导出为 WebM，保留可解码的音轨。视频可在素材库中直接播放。
- **动态壁纸**：用图片或视频生成 Wallpaper Engine 网页壁纸，也可以用渲染好的 Live2D 动作制作带待机和点击反应的角色壁纸。
- **原生 Spine 卡面壁纸**：选中只有 `default / animation` 的 Spine 4.1 模型，直接点「生成壁纸」。网页实时播放骨架，不生成视频；可在属性里保留完整卡面或隐藏原背景。
- **双角色壁纸原型（源码工具）**：10102 和 10106 同屏，分别调整大小和位置，点击播放各自的主页语音，眼睛跟随鼠标。可在标题图和花园间切换。[导入与重新生成](docs/development.md#双角色壁纸原型)。

## 快速开始

### 下载编译版

1. 下载 [Windows x64 便携版 ZIP](https://github.com/QuinMeow/NekoConneViewer/releases/latest/download/NekoConneViewer-windows-x64.zip)，完整解压到可写目录，双击 `NekoConneViewer.exe`。
2. 选择 Steam 中的 NekoConne 安装目录。程序先从本机建立角色目录，再让你选角色和导出类别；只选角色不会马上开始导出。
3. 点击「导出所选类别」。并行解包默认使用两个工作线程，可在主窗口调至 1–4。各类素材平级写入缓存，未识别项见 `index/report.json`。
4. 在素材库中预览或渲染结果。点击「生成壁纸」后，把 `wallpapers/` 中对应的 `index.html` 拖入 Wallpaper Engine 编辑器。

便携版适用于 Windows 10/11 x64，已包含 Python、FFmpeg 和语音解码器。只有首次渲染 Spine 动作需要联网：程序自动下载渲染组件，校验后缓存，下次可离线使用。Live2D 渲染使用游戏自带的 Cubism Core 和显卡的 OpenGL 驱动。

### 从源码编译

1. Fork 仓库，在自己的仓库打开 **Actions → Build Windows portable → Run workflow**。
2. 构建完成后，在该次运行的 **Artifacts** 中下载 `windows-x64`。解压后，里面的 `NekoConneViewer-windows-x64.zip` 就是便携版。

要在自己的 Windows 电脑上编译，见[本地构建与开发说明](https://github.com/QuinMeow/NekoConneViewer/blob/main/docs/development.md)。推送 `v*` 标签会触发同一套构建，测试通过后自动发布到 [Releases](https://github.com/QuinMeow/NekoConneViewer/releases)。

## 许可证

查看器源码采用 [MIT 许可证](LICENSE)。游戏素材归原权利人所有；便携版的第三方组件许可见 `THIRD_PARTY_LICENSES/`。
