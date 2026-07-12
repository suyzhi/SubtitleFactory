"""
字幕工厂 - AI 字幕翻译服务

调用 LLM API 将字幕翻译为目标语言。
严格保持时间轴不变，翻译自然且适合字幕显示。
"""

import json
import logging
from typing import List

from ..utils.task_manager import TaskCancelled, task_manager
from ..models.database import get_db
from .ai_settings import get_ai_settings
from .subtitle_cleaner import _validate_batch_results

logger = logging.getLogger(__name__)


def translate_subtitles(task_id: str, project_id: str, target_language: str = "zh"):
    """
    翻译项目字幕。
    使用 clean_text（如果有）否则用 raw_text。
    结果写入 translated_text。
    """
    ai = get_ai_settings(include_secret=True)
    if not ai.get("api_key") or ai["api_key"] == "your_api_key_here":
        raise ValueError("AI API Key 未配置，请在 App 的 AI 接入管理中设置")

    db = get_db()
    rows = db.execute(
        """SELECT id, idx, raw_text, clean_text FROM segments
           WHERE project_id = ? AND locked = 0 ORDER BY idx""",
        (project_id,)
    ).fetchall()
    db.close()

    if not rows:
        logger.info("[Translator] 没有需要翻译的字幕")
        task_manager.add_log(task_id, "info", "translating", "没有需要翻译的字幕")
        return []

    system_prompt = f"""你是专业视频字幕翻译助手。请把字幕翻译成目标语言。

目标语言：{target_language}

规则：
* 保持原意。
* 不要总结。
* 不要扩写。
* 不要加入解释。
* 先理解相邻字幕提供的上下文，再翻译当前字幕；保持人名、术语、语气和代词指代前后一致。
* context_before 与 context_after 只用于理解，不要翻译输出；只返回 items 中的 id。
* 翻译要自然，适合字幕显示。
* 每条字幕尽量简短。
* 必须保持输入数量不变。
* 必须按原 id 返回。
* 只返回 JSON 数组。

输入格式：
{{"context_before": [...], "items": [{{"id": "1", "text": "..."}}], "context_after": [...]}}

输出格式：
[{{"id": "1", "translated_text": "..."}}]"""

    all_segments = []
    for r in rows:
        source_text = r["clean_text"] if r["clean_text"] else r["raw_text"]
        all_segments.append({"id": str(r["idx"]), "text": source_text})

    total = len(all_segments)
    logger.info(f"[Translator] 开始翻译 {total} 条字幕 -> {target_language}")
    task_manager.add_log(
        task_id, "info", "translating",
        f"开始翻译 {total} 条字幕", detail=f"目标语言: {target_language}"
    )

    # 40 条一批，并额外附带前后各 3 条上下文。相比逐句翻译能显著改善
    # 指代和术语一致性，又不会明显增加 API 调用次数或响应长度。
    batch_size = 40
    all_results = []
    failed_batches = 0
    retry_count = 0
    total_batches = (total + batch_size - 1) // batch_size

    for i in range(0, total, batch_size):
        task_manager.checkpoint(task_id)
        batch = all_segments[i:i + batch_size]
        current_batch = i // batch_size + 1
        progress = 5 + (i / total) * 90
        task_manager.update_task(
            task_id, step="translating",
            progress=progress,
            message=f"正在 AI 翻译字幕 ({i + 1}-{min(i + batch_size, total)}/{total})...",
            details={
                "target_language": target_language,
                "total_segments": total,
                "batch_size": batch_size,
                "current_batch": current_batch,
                "total_batches": total_batches,
                "processed_segments": len(all_results),
                "failed_batches": failed_batches,
                "retry_count": retry_count,
            }
        )

        try:
            context_before = all_segments[max(0, i - 3):i]
            context_after = all_segments[i + len(batch):i + len(batch) + 3]
            result = _call_llm_translate(
                batch, system_prompt, ai, context_before, context_after,
                task_id=task_id,
            )
            task_manager.checkpoint(task_id)
            all_results.extend(result)
            task_manager.add_log(
                task_id, "info", "translating",
                f"第 {current_batch}/{total_batches} 批翻译完成",
                detail=f"批次范围: {i+1}-{min(i+batch_size, total)}, 已处理: {len(all_results)}/{total}"
            )
        except TaskCancelled:
            raise
        except Exception as e:
            logger.warning(f"[Translator] 批次 {current_batch} 失败: {e}")
            failed_batches += 1
            task_manager.add_log(
                task_id, "warning", "translating",
                f"第 {current_batch}/{total_batches} 批翻译失败",
                detail=str(e), suggestion="已跳过该批次，将继续处理剩余批次的字幕"
            )
            # 不以原文伪装成译文；保留已有译文并继续剩余批次。

    # Write only after all calls have reached a safe checkpoint. The transaction
    # rolls back if cancellation arrives while results are being applied.
    task_manager.checkpoint(task_id)
    db = get_db()
    updated = 0
    try:
        db.execute("BEGIN IMMEDIATE")
        for item_index, item in enumerate(all_results):
            if item_index % 20 == 0:
                task_manager.checkpoint(task_id)
            try:
                idx = int(item["id"])
                translated = item.get("translated_text", "")
                db.execute(
                    "UPDATE segments SET translated_text = ?, source_stage = 'translated' WHERE project_id = ? AND idx = ?",
                    (translated, project_id, idx)
                )
                updated += 1
            except (ValueError, KeyError):
                logger.warning(f"[Translator] 跳过无效结果: {item}")
        task_manager.checkpoint(task_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    logger.info(f"[Translator] 翻译完成: 更新 {updated} 条")
    task_manager.update_task(
        task_id, step="translate_done", progress=100,
        status="partial" if failed_batches else "running",
        message=f"翻译完成, 更新 {updated} 条" if not failed_batches else f"翻译部分完成，{failed_batches} 个批次失败",
        details={
            "target_language": target_language,
            "total_segments": total,
            "batch_size": batch_size,
            "total_batches": total_batches,
            "processed_segments": updated,
            "failed_batches": failed_batches,
            "retry_count": retry_count,
        }
    )
    task_manager.add_log(
        task_id, "info", "translating", "翻译全部完成",
        detail=f"目标语言: {target_language}, 更新 {updated}/{total} 条, 失败批次: {failed_batches}"
    )

    return all_results


def _call_llm_translate(batch: list, system_prompt: str, ai: dict,
                        context_before: list | None = None, context_after: list | None = None,
                        task_id: str | None = None) -> list:
    """调用 LLM 翻译一批字幕，自动重试一次"""
    import httpx

    headers = {
        "Authorization": f"Bearer {ai['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": ai["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps({
                "context_before": context_before or [],
                "items": batch,
                "context_after": context_after or [],
            }, ensure_ascii=False)}
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    for attempt in range(2):
        if task_id:
            task_manager.checkpoint(task_id)
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(
                    f"{ai['base_url'].rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                if task_id:
                    task_manager.checkpoint(task_id)
                data = response.json()
                content = data["choices"][0]["message"]["content"]

            parsed = _extract_json(content)
            if parsed and isinstance(parsed, list):
                return _validate_batch_results(batch, parsed, "translated_text")

        except TaskCancelled:
            raise
        except Exception as e:
            logger.warning(f"[Translator] LLM 调用失败 (尝试 {attempt + 1}): {e}")

    raise RuntimeError("AI 翻译连续两次失败或返回格式不完整")


def _extract_json(text: str):
    """从 LLM 回复中提取 JSON"""
    text = text.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return None
