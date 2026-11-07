# 插件宿主与运行库缓存

dlssnr_host_v2.dll 随仓库提供，是唯一允许提交的 DLL，身份固定在
dlss5tool_comfy/backend/runtime_catalog.json，源码保留在 native/host_v2/。
SDK 条款见 licenses/NVIDIA-RTX-SDK-LICENSE.txt，来源与编译组件说明见 THIRD_PARTY_NOTICES.md。

默认 auto 在首次执行 Model Loader 时识别 GPU，获取并验证对应的 NVIDIA DLSSNR 附件。
最终 DLL 缓存在 cache/ 的独立版本目录；cache、锁、事务文件及手动放置的 NR 均不提交。
导入和 schema 查询不联网；已校验缓存可离线复用，显卡 / 清单 / 文件身份变化会重新检查。
version.json 将插件版本绑定到本清单的修订与摘要；宿主和每档 NR 的 revision 是本项目维护编号，
不代表供应商文件版本。内容更新使用新摘要目录，旧缓存保留，回退标签后可复用旧匹配文件。

本地重建宿主仍可用 native/host_v2/build.bat，需自行准备 SDK 和 VS C++ 工具，
重建后按来源记录更新宿主身份，不能跳过摘要检查。安装用户无需这些构建工具。
