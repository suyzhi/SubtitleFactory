"""Project-scoped media analysis endpoints."""

import wave

from fastapi import APIRouter, HTTPException, Query

from ..services.waveform import get_waveform


router = APIRouter(prefix="/api")


@router.get("/projects/{project_id}/waveform")
def project_waveform(
    project_id: str,
    points: int = Query(4_000, ge=100, le=20_000),
):
    try:
        return get_waveform(project_id, points)
    except FileNotFoundError as error:
        raise HTTPException(404, detail={
            "code": "AUDIO_NOT_READY",
            "message": str(error),
            "suggestion": "请先提取音频",
            "recoverable": True,
        }) from error
    except (ValueError, wave.Error) as error:
        raise HTTPException(422, detail={
            "code": "WAVEFORM_FORMAT_UNSUPPORTED",
            "message": str(error),
            "suggestion": "请重新提取标准 WAV 音频",
            "recoverable": True,
        }) from error
