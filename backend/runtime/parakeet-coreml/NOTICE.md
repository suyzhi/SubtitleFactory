# Parakeet Core ML 转写组件

`bin/parakeet-coreml` 由字幕工厂源码 `backend/runtime/parakeet-coreml` 构建，
链接了以下开源组件，模型文件由 App 在首次使用时从官方来源下载，不随安装包分发。

| 组件 | 来源 | 许可证 |
| --- | --- | --- |
| FluidAudio 0.9.1 | https://github.com/FluidInference/FluidAudio | Apache-2.0（见同目录 LICENSE） |
| Parakeet TDT 0.6B v3（原始模型） | https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 | CC-BY-4.0 |
| Parakeet TDT 0.6B v3 Core ML 转换 | https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v3-coreml （固定版本 `7dd20fe6b1797d35f5e3307e8b1732d9a178edfe`） | CC-BY-4.0 |

模型署名：NVIDIA, “parakeet-tdt-0.6b-v3”；Core ML 转换由 FluidInference 提供。
