# 使用说明

## 安装与首次运行

安装到 ComfyUI/custom_nodes/ComfyUI-DLSS5Tool/，使用 ComfyUI 的 Python 安装缺少的
requirements.txt 依赖后重启。插件沿用已有 Torch / CUDA。

当前版本为 v0.1.0-beta.1（公测）。requirements.txt 固定版本要求 Python 3.11 或更新版本；
实际验收为 Python 3.13。仅 Git 克隆不会安装 Python 依赖，首次安装需由支持依赖安装的工具处理，
或使用 ComfyUI 自身的 Python 手动安装。首次 NR 获取还需联网。
全新环境中从启动器安装到首次出图的完整流程仍待验收。

Model Loader 的 render_gpu 和 dll_selection 默认保持 auto。宿主已包含；首次执行时
下载约 102–112 MiB 的匹配 NR，校验后保存在 runtime/cache/，后续可离线复用。
多卡优先选择受支持且专用显存最大的设备，也可手动指定显卡与 custom DLL 路径。

下载失败可重试；确认 GitHub Release 附件可访问，并预留下载预算加 15 GiB 可用空间。
校验不符时已有文件会保留。DLSS5TOOL_RUNTIME_DIR 可指定已有的持久 NR 缓存目录。

## 版本与升级

启动日志显示插件版本、运行库清单修订及宿主版本；实际执行 Loader 时记录所用 NR 修订和摘要。
可用 ComfyUI 自己的 Python 运行 `python -B scripts/show_version.py` 查看版本，
加 `--json` 可导出不含私人路径的反馈信息。查询不联网、不创建 GPU 会话。

version.json 将插件版本绑定到 DLL 清单和 Python 依赖。更新插件并重启后，
首次执行会按新清单校验 / 获取匹配 NR；旧缓存保留，回退到旧标签可复用其旧 NR 缓存。
自定义 DLL 继续可用，以执行日志中的实际摘要为准；Python 依赖需按对应版本的 requirements.txt 核对。

## 节点接线

Model Loader.model → Enhance.model；Load Image.IMAGE → Enhance.image；
Enhance.result → Adjust.result；Adjust.image → 保存节点。

Enhance.image 也可直接使用。Alpha 独立传递，Load Image.MASK 可接 Enhance.alpha_mask。
Adjust 的 protect_mask 白色保留源图、黑色使用增强结果；mask_feather 默认 0，表示不额外羽化。
遮罩与图片尺寸必须一致；B=1 遮罩可广播到批次。

现成接线见 [示例工作流](../workflows/README.md)，导入后选择自己的图片。

## HDR 与保存

适配 ComfyUI 0.44.0 的原生色彩节点，按明确的上游色彩信息处理：

| 上游 | 保存方式 |
| --- | --- |
| ImageSDRToHDR 或 ImageColorSpace → HDR | HLG 输出，SaveImageAdvanced 选 EXR、HDR |
| ImageColorSpace → HDR PQ | PQ 输出，先用 ImageColorSpace 转 linear，再保存 EXR、linear |
| ImageColorSpace → linear | 线性输出，保存 EXR、linear |

正式 HDR 输出保持浮点，预览使用独立 SDR 映射副本。线性负值和超白保持在正式输出中，
超过 10000 nit 的信号会报错。PQ AVIF 编码路径尚未验收。

没有可信色彩标记的 IMAGE 按 sRGB；不根据像素范围猜测 HDR，也不自动识别任意 EXR、ICC
或第三方加载器的色彩。LogC3、ACEScg、ACEScct 请先转换为上表受支持的格式。

## 验证范围

功能检查环境：Windows / RTX 4070 SUPER 12 GiB / 驱动 621.01 / ComfyUI 0.44.0 /
Python 3.13.11 / Torch 2.9.1+cu130。已检查 SDR、HLG、PQ、线性 HDR、Alpha、遮罩、
批次、缓存、分块和取消 / 超时 / 崩溃回收，EXR 经独立解码核对。

RTX 30 / 50 已核对下载文件和自动匹配逻辑，未做对应显卡推理验收。
目前使用合成输入做功能检查；真实照片画质、更多环境和大图完整验收仍待进行。
