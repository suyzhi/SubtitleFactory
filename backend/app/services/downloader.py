"""YouTube download service using the bundled yt-dlp Python API."""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import uuid
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, Optional, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..utils.config import DOWNLOADS_DIR
from ..utils.task_manager import TaskCancelled, task_manager
from .download_errors import DownloadServiceError
from .distribution import require_youtube_feature
from .runtime_diagnostics import (
    resolve_deno_path,
    resolve_ffmpeg_path,
    resolve_ffprobe_path,
)

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".avif", ".jpeg", ".jpg", ".png", ".webp"}
_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
_PLAYBACK_QUERY_KEYS = {"t", "start", "time_continue", "begin", "end"}
_AUTH_CHALLENGE_MARKERS = (
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",
    "sign in to confirm your age",
    "confirm your age",
    "login required",
    "this video may be inappropriate for some users",
    "age-restricted",
    "private video",
    "this is a private video",
    "join this channel",
    "members-only",
    "members only",
    "premium-only",
    "use --cookies-from-browser",
)
_MEDIA_STREAM_AUTH_MARKERS = (
    "unable to download video data: http error 403: forbidden",
    "http error 403: forbidden",
)
_COOKIE_ACCESS_MARKERS = (
    "could not copy chrome cookie database",
    "could not find chrome cookies database",
    "failed to decrypt with keychain",
    "failed to decrypt cookie",
    "keychain",
    "cookie database",
    "cookies database",
)
_COOKIE_READ_LOCK = threading.Lock()
_T = TypeVar("_T")


class _LazyYtDlp:
    """Keep yt-dlp out of the App Store module graph until a direct-build call."""

    _module: Any = None

    def __getattr__(self, name: str) -> Any:
        require_youtube_feature()
        if self._module is None:
            # The split literal is deliberate: the direct PyInstaller build
            # adds yt-dlp explicitly, while the App Store build excludes it.
            self._module = import_module("yt" + "_dlp")
        return getattr(self._module, name)


# Preserve the public test/adapter seam without importing the optional package.
yt_dlp = _LazyYtDlp()


