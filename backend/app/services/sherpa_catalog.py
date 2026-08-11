"""Pinned, reader-facing catalog for App-managed sherpa-onnx ASR models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedModelFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ManagedSherpaModel:
    id: str
    package: str
    name: str
    family: str
    adapter: str
    category_id: str
    category_name: str
    purpose: str
    language_description: str
    languages: tuple[str, ...]
    scenarios: tuple[str, ...]
    strengths: tuple[str, ...]
    limitations: tuple[str, ...]
    speed_tier: str
    accuracy_tier: str
    memory_tier: str
    timestamp_mode: str
    punctuation_mode: str
    license: str
    tags: tuple[str, ...]
    runtimes: tuple[str, ...]
    archive_size: int
    archive_sha256: str
    asset_id: int
    asset_updated_at: str
    files: tuple[ManagedModelFile, ...]
    vad_max_speech_seconds: float = 15.0
    automatic: bool = True

    @property
    def archive_name(self) -> str:
        return f"{self.package}.tar.bz2"

    @property
    def archive_url(self) -> str:
        return (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            f"asr-models/{self.archive_name}"
        )

    @property
    def source_url(self) -> str:
        return "https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models"

    @property
    def installed_bytes(self) -> int:
        return sum(item.size for item in self.files)


def _file(name: str, size: int, sha256: str) -> ManagedModelFile:
    return ManagedModelFile(name, size, sha256)


# File identities come from the official ``asr-models`` release archives and
# are checked again by scripts/verify-model-sources.py.  Only inference files
# are installed; bundled examples and conversion scripts stay in the archive.
_MODEL_SPECS = (
    dict(
        id="dolphin-base-ctc-multi-lang-int8-2025-04-02",
        package="sherpa-onnx-dolphin-base-ctc-multi-lang-int8-2025-04-02",
        name="Dolphin Base CTC 多语言",
        family="Dolphin CTC", adapter="dolphin",
        category_id="multilingual", category_name="通用多语言",
        purpose="小体积、多语言的快速字幕草稿",
        language_description="中文、英语、日韩及多种常用语言",
        languages=("*",),
        scenarios=("通用字幕", "低配置", "多语言"),
        strengths=("约 77 MiB 下载包", "原生词元时间戳", "CPU 速度快"),
        limitations=("小语种覆盖不如 Omnilingual", "复杂口音精度不如大模型"),
        speed_tier="很快", accuracy_tier="均衡", memory_tier="低",
        timestamp_mode="token", punctuation_mode="limited",
        license="Apache-2.0",
        tags=("多语言", "轻量", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=80671385,
        archive_sha256="6f23da2303c3c2e5fa6445c450fa2a7133cd57e3da070ae5f97ab9e0dfbb4a54",
        asset_id=242874759, asset_updated_at="2025-04-02T10:20:38Z",
        files=(
            _file("model.int8.onnx", 103729802, "a3aa46c97f3f60f135ff949793cb05fabe7a0b3c484dc2e3cc699d354ee11b76"),
            _file("tokens.txt", 504662, "c3788261a51df1899ea4b210b552cd42139204de72c0ad60f6cebb199078872e"),
        ),
    ),
    dict(
        id="omnilingual-asr-1600-languages-300m-ctc-v2-int8-2026-02-05",
        package="sherpa-onnx-omnilingual-asr-1600-languages-300M-ctc-v2-int8-2026-02-05",
        name="Omnilingual ASR 300M",
        family="Omnilingual CTC", adapter="omnilingual",
        category_id="multilingual", category_name="通用多语言",
        purpose="1600 多种语言和小语种的离线兜底",
        language_description="1600+ 语言，尤其适合常规模型未覆盖的小语种",
        languages=("*",),
        scenarios=("小语种", "多语言", "通用字幕"),
        strengths=("极广语言覆盖", "原生词元时间戳", "单一离线模型"),
        limitations=("普通话或英语精度并非同体积最优", "内存需求高于轻量模型"),
        speed_tier="中等", accuracy_tier="均衡", memory_tier="中",
        timestamp_mode="token", punctuation_mode="limited",
        license="Apache-2.0",
        tags=("1600+ 语言", "小语种", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=292313120,
        archive_sha256="951b32409aade32bd525310bb39e9666773ba3fc611a39e817f620936d76c631",
        asset_id=350979837, asset_updated_at="2026-02-05T05:20:21Z",
        files=(
            _file("model.int8.onnx", 365841453, "e3042b2f3b3ef0af2211bf99d2b4bf94a21f5ac0e9898827e7dd6d003a860e91"),
            _file("tokens.txt", 90630, "7d99997ef207ff14c2cfe825f2aa037528ea250113cc3c6392bfe49326884ba6"),
            _file("LICENSE", 581, "a70a523bafbb595c2844104feb313d204904dac91c3d186c05f22a10a71c7a94"),
        ),
    ),
    dict(
        id="qwen3-asr-0.6b-int8-2026-03-25",
        package="sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
        name="Qwen3-ASR 0.6B",
        family="Qwen3-ASR", adapter="qwen3",
        category_id="specialized", category_name="专业场景",
        purpose="多语言、中文方言、热词以及歌词和说唱",
        language_description="30 种语言及多种中文方言",
        languages=("*",),
        scenarios=("方言", "歌词与说唱", "热词", "高精度"),
        strengths=("复杂语音鲁棒", "支持热词能力", "歌词与快速语流表现好"),
        limitations=("只提供 VAD 片段级时间轴", "体积和内存需求较高", "不会自动启用"),
        speed_tier="较慢", accuracy_tier="高", memory_tier="高",
        timestamp_mode="segment", punctuation_mode="native",
        license="Apache-2.0（上游 Qwen）",
        tags=("30 种语言", "方言", "歌词/说唱", "CPU", "Apple GPU"),
        runtimes=("cpu", "mlx"),
        archive_size=878702423,
        archive_sha256="393f8a14e2f5fb96746aaab342997a40641001fbd5bf9592a080a8329178ee96",
        asset_id=390698077, asset_updated_at="2026-04-07T09:52:53Z",
        files=(
            _file("conv_frontend.onnx", 44148281, "d22dc4423e0940e49884e903d2ea2f7e5567c14fc1aed97e4e26d6b8f208ef9e"),
            _file("encoder.int8.onnx", 182491662, "60748d3e6744a57c9c91e1b17424a6c2990567e8adceb0783940c03ed98fa9d9"),
            _file("decoder.int8.onnx", 755914231, "4f6885be5959ae26af3089d38ee7972c5fafbeeb1cf8d5e76eab6d8b61ca5771"),
            _file("tokenizer/merges.txt", 1671853, "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"),
            _file("tokenizer/tokenizer_config.json", 12487, "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c"),
            _file("tokenizer/vocab.json", 2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
        ),
        automatic=False, vad_max_speech_seconds=12.0,
    ),
    dict(
        id="moonshine-base-zh-quantized-2026-02-27",
        package="sherpa-onnx-moonshine-base-zh-quantized-2026-02-27",
        name="Moonshine Base 中文",
        family="Moonshine", adapter="moonshine",
        category_id="lightweight", category_name="低配置与快速草稿",
        purpose="低配置 Mac 上快速生成中文草稿",
        language_description="普通话",
        languages=("zh",),
        scenarios=("低配置", "快速草稿", "通用字幕"),
        strengths=("加载快", "内存需求低", "自带基础标点"),
        limitations=("只提供 VAD 片段级时间轴", "方言和中英混说不如中文专用大模型"),
        speed_tier="很快", accuracy_tier="基础", memory_tier="低",
        timestamp_mode="segment", punctuation_mode="native",
        license="Moonshine AI Community License",
        tags=("中文", "轻量", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=99905420,
        archive_sha256="6495d4240f66bcdd2bbbbfefaa687c463150d467854001270934e88940b733c1",
        asset_id=363390057, asset_updated_at="2026-02-27T09:29:29Z",
        files=(
            _file("encoder_model.ort", 31326816, "c725a24b58595905921ea2a47e2bcf0f18c78f4d171d96136f2dcbc8c77a58a6"),
            _file("decoder_model_merged.ort", 109424520, "bf79fce626e123739ec37eceb2b2a010a93d720da266dd5d8ef9a47ef9a7dc36"),
            _file("tokens.txt", 549350, "2870d843e14c1e187bf1913a521562a63b53933814bd7f2145120468f494a049"),
            _file("LICENSE", 13344, "6148d7574a6554b7379b633cfd4c4fe5840c3f548d13bc83e00b52dc6fa00abd"),
        ),
    ),
    dict(
        id="paraformer-zh-2023-09-14",
        package="sherpa-onnx-paraformer-zh-2023-09-14",
        name="Paraformer 中文均衡版",
        family="Paraformer", adapter="paraformer",
        category_id="chinese", category_name="中文与中英混说",
        purpose="普通话字幕的速度、精度和时间轴均衡推荐",
        language_description="普通话、中英混说及部分中文方言",
        languages=("zh", "en"),
        scenarios=("通用字幕", "中英混说", "方言"),
        strengths=("中文均衡推荐", "原生词元时间戳", "已验证 CPU 与 Core ML"),
        limitations=("粤语、吴语和川渝语音应优先选择专用模型",),
        speed_tier="快", accuracy_tier="高", memory_tier="中",
        timestamp_mode="token", punctuation_mode="limited",
        license="Apache-2.0",
        tags=("普通话", "中英混说", "CPU", "Core ML"),
        runtimes=("cpu", "coreml"),
        archive_size=234051698,
        archive_sha256="9c49fd9c6fb63de8e18c1054cf3d100f804741b7e608e187923cd8ff09fa9f03",
        asset_id=155857985, asset_updated_at="2024-03-10T03:00:00Z",
        files=(
            _file("model.int8.onnx", 243371218, "f36a0433bcf096bd6d6f11b80a3ac8bed110bdca632fe0d731df8d1a84475945"),
            _file("tokens.txt", 75756, "59aba8873a2ed1e122c25fee421e25f283b63290efbde85c1f01a853d83cb6e6"),
        ),
    ),
    dict(
        id="fire-red-asr2-ctc-zh-en-int8-2026-02-25",
        package="sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25",
        name="FireRedASR2 中文高精度",
        family="FireRedASR2 CTC", adapter="fire_red_ctc",
        category_id="chinese", category_name="中文与中英混说",
        purpose="普通话、粤语和二十多种方言的高精度转写",
        language_description="普通话、粤语、英语及二十多种中文方言",
        languages=("zh", "yue", "en"),
        scenarios=("高精度", "方言", "中英混说"),
        strengths=("方言覆盖广", "原生词元时间戳", "复杂中文语音精度高"),
        limitations=("体积和内存较大", "粤语或吴语有更专门的模型"),
        speed_tier="中等", accuracy_tier="很高", memory_tier="高",
        timestamp_mode="token", punctuation_mode="limited",
        license="以上游 FireRedASR2 模型许可为准",
        tags=("中文", "粤语", "方言", "高精度", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=520516278,
        archive_sha256="1da8b737ecc5e29f36759a4460c754863e7c919a4ba325aea187331fbfc83274",
        asset_id=362637452, asset_updated_at="2026-02-26T05:44:33Z",
        files=(
            _file("model.int8.onnx", 775861420, "ca3dbabd82170110cc0b343c2890866d449984bc9cd92b9a18371ff80a81bb99"),
            _file("tokens.txt", 79172, "1bc613de2112d257e61a349c3e72d1b1a9cf19c33d3ca954197ad2171e5ea07b"),
        ),
    ),
    dict(
        id="telespeech-ctc-int8-zh-2024-06-04",
        package="sherpa-onnx-telespeech-ctc-int8-zh-2024-06-04",
        name="TeleSpeech 中文通话",
        family="TeleSpeech CTC", adapter="telespeech",
        category_id="specialized", category_name="专业场景",
        purpose="电话、线上会议和窄带中文录音",
        language_description="普通话及部分中文方言",
        languages=("zh",),
        scenarios=("电话录音", "会议通话", "窄带音频"),
        strengths=("电话信道针对性强", "原生词元时间戳", "8 kHz 来源也可由 App 统一预处理"),
        limitations=("不适合高保真节目或音乐", "不会自动启用"),
        speed_tier="快", accuracy_tier="专业", memory_tier="中",
        timestamp_mode="token", punctuation_mode="limited",
        license="TeleSpeech 模型社区许可协议",
        tags=("电话", "会议", "窄带", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=183168450,
        archive_sha256="a99439d32ae3b0d8e22e55e36d438894c9712d749eca832dd92d08a7f9af659b",
        asset_id=171821232, asset_updated_at="2024-06-04T10:40:22Z",
        files=(
            _file("model.int8.onnx", 340990949, "2bf52b42c971696dc02a2db94c52c8fc75fab321ac4cf10b5a2d6863a73a33b3"),
            _file("tokens.txt", 66642, "f1dea25fe751bc3eebd27b2ff3b86298f9598686a090701990dabddde0eba508"),
        ),
        automatic=False,
    ),
    dict(
        id="paraformer-zh-int8-2025-10-07",
        package="sherpa-onnx-paraformer-zh-int8-2025-10-07",
        name="Paraformer 川渝方言",
        family="Paraformer", adapter="paraformer",
        category_id="dialects", category_name="中文方言专用",
        purpose="四川话、重庆话和川渝地区语音",
        language_description="四川话、重庆话及川渝口音普通话",
        languages=("zh",),
        scenarios=("方言", "四川话", "重庆话"),
        strengths=("川渝语料专项训练", "体积适中"),
        limitations=("只提供 VAD 片段级时间轴", "普通话通用字幕优先选择均衡版", "不会自动启用"),
        speed_tier="快", accuracy_tier="方言专项", memory_tier="中",
        timestamp_mode="segment", punctuation_mode="limited",
        license="以上游 WSChuan-ASR 模型许可为准",
        tags=("四川话", "重庆话", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=228262632,
        archive_sha256="a071ee5419e14adb34d7f970ab98105a45e6608018b168f023ca2e4810744abe",
        asset_id=301503409, asset_updated_at="2025-10-07T12:21:16Z",
        files=(
            _file("model.int8.onnx", 238429929, "53813ee1d41722cc6370a571c887e6d0b391d25b8312cf714a31af85ea603812"),
            _file("tokens.txt", 75756, "59aba8873a2ed1e122c25fee421e25f283b63290efbde85c1f01a853d83cb6e6"),
        ),
        automatic=False,
    ),
    dict(
        id="wenetspeech-yue-u2pp-conformer-ctc-zh-en-cantonese-int8-2025-09-10",
        package="sherpa-onnx-wenetspeech-yue-u2pp-conformer-ctc-zh-en-cantonese-int8-2025-09-10",
        name="WenetSpeech 粤语专用",
        family="WeNet CTC", adapter="wenet_ctc",
        category_id="dialects", category_name="中文方言专用",
        purpose="粤语节目、访谈和中英粤混说",
        language_description="粤语、普通话和英语",
        languages=("yue", "zh", "en"),
        scenarios=("方言", "粤语", "中英混说"),
        strengths=("粤语专用", "原生词元时间戳", "体积较小"),
        limitations=("其他中文方言不适用", "自动选择只在源语言明确为粤语时启用"),
        speed_tier="很快", accuracy_tier="方言专项", memory_tier="低",
        timestamp_mode="token", punctuation_mode="limited",
        license="以上游 WSYue-ASR 模型许可为准",
        tags=("粤语", "中英粤", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=117203117,
        archive_sha256="8636295785a43538a1b4620f167bcb89c10ce5ebdcee61c72a388738b783f992",
        asset_id=291635155, asset_updated_at="2025-09-10T06:19:39Z",
        files=(
            _file("model.int8.onnx", 134698500, "201bfd9e12ec4ac9ee3b23c5e071d9fa2381a8b21df317e2e08a170d6f1f55d3"),
            _file("tokens.txt", 85361, "c7750677a1183606d2fd6f16d792e06e70d9843dba8a0c6e23a9dec78e06977a"),
        ),
    ),
    dict(
        id="wenetspeech-wu-u2pp-conformer-ctc-zh-int8-2026-02-03",
        package="sherpa-onnx-wenetspeech-wu-u2pp-conformer-ctc-zh-int8-2026-02-03",
        name="WenetSpeech 吴语专用",
        family="WeNet CTC", adapter="wenet_ctc",
        category_id="dialects", category_name="中文方言专用",
        purpose="吴语、上海话及江浙地区语音",
        language_description="吴语和上海话",
        languages=("wuu",),
        scenarios=("方言", "吴语", "上海话"),
        strengths=("吴语专用", "原生词元时间戳", "体积较小"),
        limitations=("普通话和其他方言不适用", "自动选择只在源语言明确为吴语时启用"),
        speed_tier="很快", accuracy_tier="方言专项", memory_tier="低",
        timestamp_mode="token", punctuation_mode="limited",
        license="以上游 WenetSpeech-Wu 模型许可为准",
        tags=("吴语", "上海话", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=116168264,
        archive_sha256="404d72ef1f229fe51d3043ecc2d2ded4abb9cef02234175aee97ae047e52c831",
        asset_id=349980437, asset_updated_at="2026-02-03T10:48:48Z",
        files=(
            _file("model.int8.onnx", 133299736, "9de8678c491a35616647bb6f47aa433d4399efdf7901f6dcc305792e003410ed"),
            _file("tokens.txt", 52303, "7c74b54485b599842bf7a70c1c59d020f7dfb02a49036c211b4baf74d2c84fa9"),
        ),
    ),
    dict(
        id="sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
        package="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
        name="SenseVoice 多语种与声音事件",
        family="SenseVoice", adapter="sense_voice",
        category_id="specialized", category_name="专业场景",
        purpose="中英日韩粤转写，并识别语言、情绪和声音事件",
        language_description="中文、英语、日语、韩语和粤语",
        languages=("zh", "en", "ja", "ko", "yue"),
        scenarios=("声音事件", "情绪", "多语言"),
        strengths=("语言识别", "情绪和声音事件标签", "原生词元时间戳"),
        limitations=("模型不会自动补标点", "声音事件模式不会自动启用"),
        speed_tier="快", accuracy_tier="均衡", memory_tier="中",
        timestamp_mode="token", punctuation_mode="none",
        license="以上游 SenseVoice / WSYue-ASR 模型许可为准",
        tags=("声音事件", "情绪", "中英日韩粤", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=165783878,
        archive_sha256="7305f7905bfcf77fa0b39388a313f3da35c68d971661a65475b56fb2162c8e63",
        asset_id=291346584, asset_updated_at="2025-09-09T13:14:24Z",
        files=(
            _file("model.int8.onnx", 237115547, "12ca1a2ae7ecf3e0019ef2822307ee0b5cadc9196569e379b4c4026f8205276d"),
            _file("tokens.txt", 315894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"),
        ),
        automatic=False,
    ),
    dict(
        id="moonshine-tiny-en-quantized-2026-02-27",
        package="sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27",
        name="Moonshine Tiny 英语",
        family="Moonshine", adapter="moonshine",
        category_id="lightweight", category_name="低配置与快速草稿",
        purpose="极小体积的英语快速草稿",
        language_description="英语",
        languages=("en",),
        scenarios=("低配置", "快速草稿", "英语"),
        strengths=("下载约 28 MiB", "加载很快", "内存需求很低"),
        limitations=("只提供 VAD 片段级时间轴", "精度低于 Parakeet 和大体积模型"),
        speed_tier="很快", accuracy_tier="基础", memory_tier="很低",
        timestamp_mode="segment", punctuation_mode="native",
        license="MIT",
        tags=("英语", "极小体积", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=29858559,
        archive_sha256="9ec31b342d8fa3240c3b81b8f82e1cf7e3ac467c93ca5a999b741d5887164f8d",
        asset_id=363389994, asset_updated_at="2026-02-27T09:29:20Z",
        files=(
            _file("encoder_model.ort", 13281600, "94e90a4654fc45cdfedb77c4c08e1739f48862998e58fada384b25118134f221"),
            _file("decoder_model_merged.ort", 30412256, "cf524c4862d36e9e5ab032eddc73637efd822d70e868ac575cf1a46e1e4708a0"),
            _file("tokens.txt", 549350, "2870d843e14c1e187bf1913a521562a63b53933814bd7f2145120468f494a049"),
            _file("LICENSE", 13344, "6148d7574a6554b7379b633cfd4c4fe5840c3f548d13bc83e00b52dc6fa00abd"),
        ),
    ),
    dict(
        id="medasr-ctc-en-int8-2025-12-25",
        package="sherpa-onnx-medasr-ctc-en-int8-2025-12-25",
        name="MedASR 英语医疗",
        family="MedASR CTC", adapter="medasr",
        category_id="specialized", category_name="专业场景",
        purpose="英语医疗术语、检查描述和临床录音",
        language_description="英语医疗语音",
        languages=("en",),
        scenarios=("医疗", "专业术语"),
        strengths=("医疗词汇专项", "原生词元时间戳", "体积适中"),
        limitations=("不适合普通节目", "仅英语", "必须由用户主动选择"),
        speed_tier="快", accuracy_tier="医疗专项", memory_tier="中",
        timestamp_mode="token", punctuation_mode="native",
        license="Health AI Developer Foundations Terms",
        tags=("英语", "医疗", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=131831040,
        archive_sha256="71eea1debeedce91d7d2c56bafd26c7c765aa26fdfeeb8786a2ec321eaac0241",
        asset_id=332773873, asset_updated_at="2025-12-25T08:11:47Z",
        files=(
            _file("model.int8.onnx", 154106419, "2c20f03265ee6144c566fd18b0f7bbb4f0d005d11ce9440dd641920210f4c33a"),
            _file("tokens.txt", 4712, "b43987c0f8f660068a166d155f02b1e439d1f03dda36d50759b4e282e98814f2"),
        ),
        automatic=False,
    ),
    dict(
        id="moonshine-tiny-ja-quantized-2026-02-27",
        package="sherpa-onnx-moonshine-tiny-ja-quantized-2026-02-27",
        name="Moonshine Tiny 日语",
        family="Moonshine", adapter="moonshine",
        category_id="east_asian", category_name="日韩与俄语",
        purpose="低配置设备上的日语快速草稿",
        language_description="日语",
        languages=("ja",),
        scenarios=("低配置", "快速草稿", "日语"),
        strengths=("小体积", "加载快", "内存需求低"),
        limitations=("只提供 VAD 片段级时间轴", "高质量日语优先选择 Parakeet"),
        speed_tier="很快", accuracy_tier="基础", memory_tier="低",
        timestamp_mode="segment", punctuation_mode="native",
        license="Moonshine AI Community License",
        tags=("日语", "轻量", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=47889565,
        archive_sha256="880305c9a6c33572ab269ff9731977ea42ca34c8cffcdd5d99558a9ea2b47cc2",
        asset_id=363389914, asset_updated_at="2026-02-27T09:29:14Z",
        files=(
            _file("encoder_model.ort", 13238184, "86ece73812604b9b5f1274b4d1e6eec0d783b96088ff46d49e30a53f881cad73"),
            _file("decoder_model_merged.ort", 58327272, "9fcd9b71323a496b307e20dd305c4e9a1b533c7bedd6e4f660e974967dd60bb6"),
            _file("tokens.txt", 549350, "2870d843e14c1e187bf1913a521562a63b53933814bd7f2145120468f494a049"),
            _file("LICENSE", 13344, "6148d7574a6554b7379b633cfd4c4fe5840c3f548d13bc83e00b52dc6fa00abd"),
        ),
    ),
    dict(
        id="nemo-parakeet-tdt-ctc-0.6b-ja-35000-int8",
        package="sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8",
        name="Parakeet 日语 0.6B",
        family="NeMo CTC", adapter="nemo_ctc",
        category_id="east_asian", category_name="日韩与俄语",
        purpose="日语节目和访谈的高质量本地转写",
        language_description="日语",
        languages=("ja",),
        scenarios=("高精度", "日语", "长节目"),
        strengths=("高质量日语", "原生词元时间戳", "复杂语流表现好"),
        limitations=("下载和内存需求高于轻量模型",),
        speed_tier="中等", accuracy_tier="高", memory_tier="高",
        timestamp_mode="token", punctuation_mode="native",
        license="以上游 NVIDIA Parakeet 模型许可为准",
        tags=("日语", "高精度", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=489389564,
        archive_sha256="4b0a800ef29f4f4c8667339bf6f60d5bfdc2852ddc9dc5741aea65b6f8d1306b",
        asset_id=271243108, asset_updated_at="2025-07-09T03:11:28Z",
        files=(
            _file("model.int8.onnx", 655542604, "3addd00ef5bd1742078389e540b77394e4a508bdf2f4c9ad1b4a76d93e76598e"),
            _file("tokens.txt", 28557, "732f64c53909f2620c713f4106b487d92e6f54a6915b3cd3d1dbd32f9f4f392a"),
        ),
    ),
    dict(
        id="moonshine-tiny-ko-quantized-2026-02-27",
        package="sherpa-onnx-moonshine-tiny-ko-quantized-2026-02-27",
        name="Moonshine Tiny 韩语",
        family="Moonshine", adapter="moonshine",
        category_id="east_asian", category_name="日韩与俄语",
        purpose="低配置设备上的韩语快速草稿",
        language_description="韩语",
        languages=("ko",),
        scenarios=("低配置", "快速草稿", "韩语"),
        strengths=("小体积", "加载快", "内存需求低"),
        limitations=("只提供 VAD 片段级时间轴", "高质量韩语优先选择 Zipformer"),
        speed_tier="很快", accuracy_tier="基础", memory_tier="低",
        timestamp_mode="segment", punctuation_mode="native",
        license="Moonshine AI Community License",
        tags=("韩语", "轻量", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=49153415,
        archive_sha256="d3b6c5390a7859c9ef20ff4f20b0766fcbad1dc06c0f509fe4840a3a302112dc",
        asset_id=363389854, asset_updated_at="2026-02-27T09:29:07Z",
        files=(
            _file("encoder_model.ort", 13238176, "947260d46252f48eada86a34986b3f70c01d68a343959949a77375b94debd055"),
            _file("decoder_model_merged.ort", 58327336, "95aa9f2e764b80625d2889d6ec9f05c965808e540ac50c16abd10c7ea33fe44b"),
            _file("tokens.txt", 549350, "2870d843e14c1e187bf1913a521562a63b53933814bd7f2145120468f494a049"),
            _file("LICENSE", 13344, "6148d7574a6554b7379b633cfd4c4fe5840c3f548d13bc83e00b52dc6fa00abd"),
        ),
    ),
    dict(
        id="zipformer-korean-2024-06-24",
        package="sherpa-onnx-zipformer-korean-2024-06-24",
        name="Zipformer 韩语",
        family="Zipformer Transducer", adapter="zipformer_transducer",
        category_id="east_asian", category_name="日韩与俄语",
        purpose="韩语节目、课程和访谈的高质量转写",
        language_description="韩语",
        languages=("ko",),
        scenarios=("高精度", "韩语", "长节目"),
        strengths=("高质量韩语", "原生词元时间戳", "CPU 推理快"),
        limitations=("体积高于轻量韩语模型",),
        speed_tier="快", accuracy_tier="高", memory_tier="中",
        timestamp_mode="token", punctuation_mode="native",
        license="以上游 Icefall 韩语模型许可为准",
        tags=("韩语", "高精度", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=329740690,
        archive_sha256="24bd409318f389cd2de0e295eb1acf91f4e8dfcc0d650490dd2a01f5b50d2c77",
        asset_id=175570248, asset_updated_at="2024-06-24T07:35:52Z",
        files=(
            _file("encoder-epoch-99-avg-1.int8.onnx", 70784728, "8b196d723421a0513c98ec25da2c43420c029e817f5e4a90b29ff80291c0af2b"),
            _file("decoder-epoch-99-avg-1.onnx", 11309084, "b486221625f4680659c21eaf0505c50e64a897583af4f9ca6f87dea762a33885"),
            _file("joiner-epoch-99-avg-1.int8.onnx", 2581421, "eb654db1ea2cc9d63474855f65958b6059084692a9f2eb4f3812aceb1e416a20"),
            _file("tokens.txt", 60246, "016bdf0965029263b7ad01b742366ee542ef0bef38261510e8176ff6f2e9e668"),
        ),
    ),
    dict(
        id="nemo-transducer-punct-giga-am-v3-russian-2025-12-16",
        package="sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16",
        name="GigaAM v3 俄语标点版",
        family="NeMo Transducer", adapter="nemo_transducer",
        category_id="east_asian", category_name="日韩与俄语",
        purpose="带原生标点的俄语节目和访谈转写",
        language_description="俄语",
        languages=("ru",),
        scenarios=("高精度", "俄语", "长节目"),
        strengths=("保留俄语标点", "原生词元时间戳", "俄语专项"),
        limitations=("只适用于俄语",),
        speed_tier="快", accuracy_tier="高", memory_tier="中",
        timestamp_mode="token", punctuation_mode="native",
        license="MIT",
        tags=("俄语", "原生标点", "CPU"),
        runtimes=("cpu", "coreml"),
        archive_size=170197019,
        archive_sha256="f9620a0099019c6afcee26525ef9ed3297fa50dd5691c1902af0c948fc1a470b",
        asset_id=329230860, asset_updated_at="2025-12-16T06:05:46Z",
        files=(
            _file("encoder.int8.onnx", 224570820, "369f35a71bf288d3b8e0391fabd8dba5f2314088d440bca474056b7b4b6e66bf"),
            _file("decoder.onnx", 4600132, "38fc7475443ea2a26f63211ca350f73ac50fff824ab7a3876ee2bd610c53bbc4"),
            _file("joiner.onnx", 2712896, "602ff7017a93311aad34df1437c8d7f49911353c13d6eae7a6ee7b041339465c"),
            _file("tokens.txt", 13354, "39abae20e692998290c574e606f11a9edef2902a1995463fcff63d1490cf22b7"),
            _file("LICENSE", 1070, "f00de6715714c7a63d08639cdbfaa40224eefc407302614bd19f1a8b98c875aa"),
        ),
    ),
)


MANAGED_SHERPA_MODELS: tuple[ManagedSherpaModel, ...] = tuple(
    ManagedSherpaModel(**item) for item in _MODEL_SPECS
)
MANAGED_SHERPA_BY_ID = {item.id: item for item in MANAGED_SHERPA_MODELS}


MODEL_CATEGORY_ORDER = (
    "lightweight",
    "balanced",
    "performance",
    "multilingual",
    "chinese",
    "dialects",
    "english",
    "east_asian",
    "european",
    "specialized",
    "parakeet",
)
