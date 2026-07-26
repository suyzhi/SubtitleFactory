"""
字幕工厂 - FastAPI 应用主入口
"""

import os
import sys
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# 将项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .models.database import init_db, mark_interrupted_tasks
from .utils.config import LOGS_DIR, DATA_DIR, is_frozen_app
from .api import batches, editor, maintenance, media, ocr, packages, projects, quality, settings, speakers, tasks, templates, terminology, watch_folders
from .security import ALLOWED_ORIGINS, require_loopback_session
from .services.secret_store import migrate_database_secrets
from .services.backups import scheduled_backup
from .services.watch_runtime import resume_interrupted_workflows, watch_loop
from .services.playlist_batches import recover_playlist_batches
from .version import VERSION

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
interrupted_tasks = mark_interrupted_tasks()
try:
    scheduled_backup()
except Exception:
    logger.exception("自动数据库备份失败")
try:
    migrated_secrets = migrate_database_secrets()
    if migrated_secrets:
        logger.info("已将 %s 个 AI 密钥迁移到 macOS Keychain", migrated_secrets)
except Exception:
    logger.exception("AI 密钥迁移到 macOS Keychain 失败")
    if is_frozen_app():
        raise

# ── 运行期服务 ──
@asynccontextmanager
async def lifespan(application: FastAPI):
    resume_interrupted_workflows(interrupted_tasks)
    recover_playlist_batches(interrupted_tasks)
    stop_event = threading.Event()
    worker = threading.Thread(target=watch_loop, args=(stop_event,), name="watch-folders", daemon=True)
    worker.start()
    application.state.watch_stop_event = stop_event
    application.state.watch_worker = worker
    try:
        yield
    finally:
        stop_event.set()
        worker.join(timeout=2)


# ── 创建 FastAPI 应用 ──
app = FastAPI(
    title="字幕工厂 API",
    description="YouTube 视频转写字幕桌面软件的 API 服务",
    version=VERSION,
    lifespan=lifespan,
)

# ── 仅允许桌面 WebView 与本地开发前端访问 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(require_loopback_session)


@app.exception_handler(HTTPException)
async def structured_http_error(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        error = {
            "code": exc.detail.get("code"),
            "message": exc.detail.get("message") or "请求失败",
            "suggestion": exc.detail.get("suggestion") or "",
            "details": exc.detail.get("details") or {},
            "recoverable": bool(exc.detail.get("recoverable", exc.status_code < 500)),
        }
        return JSONResponse(
            content={"error": error, "detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )
    if request.url.path.startswith("/api"):
        error = {
            "code": f"HTTP_{exc.status_code}",
            "message": str(exc.detail),
            "suggestion": "",
            "details": {},
            "recoverable": exc.status_code < 500,
        }
        return JSONResponse(
            content={"error": error, "detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def structured_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": {
        "code": "REQUEST_VALIDATION_FAILED", "message": "请求参数无效",
        "suggestion": "请检查输入内容后重试", "details": {"errors": exc.errors()},
        "recoverable": True,
    }})


@app.exception_handler(Exception)
async def structured_unexpected_error(request: Request, exc: Exception):
    logger.exception("未处理的 API 错误: %s", request.url.path, exc_info=exc)
    if request.url.path.startswith("/api"):
        return JSONResponse(status_code=500, content={"error": {
            "code": "INTERNAL_ERROR", "message": "本地引擎处理失败",
            "suggestion": "请重试；若问题持续，请导出脱敏诊断包", "details": {},
            "recoverable": True,
        }})
    raise exc

# ── 注册路由 ──
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(settings.router)
app.include_router(editor.router)
app.include_router(media.router)
app.include_router(quality.router)
app.include_router(terminology.router)
app.include_router(maintenance.router)
app.include_router(packages.router)
app.include_router(templates.router)
app.include_router(watch_folders.router)
app.include_router(batches.router)
app.include_router(speakers.router)
app.include_router(ocr.router)


# ── 根路径 / 健康检查 ──
@app.get("/")
def root():
    return {
        "name": "字幕工厂 API",
        "version": VERSION,
        "docs": "/docs",
    }

@app.get("/api/health")
def health_check():
    """健康检查接口（用于 start-desktop.sh 和前端检测后端状态）"""
    return {
        "status": "ok",
        "service": "subtitle-factory-backend",
        "version": VERSION,
        "runtime": settings.get_runtime_health(),
    }


# ── 启动提醒 ──
logger.info("=" * 50)
logger.info("字幕工厂 API 启动")
logger.info(f"数据目录: {DATA_DIR}")
logger.info(f"文档地址: http://127.0.0.1:8000/docs")
logger.info("=" * 50)
