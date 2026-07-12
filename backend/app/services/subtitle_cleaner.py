"""AI subtitle restructuring: join ASR fragments into complete spoken sentences."""

import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

from ..models.database import get_db
from ..utils.task_manager import TaskCancelled, task_manager
from .ai_providers import assigned_provider
from .ai_settings import get_ai_settings

logger = logging.getLogger(__name__)

CLEANER_SYSTEM_PROMPT = """你是谨慎、忠实的视频字幕整理助手。输入是按时间顺序排列的 ASR 碎片。

你的唯一任务是识别句子边界，把属于同一句话的相邻碎片合并。忠实保留原文及原意永远是第一优先级；这不是润色、改写或内容编辑任务。

规则：
* 每个输入 id 必须且只能出现一次，顺序必须与输入一致。
* 只能合并相邻 id，不能交换、遗漏或重复。
* clean_text 应保留该组原文的措辞、信息、语气、重复和口语特征；不得同义改写、调整叙述顺序、概括、扩写、翻译或为了“更自然”而润色。
* 只可补充必要标点、修正大小写，以及修正上下文中毫无歧义的单个 ASR 错词；不确定时必须原样保留。
* 根据语义和标点识别真正的完整句边界。完整句优先，不要把一个完整句机械截断。
* clean_text 必须完整覆盖该组所有碎片表达的内容，不能把内容移动到相邻组。
* 只返回 JSON 数组，不要解释。

输入：[{"id":"1","start":0.0,"end":1.2,"raw_text":"I want to"},{"id":"2","start":1.2,"end":2.5,"raw_text":"show you this"}]
输出：[{"ids":["1","2"],"clean_text":"I want to show you this."}]"""


