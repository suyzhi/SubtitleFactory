# 字幕工厂 0.4.1 App Store Connect 交接单

这份文件只记录已经由源码或正式资料证明的字段，以及提交前必须由开发者账号持有人补齐的字段。不得把占位值上传到 App Store Connect。

当前字段限制以 Apple 官方 [App information](https://developer.apple.com/help/app-store-connect/reference/app-information/app-information/)、[Platform version information](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information/)、[App privacy](https://developer.apple.com/help/app-store-connect/manage-app-information/manage-app-privacy/) 和 [Mac screenshot specifications](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications/) 为准。可公开、可复核的简体中文提交草案保存在 `app-store/metadata.zh-Hans.json`；法定名称、联系方式和账号决定不得提交到仓库。

## 已确定的产品字段

- App 名称：`字幕工厂`（不超过 30 个字符）
- 副标题建议：`本地优先的专业字幕工作台`（不超过 30 个字符）
- Bundle ID：`com.subtitlefactory.desktop`；创建 App 记录前必须确认这是最终 ID，首次上传后不能再改
- 版本：`0.4.1`
- 最低系统版本：macOS 14.0 Sonoma；结构化元数据、Tauri 配置和最终 App 的 `LSMinimumSystemVersion` 必须一致
- 主语言：简体中文
- 类别：Video；App Store Connect 中选择与包内类别一致的 Mac 视频类别
- 隐私政策 URL：<https://github.com/suyzhi/SubtitleFactory/blob/main/docs/PRIVACY.md>
- 登录要求：无需账号；本地导入、编辑、离线质检和导出不需要第三方服务
- 购买项目：当前版本没有 App 内购买或订阅
- App 描述：使用结构化草案中的纯文本，限制 4,000 个字符；不使用标杆产品或其他 App 名称营销
- 关键词：逗号连接后限制 100 UTF-8 bytes，每项超过 2 个字符，不重复 App 或公司名称
- 审核备注：使用结构化草案中的真实本地测试路径，限制 4,000 UTF-8 bytes；不含密钥或私人联系信息

## App 隐私申报草案

App 默认在本机处理，不集成广告、跟踪或分析 SDK。用户主动配置并执行云端功能时，第三方 Provider 可能接收：

- 音频数据：仅 Fun-Realtime-ASR 获得单独、可撤销授权后，上传当前项目的 16 kHz WAV 分段，用于 App 功能；不用于跟踪，不与身份关联。
- 其他用户内容：AI 整理、翻译、内容生成、云端 OCR、说话人增强或 AI 质检只发送当前操作所需的字幕文本、上下文或用户确认的片段，用于 App 功能；不用于跟踪，不与身份关联。

最终答案必须覆盖用户实际可选择的所有 Provider，并与 `PrivacyInfo.xcprivacy`、结构化元数据、App 内说明及公开隐私政策一致。第三方收到的数据保留与删除同时受其政策约束。Apple 要求同时覆盖集成代码中第三方合作方的实践，且产品行为变化后持续更新答案；因此发布前仍须由账号持有人在 App Store Connect 逐项确认并发布。

## 不能自动决定的账号字段

- 支持 URL：必须是独立公开页面，并提供真实、持续有效的联系方法。Apple 明确要求页面包含适用法律所需的实际联系信息；当前 GitHub Issues 禁止新建，不能使用该页面冒充支持渠道。
- Copyright / Seller：必须使用开发者账号对应的真实法定名称与年份，并同步修正包内 `NSHumanReadableCopyright`。
- SKU：创建 App 记录后不能修改，必须由账号持有人确认内部唯一值。
- App Review 联系人：姓名、可收信邮箱和可接听电话只能在私有提交环境中提供。
- Age Rating：由账号持有人按 App Store Connect 问卷确认；不能由代码仓库代填。
- Content Rights：App Store 版不获取第三方网站内容，但仍须由账号持有人确认各销售地区的内容权利答案。
- App Privacy：账号持有人须确认并发布包含第三方 Provider 在内的最终答案。
- 价格、销售地区、税务、协议与首次发布方式：由账号持有人决定；首发建议使用手动发布，以便审核通过后再做最后检查。

## 自动门禁

`python scripts/verify-app-store-metadata.py` 会检查名称、副标题、描述、关键词和审核备注限制，并把版本、Bundle ID、最低系统版本、类别及隐私数据类型与 Tauri、`Info.plist` 和 `PrivacyInfo.xcprivacy` 对齐。CI 与本地 App Store QA 都执行这条公共字段检查。打包阶段还会用 `scripts/verify-macos-deployment-target.sh` 扫描最终 App 的全部 Mach-O，拒绝任何实际要求高于 macOS 14.0 的原生依赖。

正式 `./scripts/package-app-store.sh` 还会使用 `--require-owner-fields`，要求以下值只通过私有环境变量提供；脚本只检查，不会写入日志或仓库：

- `APP_STORE_SUPPORT_URL`
- `APP_STORE_COPYRIGHT`，格式为 `2026 真实法定权利人`，不含版权符号
- `APP_STORE_SKU`
- `APP_STORE_REVIEW_CONTACT_NAME`
- `APP_STORE_REVIEW_CONTACT_EMAIL`
- `APP_STORE_REVIEW_CONTACT_PHONE`
- `APP_STORE_AGE_RATING_CONFIRMED=true`
- `APP_STORE_CONTENT_RIGHTS_CONFIRMED=true`
- `APP_STORE_PRIVACY_ANSWERS_CONFIRMED=true`
- `APP_STORE_PRICE_AND_AVAILABILITY_CONFIRMED=true`

正式门禁还会拒绝与 `Info.plist` 不一致的法定权利人，以及把隐私政策页面冒充支持页面的配置。

## 截图与审核说明

Mac 截图是必填项，必须统一采用 Apple 接受的 16:10 尺寸之一：1280×800、1440×900、2560×1600 或 2880×1800。建议至少覆盖项目库、本地导入后的工作区、字幕编辑与时间轴、模型中心、导出和隐私授权。截图中不得出现测试占位、密钥、个人路径或没有权利使用的媒体。

结构化元数据中的英文审核备注已经说明：

1. 启动后先进入项目库，点击“导入”选择审核方有权使用的本地 MP4。
2. App Store 版有意不显示 YouTube、浏览器 Cookie、监听文件夹或外部运行时入口。
3. 不配置第三方 API Key 也能完成本地导入、编辑、离线质检和导出。
4. 任何云端文本或音频操作都需要用户主动配置、明确执行；Fun-Realtime-ASR 另有可撤销的音频上传授权。
5. 如审核需要测试云端功能，应只通过 App Review Notes 安全提供专用测试凭据，不要写入截图、描述、仓库或应用包。
