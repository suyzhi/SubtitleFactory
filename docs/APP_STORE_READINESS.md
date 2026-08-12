# Mac App Store 产品与交付基线

## 唯一产品标杆

字幕工厂以 Apple 的 Final Cut Pro 为唯一主标杆，但不复制其品牌或视觉资产。重点学习的是专业媒体应用的产品结构：资源库管理、项目工作区、中央播放器与时间轴、按需检查器、可见的后台任务、键盘操作以及一步式导出。Final Cut Pro 当前的 App Store 页面同时强调资源库、智能搜索、字幕生成、Apple Silicon 性能和优化输出，这些维度与字幕工厂的目标最接近。

- 标杆来源：https://apps.apple.com/us/app/final-cut-pro-create-video/id1631624924?platform=mac
- App Review Guidelines：https://developer.apple.com/app-store/review/guidelines/
- App Sandbox：https://developer.apple.com/documentation/security/app-sandbox
- 沙盒文件访问：https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox
- 隐私清单：https://developer.apple.com/documentation/bundleresources/adding-a-privacy-manifest-to-your-app-or-third-party-sdk
- required-reason API 适用平台：https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api
- App Store 证书：https://developer.apple.com/help/account/certificates/certificates-overview
- Mac App Store provisioning profile：https://developer.apple.com/help/account/provisioning-profiles/create-an-app-store-provisioning-profile
- App 隐私申报：https://developer.apple.com/app-store/app-privacy-details/
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

## 2026-08-11 性能与交互基线

- 项目库首屏主 JavaScript 从 `535.79 kB / gzip 171.64 kB` 降至约 `396.8 kB / gzip 118.2 kB`；设置中心、播放器、内容中心、播放列表和批量生产工具改为按需加载，构建不再出现 500 kB 主块警告。
- 空闲项目库的播放列表刷新从每 2 秒一次降为每 30 秒一次；只有 `pending` 或 `running` 队列保留 2 秒刷新。
- 设置中心只在打开时挂载；深色和浅色主题变量都传入 document-level portal，模态遮罩保持半透明，`Escape` 关闭后焦点回到设置按钮。
- 项目库启动不读取 Keychain；进入项目或打开设置时才读取当前多供应商状态。瞬时失败会重试一次，连续失败仍显示可恢复提示；保存供应商或任务分配后，整理、翻译和播放列表立即使用新状态，无需重启。
- 浏览器端真实路径已覆盖直装版与 App Store 版。App Store 版不渲染链接、YouTube、批量监听、外部模型或路径入口，相应后端接口同时返回 403；本地导入、运行状态与备份仍可用。

## 2026-08-12 App Store 通道实机 QA

`./scripts/package-app-store.sh --qa` 会在没有 Apple 团队证书时构建根目录 `字幕工厂-AppStore-QA.app`。它运行完整后端、前端与 lint 验证，使用 `app_store` 编译通道构建 sidecar、Vite 和 Tauri App，逐层 ad-hoc 签名所有 Mach-O，并检查沙盒权限、Hardened Runtime、arm64、隐私清单、发行 UI 标记，以及最终包中不存在 Deno、yt-dlp 和旧设置界面。冻结 sidecar 还会实际加载 PyAV、CTranslate2、MLX、Sherpa、ONNX Runtime、Pillow、SciPy 和 tiktoken，并执行一次 MLX Metal 数组运算，避免把“模块名存在”误当成原生运行时可用。随后验收器会启动根目录最终 App，通过真实会话检查 API 鉴权、发行限制、模型目录、沙箱本地导入、包内 FFmpeg 缩略图、精确项目清理、强制退出和正常退出。构建结束仍按仓库约束清理所有可重建产物。

这份 QA App 只能验证产品逻辑和沙盒边界，不能上传 App Store Connect。ad-hoc 签名没有共同的 Apple Team ID，Hardened Runtime 会拒绝 Python sidecar 加载包内动态库，因此 QA helper 会临时加入 `com.apple.security.cs.disable-library-validation`。正式构建明确禁止该权限，并要求所有嵌套 Mach-O 使用同一个 Mac App Distribution 身份；不要把 QA 签名当成正式签名证据。

本轮真实 App 验收结果：

