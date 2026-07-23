"""YouTube download service using the bundled yt-dlp Python API."""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yt_dlp

from ..utils.config import DOWNLOADS_DIR
from ..utils.task_manager import task_manager
from .runtime_diagnostics import resolve_ffmpeg_path

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
    "use --cookies-from-browser",
)


class DownloadServiceError(RuntimeError):
    """A stable, user-actionable failure raised by the download runtime."""

    def __init__(
        self,
        message: str,
        error_code: str,
        *,
        recoverable: bool = True,
        actions: list[str] | None = None,
        suggestion: str = "请检查下载设置后重试",
    ):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = recoverable
        self.available_actions = actions or (["retry"] if recoverable else [])
        self.suggestion = suggestion


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


def _classify_download_error(exc: BaseException) -> DownloadServiceError:
    message = str(exc)
    lowered = message.lower()
    if any(marker in lowered for marker in _AUTH_CHALLENGE_MARKERS):
        return DownloadServiceError(
            "YouTube 要求登录或人机验证，Google Chrome 登录状态未能通过验证",
            "AUTH_REQUIRED",
            actions=["retry"],
            suggestion="请先在 Google Chrome 中登录 YouTube 并确认该视频可播放，再回到 App 重试",
        )
    if "private video" in lowered or "this is a private video" in lowered:
        return DownloadServiceError(
            "该视频已被作者设为私密",
            "PRIVATE_VIDEO",
            recoverable=False,
            suggestion="请使用有权限观看的 YouTube 账号，或更换视频链接",
        )
    unavailable_markers = (
        "video unavailable", "this video is unavailable",
        "has been removed", "copyright", "not available in your country",
        "members-only", "premieres in",
    )
    merge_markers = (
        "ffmpeg", "ffprobe", "merger", "merge", "postprocessing",
        "post-processing", "remux", "requested format is not available",
    )
    if any(marker in lowered for marker in unavailable_markers):
        return DownloadServiceError(
            "视频不可用，可能已删除、设为私有或存在地区/账号限制",
            "VIDEO_UNAVAILABLE",
            recoverable=False,
            suggestion="请在浏览器中确认该视频无需登录且可以正常播放",
        )
    if any(marker in lowered for marker in merge_markers):
        return DownloadServiceError(
            "音视频下载完成前后的合并步骤失败",
            "MERGE_FAILED",
            actions=["retry", "open_settings"],
            suggestion="请在下载与存储设置中检查 FFmpeg 状态后重新下载",
        )
    return DownloadServiceError(
        f"视频下载失败：{message[:400]}",
        "DOWNLOAD_FAILED",
        actions=["retry"],
        suggestion="请检查网络连接和视频地址后重试",
    )


def _needs_browser_auth(exc: BaseException) -> bool:
    lowered = str(exc).lower()
    return any(marker in lowered for marker in _AUTH_CHALLENGE_MARKERS)


def _with_chrome_cookies(options: dict) -> dict:
    authenticated = dict(options)
    authenticated["cookiesfrombrowser"] = ("chrome",)
    return authenticated


