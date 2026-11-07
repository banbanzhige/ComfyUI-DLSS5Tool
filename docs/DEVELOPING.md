# 开发与验证

生产代码在 dlss5tool_comfy/，参考快照在 reference/upstream/，工作流在 workflows/。
插件不依赖开发记录、交接资料或个人环境目录。

## 检查

在项目根目录运行：

~~~powershell
python -B scripts/check_project.py
python -B scripts/verify_handoff.py
python -B scripts/check_versions.py
python -B -m unittest discover -s tests -p test_runtime_setup.py
python -B -m unittest discover -s tests -p test_versioning.py
~~~

以上使用标准库，不下载运行库或启动 GPU。暂存修改后运行
`git diff --cached --check` 和 `python -B scripts/check_upload.py`，检查实际上传内容。

使用具备依赖的 ComfyUI Python 运行 `scripts/run_plugin_checks.py`，验证插件 CPU 合同。
`scripts/validate_comfy.py` 检查真实执行器、缓存和 EXR 回读；`scripts/validate_native.py`
检查 GPU、分块及进程回收，参数见各脚本的 --help。实机检查不能由 CPU 或 CI 结果替代。

`scripts/run_reference_checks.py` 只验证三个固定上游 CPU 模块，依赖为 requirements-reference.txt。
来源校验默认固定 74 个参考文件的原始摘要；--strict 仅用于持有本地初始交接清单的维护者。

## 临时文件与来源

临时材料使用 tmp/ 下专属任务子目录，先用 TASK.md 登记归属、用途、重建命令和复核日期。
下载 / 解压前留足峰值预算加 15 GiB 空间。文件操作前检查绝对路径并拒绝重解析点；
清理仅限本任务可重建内容，不清空共享目录、用户数据、SDK 或运行库缓存。

保留 [来源清单与第三方通知](../THIRD_PARTY_NOTICES.md)。原始清单描述来源快照，
部分交接材料只由维护者本地保留，不是运行依赖；不为消除检查失败而改写历史摘要。
原生宿主可通过 native/host_v2/build.bat 构建，需单独准备并审阅 NVIDIA SDK 与 VS C++ 工具。
修改随附宿主时同步源码、运行库清单和验证结果。

## 版本维护

- 插件版本在 dlss5tool_comfy/__init__.py，与 version.json、README、CHANGELOG 和 Git 标签保持一致。
- 修改生产代码、工作流、DLL 或 Python 依赖时提高插件版本；公测顺序如 beta.1 → beta.2。
  CI 会与 PR 基线 / 上一次推送比较，遗漏升级版本会失败。
- 运行库清单使用 catalog_revision，宿主及每档 NR 各有 revision，都是本项目修订号。
  修改 DLL / 归档身份时提高该组件修订，清单变更还须提高 catalog_revision。
- version.json 固定 UTF-8 / LF 的运行库清单及 requirements.txt 的 SHA256；修改后同步摘要。
  发布标签和附件不能原位替换；新 DLL 用新附件身份，旧版本继续固定原摘要。
- 发布前运行 `python -B scripts/check_versions.py --base <上个版本标签>` 与暂存内容审计。
  标签使用 v 加完整插件版本号，推送代码和对应标签；首次版本从未版本化基线引入清单。
- 回退先停止 ComfyUI，切换已发布标签并重启；NR 自动复用对应的旧校验缓存。
  Python 环境不自动回退或升级，需要另行核对该版本 requirements.txt；不修改 Torch / CUDA。