- 187 个后端测试与 25 个子测试、61 个前端测试、6 个 Rust 测试、lint 全部通过。
- 主 App、Python sidecar 和 Vision OCR 均为 arm64，深度严格签名校验通过；主 App 有 sandbox、网络与用户选取文件权限，helper 有 sandbox + inherit。
- Tauri 展开的 45 个 PyInstaller 运行库链接会在签名前按原始相对目标恢复；脚本逐项拒绝绝对目标、目录逃逸、内容不一致或断链。本轮避免重复复制 `115,764,320 bytes`，根目录 QA App 从约 780 MB 降至 669 MB，深度签名与完整沙盒运行验收仍通过。
- 沙盒 App 能持续启动 localhost sidecar，数据库和项目文件位于 `~/Library/Containers/com.subtitlefactory.desktop/`；无令牌请求返回 401，真实会话请求返回 200。
- `app_store` 能力矩阵关闭 YouTube、浏览器 Cookie、自定义下载目录、文件系统自动化和外部运行时路径；相应越权接口在真实 sidecar 上均返回 `DISTRIBUTION_FEATURE_UNAVAILABLE`，28 个可见模型中没有外部模型 ID。
- 捆绑 arm64 FFmpeg/FFprobe 在沙盒内可用，yt-dlp、Deno 和 EJS 报告为 `disabled`。
- 用 0.5 秒真实 MP4 走与前端相同的 multipart 上传路径，项目创建、导入和读取均成功，媒体被复制进沙盒项目库，包内 FFmpeg 生成缩略图；测试项目和文件随后精确清理。
- 主 App 被强制终止后，sidecar 进程组自动退出；重新启动并正常 Quit 后，主 App、sidecar 和 PID 文件均停止或清理。

## 原生文件交付边界

字幕、带字幕成片、短片、项目包、内容发布包、术语表、诊断包和字幕样式统一使用 macOS 原生存储面板。桌面 App 不再把大文件读入 WebView `Blob`，而是让 Python 先在 App 管理的数据目录生成文件，再由 Rust 主进程流式交付到用户明确选择的位置；纯浏览器开发模式保留 HTTP 下载回退。

- Rust 会重新规范化源路径并确认它属于本次启动确定的 App 数据根目录；目录外路径和符号链接逃逸均被拒绝。
- 用户可见文件名会清理路径分隔符、控制字符与冒号，保留扩展名并限制 UTF-8 长度。
- 覆盖文件使用同目录临时文件完整写入和同步后再原子替换；写入中断不会截断原文件，也不会遗留 `.part` 文件。
- 用户取消系统存储面板会返回明确的“已取消”，不会伪报导出成功；处理日志不再显示内部绝对输出路径。
- 诊断包、术语表和内容发布包的待交付文件使用稳定的 App 管理路径，重复导出不会无限堆积临时副本。
- 发布脚本在 sidecar 资源准备完成后执行 Rust 测试，确保目录边界、文件名、逐字节复制和失败回滚成为直装版与 App Store 版共同的发布门禁。

## 长任务与异常退出恢复边界

后台任务只使用一份 SQLite 快照作为跨进程事实来源，不再额外叠加第二套恢复队列。每次 sidecar 启动会在提交任何新任务前，用一个事务结算上次进程遗留的 `pending`、`running` 和 `paused` 状态，并同步转写草稿、AI 批次、短片渲染、内容发布包与播放列表阶段。

- 已发布字幕、项目媒体、已完成阶段和编辑草稿不会被中断恢复覆盖；未发布的转写 run 会明确变为 `APP_INTERRUPTED`，草稿段保留用于诊断，界面不再把它误报为正在运行。
- 用户主动暂停的任务不会因重启而自动继续。播放列表仍显示暂停，只有用户点击“继续”后才为该阶段创建新 worker。
- 纯本地自动字幕工作流可以在重启后创建一个带父任务关联的新任务继续；旧记录保留为真实中断历史，并移除重复的重试入口。
- Fun-Realtime-ASR 等云端音频任务绝不自动重传。崩溃时服务端是否已接收计费片段可能未知，必须由用户在恢复卡片再次确认后重试。
- AI 整理和翻译的成功批次仍可作为内容指纹缓存复用；崩溃时仍为 `running` 的远端请求会标为失败，不会被后台静默重放。
- 恢复按钮按真实任务类型路由到下载、音频提取、转写、整理、翻译、成片导出、智能工具、内容工作区或模型设置；渲染失败不会再错误显示“备用模型重试”。
- 同秒产生的旧中断记录与新继续任务使用 SQLite `rowid` 作为稳定排序兜底，项目卡片和任务中心始终优先显示真正的新任务。

## 自动保存、历史与备份恢复

