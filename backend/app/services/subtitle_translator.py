"""
字幕工厂 - AI 字幕翻译服务

调用 LLM API 将字幕翻译为目标语言。
严格保持时间轴不变，翻译自然且适合字幕显示。
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from ..utils.task_manager import TaskCancelled, task_manager
from ..models.database import get_db
from .ai_providers import assigned_provider
from .subtitle_cleaner import _validate_batch_results

logger = logging.getLogger(__name__)


def translate_subtitles(task_id: str, project_id: str, target_language: str = "zh", provider_id: str | None = None, model: str | None = None):
    """
    翻译项目字幕。
    使用 clean_text（如果有）否则用 raw_text。
    结果写入 translated_text。
    """
    ai = assigned_provider("translate", provider_id, model)

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

    batches=[all_segments[i:i+batch_size] for i in range(0,total,batch_size)]
    def process(current_batch:int,batch:list):
        start=(current_batch-1)*batch_size
        before=all_segments[max(0,start-3):start]; after=all_segments[start+len(batch):start+len(batch)+3]
        fingerprint=json.dumps({"items":batch,"target_language":target_language,"provider":ai["provider"],"model":ai["model"]},ensure_ascii=False,sort_keys=True)
        db=get_db();cached=db.execute("SELECT result_json FROM ai_batch_results WHERE project_id=? AND operation='translate' AND input_fingerprint=? AND status='success' ORDER BY updated_at DESC LIMIT 1",(project_id,fingerprint)).fetchone()
        if cached:
            try: result=json.loads(cached['result_json']);db.close();return current_batch,result,None
            except (TypeError,ValueError,json.JSONDecodeError): pass
        db.execute("INSERT OR REPLACE INTO ai_batch_results (task_id,project_id,operation,batch_index,input_fingerprint,status,result_json,attempts,error,updated_at) VALUES (?,?,?,?,?,'running','[]',0,'',datetime('now','localtime'))",(task_id,project_id,'translate',current_batch,fingerprint));db.commit();db.close()
        try:
            result=_call_llm_translate(batch,system_prompt,ai,before,after,task_id=task_id)
            db=get_db();db.execute("UPDATE ai_batch_results SET status='success',result_json=?,attempts=3,updated_at=datetime('now','localtime') WHERE task_id=? AND batch_index=?",(json.dumps(result,ensure_ascii=False),task_id,current_batch));db.commit();db.close()
            return current_batch,result,None
        except Exception as exc:
            db=get_db();db.execute("UPDATE ai_batch_results SET status='failed',attempts=3,error=?,updated_at=datetime('now','localtime') WHERE task_id=? AND batch_index=?",(str(exc)[:500],task_id,current_batch));db.commit();db.close()
            return current_batch,[],exc
    completed={}
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='subtitle-translate') as executor:
        futures=[executor.submit(process,index,batch) for index,batch in enumerate(batches,1)]
        for done,future in enumerate(as_completed(futures),1):
            task_manager.checkpoint(task_id); current_batch,result,error=future.result();completed[current_batch]=result
            if error:
                failed_batches+=1;task_manager.add_log(task_id,'warning','translating',f'第 {current_batch}/{total_batches} 批翻译失败',detail=str(error),suggestion='可重试失败批次')
            task_manager.update_task(task_id,step='translating',progress=5+done/max(total_batches,1)*90,message=f'已完成 {done}/{total_batches} 个翻译批次',details={'target_language':target_language,'total_segments':total,'total_batches':total_batches,'failed_batches':failed_batches,'ai_provider':ai['provider'],'ai_model':ai['model']})
    for index in range(1,total_batches+1): all_results.extend(completed.get(index,[]))

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

    for attempt in range(3):
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
            if getattr(getattr(e,"response",None),"status_code",None) in (401,403):
                break
            if attempt < 2:
                retry_after=getattr(getattr(e,"response",None),"headers",{}).get("Retry-After")
                try: delay=min(30.0,max(0.0,float(retry_after))) if retry_after else 2 ** attempt
                except (TypeError,ValueError): delay=2 ** attempt
                time.sleep(delay)

    raise RuntimeError("AI 翻译连续三次失败或返回格式不完整")


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
