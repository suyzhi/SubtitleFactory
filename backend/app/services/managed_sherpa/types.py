"""Shared types, constants, and facade lookup for managed sherpa-onnx models."""

from __future__ import annotations

import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Iterator

_MANIFEST_NAME = ".subtitle-factory-manifest.json"
_DOWNLOAD_LOCK = threading.Lock()
_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)


class ManagedModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: str,
        suggestion: str,
        *,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.suggestion = suggestion
        self.recoverable = recoverable
        self.available_actions = ["retry", "repair"] if recoverable else []


@dataclass(frozen=True)
class ManagedSegment:
    start: float
    end: float
    text: str
    timings: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ManagedSession:
    segments: Iterator[ManagedSegment]
    audio_duration: float
    detected_language: str
    device: str
    compute_type: str
    model_label: str
    progress_start: float = 25.0


def facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default
