"""YouTube URL normalization, yt-dlp options/execution, and video/audio download orchestration."""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, Optional, TypeVar
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ...utils.config import DOWNLOADS_DIR
from ...utils.task_manager import TaskCancelled, task_manager
from ..distribution import require_youtube_feature
from ..download_errors import DownloadServiceError
from ..runtime_diagnostics import (
    resolve_deno_path,
    resolve_ffmpeg_path,
    resolve_ffprobe_path,
    resolve_js_runtime,
    resolve_quickjs_path,
)
from .error_classifier import _classify_download_error
from .media_probe import (
    _cleanup_task_staging,
    _find_downloaded_audio,
    _find_final_video,
    _find_thumbnail,
    _probe_media,
    _promote_candidate,
    _selected_format_id,
)

logger = logging.getLogger("app.services.downloader")

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_PLAYBACK_QUERY_KEYS = {"t", "start", "time_continue", "begin", "end"}
# Retry with alternate clients that do not need a GVS PO token instead of
# failing on intermittent HTTP 403, while never reading browser cookies.
_MEDIA_RETRY_CLIENTS = ("web_embedded", "mweb")
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


def _facade(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default


def normalize_youtube_url(url: str) -> str:
    """Remove playback-position state while preserving the selected video."""
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


def _resolve_deno_path() -> Optional[Path]:
    resolver = _facade("resolve_deno_path", resolve_deno_path)
    runtime = resolver()
    return runtime.path if runtime else None


def _resolve_js_runtime() -> Optional[tuple[str, Path]]:
    quickjs_resolver = _facade("resolve_quickjs_path", resolve_quickjs_path)
    quickjs = quickjs_resolver()
    if quickjs is not None:
        return "quickjs", quickjs.path
    deno = _facade("_resolve_deno_path", _resolve_deno_path)()
    if deno is not None:
        return "deno", deno
    return None


def _with_player_client(options: dict, client: str) -> dict:
    retried = dict(options)
    extractor_args = {
        key: dict(value) for key, value in (retried.get("extractor_args") or {}).items()
    }
    youtube_args = extractor_args.setdefault("youtube", {})
    youtube_args["player_client"] = [client]
    retried["extractor_args"] = extractor_args
    return retried


def _execute_youtube_operation(
    *,
    task_id: str,
    url: str,
    options: dict,
    stage: str,
    download: bool,
    resolve_result: Callable[[dict, Any], _T],
    auth_if_result: Callable[[dict], bool] | None = None,
) -> tuple[dict, _T, dict[str, Any]]:
    """Download without reading browser credentials or prompting for passwords."""
    require_youtube_feature()
    tm = _facade("task_manager", task_manager)
    ydl_mod = _facade("yt_dlp", yt_dlp)
    time_mod = _facade("time", time)

    normalized_url = normalize_youtube_url(url)
    attempts = [{"mode": "anonymous", "authenticated": False}]
    anonymous_options = {key: value for key, value in options.items() if key != "cookiesfrombrowser"}
    candidate_options = [anonymous_options]
    for client in _MEDIA_RETRY_CLIENTS:
        candidate_options.append(_with_player_client(anonymous_options, client))
        attempts.append({"mode": f"player_client={client}", "authenticated": False})

    last_error: DownloadServiceError | None = None
    for index, attempt_options in enumerate(candidate_options):
        try:
            with ydl_mod.YoutubeDL(attempt_options) as ydl:
                info = ydl.extract_info(normalized_url, download=download)
                tm.checkpoint(task_id)
                result = resolve_result(info, ydl)
            return (
                info,
                result,
                {
                    "authenticated_attempted": False,
                    "attempts": attempts[: index + 1],
                    "failure_stage": "",
                },
            )
        except TaskCancelled:
            raise
        except Exception as exc:
            classified = _classify_download_error(exc, authenticated=False, stage=stage)
            last_error = classified
            if classified.error_code not in {"MEDIA_ACCESS_DENIED", "PO_TOKEN_REQUIRED"}:
                break
            if index + 1 < len(candidate_options):
                time_mod.sleep(min(4, index + 1))
    if last_error is None:  # pragma: no cover - the loop always runs once
        raise RuntimeError("YouTube operation produced no result or error")
    last_error.details["attempts"] = attempts[: index + 1]
    raise last_error


def extract_youtube_info(
    url: str,
    *,
    options: dict[str, Any] | None = None,
    task_id: str = "metadata",
    stage: str = "metadata",
    auth_if_result: Callable[[dict], bool] | None = None,
) -> tuple[dict, dict[str, Any]]:
    """Shared metadata/playlist entrypoint with the same runtime and auth policy."""
    ffmpeg = _facade("resolve_ffmpeg_path", resolve_ffmpeg_path)()
    resolved_options = _download_options(
        task_id,
        "%(title)s.%(ext)s",
        quiet=True,
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
    tm = _facade("task_manager", task_manager)
    current = tm.get_task(task_id) or {}
    existing = dict((current.get("details") or {}).get("download") or {})
    existing.update(patch)
    tm.update_task(task_id, details={"download": existing})


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
    tm = _facade("task_manager", task_manager)
    highest_progress = progress_start

    def progress_hook(data: dict):
        nonlocal highest_progress
        tm.wait_if_paused(task_id)
        status = data.get("status")
        if status == "downloading":
            downloaded = float(data.get("downloaded_bytes") or 0)
            total = float(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            percent = downloaded / total * 100 if total else 0
            highest_progress = max(
                highest_progress,
                min(
                    progress_end,
                    progress_start + percent / 100 * (progress_end - progress_start),
                ),
            )
            visible_percent = (
                max(0, (highest_progress - progress_start) / (progress_end - progress_start) * 100)
                if progress_end > progress_start
                else percent
            )
            tm.update_task(
                task_id,
                step="downloading",
                progress=highest_progress,
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
        format_selector = f"bestvideo{height_filter}+bestaudio/best{height_filter}/best"
    else:
        format_selector = "bestvideo+bestaudio/best"

    options = {
        "format": format_selector,
        "merge_output_format": container,
        "final_ext": container,
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": quiet,
        "no_warnings": quiet,
        "progress_hooks": [] if quiet else [progress_hook],
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
        "postprocessors": [
            {
                "key": "FFmpegVideoRemuxer",
                "preferedformat": container,
            }
        ],
        "extractor_args": {"youtube": {"player_client": ["web_embedded", "default"]}},
    }
    if thumbnail_template:
        options["writethumbnail"] = True
        options["outtmpl"] = {
            "default": output_template,
            "thumbnail": thumbnail_template,
        }
    if ffmpeg_location:
        options["ffmpeg_location"] = ffmpeg_location
    js_runtime = _facade("_resolve_js_runtime", _resolve_js_runtime)()
    if not js_runtime:
        raise DownloadServiceError(
            "下载运行环境缺少 YouTube 挑战所需的 JS 运行时（QuickJS / Deno）",
            "DOWNLOAD_RUNTIME_MISSING",
            actions=["open_settings"],
            suggestion="请重新安装包含 QuickJS 与 EJS 挑战组件的完整 App",
            details={
                "failure_stage": "runtime_preflight",
                "runtime_component": "js_runtime",
            },
        )
    js_engine, js_path = js_runtime
    try:
        import_module("yt" + "_dlp.extractor.youtube.jsc")
    except Exception as exc:
        raise DownloadServiceError(
            "下载运行环境缺少 YouTube EJS 挑战组件",
            "DOWNLOAD_RUNTIME_MISSING",
            actions=["open_settings"],
            suggestion="请重新安装包含 yt-dlp EJS 挑战组件的完整 App",
            details={
                "failure_stage": "runtime_preflight",
                "runtime_component": "ejs",
            },
        ) from exc
    options["js_runtimes"] = {js_engine: {"path": str(js_path)}}
    return options


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
    tm = _facade("task_manager", task_manager)
    ffmpeg_resolver = _facade("resolve_ffmpeg_path", resolve_ffmpeg_path)
    ffprobe_resolver = _facade("resolve_ffprobe_path", resolve_ffprobe_path)
    js_runtime_resolver = _facade("resolve_js_runtime", resolve_js_runtime)
    probe_fn = _facade("_probe_media", _probe_media)
    default_downloads_dir = _facade("DOWNLOADS_DIR", DOWNLOADS_DIR)

    tm.update_task(task_id, step="downloading", progress=2, message="正在解析视频信息...")
    ffmpeg = ffmpeg_resolver(ffmpeg_path)
    if ffmpeg is None:
        raise DownloadServiceError(
            "下载运行环境缺少可用的 FFmpeg",
            "DOWNLOAD_RUNTIME_MISSING",
            actions=["open_settings", "retry"],
            suggestion="请重新安装完整 App，或在下载与存储设置中选择可执行的 FFmpeg",
        )
    sibling_ffprobe = ffmpeg.path.with_name("ffprobe")
    ffprobe = ffprobe_resolver(sibling_ffprobe if sibling_ffprobe.is_file() else None)
    if ffprobe is None:
        raise DownloadServiceError(
            "下载运行环境缺少可用的 FFprobe",
            "DOWNLOAD_RUNTIME_MISSING",
            actions=["open_settings"],
            suggestion="请重新安装包含 FFprobe 的完整 App",
            details={"failure_stage": "runtime_preflight", "runtime_component": "ffprobe"},
        )

    normalized_url = normalize_youtube_url(url)
    project_dl_dir = Path(download_dir or default_downloads_dir) / project_id
    project_dl_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = project_dl_dir / f".download-{task_id}"
    _cleanup_task_staging(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=False)
    output_template = str(staging_dir / "%(title)s.%(ext)s")
    thumbnail_template = str(staging_dir / "thumbnail.%(ext)s")

    options = _download_options(
        task_id,
        output_template,
        thumbnail_template=thumbnail_template,
        ffmpeg_location=str(ffmpeg.path),
        quality=quality,
        container=container,
        progress_start=progress_start,
        progress_end=progress_end,
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
        validation = probe_fn(
            video_candidate,
            ffprobe_path=ffprobe.path,
            expected_container=container,
            expected_duration=float(info.get("duration") or 0),
            require_video=True,
        )
        unique = uuid.uuid4().hex[:8]
        final_video = _promote_candidate(
            video_candidate,
            project_dl_dir,
            f"video-{info.get('id') or project_id}-{unique}",
        )
        promoted_paths.append(final_video)
        thumbnail_candidate = _find_thumbnail(info, str(staging_dir))
        final_thumbnail: Path | None = None
        if thumbnail_candidate:
            final_thumbnail = _promote_candidate(
                Path(thumbnail_candidate),
                project_dl_dir,
                f"thumbnail-{unique}",
            )
            promoted_paths.append(final_thumbnail)
        title = info.get("title") or os.path.basename(video_path)
        thumbnail_url = info.get("thumbnail")
        if not isinstance(thumbnail_url, str) or not thumbnail_url.startswith(
            ("http://", "https://")
        ):
            thumbnail_url = None
        js_runtime_info = js_runtime_resolver()
        download_details = {
            **attempt_details,
            "format_id": _selected_format_id(info),
            **validation,
            "ffmpeg_source": ffmpeg.source,
            "ffprobe_source": ffprobe.source,
            "js_runtime_engine": js_runtime_info[0] if js_runtime_info else "unavailable",
            "js_runtime_source": js_runtime_info[1].source if js_runtime_info else "unavailable",
            "deno_source": js_runtime_info[1].source if js_runtime_info else "unavailable",
        }
        tm.update_task(
            task_id,
            step="downloaded",
            progress=progress_end,
            message="视频下载完成",
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
        tm.checkpoint(task_id)
        raise _classify_download_error(exc, stage="downloading") from exc
    finally:
        if not completed:
            for promoted in promoted_paths:
                try:
                    promoted.unlink(missing_ok=True)
                except OSError:
                    logger.warning("[Download] 清理未提交候选文件失败 (%s)", promoted.name)
        cleanup = _cleanup_task_staging(staging_dir)
        _update_download_details(task_id, {"cleanup": cleanup})


def download_audio_source(
    task_id: str,
    url: str,
    project_id: str,
    download_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Download only the best available audio stream into task-local staging."""
    tm = _facade("task_manager", task_manager)
    js_runtime_resolver = _facade("resolve_js_runtime", resolve_js_runtime)
    default_downloads_dir = _facade("DOWNLOADS_DIR", DOWNLOADS_DIR)

    tm.update_task(
        task_id,
        step="downloading_audio",
        progress=2,
        message="正在解析并下载音频...",
    )
    normalized_url = normalize_youtube_url(url)
    staging_dir = Path(download_dir or default_downloads_dir) / project_id / f".audio-{task_id}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(staging_dir / "source_audio.%(ext)s")
    options = _download_options(
        task_id,
        output_template,
        progress_start=2,
        progress_end=42,
    )
    options.update(
        {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
        }
    )
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
        if not isinstance(thumbnail_url, str) or not thumbnail_url.startswith(
            ("http://", "https://")
        ):
            thumbnail_url = None
        js_runtime_info = js_runtime_resolver()
        tm.update_task(
            task_id,
            step="audio_downloaded",
            progress=45,
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
                    "js_runtime_engine": js_runtime_info[0] if js_runtime_info else "unavailable",
                    "js_runtime_source": js_runtime_info[1].source if js_runtime_info else "unavailable",
                    "deno_source": js_runtime_info[1].source if js_runtime_info else "unavailable",
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
        tm.checkpoint(task_id)
        raise _classify_download_error(exc, stage="downloading_audio") from exc
    finally:
        if not succeeded:
            cleanup = _cleanup_task_staging(staging_dir)
            _update_download_details(task_id, {"cleanup": cleanup})


def get_video_info(url: str) -> dict:
    ffmpeg = _facade("resolve_ffmpeg_path", resolve_ffmpeg_path)()
    options = _download_options(
        "info",
        "%(title)s.%(ext)s",
        quiet=True,
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