def clean_subtitles(task_id: str, project_id: str, target_length: int = 42, provider_id: str | None = None, model: str | None = None):
    target_length = max(16, min(100, int(target_length)))
    try:
        ai = assigned_provider("clean", provider_id, model)
    except ValueError:
        ai = get_ai_settings(include_secret=True)

    db = get_db()
    rows = [dict(row) for row in db.execute(
        "SELECT * FROM segments WHERE project_id = ? ORDER BY idx", (project_id,)
    ).fetchall()]
    db.close()
    if not rows:
        task_manager.update_task(task_id, step="cleaning", progress=100, message="没有字幕需要处理")
        return []

    initial_fingerprint = _fingerprint(rows)
    batches = _build_semantic_batches(rows)
    total_batches = len(batches)
    grouped_results = []
    failed_batches = 0
    task_manager.add_log(
        task_id, "info", "AI 句子重组",
        f"开始分析 {len(rows)} 条字幕，将在 {total_batches} 个语义批次中识别完整句子",
        detail=f"AI: {ai['provider']} · {ai['model']}"
    )

    def process(batch_index: int, batch: list[dict]):
        fingerprint = json.dumps({"segments":_fingerprint(batch),"provider":ai["provider"],"model":ai["model"],"target_length":target_length}, ensure_ascii=False, sort_keys=True)
        db=get_db()
        cached=db.execute("SELECT result_json FROM ai_batch_results WHERE project_id=? AND operation='clean' AND input_fingerprint=? AND status='success' ORDER BY updated_at DESC LIMIT 1",(project_id,fingerprint)).fetchone()
        if cached:
            try:
                groups=json.loads(cached["result_json"]);db.close();return batch_index,groups,None
            except (TypeError,ValueError,json.JSONDecodeError):
                pass
        db.execute("""INSERT OR REPLACE INTO ai_batch_results
            (task_id,project_id,operation,batch_index,input_fingerprint,status,result_json,attempts,error,updated_at)
            VALUES (?,?,?,?,?,'running','[]',0,'',?)""", (task_id,project_id,"clean",batch_index,fingerprint,_now())); db.commit(); db.close()
        try:
            groups = _call_llm_group(batch, ai, target_length, task_id=task_id)
            db=get_db(); db.execute("UPDATE ai_batch_results SET status='success',result_json=?,attempts=3,error='',updated_at=? WHERE task_id=? AND batch_index=?", (json.dumps(groups,ensure_ascii=False),_now(),task_id,batch_index)); db.commit(); db.close()
            return batch_index, groups, None
        except Exception as exc:
            db=get_db(); db.execute("UPDATE ai_batch_results SET status='failed',attempts=3,error=?,updated_at=? WHERE task_id=? AND batch_index=?", (str(exc)[:500],_now(),task_id,batch_index)); db.commit(); db.close()
            groups=[{"ids":[str(row["idx"])],"clean_text":row["clean_text"] or row["raw_text"]} for row in batch]
            return batch_index, groups, exc

    completed={}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="subtitle-ai") as executor:
        futures=[executor.submit(process,index,batch) for index,batch in enumerate(batches,1)]
        for done_count, future in enumerate(as_completed(futures),1):
            task_manager.checkpoint(task_id); index,groups,error=future.result(); completed[index]=groups
            if error:
                failed_batches += 1; task_manager.add_log(task_id,"warning","AI 句子重组",f"第 {index}/{total_batches} 批未能重组",detail=str(error),suggestion="可单独重试失败批次")
            task_manager.update_task(task_id,step="restructuring",progress=5+done_count/max(total_batches,1)*88,message=f"已完成 {done_count}/{total_batches} 个批次",details={"total_segments":len(rows),"total_batches":total_batches,"completed_batches":done_count,"failed_batches":failed_batches,"ai_provider":ai["provider"],"ai_model":ai["model"]})
    for index in range(1,total_batches+1): grouped_results.extend(completed[index])

    # AI results stay in memory until every batch has completed. Cancellation at
    # either checkpoint below leaves the original subtitle rows untouched.
    task_manager.checkpoint(task_id)
    final_segments = _compose_final_segments(rows, grouped_results)
    task_manager.checkpoint(task_id)
    conflicts = _commit_restructured_segments(
        project_id, rows, initial_fingerprint, final_segments, task_id=task_id
    )
    merged_count = len(rows) - len(final_segments)
    status = "partial" if failed_batches else "running"
    message = f"AI 整理完成：{len(rows)} 条重组为 {len(final_segments)} 条完整字幕"
    if failed_batches:
        message += f"，{failed_batches} 个批次保持原样"
    task_manager.update_task(
        task_id, step="cleaning_done", progress=100, status=status, message=message,
        details={
            "source_segments": len(rows), "total_segments": len(final_segments),
            "merged_segments": max(0, merged_count), "failed_batches": failed_batches,
            "conflict_batches": conflicts,
            "total_batches": total_batches, "ai_provider": ai["provider"], "ai_model": ai["model"],
            "target_length": target_length,
        },
    )
    task_manager.add_log(task_id, "info", "AI 句子重组", message, suggestion="如效果不合适，可使用“撤销整理”恢复")
    return final_segments


