# 来源与第三方说明

本插件基于 [DLSS5Tool](https://github.com/banbanzhige/DLSS5Tool) 的源码和参数合同，
保留根 [GPLv3 文本](LICENSE) 与 [原始 MIT 声明](licenses/DLSS5Tool-original-MIT.txt)。

- [原始来源清单](docs/provenance/SOURCE_MANIFEST.json) 保存来源版本、相对路径和 SHA256。
- [上游第三方通知](docs/provenance/UPSTREAM_THIRD_PARTY_NOTICES.md)、
  [实时参考通知](docs/provenance/UPSTREAM_REALTIME_NOTICE.md) 和来源锁定清单保留原文。
- reference/upstream/ 和 native/quality_reference/ 为来源参考，不参与插件 Python 运行时导入。
- backend/adapters.py 提取物理设备发现，core/residual.py 提取 NumPy 残差算法；
  native/host_v2/ 保留宿主源码和构建入口，其他节点、色彩与宿主进程逻辑为本插件适配。

NumPy、SciPy 使用 BSD 许可，Pillow 使用 MIT-CMU 许可；Torch 由 ComfyUI 环境提供。
NVIDIA 光流头声明、torchvision 参考源码保持 licenses/ 中各自的原始声明。

## NVIDIA 宿主与运行库

runtime/dlssnr_host_v2.dll 随仓库提供，源码位于 native/host_v2/，大小和 SHA256 固定在
[运行库清单](dlss5tool_comfy/backend/runtime_catalog.json)。链接的 SDK 部分保留
[NVIDIA RTX SDK 条款](licenses/NVIDIA-RTX-SDK-LICENSE.txt)，不重新许可供应商组件。

This software contains source code provided by NVIDIA Corporation.

NVIDIA SDK、NR 本体、模型和驱动不放入 Git。自动配置从固定的维护者附件取得 NR，
压缩包和 DLL 分别核验摘要；该来源不是 NVIDIA 官方托管或官方兼容承诺。
SDK 条款不替代各运行库来源的权利。本项目与 NVIDIA 无官方关联。
