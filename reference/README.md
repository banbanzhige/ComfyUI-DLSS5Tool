# 后端迁移参考

upstream/dlss5tool 是按静态 Python 导入递归计算的完整本地模块依赖闭包，保持逐字节原文。
核心种子是 dlss_engine、dlss_host_process、parameter_contract、appearance、
residual_composition、image_alpha 与 app_settings。

由于 HDR 转换和宿主包装仍有跨模块引用，闭包也保留部分视频、超分、光流 / CUDA 兼容代码。
它们只为源码完整和后续拆分保留，不扩展已确认的第一版产品范围。
未复制 GUI、用户状态、虚拟环境、模型、SDK 或运行库二进制。

优先提取顺序：

1. 参数 / 外观合同：parameter_contract.py、appearance.py。
2. 独立进程与 DLL：dlss_engine.py、dlss_host_process.py、adapter_inventory.py、adapter_probe.py。
3. NumPy 残差与 HDR 合成：residual_composition.py、video_export.py 内对应的纯函数。
4. 资源管理与透明度：memory_budget.py、shared_cache_budget.py、image_alpha.py。

旧 paths.py、mod_paths.py、app_settings.py 仍是参考语义，不能未经适配作为插件设置系统。
尤其不能导入桌面 GUI，也不能把原图片读写入口的 8 位转换当作新张量接口。

upstream/tests 仅保留三个无需 GPU 的选定模块：host_queue、hdr_pipeline、sdr_composition_buffers。
它们可独立运行，验证参考合同；不表示新的三节点已经实现。