def undo_last_clean(project_id: str) -> int:
    db = get_db()
    revision = db.execute(
        "SELECT * FROM segment_revisions WHERE project_id = ? AND operation = 'ai_clean' ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if not revision:
        db.close()
        raise ValueError("没有可撤销的 AI 整理记录")
    segments = json.loads(revision["segments_json"])
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM segments WHERE project_id = ?", (project_id,))
        for segment in segments:
            _insert_segment(db, segment)
        db.execute("DELETE FROM segment_revisions WHERE id = ?", (revision["id"],))
        db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now(), project_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return len(segments)


def _build_semantic_batches(rows: list[dict], max_size: int = 60, max_chars: int = 6000) -> list[list[dict]]:
    """Locked rows are hard boundaries; unlocked runs end near punctuation when possible."""
    batches = []
    run = []
    for row in rows:
        if row.get("locked"):
            if run:
                batches.extend(_split_run(run, max_size, max_chars))
                run = []
            continue
        run.append(row)
    if run:
        batches.extend(_split_run(run, max_size, max_chars))
    return batches


def _split_run(run: list[dict], max_size: int, max_chars: int = 6000) -> list[list[dict]]:
    result = []
    cursor = 0
    while cursor < len(run):
        remaining = len(run) - cursor
        char_limit = cursor
        chars = 0
        while char_limit < len(run) and char_limit-cursor < max_size:
            next_chars=len(run[char_limit].get("raw_text", ""))
            if char_limit > cursor and chars+next_chars > max_chars: break
            chars += next_chars; char_limit += 1
        if remaining <= max_size and char_limit == len(run):
            result.append(run[cursor:])
            break
        high = max(cursor+1,char_limit)
        low = min(high, cursor + max(12, (high-cursor)-12))
        candidates = [
            pos for pos in range(low, high + 1)
            if re.search(r"[.!?。！？][\"'”’)]?$", (run[pos - 1]["raw_text"] or "").strip())
        ]
        cut = candidates[-1] if candidates else high
        result.append(run[cursor:cut])
        cursor = cut
    return result


def _call_llm_group(
    batch: list[dict], ai: dict, target_length: int = 42,
    task_id: str | None = None,
) -> list[dict]:
    import httpx

    prompt_batch = [
        {"id": str(row["idx"]), "start": round(row["start"], 3), "end": round(row["end"], 3), "raw_text": row["raw_text"]}
        for row in batch
    ]
    length_instruction = f"""

用户提供的 {target_length} 个显示字符只是“遇到多个同样自然的句界时”的弱偏好，不是长度限制：
* 不要计算、凑齐或强制满足字符数，也不要为了该数字删改内容。
* 如果一个完整句超过 {target_length} 个字符，必须保留完整句并允许超长。
* 只有原文自身存在明确的完整句边界时才分组；目标字符数本身绝不是拆句理由。"""
    error = None
    for attempt in range(3):
        if task_id:
            task_manager.checkpoint(task_id)
        try:
            retry_instruction = (
                "\n\n上一次输出未通过 ID 映射或原文忠实度校验。请逐词对照输入，"
                "只做保守合并和标点修正，不要润色、改写或移动内容。"
                if attempt else ""
            )
            payload = {
                "model": ai["model"],
                "messages": [
                    {"role": "system", "content": CLEANER_SYSTEM_PROMPT + length_instruction + retry_instruction},
                    {"role": "user", "content": json.dumps(prompt_batch, ensure_ascii=False)},
                ],
                "temperature": 0,
                "max_tokens": 4096,
            }
            response = httpx.post(
                f"{ai['base_url'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {ai['api_key']}", "Content-Type": "application/json"},
                json=payload, timeout=120,
            )
            response.raise_for_status()
            if task_id:
                task_manager.checkpoint(task_id)
            content = response.json()["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            validated = _validate_grouped_results(batch, parsed)
            return validated
        except TaskCancelled:
            raise
        except Exception as exc:
            error = exc
            logger.warning("[Cleaner] LLM 句子重组失败（第 %s 次）: %s", attempt + 1, exc)
            if getattr(getattr(exc,"response",None),"status_code",None) in (401,403):
                break
            if attempt < 2:
                retry_after=getattr(getattr(exc,"response",None),"headers",{}).get("Retry-After")
                try: delay=min(30.0,max(0.0,float(retry_after))) if retry_after else 2 ** attempt
                except (TypeError,ValueError): delay=2 ** attempt
                time.sleep(delay)
    raise RuntimeError(f"AI 连续三次返回无效句子分组：{error}")


def _validate_grouped_results(batch: list[dict], parsed) -> list[dict]:
    if isinstance(parsed, dict):
        parsed = parsed.get("groups")
    if not isinstance(parsed, list):
        raise ValueError("AI 未返回 JSON 数组")
    expected = [str(row["idx"]) for row in batch]
    row_by_idx = {str(row["idx"]): row for row in batch}
    consumed = set()
    normalized = []
    for group in parsed:
        if not isinstance(group, dict) or not isinstance(group.get("ids"), list):
            continue
        ids = [str(value) for value in group["ids"]]
        text = group.get("clean_text")
        if not ids or not isinstance(text, str) or not text.strip():
            continue
        if any(value not in row_by_idx for value in ids):
            continue
        positions = [expected.index(value) for value in ids]
        if ids != expected[min(positions):max(positions) + 1]:
            raise ValueError("AI 字幕 ID 顺序变化或分组不连续")
        if consumed.intersection(ids):
            continue

        clean_text = text.strip()
        source_text = _join_text([
            row_by_idx[value].get("raw_text", "") or "" for value in ids
        ])
        if source_text and not _is_faithful_rewrite(source_text, clean_text):
            logger.warning(
                "[Cleaner] 字幕 %s 改写幅度过大，已回退为原文合并",
                ",".join(ids),
            )
            clean_text = source_text

        # Character count is deliberately not validated here. A complete sentence
        # is allowed to exceed the user's target length.
        normalized.append({"ids": ids, "clean_text": clean_text})
        consumed.update(ids)

    # Malformed groups and omitted IDs fall back to the original subtitle.  This
    # keeps one bad group from invalidating useful work from the entire batch.
    for value in expected:
        if value not in consumed:
            row = row_by_idx[value]
            normalized.append({"ids": [value], "clean_text": row.get("clean_text") or row.get("raw_text") or ""})
    normalized.sort(key=lambda group: expected.index(group["ids"][0]))
    return normalized


def _is_faithful_rewrite(source_text: str, clean_text: str) -> bool:
    """Reject creative rewrites while allowing punctuation/case/obvious typo fixes."""
    source = "".join(char.casefold() for char in source_text if char.isalnum())
    cleaned = "".join(char.casefold() for char in clean_text if char.isalnum())
    if not source:
        return bool(cleaned)
    if not cleaned:
        return False

    length_ratio = len(cleaned) / len(source)
    if length_ratio < 0.65 or length_ratio > 1.35:
        return False
    return SequenceMatcher(None, source, cleaned, autojunk=False).ratio() >= 0.72


def _compose_final_segments(rows: list[dict], groups: list[dict]) -> list[dict]:
    by_idx = {str(row["idx"]): row for row in rows}
    group_by_first = {group["ids"][0]: group for group in groups}
    consumed = set()
    output = []
    for row in rows:
        key = str(row["idx"])
        if key in consumed:
            continue
        if row.get("locked") or key not in group_by_first:
            output.append(dict(row))
            consumed.add(key)
            continue
        group = group_by_first[key]
        source_rows = [by_idx[value] for value in group["ids"]]
        consumed.update(group["ids"])
        first, last = source_rows[0], source_rows[-1]
        raw_text = _join_text([item["raw_text"] for item in source_rows])
        speakers = {item.get("speaker") or "" for item in source_rows}
        output.append({
            "id": first["id"], "project_id": first["project_id"], "idx": 0,
            "start": first["start"], "end": last["end"], "raw_text": raw_text,
            "clean_text": group["clean_text"],
            "translated_text": "",
            "speaker": speakers.pop() if len(speakers) == 1 else "", "locked": 0,
            "is_draft": 0, "source_stage": "cleaned",
            "_source_ids": [item["id"] for item in source_rows],
        })
    for index, segment in enumerate(output, 1):
        segment["idx"] = index
    return output


def _commit_restructured_segments(
    project_id: str, original: list[dict], fingerprint,
    final_segments: list[dict], task_id: str | None = None,
):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        current = [dict(row) for row in db.execute(
            "SELECT * FROM segments WHERE project_id = ? ORDER BY idx", (project_id,)
        ).fetchall()]
        original_by_id={row["id"]: row for row in original}; current_by_id={row["id"]: row for row in current}
        reconciled=[]; consumed=set(); conflicts=0
        for segment in final_segments:
            source_ids=segment.pop("_source_ids", [segment["id"]])
            unchanged=all(source_id in current_by_id and source_id in original_by_id and _fingerprint([current_by_id[source_id]]) == _fingerprint([original_by_id[source_id]]) for source_id in source_ids)
            if unchanged: reconciled.append(segment)
            else:
                conflicts += 1; reconciled.extend(dict(current_by_id[source_id]) for source_id in source_ids if source_id in current_by_id)
            consumed.update(source_ids)
        reconciled.extend(dict(row) for row in current if row["id"] not in consumed)
        reconciled=sorted(reconciled,key=lambda item:item["start"])
        for index, segment in enumerate(reconciled,1): segment["idx"]=index
        if task_id:
            task_manager.checkpoint(task_id)
        revision_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO segment_revisions (id, project_id, operation, segments_json, created_at) VALUES (?, ?, 'ai_clean', ?, ?)",
            (revision_id, project_id, json.dumps(original, ensure_ascii=False), _now()),
        )
        db.execute("DELETE FROM segments WHERE project_id = ?", (project_id,))
        for index, segment in enumerate(reconciled):
            if task_id and index % 20 == 0:
                task_manager.checkpoint(task_id)
            _insert_segment(db, segment)
        db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now(), project_id))
        old_revisions = db.execute(
            "SELECT id FROM segment_revisions WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET 10",
            (project_id,),
        ).fetchall()
        if old_revisions:
            db.executemany("DELETE FROM segment_revisions WHERE id = ?", [(row["id"],) for row in old_revisions])
        if task_id:
            task_manager.checkpoint(task_id)
        db.commit()
        return conflicts
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _insert_segment(db, segment: dict):
    db.execute(
        """INSERT INTO segments
           (id, project_id, idx, start, end, raw_text, clean_text, translated_text, speaker, locked, is_draft, source_stage)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            segment["id"], segment["project_id"], segment["idx"], segment["start"], segment["end"],
            segment.get("raw_text", ""), segment.get("clean_text", ""), segment.get("translated_text", ""),
            segment.get("speaker", ""), int(bool(segment.get("locked"))), int(bool(segment.get("is_draft"))),
            segment.get("source_stage", "final"),
        ),
    )


def _fingerprint(rows: list[dict]):
    return [
        (row["id"], row["idx"], row["start"], row["end"], row.get("raw_text", ""),
         row.get("clean_text", ""), row.get("translated_text", ""), int(bool(row.get("locked"))))
        for row in rows
    ]


def _join_text(parts: list[str]) -> str:
    result = ""
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        if not result:
            result = part
        elif re.search(r"[\u3400-\u9fff]$", result) and re.match(r"^[\u3400-\u9fff]", part):
            result += part
        else:
            result += " " + part
    return result


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [value for value in (text.find("["), text.find("{")) if value >= 0]
        if not start_candidates:
            return None
        start = min(start_candidates)
        for closing in ("]", "}"):
            end = text.rfind(closing)
            if end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue
        return None


def _validate_batch_results(batch: list, parsed: list, text_field: str) -> list:
    """Strict one-to-one result validation reused by subtitle translation."""
    expected = [str(item["id"]) for item in batch]
    normalized = {}
    duplicates = set()
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        value = item.get(text_field)
        if item_id in expected and isinstance(value, str) and value.strip():
            if item_id in normalized:
                duplicates.add(item_id)
            normalized[item_id] = {"id": item_id, text_field: value.strip()}
    if set(normalized) != set(expected) or duplicates:
        raise ValueError("AI 返回的字幕 ID 有遗漏、重复或顺序变化")
    return [normalized[item_id] for item_id in expected]


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")