def normalize_youtube_url(url: str) -> str:
    """Remove playback-position state while preserving the selected video.

    Share links, Shorts, embeds and live URLs are reduced to a canonical watch
    URL. This prevents ``t=110s`` and similar UI state from being interpreted as
    a partial-download request by current or future yt-dlp versions.
    """
    value = (url or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return value
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or host not in _YOUTUBE_HOSTS:
        return value

    query = parse_qsl(parsed.query, keep_blank_values=True)
    video_id = ""
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.path.rstrip("/") == "/watch":
        video_id = next((item for key, item in query if key.lower() == "v"), "")
    else:
        parts = [item for item in parsed.path.split("/") if item]
        if len(parts) >= 2 and parts[0].lower() in {"shorts", "embed", "live"}:
            video_id = parts[1]
    if video_id:
        return urlunparse(("https", "www.youtube.com", "/watch", "", urlencode({"v": video_id}), ""))

    filtered = [(key, item) for key, item in query if key.lower() not in _PLAYBACK_QUERY_KEYS]
    return urlunparse(parsed._replace(query=urlencode(filtered, doseq=True), fragment=""))


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Unwrap yt-dlp's DownloadError without depending on message formatting."""
    chain: list[BaseException] = []
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1 and isinstance(exc_info[1], BaseException):
            pending.append(exc_info[1])
        for nested in (
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
            getattr(current, "cause", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return chain


def _safe_error_text(exc: BaseException) -> tuple[str, str]:
    chain = _exception_chain(exc)
    values: list[str] = []
    for item in chain:
        for value in (getattr(item, "orig_msg", None), str(item)):
            if value and value not in values:
                values.append(str(value))
    raw = " | ".join(values)
    # Do not persist signed media URLs, Cookie values, or long opaque tokens.
    safe = re.sub(r"https?://\S+", "[redacted-url]", raw)
    safe = re.sub(r"(?i)(cookie|authorization)\s*[:=]\s*\S+", r"\1=[redacted]", safe)
    safe = re.sub(r"[A-Za-z0-9_-]{80,}", "[redacted-token]", safe)
    normalized = " ".join(raw.lower().split())
    return normalized, safe[:600]


def _has_exception_type(exc: BaseException, name: str) -> bool:
    return any(type(item).__name__ == name for item in _exception_chain(exc))


def _classify_download_error(
    exc: BaseException,
    *,
    authenticated: bool = False,
    stage: str = "download",
) -> DownloadServiceError:
    lowered, safe_text = _safe_error_text(exc)
    diagnostic = {
        "failure_stage": stage,
        "diagnostic_category": type(_exception_chain(exc)[-1]).__name__,
        "authenticated_attempted": authenticated,
    }

    for item in _exception_chain(exc):
        if isinstance(item, OSError):
            if item.errno == 28:
                return DownloadServiceError(
                    "磁盘空间不足，无法完成媒体下载",
                    "DISK_FULL", actions=["open_settings", "retry"],
                    suggestion="请释放下载磁盘空间或更改存储目录后重试",
                    details=diagnostic,
                )
            if item.errno in {1, 13, 30}:
                return DownloadServiceError(
                    "输出目录没有写入权限",
                    "OUTPUT_PERMISSION_DENIED", actions=["open_settings", "retry"],
                    suggestion="请在下载与存储设置中选择可写目录后重试",
                    details=diagnostic,
                )

    if any(marker in lowered for marker in _COOKIE_ACCESS_MARKERS):
        return DownloadServiceError(
            "无法读取 Google Chrome 登录状态",
            "COOKIE_ACCESS_FAILED", actions=["retry", "open_settings"],
            suggestion="请退出可能锁定 Cookie 数据库的 Chrome 辅助进程，确认 macOS 钥匙串授权后重试",
            details=diagnostic,
        )

    membership = any(marker in lowered for marker in (
        "join this channel", "members-only", "members only",
        "subscriber-only", "subscriber only", "premium-only",
    ))
    if membership:
        return DownloadServiceError(
            "当前 YouTube 账号没有该会员内容的观看权限" if authenticated
            else "该视频需要频道会员或 Premium 权限",
            "MEMBERSHIP_REQUIRED",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="请在 Google Chrome 中切换到有权限的 YouTube 账号并确认视频可播放，再重试",
            details=diagnostic,
        )

    private = "private video" in lowered or "this is a private video" in lowered
    if private:
        return DownloadServiceError(
            "当前 YouTube 账号无权观看该私密视频",
            "PRIVATE_VIDEO",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="请在 Google Chrome 中登录获授权账号并确认视频可播放，再重试",
            details=diagnostic,
        )

    age_restricted = any(marker in lowered for marker in (
        "age-restricted", "age restricted", "confirm your age",
        "inappropriate for some users",
    ))
    if age_restricted:
        return DownloadServiceError(
            "YouTube 年龄限制验证未通过",
            "AGE_RESTRICTED",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="请在 Google Chrome 中使用已完成年龄验证的账号播放一次该视频，再重试",
            details=diagnostic,
        )

    if any(marker in lowered for marker in _AUTH_CHALLENGE_MARKERS):
        return DownloadServiceError(
            "YouTube 要求登录或验证码验证",
            "AUTH_REQUIRED",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="请先在 Google Chrome 中登录 YouTube 并确认该视频可播放，再回到 App 重试",
            details=diagnostic,
        )

    if "po token" in lowered or "proof of origin" in lowered:
        return DownloadServiceError(
            "YouTube 要求当前版本尚未配置的 PO Token",
            "PO_TOKEN_REQUIRED", actions=["retry"],
            suggestion="请稍后重试或安装包含兼容 YouTube 挑战支持的新版本；当前 App 不会加载第三方 PO Token 提供器",
            details=diagnostic,
        )

    if any(marker in lowered for marker in _MEDIA_STREAM_AUTH_MARKERS):
        return DownloadServiceError(
            "YouTube 拒绝了媒体流访问",
            "MEDIA_ACCESS_DENIED",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="请先在 Google Chrome 中确认视频可播放；若仍失败，稍后等待 YouTube 媒体授权刷新",
            details=diagnostic,
        )

    if any(marker in lowered for marker in (
        "http error 429", "too many requests", "rate limit",
        "content isn't available, try again later",
    )):
        return DownloadServiceError(
            "YouTube 暂时限制了请求频率",
            "RATE_LIMITED", actions=["retry"],
            suggestion="请停止立即重试，在 Google Chrome 完成验证或等待一段时间后再试",
            details=diagnostic,
        )

    if _has_exception_type(exc, "GeoRestrictedError") or any(marker in lowered for marker in (
        "not available in your country", "not available in your region",
        "geo-restricted", "geo restricted",
    )):
        return DownloadServiceError(
            "该视频在当前地区不可用",
            "GEO_RESTRICTED", recoverable=False, actions=[],
            suggestion="请确认该视频在当前网络地区可以合法播放",
            details=diagnostic,
        )

    if "drm protected" in lowered or "drm-protected" in lowered:
        return DownloadServiceError(
            "该视频受 DRM 保护，无法由下载器获取",
            "DRM_PROTECTED", recoverable=False, actions=[],
            suggestion="请改用不受 DRM 保护且允许下载的媒体来源",
            details=diagnostic,
        )

    http_not_found = any(
        isinstance(item, HTTPError) and int(item.code) in {404, 410}
        for item in _exception_chain(exc)
    )
    if http_not_found or any(marker in lowered for marker in (
        "has been removed", "removed by the uploader", "copyright",
        "account associated with this video has been terminated",
    )):
        return DownloadServiceError(
            "该视频已删除或因版权原因不可用",
            "VIDEO_REMOVED", recoverable=False, actions=[],
            suggestion="请更换仍可在 YouTube 播放的视频链接",
            details=diagnostic,
        )

    if any(marker in lowered for marker in (
        "requested format is not available", "no video formats found",
        "requested format not available", "only images are available",
    )):
        return DownloadServiceError(
            "所选画质或容器没有可用的原始格式",
            "FORMAT_UNAVAILABLE", actions=["retry", "open_settings"],
            suggestion="请改用 MKV 容器或调整画质；App 不会静默降低画质或转码",
            details=diagnostic,
        )

    merge_markers = (
        "ffmpeg", "ffprobe", "merger", "merge", "postprocessing",
        "post-processing", "remux",
    )
    if any(marker in lowered for marker in merge_markers):
        return DownloadServiceError(
            "下载完成后的音视频封装失败",
            "MERGE_FAILED", actions=["retry", "open_settings"],
            suggestion="请检查 FFmpeg 和磁盘空间；若选择 MP4，请改用 MKV 以保留最高原始画质",
            details=diagnostic,
        )

    network_types = (
        TimeoutError, socket.timeout, socket.gaierror, ConnectionError, URLError,
    )
    network_exception = any(
        isinstance(item, network_types) and not isinstance(item, HTTPError)
        for item in _exception_chain(exc)
    )
    http_5xx = any(
        isinstance(item, HTTPError) and 500 <= int(item.code) <= 599
        for item in _exception_chain(exc)
    )
    if network_exception or http_5xx or any(marker in lowered for marker in (
        "timed out", "temporary failure in name resolution", "name or service not known",
        "connection reset", "connection aborted", "remote end closed connection",
        "http error 500", "http error 502", "http error 503", "http error 504",
    )):
        return DownloadServiceError(
            "网络连接暂时中断，下载尚未完成",
            "NETWORK_TEMPORARY", automatic_retry=True, actions=["retry"],
            suggestion="App 会按上限自动重试；若持续失败，请检查 DNS、代理或网络连接",
            details=diagnostic,
        )

    if "video unavailable" in lowered or "this video is unavailable" in lowered:
        return DownloadServiceError(
            "YouTube 返回视频不可用",
            "DOWNLOAD_FAILED", actions=["retry"],
            suggestion="请在 Google Chrome 中确认视频当前仍可播放，再重试",
            details=diagnostic,
        )

    return DownloadServiceError(
        f"视频下载失败：{safe_text or type(exc).__name__}",
        "DOWNLOAD_FAILED",
        actions=["retry"],
        suggestion="请复制脱敏诊断信息；该错误不会被后台自动循环重试",
        details=diagnostic,
    )


def _needs_browser_auth(exc: BaseException) -> bool:
    return _classify_download_error(exc).auth_retry_eligible


def _with_chrome_cookies(options: dict) -> dict:
    authenticated = dict(options)
    authenticated["cookiesfrombrowser"] = ("chrome",)
    return authenticated


def _resolve_deno_path() -> Optional[Path]:
    runtime = resolve_deno_path()
    return runtime.path if runtime else None


def _execute_youtube_operation(
    *,
    task_id: str,
    url: str,
    options: dict,
    stage: str,
    download: bool,
    resolve_result: Callable[[dict, yt_dlp.YoutubeDL], _T],
    auth_if_result: Callable[[dict], bool] | None = None,
) -> tuple[dict, _T, dict[str, Any]]:
    """Run one anonymous attempt and, only when eligible, one Chrome attempt."""
    require_youtube_feature()
    normalized_url = normalize_youtube_url(url)
    attempts: list[dict[str, Any]] = []

    def run(attempt_options: dict, authenticated: bool) -> tuple[dict, _T]:
        attempts.append({
            "mode": "chrome" if authenticated else "anonymous",
            "authenticated": authenticated,
        })
        if authenticated:
            # Force the Chrome/Keychain read while serialized, then release the
            # process lock before network transfer. The cookie jar stays only
            # in this yt-dlp instance's memory.
            with _COOKIE_READ_LOCK:
                ydl = yt_dlp.YoutubeDL(attempt_options)
                _ = getattr(ydl, "cookiejar", None)
        else:
            ydl = yt_dlp.YoutubeDL(attempt_options)
        with ydl:
            info = ydl.extract_info(normalized_url, download=download)
            task_manager.checkpoint(task_id)
            return info, resolve_result(info, ydl)

    try:
        info, result = run(options, False)
    except TaskCancelled:
        raise
    except Exception as anonymous_exc:
        classified = _classify_download_error(
            anonymous_exc, authenticated=False, stage=stage,
        )
        if not classified.auth_retry_eligible:
            classified.details["attempts"] = attempts
            raise classified from anonymous_exc
        task_manager.update_task(
            task_id, step=stage, progress=4,
            message="YouTube 要求权限验证，正在使用 Google Chrome 登录状态重试一次...",
            details={"download": {
                "authenticated_attempted": True,
                "attempts": attempts,
                "failure_stage": stage,
            }},
        )
        try:
            info, result = run(_with_chrome_cookies(options), True)
        except TaskCancelled:
            raise
        except Exception as authenticated_exc:
            final = _classify_download_error(
                authenticated_exc, authenticated=True, stage=stage,
            )
            final.details["attempts"] = attempts
            raise final from authenticated_exc
    else:
        if auth_if_result and auth_if_result(info):
            task_manager.update_task(
                task_id, step=stage, progress=4,
                message="播放列表包含权限条目，正在使用 Google Chrome 登录状态重新解析一次...",
            )
            try:
                info, result = run(_with_chrome_cookies(options), True)
            except TaskCancelled:
                raise
            except Exception as authenticated_exc:
                final = _classify_download_error(
                    authenticated_exc, authenticated=True, stage=stage,
                )
                final.details["attempts"] = attempts
                raise final from authenticated_exc

    return info, result, {
        "authenticated_attempted": len(attempts) > 1,
        "attempts": attempts,
        "failure_stage": "",
    }


def extract_youtube_info(
    url: str,
    *,
    options: dict[str, Any] | None = None,
    task_id: str = "metadata",
    stage: str = "metadata",
    auth_if_result: Callable[[dict], bool] | None = None,
) -> tuple[dict, dict[str, Any]]:
    """Shared metadata/playlist entrypoint with the same runtime and auth policy."""
    ffmpeg = resolve_ffmpeg_path()
    resolved_options = _download_options(
        task_id, "%(title)s.%(ext)s", quiet=True,
        ffmpeg_location=str(ffmpeg.path) if ffmpeg else None,
    )
    resolved_options.update(options or {})
    info, _, attempt_details = _execute_youtube_operation(
        task_id=task_id,
        url=url,
        options=resolved_options,
        stage=stage,
        download=False,
        resolve_result=lambda _info, _ydl: None,
        auth_if_result=auth_if_result,
    )
    return info, attempt_details


def _update_download_details(task_id: str, patch: dict[str, Any]) -> None:
    current = task_manager.get_task(task_id) or {}
    existing = dict((current.get("details") or {}).get("download") or {})
    existing.update(patch)
    task_manager.update_task(task_id, details={"download": existing})


def _download_options(
    task_id: str,
    output_template: str,
    quiet: bool = False,
    thumbnail_template: Optional[str] = None,
    ffmpeg_location: Optional[str] = None,
    quality: str = "best",
    container: str = "mp4",
    progress_start: float = 10,
    progress_end: float = 95,
) -> dict:
    highest_progress = progress_start

    def progress_hook(data: dict):
        nonlocal highest_progress
        task_manager.wait_if_paused(task_id)
        status = data.get("status")
        if status == "downloading":
            downloaded = float(data.get("downloaded_bytes") or 0)
            total = float(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            percent = downloaded / total * 100 if total else 0
            # yt-dlp invokes the same hook separately for bestvideo and
            # bestaudio.  The byte counter therefore resets between streams;
            # never let that implementation detail move the product progress
            # bar backwards.
            highest_progress = max(
                highest_progress,
                min(progress_end, progress_start + percent / 100 * (progress_end - progress_start)),
            )
            visible_percent = (
                max(0, (highest_progress - progress_start) / (progress_end - progress_start) * 100)
                if progress_end > progress_start else percent
            )
            task_manager.update_task(
                task_id, step="downloading", progress=highest_progress,
                message=f"正在下载媒体 {visible_percent:.0f}%" if total else "正在下载媒体...",
                details={"downloaded_bytes": int(downloaded), "total_bytes": int(total)},
            )

    container = container if container in {"mp4", "mkv", "webm"} else "mp4"
    height_limit = {"1080p": 1080, "720p": 720}.get(quality)
    height_filter = f"[height<={height_limit}]" if height_limit else ""
    if container == "webm":
        format_selector = (
            f"bestvideo[ext=webm]{height_filter}+bestaudio[ext=webm]/"
            f"bestvideo{height_filter}+bestaudio/best{height_filter}/best"
        )
    elif height_filter:
        format_selector = (
            f"bestvideo{height_filter}+bestaudio/best{height_filter}/best"
        )
    else:
        format_selector = "bestvideo+bestaudio/best"

    options = {
        # yt-dlp's recommended unrestricted selector: prefer the highest-quality
        # video stream and best audio stream, with a combined-format fallback.
        # Deliberately do not add a height/resolution filter here.
        "format": format_selector,
        "merge_output_format": container,
        "final_ext": container,
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": quiet,
        "no_warnings": quiet,
        "progress_hooks": [] if quiet else [progress_hook],
        # Keep yt-dlp's transport retries bounded. A separate task-level retry
        # is allowed only for NETWORK_TEMPORARY and never for permission errors.
        "retries": 3,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "file_access_retries": 3,
        "socket_timeout": 30,
        "continuedl": True,
        "retry_sleep_functions": {
            "http": lambda n: min(20, 2 ** max(0, n)),
            "fragment": lambda n: min(20, 2 ** max(0, n)),
            "extractor": lambda n: min(20, 2 ** max(0, n)),
            "file_access": lambda n: min(20, 2 ** max(0, n)),
        },
        # merge_output_format only applies when separate streams are merged.
        # Remux a single-file fallback as well, without re-encoding its quality.
        "postprocessors": [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": container,
        }],
    }
    if thumbnail_template:
        options["writethumbnail"] = True
        options["outtmpl"] = {
            "default": output_template,
            "thumbnail": thumbnail_template,
        }
    if ffmpeg_location:
        # yt-dlp requires ffmpeg to combine bestvideo+bestaudio. Passing the
        # exact resolved binary makes packaged builds independent of PATH.
        options["ffmpeg_location"] = ffmpeg_location
    deno = _resolve_deno_path()
    if not deno:
        raise DownloadServiceError(
            "下载运行环境缺少 YouTube 挑战所需的 Deno",
            "DOWNLOAD_RUNTIME_MISSING", actions=["open_settings"],
            suggestion="请重新安装包含 Deno 与 EJS 挑战组件的完整 App",
            details={
                "failure_stage": "runtime_preflight",
                "runtime_component": "deno",
            },
        )
    try:
        import_module("yt" + "_dlp.extractor.youtube.jsc")
    except Exception as exc:
        raise DownloadServiceError(
            "下载运行环境缺少 YouTube EJS 挑战组件",
            "DOWNLOAD_RUNTIME_MISSING", actions=["open_settings"],
            suggestion="请重新安装包含 yt-dlp EJS 挑战组件的完整 App",
            details={
                "failure_stage": "runtime_preflight",
                "runtime_component": "ejs",
            },
        ) from exc
    options["js_runtimes"] = {"deno": {"path": str(deno)}}
    return options


def _find_thumbnail(info: dict, project_dl_dir: str) -> Optional[str]:
    """Return the downloaded thumbnail path, preferring yt-dlp metadata."""
    candidates = [
        item.get("filepath")
        for item in reversed(info.get("thumbnails") or [])
        if isinstance(item, dict)
    ]
    candidates.extend(
        str(path)
        for path in sorted(
            Path(project_dl_dir).glob("thumbnail.*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    )
    return next((
        path for path in candidates
        if path and Path(path).suffix.lower() in _IMAGE_EXTENSIONS and os.path.isfile(path)
    ), None)


def _find_final_video(
    info: dict,
    ydl: yt_dlp.YoutubeDL,
    extension: str = "mp4",
) -> Optional[str]:
    """Resolve yt-dlp's final post-processed file, never a temporary stream."""
    candidates = [info.get("filepath"), info.get("_filename")]
    prepared = ydl.prepare_filename(info)
    if prepared:
        candidates.extend([prepared, f"{os.path.splitext(prepared)[0]}.{extension}"])
    return next((
        path for path in candidates
        if path and Path(path).suffix.lower() == f".{extension}" and os.path.isfile(path)
    ), None)


def _find_downloaded_audio(info: dict, ydl: yt_dlp.YoutubeDL) -> Optional[str]:
    candidates = [info.get("filepath"), info.get("_filename")]
    candidates.extend(
        item.get("filepath")
        for item in info.get("requested_downloads") or []
        if isinstance(item, dict)
    )
    prepared = ydl.prepare_filename(info)
    if prepared:
        candidates.append(prepared)
    return next((
        path for path in candidates
        if path
        and os.path.isfile(path)
        and Path(path).suffix.lower() not in _IMAGE_EXTENSIONS | {".part", ".ytdl"}
    ), None)


def _cleanup_task_staging(staging_dir: Path) -> dict[str, Any]:
    result = {
        "staging_path": staging_dir.name,
        "removed": False,
        "error": "",
    }
    try:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        result["removed"] = not staging_dir.exists()
    except OSError as exc:
        result["error"] = type(exc).__name__
        logger.warning("[Download] 清理任务暂存目录失败 (%s): %s", staging_dir.name, exc)
    return result


def _probe_media(
    media_path: Path,
    *,
    ffprobe_path: Path,
    expected_container: str | None,
    expected_duration: float,
    require_video: bool,
) -> dict[str, Any]:
    if not media_path.is_file() or media_path.stat().st_size <= 0:
        raise DownloadServiceError(
            "下载得到的媒体文件为空",
            "DOWNLOAD_FAILED", actions=["retry"],
            suggestion="请检查磁盘空间并重新下载",
            details={"failure_stage": "media_validation"},
        )
    try:
        completed = subprocess.run(
            [
                str(ffprobe_path), "-v", "error",
                "-show_entries",
                "format=format_name,duration,size:stream=codec_type,codec_name",
                "-of", "json", str(media_path),
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DownloadServiceError(
            "无法使用 FFprobe 验证下载文件",
            "DOWNLOAD_RUNTIME_MISSING", actions=["open_settings", "retry"],
            suggestion="请重新安装完整 App，或检查 FFprobe 运行状态",
            details={"failure_stage": "media_validation", "runtime_component": "ffprobe"},
        ) from exc
    if completed.returncode != 0:
        raise DownloadServiceError(
            "下载文件未通过媒体完整性验证",
            "DOWNLOAD_FAILED", actions=["retry"],
            suggestion="文件可能不完整；App 已保留旧媒体，请重新下载",
            details={"failure_stage": "media_validation", "diagnostic_category": "ffprobe_rejected"},
        )

    import json
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise DownloadServiceError(
            "FFprobe 返回了无法解析的媒体信息",
            "DOWNLOAD_FAILED", actions=["retry"],
            suggestion="请重新下载；若持续出现，请复制诊断信息",
            details={"failure_stage": "media_validation"},
        ) from exc
    streams = payload.get("streams") or []
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    if require_video and (not video_streams or not audio_streams):
        raise DownloadServiceError(
            "下载文件缺少预期的视频流或音频流",
            "DOWNLOAD_FAILED", actions=["retry"],
            suggestion="请重新下载；MP4 封装持续失败时请改用 MKV",
            details={"failure_stage": "media_validation"},
        )
    if not require_video and not audio_streams:
        raise DownloadServiceError(
            "下载文件不包含可用音轨",
            "DOWNLOAD_FAILED", actions=["retry"],
            suggestion="请确认源视频包含音频后重新下载",
            details={"failure_stage": "media_validation"},
        )

    media_format = payload.get("format") or {}
    duration = float(media_format.get("duration") or 0)
    if duration <= 0:
        raise DownloadServiceError(
            "下载文件没有有效时长",
            "DOWNLOAD_FAILED", actions=["retry"],
            suggestion="请重新下载；App 已保留原有媒体",
            details={"failure_stage": "media_validation"},
        )
    if expected_duration > 0 and abs(duration - expected_duration) > max(10, expected_duration * 0.1):
        raise DownloadServiceError(
            "下载文件时长与 YouTube 元数据明显不一致",
            "DOWNLOAD_FAILED", actions=["retry"],
            suggestion="文件可能不完整；请重新下载",
            details={"failure_stage": "media_validation"},
        )

    format_name = str(media_format.get("format_name") or "")
    container_aliases = {
        "mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
        "mkv": {"matroska", "webm"},
        "webm": {"matroska", "webm"},
    }
    detected_formats = set(format_name.split(","))
    if expected_container and not (detected_formats & container_aliases.get(expected_container, {expected_container})):
        raise DownloadServiceError(
            "下载文件容器与用户选择不一致",
            "MERGE_FAILED", actions=["retry", "open_settings"],
            suggestion="请改用 MKV 容器以保留最高原始画质",
            details={"failure_stage": "media_validation"},
        )
    return {
        "duration": duration,
        "container": expected_container or format_name,
        "format_name": format_name,
        "video_codec": video_streams[0].get("codec_name") if video_streams else "",
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else "",
        "file_size": media_path.stat().st_size,
    }


def _selected_format_id(info: dict) -> str:
    requested = [
        str(item.get("format_id"))
        for item in info.get("requested_formats") or []
        if isinstance(item, dict) and item.get("format_id")
    ]
    return "+".join(requested) or str(info.get("format_id") or "")


def _promote_candidate(candidate: Path, project_dir: Path, stem: str) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    destination = project_dir / f"{stem}{candidate.suffix.lower()}"
    os.replace(candidate, destination)
    return destination


def remove_managed_download_file(
    path: str | os.PathLike[str] | None,
    *,
    project_id: str,
    download_dir: str | os.PathLike[str] | None,
) -> bool:
    """Delete only an App-managed file inside the selected project's folder."""
    if not path:
        return False
    candidate = Path(path).expanduser().resolve(strict=False)
    project_root = (
        Path(download_dir or DOWNLOADS_DIR).expanduser().resolve(strict=False) / project_id
    )
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return False
    if candidate.is_file():
        candidate.unlink()
        return True
    return False


def download_video(
    task_id: str,
    url: str,
    project_id: str,
    ffmpeg_path: str | os.PathLike[str] | None = None,
    download_dir: str | os.PathLike[str] | None = None,
    quality: str = "best",
    container: str = "mp4",
    progress_start: float = 10,
    progress_end: float = 95,
) -> str:
    task_manager.update_task(task_id, step="downloading", progress=2, message="正在解析视频信息...")
    ffmpeg = resolve_ffmpeg_path(ffmpeg_path)
    if ffmpeg is None:
        raise DownloadServiceError(
            "下载运行环境缺少可用的 FFmpeg",
            "DOWNLOAD_RUNTIME_MISSING",
            actions=["open_settings", "retry"],
            suggestion="请重新安装完整 App，或在下载与存储设置中选择可执行的 FFmpeg",
        )
    sibling_ffprobe = ffmpeg.path.with_name("ffprobe")
    ffprobe = resolve_ffprobe_path(sibling_ffprobe if sibling_ffprobe.is_file() else None)
    if ffprobe is None:
        raise DownloadServiceError(
            "下载运行环境缺少可用的 FFprobe",
            "DOWNLOAD_RUNTIME_MISSING",
            actions=["open_settings"],
            suggestion="请重新安装包含 FFprobe 的完整 App",
            details={"failure_stage": "runtime_preflight", "runtime_component": "ffprobe"},
        )

    normalized_url = normalize_youtube_url(url)
    project_dl_dir = Path(download_dir or DOWNLOADS_DIR) / project_id
    project_dl_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = project_dl_dir / f".download-{task_id}"
    _cleanup_task_staging(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=False)
    output_template = str(staging_dir / "%(title)s.%(ext)s")
    thumbnail_template = str(staging_dir / "thumbnail.%(ext)s")

    options = _download_options(
        task_id, output_template, thumbnail_template=thumbnail_template,
        ffmpeg_location=str(ffmpeg.path), quality=quality, container=container,
        progress_start=progress_start, progress_end=progress_end,
    )

    promoted_paths: list[Path] = []
    completed = False
    try:
        info, video_path, attempt_details = _execute_youtube_operation(
            task_id=task_id,
            url=normalized_url,
            options=options,
            stage="downloading",
            download=True,
            resolve_result=lambda extracted, ydl: _find_final_video(extracted, ydl, container),
        )
        if not video_path:
            raise DownloadServiceError(
                f"下载结束后未找到合并完成的 {container.upper()} 视频文件",
                "MERGE_FAILED",
                actions=["retry", "open_settings"],
                suggestion="请检查 FFmpeg 和剩余磁盘空间；MP4 失败时请改用 MKV",
                details={"failure_stage": "final_file_lookup"},
            )
        video_candidate = Path(video_path)
        validation = _probe_media(
            video_candidate,
            ffprobe_path=ffprobe.path,
            expected_container=container,
            expected_duration=float(info.get("duration") or 0),
            require_video=True,
        )
        unique = uuid.uuid4().hex[:8]
        final_video = _promote_candidate(
            video_candidate, project_dl_dir,
            f"video-{info.get('id') or project_id}-{unique}",
        )
        promoted_paths.append(final_video)
        thumbnail_candidate = _find_thumbnail(info, str(staging_dir))
        final_thumbnail: Path | None = None
        if thumbnail_candidate:
            final_thumbnail = _promote_candidate(
                Path(thumbnail_candidate), project_dl_dir, f"thumbnail-{unique}",
            )
            promoted_paths.append(final_thumbnail)
        title = info.get("title") or os.path.basename(video_path)
        thumbnail_url = info.get("thumbnail")
        if not isinstance(thumbnail_url, str) or not thumbnail_url.startswith(("http://", "https://")):
            thumbnail_url = None
        download_details = {
            **attempt_details,
            "format_id": _selected_format_id(info),
            **validation,
            "ffmpeg_source": ffmpeg.source,
            "ffprobe_source": ffprobe.source,
            "deno_source": (resolve_deno_path().source if resolve_deno_path() else "unavailable"),
        }
        task_manager.update_task(
            task_id, step="downloaded", progress=progress_end, message="视频下载完成",
            details={
                "video_path": str(final_video),
                "title": title,
                "file_size": final_video.stat().st_size,
                "thumbnail_url": thumbnail_url,
                "thumbnail_path": str(final_thumbnail) if final_thumbnail else None,
                "normalized_url": normalized_url,
                "ffmpeg_source": ffmpeg.source,
                "download": download_details,
            },
        )
        completed = True
        return str(final_video)
    except TaskCancelled:
        raise
    except DownloadServiceError:
        raise
    except Exception as exc:
        task_manager.checkpoint(task_id)
        raise _classify_download_error(exc, stage="downloading") from exc
    finally:
        if not completed:
            for promoted in promoted_paths:
                try:
                    promoted.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "[Download] 清理未提交候选文件失败 (%s)",
                        promoted.name,
                    )
        cleanup = _cleanup_task_staging(staging_dir)
        _update_download_details(task_id, {"cleanup": cleanup})


def download_audio_source(
    task_id: str,
    url: str,
    project_id: str,
    download_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Download only the best available audio stream into task-local staging."""
    task_manager.update_task(
        task_id, step="downloading_audio", progress=2,
        message="正在解析并下载音频...",
    )
    normalized_url = normalize_youtube_url(url)
    staging_dir = Path(download_dir or DOWNLOADS_DIR) / project_id / f".audio-{task_id}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(staging_dir / "source_audio.%(ext)s")
    options = _download_options(
        task_id, output_template, progress_start=2, progress_end=42,
    )
    options.update({
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
    })
    options.pop("merge_output_format", None)
    options.pop("final_ext", None)
    options.pop("postprocessors", None)

    succeeded = False
    try:
        info, audio_source_path, attempt_details = _execute_youtube_operation(
            task_id=task_id,
            url=normalized_url,
            options=options,
            stage="downloading_audio",
            download=True,
            resolve_result=_find_downloaded_audio,
        )
        if not audio_source_path:
            raise DownloadServiceError(
                "音频下载结束后未找到可转换的音轨",
                "DOWNLOAD_FAILED",
                actions=["retry"],
                suggestion="请检查视频是否包含音轨和剩余磁盘空间后重试",
                details={"failure_stage": "audio_file_lookup"},
            )
        thumbnail_url = info.get("thumbnail")
        if not isinstance(thumbnail_url, str) or not thumbnail_url.startswith(("http://", "https://")):
            thumbnail_url = None
        task_manager.update_task(
            task_id, step="audio_downloaded", progress=45,
            message="音频下载完成，正在准备识别格式",
            details={
                "audio_source_path": audio_source_path,
                "title": info.get("title") or "",
                "thumbnail_url": thumbnail_url,
                "normalized_url": normalized_url,
                "youtube_video_id": info.get("id") or "",
                "source_duration": float(info.get("duration") or 0),
                "download": {
                    **attempt_details,
                    "format_id": _selected_format_id(info),
                    "container": Path(audio_source_path).suffix.lower().lstrip("."),
                    "file_size": Path(audio_source_path).stat().st_size,
                    "deno_source": (
                        resolve_deno_path().source if resolve_deno_path() else "unavailable"
                    ),
                },
            },
        )
        succeeded = True
        return audio_source_path
    except TaskCancelled:
        raise
    except DownloadServiceError:
        raise
    except Exception as exc:
        task_manager.checkpoint(task_id)
        raise _classify_download_error(exc, stage="downloading_audio") from exc
    finally:
        if not succeeded:
            cleanup = _cleanup_task_staging(staging_dir)
            _update_download_details(task_id, {"cleanup": cleanup})


def get_video_info(url: str) -> dict:
    ffmpeg = resolve_ffmpeg_path()
    options = _download_options(
        "info", "%(title)s.%(ext)s", quiet=True,
        ffmpeg_location=str(ffmpeg.path) if ffmpeg else None,
    )
    info, _, attempt_details = _execute_youtube_operation(
        task_id="info",
        url=url,
        options=options,
        stage="metadata",
        download=False,
        resolve_result=lambda _info, _ydl: None,
    )
    return {
        "title": info.get("title", ""),
        "duration": float(info.get("duration") or 0),
        "id": info.get("id", ""),
        "thumbnail_url": info.get("thumbnail") or None,
        "download": attempt_details,
    }
