# Mac App Store 产品与交付基线

## 唯一产品标杆

字幕工厂以 Apple 的 Final Cut Pro 为唯一主标杆，但不复制其品牌或视觉资产。重点学习的是专业媒体应用的产品结构：资源库管理、项目工作区、中央播放器与时间轴、按需检查器、可见的后台任务、键盘操作以及一步式导出。Final Cut Pro 当前的 App Store 页面同时强调资源库、智能搜索、字幕生成、Apple Silicon 性能和优化输出，这些维度与字幕工厂的目标最接近。

- 标杆来源：https://apps.apple.com/us/app/final-cut-pro-create-video/id1631624924?platform=mac
- App Review Guidelines：https://developer.apple.com/app-store/review/guidelines/
- App Sandbox：https://developer.apple.com/documentation/security/app-sandbox
- 隐私清单：https://developer.apple.com/documentation/bundleresources/adding-a-privacy-manifest-to-your-app-or-third-party-sdk
- required-reason API 适用平台：https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api
- Tauri App Store 交付：https://v2.tauri.app/distribute/app-store/
- Tauri macOS Hardened Runtime 配置：https://v2.tauri.app/reference/config/#macconfig

## 发行通道

| 能力 | 直装版 | Mac App Store 版 |
| --- | --- | --- |
| 本地视频导入、转写、编辑、质检和导出 | 保留 | 保留 |
| App 管理的本地模型下载 | 保留 | 保留，并在下载前显示大小 |
| 用户明确授权的云端 AI / 云端转写 | 保留 | 保留 |
| YouTube 网页播放、下载和 Chrome Cookie 重试 | 保留 | 禁用 |
| 持久监听文件夹、路径式批量导入 | 保留 | 在实现安全作用域书签前禁用 |
| 外部模型、CLI 和自定义下载目录 | 保留 | 禁用 |

App Store 版的限制是发行策略，不是运行失败后的静默降级。Rust 启动器把编译期通道传给前端和 Python sidecar；前端不显示入口，后端仍会对越权请求返回稳定的 `DISTRIBUTION_FEATURE_UNAVAILABLE`。

## 当前 P0 清单

- [x] 独立发行通道与前后端双重能力门禁。
- [x] App Store sidecar 构建时强制排除下载器实现、`yt-dlp` 与 Deno，并检查最终运行包。
- [x] App Store 的 App Sandbox、网络、用户选取文件与 Keychain 权限模板。
- [x] 保留 Tauri 默认开启的 Hardened Runtime，并在最终逐层签名时显式使用 `--options runtime`。
- [x] `Info.plist` 出口合规声明与 `PrivacyInfo.xcprivacy` 基础清单。
- [x] 核对 Apple 当前 required-reason API 文档：其申报平台不包含原生 macOS，因此当前 `NSPrivacyAccessedAPITypes` 保持空数组，不添加未经证实的理由。
- [ ] 使用完整 Xcode 安装并确认当前 macOS SDK。
- [ ] 注册与 `com.subtitlefactory.desktop` 一致的 App ID，或在所有版本文件中统一替换为最终 Bundle ID。
- [ ] 安装 Mac App Distribution、Mac Installer Distribution 证书和 Mac App Store Connect provisioning profile。
- [ ] 对主 App、Python sidecar、FFmpeg/FFprobe、Vision OCR 和所有嵌套 Mach-O 逐层签名；helper 只使用 sandbox + inherit 权限。
- [ ] 在沙盒中验证导入、模型下载、Keychain、localhost sidecar、云端授权、导出和退出清理。
- [ ] 在 Apple Distribution 签名的完整 `.app` 上分别记录首次启动与第二次启动时间；本地 ad-hoc 独立 sidecar 的对照值为约 29.5 秒 / 0.5 秒，不能代替正式信任链验收。
- [ ] 使用最终 Xcode 版本生成并检查 Privacy Report，再通过 App Store Connect 自动验证确认隐私清单；如果 Apple 后续把原生 macOS 纳入 required-reason API 范围，则按实际调用补充批准理由。
- [ ] 生成并验证签名 `.pkg`，上传 App Store Connect/TestFlight，处理自动验证与审核反馈。

## 当前外部前置条件

2026-08-11 的本机检查结果：macOS 26.6.1、arm64；只有 `/Library/Developer/CommandLineTools`，没有 `/Applications/Xcode.app`；钥匙串中没有有效代码签名身份。因此源码、测试和本地 ad-hoc `.app` 可以继续优化，但 App Store 签名 `.app`、`.pkg` 与上传必须等完整 Xcode、开发者团队资料、证书和 provisioning profile 就绪后才能完成。
