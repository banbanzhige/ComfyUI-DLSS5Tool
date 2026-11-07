# ComfyUI-DLSS5Tool

用于 ComfyUI 的图片增强插件，支持 SDR / HDR、自动分块和保护遮罩。

当前版本：**v0.1.0-beta.1（公测）**，见 [更新记录](CHANGELOG.md)；启动日志也会显示版本。

## 安装

1. 在启动器的自定义节点 Git 安装入口填写：
   `https://github.com/banbanzhige/ComfyUI-DLSS5Tool.git`
   也可克隆到 `ComfyUI/custom_nodes/ComfyUI-DLSS5Tool/`。
2. 使用 ComfyUI 的 Python 安装 [requirements.txt](requirements.txt) 中缺少的依赖，然后重启。
3. 导入 [示例工作流](workflows/examples/)，选择自己的图片即可开始。

## 使用

- **Model Loader**：两项保持 `auto`，自动匹配显卡与运行库。
- **Enhance**：增强图片，自动分块，保持原始尺寸。
- **Adjust**：调整效果；保护遮罩白色保留原图、黑色使用增强结果。

`dlssnr_host_v2.dll` 已包含。首次运行需联网下载匹配的 NR，之后使用本地缓存。
HDR 接线和保存方法见 [使用说明](docs/USAGE.md)。
更新插件后重启 ComfyUI；DLL 按该版本清单匹配，已校验的旧缓存保留。

## 兼容性

依赖要求 Python 3.11 或更新版本；实测为 Python 3.13。
已在 Windows / ComfyUI 0.44.0 / RTX 4070 SUPER 验证 SDR、HLG、PQ 和线性 HDR。
RTX 30 / 50 已有自动匹配档位，仍待对应显卡实测；更多环境和大图画质也待验证。

## 许可

保留 GPLv3 和原始 MIT 声明，见 [LICENSE](LICENSE) 与 [第三方说明](THIRD_PARTY_NOTICES.md)。
NVIDIA SDK 和 NR 本体不放入 Git；本项目与 NVIDIA 无官方关联。
