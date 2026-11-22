"""
字幕工厂（简洁版）- FastAPI 应用主入口
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# 将项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .api import projects, settings, tasks
from .models.database import init_db, mark_interrupted_tasks
from .security import ALLOWED_ORIGINS, require_local_origin
from .services.distribution import (
    DistributionPolicyError,
    distribution_capabilities,
    require_project_distribution,
)
from .services.workflow_recovery import resume_interrupted_workflows
from .utils.config import DATA_DIR, LOGS_DIR, is_frozen_app
from .version import VERSION

# ── 日志配置（轮转：单文件 5 MiB，保留 5 份历史） ──
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        RotatingFileHandler(
            LOGS_DIR / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# 发布版（冻结 sidecar）不对外暴露交互式 API 文档；开发模式保留 /docs 与 /redoc。
DOCS_ENABLED = not is_frozen_app()

# ── 初始化数据库 ──
init_db()
interrupted_tasks = mark_interrupted_tasks()


@asynccontextmanager
async def lifespan(application: FastAPI):
    del application
    resume_interrupted_workflows(interrupted_tasks)
    yield


# ── 创建 FastAPI 应用 ──
app = FastAPI(
    title="字幕工厂 API（简洁版）",
    description="视频下载、本地模型转写、AI 整理与翻译的本地 API 服务",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
)

# ── 仅允许桌面 WebView 与本地开发前端访问 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reject requests originating from unrelated websites.
app.middleware("http")(require_local_origin)


@app.exception_handler(HTTPException)
async def structured_http_error(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        error = {
            "code": exc.detail.get("code"),
            "message": exc.detail.get("message") or "请求失败",
            "suggestion": exc.detail.get("suggestion") or "",
            "details": exc.detail.get("details") or {},
            "recoverable": bool(exc.detail.get("recoverable", exc.status_code < 500)),
            "available_actions": exc.detail.get("available_actions") or [],
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
    del request
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "REQUEST_VALIDATION_FAILED",
                "message": "请求参数无效",
                "suggestion": "请检查输入内容后重试",
                "details": {"errors": exc.errors()},
                "recoverable": True,
            }
        },
    )


@app.exception_handler(DistributionPolicyError)
async def distribution_policy_error(request: Request, exc: DistributionPolicyError):
    del request
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "code": exc.error_code,
                "message": str(exc),
                "suggestion": exc.suggestion,
                "details": {"feature": exc.feature},
                "recoverable": exc.recoverable,
                "available_actions": exc.available_actions,
            }
        },
    )


@app.exception_handler(Exception)
async def structured_unexpected_error(request: Request, exc: Exception):
    logger.exception("未处理的 API 错误: %s", request.url.path, exc_info=exc)
    if request.url.path.startswith("/api"):
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "本地引擎处理失败",
                    "suggestion": "请重试；若问题持续，请重启 App",
                    "details": {},
                    "recoverable": True,
                }
            },
        )
    raise exc


# ── 注册路由 ──
project_distribution_dependency = [Depends(require_project_distribution)]
app.include_router(projects.router, dependencies=project_distribution_dependency)
app.include_router(tasks.router, dependencies=project_distribution_dependency)
app.include_router(settings.router)


# ── 根路径 / 健康检查 ──
@app.get("/")
def root():
    return {
        "name": "字幕工厂 API（简洁版）",
        "version": VERSION,
        "docs": "/docs",
        "distribution": distribution_capabilities().as_dict(),
    }


@app.get("/api/health")
def health_check():
    """健康检查接口（用于 start-desktop.sh 和前端检测后端状态）"""
    return {
        "status": "ok",
        "service": "subtitle-factory-backend",
        "version": VERSION,
        "distribution": distribution_capabilities().as_dict(),
        "runtime": settings.get_runtime_health(),
    }


logger.info("=" * 50)
logger.info("字幕工厂 API（简洁版）启动")
logger.info(f"数据目录: {DATA_DIR}")
if DOCS_ENABLED:
    logger.info("文档地址: http://127.0.0.1:%s/docs", os.getenv("SUBTITLE_FACTORY_PORT", "8000"))
else:
    logger.info("交互式 API 文档已在发布版禁用")
logger.info("=" * 50)
