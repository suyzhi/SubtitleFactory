"""Distribution-neutral download error contract used by API orchestration."""

from __future__ import annotations

from typing import Any


class DownloadServiceError(RuntimeError):
    """A stable, user-actionable failure raised by the direct download runtime."""

    def __init__(
        self,
        message: str,
        error_code: str,
        *,
        recoverable: bool = True,
        automatic_retry: bool = False,
        auth_retry_eligible: bool = False,
        actions: list[str] | None = None,
        suggestion: str = "请检查下载设置后重试",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = recoverable
        self.automatic_retry = automatic_retry
        self.auth_retry_eligible = auth_retry_eligible
        self.available_actions = actions or (["retry"] if recoverable else [])
        self.suggestion = suggestion
        self.details = details or {}
