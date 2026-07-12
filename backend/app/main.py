"""
字幕工厂 - FastAPI 应用主入口
"""

import os
import sys
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 将项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .models.database import init_db, mark_interrupted_tasks
from .utils.config import LOGS_DIR, DATA_DIR
from .api import projects, settings, tasks

# ── 日志配置 ──
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── 初始化数据库 ──
init_db()
mark_interrupted_tasks()

# ── 创建 FastAPI 应用 ──
app = FastAPI(
    title="字幕工厂 API",
    description="YouTube 视频转写字幕桌面软件的 API 服务",
    version="0.3.0",
)

# ── CORS（允许 Tauri 前端访问）──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],                          # 开发阶段允许所有源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 注册路由 ──
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(settings.router)


# ── 静态文件服务（视频/音频） ──
app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")


# ── 根路径 / 健康检查 ──
@app.get("/")
def root():
    return {
        "name": "字幕工厂 API",
        "version": "0.3.0",
        "docs": "/docs",
    }

@app.get("/api/health")
def health_check():
    """健康检查接口（用于 start-desktop.sh 和前端检测后端状态）"""
    return {
        "status": "ok",
        "service": "subtitle-factory-backend",
        "version": "0.3.0",
        "runtime": settings.get_runtime_health(),
    }


# ── 启动提醒 ──
logger.info("=" * 50)
logger.info("字幕工厂 API 启动")
logger.info(f"数据目录: {DATA_DIR}")
logger.info(f"文档地址: http://127.0.0.1:8000/docs")
logger.info("=" * 50)
