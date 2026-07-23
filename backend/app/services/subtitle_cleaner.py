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
from .editor import SEGMENT_COLUMNS, history_step

logger = logging.getLogger(__name__)


class BatchRetryError(RuntimeError):
    def __init__(self, message: str, error_code: str = "CLEAN_BATCH_RETRY_CONFLICT"):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = False
        self.available_actions = []
        self.suggestion = "字幕在失败后已被修改，请重新执行整份 AI 整理"


class AIOutputLengthError(ValueError):
    """The provider stopped because its output token budget was exhausted."""


CLEANER_SYSTEM_PROMPT = """你是谨慎、忠实的视频字幕整理助手。输入是按时间顺序排列的 ASR 碎片。

你的唯一任务是识别句子边界，把属于同一句话的相邻碎片合并。忠实保留原文及原意永远是第一优先级；这不是润色、改写或内容编辑任务。

规则：
* 每个输入 id 必须且只能出现一次，顺序必须与输入一致。
* 只能合并相邻 id，不能交换、遗漏或重复。
* clean_text 应保留该组原文的措辞、信息、语气、重复和口语特征；不得同义改写、调整叙述顺序、概括、扩写、翻译或为了“更自然”而润色。
* 只可补充必要标点、修正大小写，以及修正上下文中毫无歧义的单个 ASR 错词；不确定时必须原样保留。
* 根据语义和标点识别真正的完整句边界。完整句优先，不要把一个完整句机械截断。
* clean_text 必须完整覆盖该组所有碎片表达的内容，不能把内容移动到相邻组。
* 输出必须是一个 JSON 对象，且只能包含 groups 字段；groups 必须是 JSON 数组。
* 响应的第一个字符必须是 {，最后一个字符必须是 }。严禁 Markdown 代码块、解释、前后缀、注释或尾随逗号。
* 每个 groups 元素只能使用 {"ids":[...],"clean_text":"..."} 结构；字符串中的换行和引号必须正确进行 JSON 转义。

输入：[{"id":"1","start":0.0,"end":1.2,"raw_text":"I want to"},{"id":"2","start":1.2,"end":2.5,"raw_text":"show you this"}]
唯一合法输出：{"groups":[{"ids":["1","2"],"clean_text":"I want to show you this."}]}"""


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
    failed_batch_indexes: list[int] = []
    request_attempts = 0
    adaptive_splits = 0
    length_recoveries = 0
    cache_hits = 0
    prompt_tokens = 0
    completion_tokens = 0
    failed_leaf_batches = 0
    task_manager.add_log(
        task_id, "info", "AI 句子重组",
        f"开始分析 {len(rows)} 条字幕，将在 {total_batches} 个语义批次中识别完整句子",
        detail=(
            f"AI: {ai['provider']} · {ai['model']} · "
            f"输入字符 {_batch_character_count(rows)} · 单批最多 32 条/3500 字符"
        )
    )

    def process(batch_index: int, batch: list[dict]):
        fingerprint = json.dumps({"segments":_fingerprint(batch),"provider":ai["provider"],"model":ai["model"],"target_length":target_length}, ensure_ascii=False, sort_keys=True)
        db=get_db()
        cached=db.execute("SELECT result_json FROM ai_batch_results WHERE project_id=? AND operation='clean' AND input_fingerprint=? AND status='success' ORDER BY updated_at DESC LIMIT 1",(project_id,fingerprint)).fetchone()
        if cached:
            try:
                groups=json.loads(cached["result_json"]);db.close();return batch_index,groups,None,{"cache_hits":1}
            except (TypeError,ValueError,json.JSONDecodeError):
                pass
        db.execute("""INSERT OR REPLACE INTO ai_batch_results
            (task_id,project_id,operation,batch_index,input_fingerprint,status,result_json,attempts,error,updated_at)
            VALUES (?,?,?,?,?,'running','[]',0,'',?)""", (task_id,project_id,"clean",batch_index,fingerprint,_now())); db.commit(); db.close()
        diagnostics: dict[str, int] = {}
        try:
            groups = _call_llm_group(
                batch, ai, target_length, task_id=task_id, diagnostics=diagnostics,
            )
            attempts = int(diagnostics.get("request_attempts", 0))
            db=get_db(); db.execute("UPDATE ai_batch_results SET status='success',result_json=?,attempts=?,error='',updated_at=? WHERE task_id=? AND batch_index=?", (json.dumps(groups,ensure_ascii=False),attempts,_now(),task_id,batch_index)); db.commit(); db.close()
            return batch_index, groups, None, diagnostics
        except Exception as exc:
            attempts = int(diagnostics.get("request_attempts", 0))
            db=get_db(); db.execute("UPDATE ai_batch_results SET status='failed',attempts=?,error=?,updated_at=? WHERE task_id=? AND batch_index=?", (attempts,str(exc)[:500],_now(),task_id,batch_index)); db.commit(); db.close()
            groups=[{"ids":[str(row["idx"])],"clean_text":row["clean_text"] or row["raw_text"]} for row in batch]
            return batch_index, groups, exc, diagnostics

    completed={}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="subtitle-ai") as executor:
        futures=[executor.submit(process,index,batch) for index,batch in enumerate(batches,1)]
        for done_count, future in enumerate(as_completed(futures),1):
            task_manager.checkpoint(task_id); index,groups,error,diagnostics=future.result(); completed[index]=groups
            request_attempts += int(diagnostics.get("request_attempts", 0))
            adaptive_splits += int(diagnostics.get("adaptive_splits", 0))
            length_recoveries += int(diagnostics.get("length_recoveries", 0))
            cache_hits += int(diagnostics.get("cache_hits", 0))
            prompt_tokens += int(diagnostics.get("prompt_tokens", 0))
            completion_tokens += int(diagnostics.get("completion_tokens", 0))
            failed_leaf_batches += int(diagnostics.get("failed_leaf_batches", 0))
            if error:
                failed_batches += 1; failed_batch_indexes.append(index); task_manager.add_log(task_id,"warning","AI 句子重组",f"第 {index}/{total_batches} 批未能重组",detail=str(error),suggestion="可在“整理”设置中单独重试该批次")
            task_manager.update_task(task_id,step="restructuring",progress=5+done_count/max(total_batches,1)*88,message=f"已完成 {done_count}/{total_batches} 个批次",details={"total_segments":len(rows),"total_batches":total_batches,"completed_batches":done_count,"failed_batches":failed_batches,"failed_batch_indexes":sorted(failed_batch_indexes),"ai_provider":ai["provider"],"ai_model":ai["model"],"request_attempts":request_attempts,"adaptive_splits":adaptive_splits,"length_recoveries":length_recoveries,"cache_hits":cache_hits,"prompt_tokens":prompt_tokens,"completion_tokens":completion_tokens,"failed_leaf_batches":failed_leaf_batches})
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
            "failed_batch_indexes": sorted(failed_batch_indexes),
            "request_attempts": request_attempts,
            "adaptive_splits": adaptive_splits,
            "length_recoveries": length_recoveries,
            "cache_hits": cache_hits,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "failed_leaf_batches": failed_leaf_batches,
        },
    )
    task_manager.add_log(task_id, "info", "AI 句子重组", message, suggestion="如效果不合适，可使用“撤销整理”恢复")
    return final_segments


