# 工作流资料

examples/ 保存已在指定环境执行的 UI / API 工作流，界面图从真实前端导出：

| 示例 | 内容 | 验证 |
| --- | --- | --- |
| 01_basic_sdr | Load Image → Enhance → Adjust → Save Image | 实际执行器 / GPU |
| 02_masked_sdr | 增加 Load Image (as Mask)，默认示例羽化 2 px | 实际执行器 / GPU / 软边 MASK |
| 03_native_hlg | 原生 SDR→HDR → Enhance → Adjust → HDR EXR | 实际页面执行、双预览、EXR 回读 |
| 04_native_pq | 原生转 PQ → Enhance → Adjust → 转 linear → EXR | 实际执行器 / GPU / EXR 回读 |
| 05_native_linear | 原生转 linear → Enhance → Adjust → linear EXR | 实际执行器 / GPU / 超白及 EXR 回读 |

每套 .json 用于界面导入，.api.json 用于 API；在自己的安装中选择输入图片，Loader 默认 auto / auto。
contract.png 是验证脚本生成的合成输入名称，不是随项目附带的用户素材。
首次运行会匹配 GPU 并校验下载对应运行库，自有宿主已包含；可手动覆盖 GPU 与 DLL。
当前推理证据仍来自 RTX 4070 SUPER，不能扩写为所有硬件均已验收。
安装、HDR 保存设置与验证范围见 [使用说明](../docs/USAGE.md)。

planned/ 保留带占位输入的初始拓扑模板，已同步现行 schema：

- 01_basic_sdr：Load Image → Model Loader / Enhance → Adjust → Save Image。
- 02_masked_sdr：增加现有 Load Image (as Mask) → Adjust.protect_mask。

稳定节点 ID 为 DLSS5ToolModelLoader、DLSS5ToolEnhance、DLSS5ToolAdjust，
与 node-contract.json 对应。风格用原生下拉，后端仍映射 0 / 1 / 2。
示例输入文件是占位名称，使用时需选择自己的图片和遮罩。

占位模板的原文件不作为已执行证据；使用 examples 中的实际版本。
真实照片画质、更多硬件和更大图片仍待验收。