def _resolve_deno_path() -> Optional[Path]:
    candidates = [
        os.getenv("SUBTITLE_FACTORY_BUNDLED_DENO"),
        str(Path(os.getenv("SUBTITLE_FACTORY_RESOURCE_DIR", "")) / "bin" / "deno")
        if os.getenv("SUBTITLE_FACTORY_RESOURCE_DIR") else None,
        shutil.which("deno"),
    ]
    return next((
        Path(candidate).expanduser().resolve(strict=False)
        for candidate in candidates
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK)
    ), None)


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
        # Long playlist jobs must tolerate short proxy/CDN outages without
        # turning one transient 503 into a permanently failed video.  yt-dlp
        # keeps partial files and resumes them on the next attempt.
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 5,
        "file_access_retries": 5,
        "socket_timeout": 30,
        "continuedl": True,
        "retry_sleep_functions": {
            "http": lambda attempt: min(30, 2 ** max(0, attempt)),
            "fragment": lambda attempt: min(20, 2 ** max(0, attempt)),
            "extractor": lambda attempt: min(20, 2 ** max(0, attempt)),
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
    if deno:
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

    normalized_url = normalize_youtube_url(url)
    project_dl_dir = os.path.join(download_dir or DOWNLOADS_DIR, project_id)
    os.makedirs(project_dl_dir, exist_ok=True)
    output_template = os.path.join(project_dl_dir, "%(title)s.%(ext)s")
    thumbnail_template = os.path.join(project_dl_dir, "thumbnail.%(ext)s")

    # Avoid returning a stale cover when a project downloads a different URL.
    for old_thumbnail in Path(project_dl_dir).glob("thumbnail.*"):
        if old_thumbnail.suffix.lower() in _IMAGE_EXTENSIONS and old_thumbnail.is_file():
            old_thumbnail.unlink()

    options = _download_options(
        task_id, output_template, thumbnail_template=thumbnail_template,
        ffmpeg_location=str(ffmpeg.path), quality=quality, container=container,
        progress_start=progress_start, progress_end=progress_end,
    )

    def extract() -> tuple[dict, Optional[str]]:
        with yt_dlp.YoutubeDL(options) as ydl:
            extracted = ydl.extract_info(normalized_url, download=True)
            task_manager.checkpoint(task_id)
            return extracted, _find_final_video(extracted, ydl, container)

    try:
        try:
            info, video_path = extract()
        except yt_dlp.utils.DownloadError as exc:
            if not _needs_browser_auth(exc):
                raise
            task_manager.update_task(
                task_id, step="downloading", progress=4,
                message="YouTube 要求验证，正在使用 Google Chrome 登录状态重试...",
            )
            options = _with_chrome_cookies(options)
            info, video_path = extract()
        thumbnail_path = _find_thumbnail(info, project_dl_dir)
        if not video_path:
            raise DownloadServiceError(
                "下载结束后未找到合并完成的 MP4 视频文件",
                "MERGE_FAILED",
                actions=["retry", "open_settings"],
                suggestion="请检查 FFmpeg 状态和剩余磁盘空间后重新下载",
            )
        title = info.get("title") or os.path.basename(video_path)
        thumbnail_url = info.get("thumbnail")
        if not isinstance(thumbnail_url, str) or not thumbnail_url.startswith(("http://", "https://")):
            thumbnail_url = None
        task_manager.update_task(
            task_id, step="downloaded", progress=progress_end, message="视频下载完成",
            details={
                "video_path": video_path,
                "title": title,
                "file_size": os.path.getsize(video_path),
                "thumbnail_url": thumbnail_url,
                "thumbnail_path": thumbnail_path,
                "normalized_url": normalized_url,
                "ffmpeg_source": ffmpeg.source,
            },
        )
        return video_path
    except yt_dlp.utils.DownloadError as exc:
        # A progress-hook cancellation can be wrapped as DownloadError by
        # yt-dlp; restore the cooperative TaskCancelled control flow.
        task_manager.checkpoint(task_id)
        raise _classify_download_error(exc) from exc
    except DownloadServiceError:
        raise
    except (OSError, ValueError) as exc:
        raise _classify_download_error(exc) from exc


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

    def extract() -> tuple[dict, Optional[str]]:
        with yt_dlp.YoutubeDL(options) as ydl:
            extracted = ydl.extract_info(normalized_url, download=True)
            task_manager.checkpoint(task_id)
            return extracted, _find_downloaded_audio(extracted, ydl)

    try:
        try:
            info, audio_source_path = extract()
        except yt_dlp.utils.DownloadError as exc:
            if not _needs_browser_auth(exc):
                raise
            task_manager.update_task(
                task_id, step="downloading_audio", progress=4,
                message="YouTube 要求验证，正在使用 Google Chrome 登录状态重试...",
            )
            options = _with_chrome_cookies(options)
            info, audio_source_path = extract()
        if not audio_source_path:
            raise DownloadServiceError(
                "音频下载结束后未找到可转换的音轨",
                "AUDIO_DOWNLOAD_FAILED",
                actions=["retry"],
                suggestion="请检查网络、视频权限和剩余磁盘空间后重试",
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
            },
        )
        return audio_source_path
    except yt_dlp.utils.DownloadError as exc:
        task_manager.checkpoint(task_id)
        raise _classify_download_error(exc) from exc
    except DownloadServiceError:
        raise
    except (OSError, ValueError) as exc:
        raise _classify_download_error(exc) from exc


def get_video_info(url: str) -> dict:
    try:
        ffmpeg = resolve_ffmpeg_path()
        options = _download_options(
            "info", "%(title)s.%(ext)s", quiet=True,
            ffmpeg_location=str(ffmpeg.path) if ffmpeg else None,
        )
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(normalize_youtube_url(url), download=False)
        except yt_dlp.utils.DownloadError as exc:
            if not _needs_browser_auth(exc):
                raise
            with yt_dlp.YoutubeDL(_with_chrome_cookies(options)) as ydl:
                info = ydl.extract_info(normalize_youtube_url(url), download=False)
        return {
            "title": info.get("title", ""),
            "duration": float(info.get("duration") or 0),
            "id": info.get("id", ""),
            "thumbnail_url": info.get("thumbnail") or None,
        }
    except Exception as exc:
        logger.warning("[Download] 获取视频信息失败: %s", exc)
        return {"title": "", "duration": 0, "id": "", "thumbnail_url": None}