def retry_clean_batch(task_id: str, original_task_id: str, batch_index: int):
    """Retry one stored failed batch and reconcile only its original segment range."""
    db = get_db()
    try:
        failed = db.execute(
            """SELECT * FROM ai_batch_results
               WHERE task_id=? AND operation='clean' AND batch_index=? AND status='failed'""",
            (original_task_id, batch_index),
        ).fetchone()
        if not failed:
            raise BatchRetryError("这个失败批次不存在，或已经重试成功", "CLEAN_BATCH_NOT_FOUND")
        failed = dict(failed)
        try:
            stored = json.loads(failed["input_fingerprint"] or "{}")
            stored_segments = stored["segments"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BatchRetryError("失败批次缺少可恢复的输入快照", "CLEAN_BATCH_SNAPSHOT_INVALID") from exc
        current = [dict(row) for row in db.execute(
            "SELECT * FROM segments WHERE project_id=? ORDER BY idx", (failed["project_id"],)
        ).fetchall()]
    finally:
        db.close()

    if not stored_segments:
        raise BatchRetryError("失败批次的输入快照为空", "CLEAN_BATCH_SNAPSHOT_INVALID")
    positions = {row["id"]: index for index, row in enumerate(current)}
    stored_ids = [str(item[0]) for item in stored_segments]
    if any(segment_id not in positions for segment_id in stored_ids):
        raise BatchRetryError("失败批次中的字幕已被删除或合并，不能安全覆盖")
    selected_positions = [positions[segment_id] for segment_id in stored_ids]
    first_position = min(selected_positions)
    if selected_positions != list(range(first_position, first_position + len(stored_ids))):
        raise BatchRetryError("失败批次中的字幕顺序已改变，不能安全覆盖")

    batch = [current[position] for position in selected_positions]
    for snapshot, row in zip(stored_segments, batch):
        current_signature = (
            row["id"], float(row["start"]), float(row["end"]), row.get("raw_text", ""),
            row.get("clean_text", ""), row.get("translated_text", ""), int(bool(row.get("locked"))),
        )
        stored_signature = (
            str(snapshot[0]), float(snapshot[2]), float(snapshot[3]), snapshot[4] or "",
            snapshot[5] or "", snapshot[6] or "", int(bool(snapshot[7])),
        )
        if current_signature != stored_signature:
            raise BatchRetryError("失败批次中的字幕内容或时间已经修改，不能安全覆盖")

    provider_id = stored.get("provider")
    model = stored.get("model")
    target_length = max(16, min(100, int(stored.get("target_length") or 42)))
    try:
        ai = assigned_provider("clean", provider_id, model)
    except ValueError:
        ai = get_ai_settings(include_secret=True)

    retry_fingerprint = json.dumps(
        {"segments": _fingerprint(batch), "provider": ai["provider"], "model": ai["model"],
         "target_length": target_length, "retry_of": original_task_id},
        ensure_ascii=False, sort_keys=True,
    )
    task_manager.update_task(
        task_id, step="retrying_failed_batch", progress=10,
        message=f"正在单独重试第 {batch_index} 批（{len(batch)} 条字幕）",
        details={"retry_of": original_task_id, "batch_index": batch_index,
                 "batch_segments": len(batch), "target_length": target_length},
    )
    task_manager.add_log(
        task_id, "info", "AI 批次重试", f"只重试第 {batch_index} 批",
        detail=f"范围 {batch[0]['start']:.2f}s–{batch[-1]['end']:.2f}s，共 {len(batch)} 条",
    )
    db = get_db()
    db.execute(
        """INSERT OR REPLACE INTO ai_batch_results
           (task_id,project_id,operation,batch_index,input_fingerprint,status,result_json,attempts,error,updated_at)
           VALUES (?,?,?,?,?,'running','[]',0,'',?)""",
        (task_id, failed["project_id"], "clean", batch_index, retry_fingerprint, _now()),
    )
    db.commit()
    db.close()

    try:
        diagnostics: dict[str, int] = {}
        groups = _call_llm_group(
            batch, ai, target_length, task_id=task_id, diagnostics=diagnostics,
        )
        task_manager.update_task(task_id, progress=75, message=f"第 {batch_index} 批返回有效 JSON，正在应用")
        final_segments = _compose_final_segments(current, groups)
        conflicts = _commit_restructured_segments(
            failed["project_id"], current, _fingerprint(current), final_segments, task_id=task_id
        )
        if conflicts:
            raise BatchRetryError("应用重试结果时检测到字幕被同时修改，结果未覆盖原字幕")
    except Exception as exc:
        db = get_db()
        attempts = int(locals().get("diagnostics", {}).get("request_attempts", 0))
        db.execute(
            "UPDATE ai_batch_results SET status='failed',attempts=?,error=?,updated_at=? WHERE task_id=? AND batch_index=?",
            (attempts, str(exc)[:500], _now(), task_id, batch_index),
        )
        db.commit()
        db.close()
        task_manager.update_task(
            task_id,
            details={"failed_batches": 1, "failed_batch_indexes": [batch_index],
                     "batch_index": batch_index, "single_batch_retry": True},
        )
        raise

    db = get_db()
    try:
        result_json = json.dumps(groups, ensure_ascii=False)
        attempts = int(diagnostics.get("request_attempts", 0))
        db.execute(
            "UPDATE ai_batch_results SET status='success',result_json=?,attempts=?,error='',updated_at=? WHERE task_id=? AND batch_index=?",
            (result_json, attempts, _now(), task_id, batch_index),
        )
        db.execute(
            "UPDATE ai_batch_results SET status='success',result_json=?,error='',updated_at=? WHERE task_id=? AND batch_index=?",
            (result_json, _now(), original_task_id, batch_index),
        )
        remaining_rows = db.execute(
            "SELECT batch_index FROM ai_batch_results WHERE task_id=? AND status='failed' ORDER BY batch_index",
            (original_task_id,),
        ).fetchall()
        db.commit()
    finally:
        db.close()

    remaining = [int(row["batch_index"]) for row in remaining_rows]
    original = task_manager.get_task(original_task_id)
    if original:
        task_manager.update_task(
            original_task_id,
            status="partial" if remaining else "success",
            message=(f"仍有 {len(remaining)} 个整理批次失败" if remaining else "所有整理批次均已成功"),
            details={"failed_batches": len(remaining), "failed_batch_indexes": remaining},
        )
    task_manager.update_task(
        task_id, step="batch_retry_done", progress=100,
        message=f"第 {batch_index} 批已重新整理成功",
        details={
            "failed_batches": len(remaining), "remaining_failed_batch_indexes": remaining,
            "request_attempts": int(diagnostics.get("request_attempts", 0)),
            "adaptive_splits": int(diagnostics.get("adaptive_splits", 0)),
            "length_recoveries": int(diagnostics.get("length_recoveries", 0)),
        },
    )
    return final_segments


def undo_last_clean(project_id: str) -> int:
    db = get_db()
    latest = db.execute(
        "SELECT operation FROM edit_operations WHERE project_id=? AND undone=0 ORDER BY result_revision DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    project = db.execute("SELECT edit_revision FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not latest or latest["operation"] != "ai_clean" or not project:
        raise ValueError("最近一次编辑不是可撤销的 AI 整理")
    return history_step(project_id, int(project["edit_revision"] or 0), "undo")["affected_count"]


def _build_semantic_batches(rows: list[dict], max_size: int = 32, max_chars: int = 3500) -> list[list[dict]]:
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
    task_id: str | None = None, diagnostics: dict[str, int] | None = None,
    _depth: int = 0,
) -> list[dict]:
    import httpx

    if not batch:
        return []
    if diagnostics is None:
        diagnostics = {}
    diagnostics["max_split_depth"] = max(
        int(diagnostics.get("max_split_depth", 0)), _depth,
    )
    prompt_batch = [
        {"id": str(row["idx"]), "start": round(row["start"], 3), "end": round(row["end"], 3), "raw_text": row["raw_text"]}
        for row in batch
    ]
    length_instruction = f"""

用户提供的 {target_length} 个显示字符只是“遇到多个同样自然的句界时”的弱偏好，不是长度限制：
* 不要计算、凑齐或强制满足字符数，也不要为了该数字删改内容。
* 如果一个完整句超过 {target_length} 个字符，必须保留完整句并允许超长。
* 只有原文自身存在明确的完整句边界时才分组；目标字符数本身绝不是拆句理由。"""
    error: Exception | None = None
    for attempt in range(2):
        if task_id:
            task_manager.checkpoint(task_id)
        try:
            retry_instruction = (
                "\n\n上一次响应不是可接受的严格 JSON。现在必须只返回一个 JSON 对象："
                "{\"groups\":[{\"ids\":[\"...\"],\"clean_text\":\"...\"}]}。"
                "不要使用 Markdown，不要解释；逐词对照输入，只做保守合并和标点修正。"
                if attempt else ""
            )
            payload = {
                "model": ai["model"],
                "messages": [
                    {"role": "system", "content": CLEANER_SYSTEM_PROMPT + length_instruction + retry_instruction},
                    {"role": "user", "content": json.dumps({
                        "required_output_schema": {"groups": [{"ids": ["string"], "clean_text": "string"}]},
                        "segments": prompt_batch,
                    }, ensure_ascii=False)},
                ],
                "temperature": 0,
                "max_tokens": _output_token_budget(prompt_batch),
            }
            if ai.get("provider") in {"deepseek", "openai"}:
                payload["response_format"] = {"type": "json_object"}
            diagnostics["request_attempts"] = int(diagnostics.get("request_attempts", 0)) + 1
            response = httpx.post(
                f"{ai['base_url'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {ai['api_key']}", "Content-Type": "application/json"},
                json=payload, timeout=120,
            )
            response.raise_for_status()
            if task_id:
                task_manager.checkpoint(task_id)
            response_data = response.json()
            choice = response_data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise AIOutputLengthError("AI 输出达到长度上限，JSON 被截断")
            usage = response_data.get("usage") or {}
            diagnostics["prompt_tokens"] = int(diagnostics.get("prompt_tokens", 0)) + int(usage.get("prompt_tokens") or 0)
            diagnostics["completion_tokens"] = int(diagnostics.get("completion_tokens", 0)) + int(usage.get("completion_tokens") or 0)
            content = choice["message"]["content"]
            parsed = _extract_json(content)
            validated = _validate_grouped_results(batch, parsed)
            return validated
        except TaskCancelled:
            raise
        except AIOutputLengthError as exc:
            error = exc
            diagnostics["length_recoveries"] = int(diagnostics.get("length_recoveries", 0)) + 1
            logger.warning(
                "[Cleaner] AI 输出达到上限，准备缩小批次（%s 条，深度 %s）",
                len(batch), _depth,
            )
            break
        except Exception as exc:
            error = exc
            logger.warning("[Cleaner] LLM 句子重组失败（第 %s 次）: %s", attempt + 1, exc)
            if getattr(getattr(exc,"response",None),"status_code",None) in (401,403):
                raise RuntimeError(f"AI 认证失败：{exc}") from exc
            if attempt < 1:
                retry_after=getattr(getattr(exc,"response",None),"headers",{}).get("Retry-After")
                try: delay=min(30.0,max(0.0,float(retry_after))) if retry_after else 2 ** attempt
                except (TypeError,ValueError): delay=2 ** attempt
                time.sleep(delay)

    if len(batch) == 1:
        diagnostics["failed_leaf_batches"] = int(diagnostics.get("failed_leaf_batches", 0)) + 1
        raise RuntimeError(f"AI 在最小批次仍未返回符合约束的 JSON 分组：{error}") from error

    left, right = _split_batch_for_recovery(batch)
    diagnostics["adaptive_splits"] = int(diagnostics.get("adaptive_splits", 0)) + 1
    if task_id:
        reason = "输出达到长度上限" if isinstance(error, AIOutputLengthError) else "连续返回无效 JSON"
        task_manager.add_log(
            task_id, "warning", "AI 自适应拆批",
            f"{reason}，已将 {len(batch)} 条缩小为 {len(left)} + {len(right)} 条",
            detail=(
                f"输入字符 {_batch_character_count(batch)} · "
                f"递归深度 {_depth + 1} · 已发出请求 {diagnostics.get('request_attempts', 0)} 次"
            ),
            suggestion="无需手动重试，系统正在自动缩小请求范围",
        )
    return [
        *_call_llm_group(
            left, ai, target_length, task_id=task_id,
            diagnostics=diagnostics, _depth=_depth + 1,
        ),
        *_call_llm_group(
            right, ai, target_length, task_id=task_id,
            diagnostics=diagnostics, _depth=_depth + 1,
        ),
    ]


