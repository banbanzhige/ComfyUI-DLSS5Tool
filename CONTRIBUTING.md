# 贡献

修改前先检查现有差异，保持任务范围清晰。节点、参数和工作流变更应同步文档，
验证方法见 [开发说明](docs/DEVELOPING.md)。

生产代码位于 dlss5tool_comfy/，根入口只负责注册。reference/upstream/ 是固定来源快照，
运行时不依赖它；修改快照需单独记录来源，不能为通过检查而重写历史摘要。

提交前运行静态、来源和受影响模块检查，再暂存明确的修改并执行：

~~~powershell
git diff --cached --check
python -B scripts/check_upload.py
~~~

不要提交开发环境、NR / SDK、下载缓存、内部记录、私人素材或凭证。
runtime/dlssnr_host_v2.dll 是固定摘要的随附宿主，保留其源码和第三方通知。
提交使用 GitHub noreply 邮箱。不要更改宿主 ComfyUI 的 Torch / CUDA。

PR 说明改动、实际验证和未测范围；GPU / HDR 结论须有对应实机证据。
保留 [LICENSE](LICENSE)、原始 MIT 声明和 [第三方许可](THIRD_PARTY_NOTICES.md)。
