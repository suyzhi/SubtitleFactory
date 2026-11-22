"""YouTube download exception unwrapping, redaction, and user-facing classification."""

from __future__ import annotations

import re
import socket
from urllib.error import HTTPError, URLError

from ..download_errors import DownloadServiceError

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
                    "DISK_FULL",
                    actions=["open_settings", "retry"],
                    suggestion="请释放下载磁盘空间或更改存储目录后重试",
                    details=diagnostic,
                )
            if item.errno in {1, 13, 30}:
                return DownloadServiceError(
                    "输出目录没有写入权限",
                    "OUTPUT_PERMISSION_DENIED",
                    actions=["open_settings", "retry"],
                    suggestion="请在下载与存储设置中选择可写目录后重试",
                    details=diagnostic,
                )

    if any(marker in lowered for marker in _COOKIE_ACCESS_MARKERS):
        return DownloadServiceError(
            "无法读取 Google Chrome 登录状态",
            "COOKIE_ACCESS_FAILED",
            actions=["retry", "open_settings"],
            suggestion="此链接可能需要登录或额外权限；请使用可公开下载的链接，或在浏览器中获取有权使用的媒体后本地导入",
            details=diagnostic,
        )

    membership = any(
        marker in lowered
        for marker in (
            "join this channel",
            "members-only",
            "members only",
            "subscriber-only",
            "subscriber only",
            "premium-only",
        )
    )
    if membership:
        return DownloadServiceError(
            "当前 YouTube 账号没有该会员内容的观看权限"
            if authenticated
            else "该视频需要频道会员或 Premium 权限",
            "MEMBERSHIP_REQUIRED",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="此链接可能需要登录或额外权限；请使用可公开下载的链接，或在浏览器中获取有权使用的媒体后本地导入",
            details=diagnostic,
        )

    private = "private video" in lowered or "this is a private video" in lowered
    if private:
        return DownloadServiceError(
            "当前 YouTube 账号无权观看该私密视频",
            "PRIVATE_VIDEO",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="此链接可能需要登录或额外权限；请使用可公开下载的链接，或在浏览器中获取有权使用的媒体后本地导入",
            details=diagnostic,
        )

    age_restricted = any(
        marker in lowered
        for marker in (
            "age-restricted",
            "age restricted",
            "confirm your age",
            "inappropriate for some users",
        )
    )
    if age_restricted:
        return DownloadServiceError(
            "YouTube 年龄限制验证未通过",
            "AGE_RESTRICTED",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="此链接可能需要登录或额外权限；请使用可公开下载的链接，或在浏览器中获取有权使用的媒体后本地导入",
            details=diagnostic,
        )

    if any(marker in lowered for marker in _AUTH_CHALLENGE_MARKERS):
        return DownloadServiceError(
            "YouTube 要求登录或验证码验证",
            "AUTH_REQUIRED",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="此链接可能需要登录或额外权限；请使用可公开下载的链接，或在浏览器中获取有权使用的媒体后本地导入",
            details=diagnostic,
        )

    if "po token" in lowered or "proof of origin" in lowered:
        return DownloadServiceError(
            "YouTube 要求当前版本尚未配置的 PO Token",
            "PO_TOKEN_REQUIRED",
            actions=["retry"],
            suggestion="请稍后重试或安装包含兼容 YouTube 挑战支持的新版本；当前 App 不会加载第三方 PO Token 提供器",
            details=diagnostic,
        )

    if any(marker in lowered for marker in _MEDIA_STREAM_AUTH_MARKERS):
        return DownloadServiceError(
            "YouTube 拒绝了媒体流访问",
            "MEDIA_ACCESS_DENIED",
            auth_retry_eligible=not authenticated,
            actions=["retry"],
            suggestion="此链接可能需要登录或额外权限；请使用可公开下载的链接，或在浏览器中获取有权使用的媒体后本地导入",
            details=diagnostic,
        )

    if any(
        marker in lowered
        for marker in (
            "http error 429",
            "too many requests",
            "rate limit",
            "content isn't available, try again later",
        )
    ):
        return DownloadServiceError(
            "YouTube 暂时限制了请求频率",
            "RATE_LIMITED",
            actions=["retry"],
            suggestion="此链接可能需要登录或额外权限；请使用可公开下载的链接，或在浏览器中获取有权使用的媒体后本地导入",
            details=diagnostic,
        )

    if _has_exception_type(exc, "GeoRestrictedError") or any(
        marker in lowered
        for marker in (
            "not available in your country",
            "not available in your region",
            "geo-restricted",
            "geo restricted",
        )
    ):
        return DownloadServiceError(
            "该视频在当前地区不可用",
            "GEO_RESTRICTED",
            recoverable=False,
            actions=[],
            suggestion="请确认该视频在当前网络地区可以合法播放",
            details=diagnostic,
        )

    if "drm protected" in lowered or "drm-protected" in lowered:
        return DownloadServiceError(
            "该视频受 DRM 保护，无法由下载器获取",
            "DRM_PROTECTED",
            recoverable=False,
            actions=[],
            suggestion="请改用不受 DRM 保护且允许下载的媒体来源",
            details=diagnostic,
        )

    http_not_found = any(
        isinstance(item, HTTPError) and int(item.code) in {404, 410}
        for item in _exception_chain(exc)
    )
    if http_not_found or any(
        marker in lowered
        for marker in (
            "has been removed",
            "removed by the uploader",
            "copyright",
            "account associated with this video has been terminated",
        )
    ):
        return DownloadServiceError(
            "该视频已删除或因版权原因不可用",
            "VIDEO_REMOVED",
            recoverable=False,
            actions=[],
            suggestion="请更换仍可在 YouTube 播放的视频链接",
            details=diagnostic,
        )

    if any(
        marker in lowered
        for marker in (
            "requested format is not available",
            "no video formats found",
            "requested format not available",
            "only images are available",
        )
    ):
        return DownloadServiceError(
            "所选画质或容器没有可用的原始格式",
            "FORMAT_UNAVAILABLE",
            actions=["retry", "open_settings"],
            suggestion="请改用 MKV 容器或调整画质；App 不会静默降低画质或转码",
            details=diagnostic,
        )

    merge_markers = (
        "ffmpeg",
        "ffprobe",
        "merger",
        "merge",
        "postprocessing",
        "post-processing",
        "remux",
    )
    if any(marker in lowered for marker in merge_markers):
        return DownloadServiceError(
            "下载完成后的音视频封装失败",
            "MERGE_FAILED",
            actions=["retry", "open_settings"],
            suggestion="请检查 FFmpeg 和磁盘空间；若选择 MP4，请改用 MKV 以保留最高原始画质",
            details=diagnostic,
        )

    network_types = (
        TimeoutError,
        socket.timeout,
        socket.gaierror,
        ConnectionError,
        URLError,
    )
    network_exception = any(
        isinstance(item, network_types) and not isinstance(item, HTTPError)
        for item in _exception_chain(exc)
    )
    http_5xx = any(
        isinstance(item, HTTPError) and 500 <= int(item.code) <= 599
        for item in _exception_chain(exc)
    )
    if (
        network_exception
        or http_5xx
        or any(
            marker in lowered
            for marker in (
                "timed out",
                "temporary failure in name resolution",
                "name or service not known",
                "connection reset",
                "connection aborted",
                "remote end closed connection",
                "http error 500",
                "http error 502",
                "http error 503",
                "http error 504",
            )
        )
    ):
        return DownloadServiceError(
            "网络连接暂时中断，下载尚未完成",
            "NETWORK_TEMPORARY",
            automatic_retry=True,
            actions=["retry"],
            suggestion="App 会按上限自动重试；若持续失败，请检查 DNS、代理或网络连接",
            details=diagnostic,
        )

    if "video unavailable" in lowered or "this video is unavailable" in lowered:
        return DownloadServiceError(
            "YouTube 返回视频不可用",
            "DOWNLOAD_FAILED",
            actions=["retry"],
            suggestion="此链接可能需要登录或额外权限；请使用可公开下载的链接，或在浏览器中获取有权使用的媒体后本地导入",
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
