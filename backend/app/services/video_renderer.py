"""
字幕工厂 - 视频压制服务 (ffmpeg)

将字幕压制进视频，生成带硬字幕的 MP4。
优先使用 ASS 字幕以获得更好的样式控制。
"""

import subprocess
import os
import logging

from ..utils.config import EXPORTS_DIR
from ..utils.task_manager import task_manager
from .runtime_diagnostics import resolve_ffmpeg_path

logger = logging.getLogger(__name__)


def burn_subtitles(task_id: str, video_path: str, subtitle_path: str,
                   output_path: str = None, project_id: str = None,
                   ffmpeg_path: str = None):
    """
    使用 ffmpeg 将字幕硬编码到视频中。
    支持 ASS 和 SRT 字幕。
    """
    task_manager.update_task(task_id, step="rendering", progress=80, message="正在压制字幕到视频...")
    task_manager.add_log(task_id, "info", "rendering", "开始压制字幕到视频",
                         detail=f"视频: {video_path}, 字幕: {subtitle_path}")

    if ffmpeg_path is None:
        try:
            from .app_settings import get_app_settings
            ffmpeg_path = get_app_settings().get("ffmpeg_path")
        except Exception:
            # Database initialization is intentionally not a prerequisite for
            # the renderer's standalone/unit-test use.
            ffmpeg_path = None
    ffmpeg = resolve_ffmpeg_path(ffmpeg_path)
    if ffmpeg is None:
        error = RuntimeError("视频导出缺少可用的 FFmpeg")
        error.error_code = "DOWNLOAD_RUNTIME_MISSING"
        error.recoverable = True
        error.available_actions = ["open_settings", "retry"]
        error.suggestion = "请在下载与存储设置中检查 FFmpeg 状态"
        raise error

    if not output_path:
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        output_path = os.path.join(EXPORTS_DIR, f"{project_id or 'output'}_hardsub.mp4")

    # 判断字幕格式
    subtitle_ext = os.path.splitext(subtitle_path)[1].lower()
    is_ass = subtitle_ext == ".ass"

    # 构建 ffmpeg 命令
    # 优先硬字幕；若用户机器上的 ffmpeg 未编译 libass，则自动回退为
    # 默认开启的内嵌字幕轨。这样 MP4/MKV 导出不会因为环境差异完全失效。
    escaped_path = subtitle_path.replace(":", "\\:").replace("'", "'\\''")

    cmd = [
        str(ffmpeg.path),
        "-i", video_path,
        "-vf", f"subtitles=filename='{escaped_path}'",
        "-c:v", "libx264",           # 视频编码
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",               # 音频编码
        "-b:a", "128k",
    ]
    container = os.path.splitext(output_path)[1].lstrip(".").lower() or "mp4"
    if container == "mp4":
        cmd.extend(["-movflags", "+faststart"])
    cmd.extend(["-y", output_path])

    try:
        logger.info(f"[Renderer] 开始压制: {video_path}")
        task_manager.add_log(
            task_id, "info", "rendering", "正在编码视频",
            detail=f"编码器: libx264, 音频: AAC, 字幕格式: {subtitle_ext}"
        )
        task_manager.update_task(task_id, step="rendering", progress=85, message="正在编码视频（这可能需要一些时间）...")

        task_manager.checkpoint(task_id)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        task_manager.checkpoint(task_id)

        subtitle_mode = "burned"
        if result.returncode != 0 and ("No such filter: 'subtitles'" in result.stderr or "Error parsing" in result.stderr):
            subtitle_codec = "mov_text" if container == "mp4" else "ass"
            fallback = [
                str(ffmpeg.path), "-i", video_path, "-i", subtitle_path,
                "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
                "-c:v", "copy", "-c:a", "copy", "-c:s", subtitle_codec,
                "-disposition:s:0", "default",
            ]
            if container == "mp4":
                fallback.extend(["-movflags", "+faststart"])
            fallback.extend(["-y", output_path])
            task_manager.add_log(
                task_id, "warning", "rendering", "当前 ffmpeg 不支持硬字幕，已自动改用内嵌字幕轨",
                detail=f"容器: {container.upper()} · 字幕编码: {subtitle_codec}",
            )
            task_manager.checkpoint(task_id)
            result = subprocess.run(fallback, capture_output=True, text=True, timeout=7200)
            task_manager.checkpoint(task_id)
            subtitle_mode = "embedded"

        if result.returncode != 0:
            task_manager.add_log(task_id, "error", "rendering", "视频导出失败", detail=result.stderr[-1200:])
            raise Exception(f"ffmpeg 视频导出失败: {result.stderr[-1200:]}")

        if not os.path.exists(output_path):
            raise Exception(f"输出文件未生成: {output_path}")

        file_size = os.path.getsize(output_path)
        logger.info(f"[Renderer] 压制完成: {output_path} ({file_size/1024/1024:.1f}MB)")
        task_manager.update_task(
            task_id, step="render_done", progress=95, message="视频压制完成",
            details={
                "video_path": video_path,
                "subtitle_path": subtitle_path,
                "output_path": output_path,
                "output_size": file_size,
                "format": container,
                "subtitle_mode": subtitle_mode,
                "codec_info": "libx264 + AAC",
            }
        )
        task_manager.add_log(
            task_id, "info", "rendering", "视频压制完成",
            detail=f"输出: {output_path}, 大小: {file_size/1024/1024:.1f}MB"
        )
        return output_path

    except subprocess.TimeoutExpired:
        task_manager.add_log(task_id, "error", "rendering", "视频压制超时", detail="超过 2 小时")
        raise Exception("视频压制超时（超过 2 小时）")
    except FileNotFoundError:
        task_manager.add_log(task_id, "error", "rendering", "FFmpeg 运行时不可用")
        raise Exception("FFmpeg 运行时不可用")
