"""Discovery and validation for desktop download/runtime dependencies.

Runtime lookup is intentionally centralized so download, health checks and the
settings UI all report the same answer.  Release builds prefer the tools placed
inside the App bundle and do not inherit developer-specific paths by default.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

import yt_dlp

from ..utils.config import (
    BASE_DIR,
    DOWNLOADS_DIR,
    environment_path_overrides_enabled,
    is_frozen_app,
)


@dataclass(frozen=True)
class RuntimeExecutable:
    name: str
    path: Path
    source: str
    available: bool
    version: str = ""
    architectures: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["path"] = str(self.path)
        payload["architectures"] = list(self.architectures)
        return payload


def _candidate_path(value: str | os.PathLike[str] | None, executable_name: str) -> Path | None:
    if not value:
        return None
    raw = Path(value).expanduser()
    if raw.is_dir():
        raw = raw / executable_name
    if not raw.is_absolute() and len(raw.parts) == 1:
        found = shutil.which(str(raw))
        if found:
            raw = Path(found)
    try:
        return raw.resolve()
    except OSError:
        return raw.absolute()


def _detect_architectures(path: Path) -> tuple[str, ...]:
    """Return architectures reported by the native `file` utility, if any."""
    file_tool = "/usr/bin/file" if Path("/usr/bin/file").is_file() else shutil.which("file")
    if not file_tool:
        return ()
    try:
        result = subprocess.run(
            [file_tool, "-b", str(path)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    description = (result.stdout or "").lower()
    found: list[str] = []
    if "arm64" in description or "aarch64" in description:
        found.append("arm64")
    if "x86_64" in description or "x86-64" in description:
        found.append("x86_64")
    return tuple(found)


def _architecture_error(architectures: Sequence[str]) -> str:
    if not architectures:
        # Shell-script launchers and some platforms do not expose a binary
        # architecture. Their executable/version probes remain authoritative.
        return ""
    machine = platform.machine().lower()
    expected = "arm64" if machine in {"arm64", "aarch64"} else (
        "x86_64" if machine in {"x86_64", "amd64"} else ""
    )
    if expected and expected not in architectures:
        return f"运行时架构不兼容（当前系统 {expected}）"
    return ""


def validate_runtime_executable(
    path: str | os.PathLike[str],
    *,
    name: str,
    source: str,
    version_args: Sequence[str] = ("-version",),
) -> RuntimeExecutable:
    """Validate existence, executable permission, architecture and startup."""
    candidate = _candidate_path(path, name) or Path(path)
    if not candidate.is_file():
        return RuntimeExecutable(name, candidate, source, False, error="文件不存在")
    if not os.access(candidate, os.X_OK):
        return RuntimeExecutable(name, candidate, source, False, error="文件不可执行")

    architectures = _detect_architectures(candidate)
    arch_error = _architecture_error(architectures)
    if arch_error:
        return RuntimeExecutable(
            name, candidate, source, False,
            architectures=architectures, error=arch_error,
        )

    try:
        result = subprocess.run(
            [str(candidate), *version_args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RuntimeExecutable(
            name, candidate, source, False,
            architectures=architectures,
            error=f"无法启动：{type(exc).__name__}",
        )
    combined = (getattr(result, "stdout", "") or getattr(result, "stderr", "") or "").strip()
    version = combined.splitlines()[0][:240] if combined else ""
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        return RuntimeExecutable(
            name, candidate, source, False, version=version,
            architectures=architectures, error=f"版本检查失败（退出码 {return_code}）",
        )
    return RuntimeExecutable(
        name, candidate, source, True, version=version,
        architectures=architectures,
    )


def _bundled_candidates(name: str) -> Iterable[tuple[Path, str]]:
    env_name = f"SUBTITLE_FACTORY_BUNDLED_{name.upper().replace('-', '_')}"
    injected = _candidate_path(os.getenv(env_name), name)
    if injected:
        yield injected, "bundled"

    roots: list[Path] = []
    if is_frozen_app():
        roots.append(Path(sys.executable).resolve().parent)
        frozen_root = getattr(sys, "_MEIPASS", None)
        if frozen_root:
            roots.append(Path(frozen_root).resolve())
    else:
        # These are generated bundle staging locations, never a developer's
        # Homebrew prefix or other machine-specific absolute path.
        roots.extend([
            BASE_DIR / "runtime",
            BASE_DIR.parent / "frontend" / "src-tauri" / "backend-runtime",
        ])
    seen: set[Path] = set()
    for root in roots:
        for candidate in (root / "bin" / name, root / name, root / "tools" / name):
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield resolved, "bundled"


def _resolve_executable(
    name: str,
    *,
    user_path: str | os.PathLike[str] | None,
    environment_variable: str,
    version_args: Sequence[str],
) -> RuntimeExecutable | None:
    candidates: list[tuple[Path, str]] = list(_bundled_candidates(name))
    custom = _candidate_path(user_path, name)
    if custom:
        candidates.append((custom, "user"))
    if environment_path_overrides_enabled():
        environment = _candidate_path(os.getenv(environment_variable), name)
        if environment:
            candidates.append((environment, "environment"))
    system = shutil.which(name)
    if system:
        candidates.append((Path(system).resolve(), "path"))

    seen: set[Path] = set()
    for path, source in candidates:
        if path in seen:
            continue
        seen.add(path)
        result = validate_runtime_executable(
            path, name=name, source=source, version_args=version_args,
        )
        if result.available:
            return result
    return None


def resolve_ffmpeg_path(
    user_path: str | os.PathLike[str] | None = None,
) -> RuntimeExecutable | None:
    """Resolve FFmpeg in bundled → App setting → environment → PATH order."""
    return _resolve_executable(
        "ffmpeg", user_path=user_path, environment_variable="FFMPEG_PATH",
        version_args=("-version",),
    )


def resolve_ffprobe_path(
    user_path: str | os.PathLike[str] | None = None,
) -> RuntimeExecutable | None:
    return _resolve_executable(
        "ffprobe", user_path=user_path, environment_variable="FFPROBE_PATH",
        version_args=("-version",),
    )


def resolve_yt_dlp_path(
    user_path: str | os.PathLike[str] | None = None,
) -> RuntimeExecutable | None:
    return _resolve_executable(
        "yt-dlp", user_path=user_path, environment_variable="YT_DLP_PATH",
        version_args=("--version",),
    )


def _output_directory_status(output_dir: str | os.PathLike[str] | None) -> dict:
    path = Path(output_dir or DOWNLOADS_DIR).expanduser()
    existing = path if path.exists() else path.parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    writable = existing.is_dir() and os.access(existing, os.W_OK)
    try:
        free_bytes = shutil.disk_usage(existing).free
    except OSError:
        free_bytes = 0
        writable = False
    return {
        "path": str(path.resolve()),
        "exists": path.is_dir(),
        "writable": writable,
        "free_bytes": free_bytes,
    }


def get_download_runtime_status(
    user_ffmpeg_path: str | os.PathLike[str] | None = None,
    user_download_dir: str | os.PathLike[str] | None = None,
    user_yt_dlp_path: str | os.PathLike[str] | None = None,
) -> dict:
    """Status payload shared by health checks and the settings preflight."""
    ffmpeg = resolve_ffmpeg_path(user_ffmpeg_path)
    ffprobe = resolve_ffprobe_path(None)
    cli = resolve_yt_dlp_path(user_yt_dlp_path)
    version = getattr(getattr(yt_dlp, "version", None), "__version__", "")
    yt_dlp_status = {
        "available": bool(version),
        "source": "python_package",
        "version": version,
        "cli": cli.to_dict() if cli else None,
    }
    output = _output_directory_status(user_download_dir)
    return {
        "ok": bool(ffmpeg and yt_dlp_status["available"] and output["writable"]),
        "ffmpeg": ffmpeg.to_dict() if ffmpeg else {
            "name": "ffmpeg", "available": False, "error": "未找到可用的 FFmpeg",
            "path": "", "source": "unavailable", "version": "", "architectures": [],
        },
        "ffprobe": ffprobe.to_dict() if ffprobe else {
            "name": "ffprobe", "available": False, "error": "未找到可用的 FFprobe",
            "path": "", "source": "unavailable", "version": "", "architectures": [],
        },
        "yt_dlp": yt_dlp_status,
        "output": output,
    }
