# GPU 合成参考

这些文件按原文保存，来源路径和摘要见 docs/provenance/SOURCE_MANIFEST.json。
其中 quality.cpp 依赖生成的 shaders/VeyraResidualCS.h；本目录没有该生成物，
也没有完整 Magpie / Veyra 外部源码，因此不是独立可构建目标。

CMakeLists.upstream.txt 保留原目标定义，reference/upstream/scripts/prepare_realtime_build.py
保留原 shader 适配逻辑。固定来源版本与约束见 docs/provenance/。
新项目先保留 NumPy 参考链路，在需要 GPU 合成时提取最小构建并做对应验收。
