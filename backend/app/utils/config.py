"""
字幕工厂 - 全局配置
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 加载 .env
load_dotenv(BASE_DIR / ".env")

# 数据目录
DATA_DIR = Path(os.getenv("SUBTITLE_FACTORY_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
PROJECTS_DIR = DATA_DIR / "projects"
DOWNLOADS_DIR = DATA_DIR / "downloads"
AUDIO_DIR = DATA_DIR / "audio"
SUBTITLES_DIR = DATA_DIR / "subtitles"
EXPORTS_DIR = DATA_DIR / "exports"
MODELS_DIR = DATA_DIR / "models"
# Runtime-created files must never be written beside a frozen sidecar inside
# the read-only App bundle.  Tauri supplies SUBTITLE_FACTORY_DATA_DIR in a
# release build; development and tests retain the deterministic fallback above.
LOGS_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "subtitles.db"

# 创建目录
for d in [PROJECTS_DIR, DOWNLOADS_DIR, AUDIO_DIR, SUBTITLES_DIR, EXPORTS_DIR, MODELS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# LLM API
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

def is_frozen_app() -> bool:
    """Whether the backend is running from the packaged PyInstaller sidecar."""
    return bool(getattr(sys, "frozen", False))


def environment_path_overrides_enabled() -> bool:
    """Allow developer path overrides without making them release defaults.

    Frozen builds ignore ambient path overrides unless the user deliberately
    enables the advanced escape hatch.  The dedicated bundled-runtime variables
    remain available to the Tauri launcher and are handled separately.
    """
    return not is_frozen_app() or os.getenv(
        "SUBTITLE_FACTORY_ALLOW_ENV_PATHS", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


# Whisper. A release always starts from the safe, supported Small model unless
# an App setting explicitly chooses something else.
WHISPER_MODEL = (
    os.getenv("WHISPER_MODEL", "small")
    if environment_path_overrides_enabled()
    else "small"
)
WHISPER_MODELS_DIR = MODELS_DIR / "whisper"

# 外部工具路径
YT_DLP_PATH = os.getenv("YT_DLP_PATH", "yt-dlp")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.getenv("FFPROBE_PATH", "ffprobe")

# 服务器
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# 字幕约束
MAX_CHARS_CN = 20      # 中文字幕每行最大汉字数
MAX_CHARS_EN = 42      # 英文字幕每行最大字符数
MIN_DURATION = 1.0     # 单条最小秒数
MAX_DURATION = 7.0     # 单条最大秒数