- 单行自动保存和手动草稿分别使用事务化正式历史与持久草稿；关闭自动保存时，每次草稿修改先同步写入 WebView 本机恢复副本，再严格串行写入 SQLite，提交和删除会等待已在途的正式编辑与草稿写入。
- 草稿提交和草稿清理在同一 SQLite 事务内完成，异常退出不会把已提交草稿再次提示给用户。旧版草稿默认只保留不套用，先预览、再显式确认，恢复也记录为可撤销操作。
- `Cmd/Ctrl + S` 、撤销与重做快捷键已与文档一致；文本输入框保留系统级撤销，不会被项目历史抢占。
- 项目切换前会等待旧项目的编辑和草稿队列收敛；带项目 ID 的异步结果仅能刷新同一活动项目，防止旧请求覆盖新工作区。
- 每份新数据库备份有独立 SHA-256 校验记录和仅当前用户可读的文件权限。恢复先取得独占维护门，要求无在途写请求、任务或尚未退出的 worker；随后创建 pre-restore 安全备份、验证、排队并请求 App 安全重启，sidecar 只在任何路由和 worker 打开 SQLite 之前原子应用备份。
- 主 App 与 frozen sidecar 以私有 EOF 生命周期管道绑定；外部信号、崩溃或强制结束绕过 Tauri 正常退出事件时，sidecar 仍会终止自身及同组 helper，避免后台孤儿进程继续占用媒体或数据库。

## 当前 P0 清单

- [x] 独立发行通道与前后端双重能力门禁。
- [x] App Store sidecar 构建时强制排除下载器实现、`yt-dlp` 与 Deno，并检查最终运行包。
- [x] App Store 的 App Sandbox、网络、用户选取文件与 Keychain 权限模板。
- [x] 保留 Tauri 默认开启的 Hardened Runtime，并在最终逐层签名时显式使用 `--options runtime`。
- [x] `Info.plist` 出口合规声明与 `PrivacyInfo.xcprivacy` 基础清单。
- [x] 核对 Apple 当前 required-reason API 文档：其申报平台不包含原生 macOS，因此当前 `NSPrivacyAccessedAPITypes` 保持空数组，不添加未经证实的理由。
- [x] 版本号、设置页占位、模型下载请求标识、README、Changelog 与 CycloneDX SBOM 纳入同一发布一致性门禁；CI 会重新生成供应链清单并拒绝未提交的漂移。
- [x] “快捷键与关于”提供可访问的完整隐私政策入口；Tauri opener 权限只允许公开仓库中的这一条精确 HTTPS URL，不能打开任意地址。
- [ ] 提供带真实、持续可联系信息的公开支持 URL；当前仓库 Issues 禁止新建，不能把它伪报为客服渠道。
- [ ] 解锁桌面后制作并人工审核 16:10 的 Mac App Store 截图（1280×800、1440×900、2560×1600 或 2880×1800）。
- [ ] 使用完整 Xcode 安装并确认当前 macOS SDK。
- [ ] 注册与 `com.subtitlefactory.desktop` 一致的 App ID，或在所有版本文件中统一替换为最终 Bundle ID。
- [ ] 安装 Mac App Distribution、Mac Installer Distribution 证书和 Mac App Store Connect provisioning profile。
- [x] 本地 QA 对主 App、Python sidecar、FFmpeg/FFprobe、Vision OCR 和所有嵌套 Mach-O 逐层签名；helper 使用 sandbox + inherit，并自动防止 QA 的临时库验证例外进入正式版。
- [x] 在本地沙盒 QA App 中验证媒体导入、localhost sidecar、能力门禁、鉴权、捆绑运行时和退出清理。
- [x] 导出改用原生存储面板、受控源路径和原子流式复制；浏览器仅保留开发回退。
- [ ] 解锁桌面后逐项人工点击字幕、成片、项目包、内容包、术语表、诊断包与样式导出，验证存储面板命名、取消和覆盖交互。
- [ ] 使用真实 Apple Distribution 身份重复逐层签名验收，并验证 provisioning profile、Team ID 和 Keychain access group 完全一致。
- [ ] 使用真实 Apple Distribution 签名在沙盒中验证模型下载、Keychain、云端授权、导出与第二次启动；这些依赖团队身份的路径不能由 ad-hoc QA 代替。
- [ ] 在 Apple Distribution 签名的完整 `.app` 上分别记录首次启动与第二次启动时间；本地 ad-hoc 独立 sidecar 的对照值为约 29.5 秒 / 0.5 秒，不能代替正式信任链验收。
- [ ] 使用最终 Xcode 版本生成并检查 Privacy Report，再通过 App Store Connect 自动验证确认隐私清单；如果 Apple 后续把原生 macOS 纳入 required-reason API 范围，则按实际调用补充批准理由。
- [ ] 生成并验证签名 `.pkg`，上传 App Store Connect/TestFlight，处理自动验证与审核反馈。

## 当前外部前置条件

2026-08-12 的本机检查结果：macOS 26.6.1、arm64；只有 `/Library/Developer/CommandLineTools`，没有 `/Applications/Xcode.app`；钥匙串中没有有效代码签名身份。因此源码、测试和本地沙盒 QA `.app` 可以继续优化，但可提交的 App Store 签名 `.app`、`.pkg` 与上传必须等完整 Xcode、开发者团队资料、证书和 provisioning profile 就绪后才能完成。
