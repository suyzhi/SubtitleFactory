"""
字幕工厂 - 全局配置
"""

import os
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
LOGS_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "subtitles.db"

# 创建目录
for d in [PROJECTS_DIR, DOWNLOADS_DIR, AUDIO_DIR, SUBTITLES_DIR, EXPORTS_DIR, MODELS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# LLM API
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# Whisper
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")

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