def _output_token_budget(prompt_batch: list[dict]) -> int:
    """Conservative output allowance, capped at the provider-safe 8192 ceiling."""
    serialized = json.dumps(prompt_batch, ensure_ascii=False, separators=(",", ":"))
    cjk_count = sum(1 for char in serialized if "\u3400" <= char <= "\u9fff")
    non_cjk_count = len(serialized) - cjk_count
    estimated_tokens = cjk_count + non_cjk_count // 3 + len(prompt_batch) * 24
    estimated = int(estimated_tokens * 1.35)
    return min(8192, max(2048, estimated))


def _batch_character_count(batch: list[dict]) -> int:
    return sum(len(str(row.get("raw_text") or "")) for row in batch)


def _split_batch_for_recovery(batch: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split near the midpoint, preferring a sentence ending or a real pause."""
    midpoint = len(batch) / 2
    best_position = 1
    best_score = float("-inf")
    for position in range(1, len(batch)):
        previous = batch[position - 1]
        following = batch[position]
        distance_penalty = abs(position - midpoint) * 3
        sentence_bonus = 18 if re.search(
            r"[.!?。！？][\"'”’)]?$", str(previous.get("raw_text") or "").strip()
        ) else 0
        try:
            gap = float(following.get("start") or 0) - float(previous.get("end") or 0)
        except (TypeError, ValueError):
            gap = 0
        pause_bonus = min(18, max(0, gap) * 12)
        score = sentence_bonus + pause_bonus - distance_penalty
        if score > best_score:
            best_position = position
            best_score = score
    return batch[:best_position], batch[best_position:]


def _validate_grouped_results(batch: list[dict], parsed) -> list[dict]:
    if isinstance(parsed, dict):
        parsed = parsed.get("groups")
    if not isinstance(parsed, list):
        raise ValueError("AI 未返回包含 groups 数组的 JSON 对象")
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
        before_snapshot = [{column: row.get(column) for column in SEGMENT_COLUMNS} for row in current]
        revision = int(db.execute("SELECT edit_revision FROM projects WHERE id=?", (project_id,)).fetchone()[0] or 0)
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
        db.execute("DELETE FROM segments WHERE project_id = ?", (project_id,))
        for index, segment in enumerate(reconciled):
            if task_id and index % 20 == 0:
                task_manager.checkpoint(task_id)
            _insert_segment(db, segment)
        after_rows = [dict(row) for row in db.execute("SELECT * FROM segments WHERE project_id=? ORDER BY idx", (project_id,))]
        after_snapshot = [{column: row.get(column) for column in SEGMENT_COLUMNS} for row in after_rows]
        operation_id = str(uuid.uuid4()); next_revision = revision + 1
        db.execute("DELETE FROM edit_operations WHERE project_id=? AND undone=1", (project_id,))
        db.execute(
            """INSERT INTO edit_operations(id,project_id,operation,before_json,after_json,base_revision,result_revision,undone,created_at)
               VALUES (?,?,?,?,?,?,?,0,?)""",
            (operation_id, project_id, "ai_clean", json.dumps(before_snapshot, ensure_ascii=False),
             json.dumps(after_snapshot, ensure_ascii=False), revision, next_revision, _now()),
        )
        db.execute("UPDATE projects SET edit_revision=?,updated_at=? WHERE id=?", (next_revision, _now(), project_id))
        db.execute("""DELETE FROM edit_operations WHERE id IN (
            SELECT id FROM edit_operations WHERE project_id=? AND undone=0
            ORDER BY result_revision DESC LIMIT -1 OFFSET 500)""", (project_id,))
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
           (id, project_id, idx, start, end, raw_text, clean_text, translated_text, speaker, speaker_id, locked, is_draft, source_stage, transcription_run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            segment["id"], segment["project_id"], segment["idx"], segment["start"], segment["end"],
            segment.get("raw_text", ""), segment.get("clean_text", ""), segment.get("translated_text", ""),
            segment.get("speaker", ""), segment.get("speaker_id"), int(bool(segment.get("locked"))), int(bool(segment.get("is_draft"))),
            segment.get("source_stage", "final"), segment.get("transcription_run_id"),
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
