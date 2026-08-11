"""Distribution-channel policy shared by API and runtime services.

The direct build keeps the complete desktop feature set.  The Mac App Store
build deliberately excludes third-party media acquisition so the submitted
binary has a small, reviewable authorization surface.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


DIRECT_CHANNEL = "direct"
APP_STORE_CHANNEL = "app_store"
CHANNEL_ENV = "SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL"
_VALID_CHANNELS = {DIRECT_CHANNEL, APP_STORE_CHANNEL}
_EXTERNAL_MODEL_IDS = {"custom", "parakeet-tdt-0.6b-v3-coreml"}


class DistributionPolicyError(RuntimeError):
    """Raised when a build channel intentionally does not expose a feature."""

    def __init__(self, feature: str, message: str, suggestion: str):
        super().__init__(message)
        self.feature = feature
        self.error_code = "DISTRIBUTION_FEATURE_UNAVAILABLE"
        self.suggestion = suggestion
        self.recoverable = False
        self.available_actions: list[str] = []


@dataclass(frozen=True)
class DistributionCapabilities:
    channel: str
    youtube: bool
    browser_cookies: bool
    custom_download_directory: bool
    filesystem_automation: bool
    external_runtime_paths: bool

    def as_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def distribution_channel() -> str:
    """Return a validated channel without caching mutable process state."""
    value = os.getenv(CHANNEL_ENV, DIRECT_CHANNEL).strip().lower()
    return value if value in _VALID_CHANNELS else DIRECT_CHANNEL


def distribution_capabilities() -> DistributionCapabilities:
    app_store = distribution_channel() == APP_STORE_CHANNEL
    return DistributionCapabilities(
        channel=APP_STORE_CHANNEL if app_store else DIRECT_CHANNEL,
        youtube=not app_store,
        browser_cookies=not app_store,
        custom_download_directory=not app_store,
        filesystem_automation=not app_store,
        external_runtime_paths=not app_store,
    )


def is_external_model_reference(model_id: object) -> bool:
    return isinstance(model_id, str) and (
        model_id in _EXTERNAL_MODEL_IDS or model_id.startswith("local:")
    )


def require_youtube_feature() -> None:
    if distribution_capabilities().youtube:
        return
    raise DistributionPolicyError(
        "youtube",
        "Mac App Store 版本不提供第三方网站媒体读取、播放或下载",
        "请导入您有权处理的本地视频文件",
    )


def require_filesystem_automation() -> None:
    if distribution_capabilities().filesystem_automation:
        return
    raise DistributionPolicyError(
        "filesystem_automation",
        "Mac App Store 版本暂不提供持久监听文件夹或路径式批量导入",
        "请使用导入按钮把视频复制到 App 的受保护项目库",
    )


def require_external_runtime_paths() -> None:
    if distribution_capabilities().external_runtime_paths:
        return
    raise DistributionPolicyError(
        "external_runtime_paths",
        "Mac App Store 版本不接受外部模型、可执行文件或自定义下载目录",
        "请使用 App 管理的模型和受保护项目库",
    )


def require_project_distribution(project_id: str | None = None) -> None:
    """Block legacy web-source projects without mutating direct-build data."""
    if not project_id or distribution_capabilities().youtube:
        return
    from ..models.database import get_db

    db = get_db()
    try:
        row = db.execute(
            "SELECT source_type FROM projects WHERE id=?", (project_id,),
        ).fetchone()
    finally:
        db.close()
    if row and row["source_type"] == "youtube":
        require_youtube_feature()
