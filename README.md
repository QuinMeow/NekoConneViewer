# NekoConne Viewer

## 功能

- **图片**：浏览角色立绘、皮肤和场景图，按名称或编号查找，导出为 PNG。也可以导入 UnityFS 素材包和 DDS 贴图。
- **模型与动作**：导出 Live2D（Cubism）和 Spine 模型及配套贴图。选定动作后，可以渲染透明背景的 PNG 和 WebM；Spine 还可以选择皮肤。
- **语音与视频**：将 CRI 语音导出为 WAV，按编号试听；将游戏影片导出为 WebM，保留原有音轨。
- **动态壁纸**：用图片或视频生成 Wallpaper Engine 网页壁纸，也可以用渲染好的 Live2D 动作制作带待机和点击反应的角色壁纸。

## 快速开始

### 下载编译版

1. 下载 [Windows x64 便携版 ZIP](https://github.com/QuinMeow/NekoConneViewer/releases/latest/download/NekoConneViewer-windows-x64.zip)，完整解压到可写目录，双击 `NekoConneViewer.exe`。
2. 选择 Steam 中的 NekoConne 安装目录，勾选素材类型，点击「运行所选任务」。结果默认保存在 `exports/`；已有素材库可直接选作「本地导出缓存」。全量语音约占 7.8 GB。
3. 在「素材库」中搜索角色或编号，选中结果后点击「打开」或「导出素材」。要制作壁纸，点击「生成壁纸」，将生成目录中的 `index.html` 拖入 Wallpaper Engine 编辑器。

便携版适用于 Windows 10/11 x64，已包含 Python、FFmpeg 和语音解码器。只有首次渲染 Spine 动作需要联网：程序自动下载渲染组件，校验后缓存，下次可离线使用。Live2D 渲染使用游戏自带的 Cubism Core 和显卡的 OpenGL 驱动。

### 从源码编译

1. Fork 仓库，在自己的仓库打开 **Actions → Build Windows portable → Run workflow**。
2. 构建完成后，在该次运行的 **Artifacts** 中下载 `windows-x64`。解压后，里面的 `NekoConneViewer-windows-x64.zip` 就是便携版。

要在自己的 Windows 电脑上编译，见[本地构建与开发说明](https://github.com/QuinMeow/NekoConneViewer/blob/main/docs/development.md)。推送 `v*` 标签会触发同一套构建，测试通过后自动发布到 [Releases](https://github.com/QuinMeow/NekoConneViewer/releases)。

## 许可证

查看器源码采用 [MIT 许可证](LICENSE)。游戏素材归原权利人所有；便携版的第三方组件许可见 `THIRD_PARTY_LICENSES/`。
