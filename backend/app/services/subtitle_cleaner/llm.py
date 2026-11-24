"""LLM grouping calls, adaptive batch splitting, and faithful-rewrite validation."""

from __future__ import annotations

import json
import logging
import time
from difflib import SequenceMatcher

from ...utils.task_manager import TaskCancelled, task_manager
from ..ai_providers import (
    AIProviderRequestError,
    prepare_chat_payload,
    raise_for_provider_status,
)
from .batching import (
    _batch_character_count,
    _extract_json,
    _join_text,
    _output_token_budget,
    _split_batch_for_recovery,
    facade_attr,
)

logger = logging.getLogger(__name__)


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


def _validate_grouped_results(batch: list[dict], parsed) -> list[dict]:
    if isinstance(parsed, dict):
        parsed = parsed.get("groups")
    if not isinstance(parsed, list):
        raise ValueError("AI 未返回包含 groups 数组的 JSON 对象")
    expected = [str(row["idx"]) for row in batch]
    row_by_idx = {str(row["idx"]): row for row in batch}
    position_by_idx = {value: position for position, value in enumerate(expected)}
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
        positions = [position_by_idx[value] for value in ids]
        if ids != expected[min(positions) : max(positions) + 1]:
            raise ValueError("AI 字幕 ID 顺序变化或分组不连续")
        if consumed.intersection(ids):
            continue

        clean_text = text.strip()
        source_text = _join_text([row_by_idx[value].get("raw_text", "") or "" for value in ids])
        if source_text and not _is_faithful_rewrite(source_text, clean_text):
            logger.warning(
                "[Cleaner] 字幕 %s 改写幅度过大，已回退为原文合并",
                ",".join(ids),
            )
            clean_text = source_text

        normalized.append({"ids": ids, "clean_text": clean_text})
        consumed.update(ids)

    for value in expected:
        if value not in consumed:
            row = row_by_idx[value]
            normalized.append(
                {"ids": [value], "clean_text": row.get("clean_text") or row.get("raw_text") or ""}
            )
    normalized.sort(key=lambda group: position_by_idx[group["ids"][0]])
    return normalized


def _call_llm_group(
    batch: list[dict],
    ai: dict,
    target_length: int = 42,
    task_id: str | None = None,
    diagnostics: dict[str, int] | None = None,
    _depth: int = 0,
) -> list[dict]:
    import httpx

    tm = facade_attr("task_manager", task_manager)
    if not batch:
        return []
    if diagnostics is None:
        diagnostics = {}
    diagnostics["max_split_depth"] = max(
        int(diagnostics.get("max_split_depth", 0)),
        _depth,
    )
    prompt_batch = [
        {
            "id": str(row["idx"]),
            "start": round(row["start"], 3),
            "end": round(row["end"], 3),
            "raw_text": row["raw_text"],
        }
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
            tm.checkpoint(task_id)
        try:
            retry_instruction = (
                "\n\n上一次响应不是可接受的严格 JSON。现在必须只返回一个 JSON 对象："
                '{"groups":[{"ids":["..."],"clean_text":"..."}]}。'
                "不要使用 Markdown，不要解释；逐词对照输入，只做保守合并和标点修正。"
                if attempt
                else ""
            )
            payload = {
                "model": ai["model"],
                "messages": [
                    {
                        "role": "system",
                        "content": CLEANER_SYSTEM_PROMPT + length_instruction + retry_instruction,
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "required_output_schema": {
                                    "groups": [{"ids": ["string"], "clean_text": "string"}]
                                },
                                "segments": prompt_batch,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "temperature": 0,
                "max_tokens": _output_token_budget(prompt_batch),
            }
            if ai.get("provider") in {"deepseek", "openai"}:
                payload["response_format"] = {"type": "json_object"}
            payload = prepare_chat_payload(ai, payload)
            diagnostics["request_attempts"] = int(diagnostics.get("request_attempts", 0)) + 1
            response = httpx.post(
                f"{ai['base_url'].rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {ai['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            raise_for_provider_status(response)
            if task_id:
                tm.checkpoint(task_id)
            response_data = response.json()
            choice = response_data["choices"][0]
            usage = response_data.get("usage") or {}
            diagnostics["prompt_tokens"] = int(diagnostics.get("prompt_tokens", 0)) + int(
                usage.get("prompt_tokens") or 0
            )
            diagnostics["completion_tokens"] = int(
                diagnostics.get("completion_tokens", 0)
            ) + int(usage.get("completion_tokens") or 0)
            completion_details = usage.get("completion_tokens_details") or {}
            diagnostics["reasoning_tokens"] = int(diagnostics.get("reasoning_tokens", 0)) + int(
                completion_details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0
            )
            if choice.get("finish_reason") == "length":
                raise AIOutputLengthError("AI 输出达到长度上限，JSON 被截断")
            content = choice["message"]["content"]
            parsed = _extract_json(content)
            return _validate_grouped_results(batch, parsed)
        except TaskCancelled:
            raise
        except AIOutputLengthError as exc:
            error = exc
            diagnostics["length_recoveries"] = int(diagnostics.get("length_recoveries", 0)) + 1
            logger.warning(
                "[Cleaner] AI 输出达到上限，准备缩小批次（%s 条，深度 %s）",
                len(batch),
                _depth,
            )
            break
        except AIProviderRequestError as exc:
            error = exc
            logger.warning("[Cleaner] AI 服务请求失败（第 %s 次）: %s", attempt + 1, exc)
            if not exc.retryable or attempt >= 1:
                raise
            retry_after = getattr(getattr(exc, "__cause__", None), "response", None)
            retry_after = getattr(retry_after, "headers", {}).get("Retry-After")
            try:
                delay = min(30.0, max(0.0, float(retry_after))) if retry_after else 2**attempt
            except (TypeError, ValueError):
                delay = 2**attempt
            time.sleep(delay)
        except httpx.RequestError as exc:
            error = exc
            logger.warning("[Cleaner] AI 网络请求失败（第 %s 次）: %s", attempt + 1, exc)
            if attempt >= 1:
                raise RuntimeError(f"AI 网络请求失败：{exc}") from exc
            time.sleep(2**attempt)
        except Exception as exc:
            error = exc
            logger.warning("[Cleaner] LLM 句子重组失败（第 %s 次）: %s", attempt + 1, exc)
            if attempt < 1:
                time.sleep(2**attempt)

    if len(batch) == 1:
        diagnostics["failed_leaf_batches"] = int(diagnostics.get("failed_leaf_batches", 0)) + 1
        raise RuntimeError(f"AI 在最小批次仍未返回符合约束的 JSON 分组：{error}") from error

    left, right = _split_batch_for_recovery(batch)
    diagnostics["adaptive_splits"] = int(diagnostics.get("adaptive_splits", 0)) + 1
    if task_id:
        reason = (
            "输出达到长度上限" if isinstance(error, AIOutputLengthError) else "连续返回无效 JSON"
        )
        tm.add_log(
            task_id,
            "warning",
            "AI 自适应拆批",
            f"{reason}，已将 {len(batch)} 条缩小为 {len(left)} + {len(right)} 条",
            detail=(
                f"输入字符 {_batch_character_count(batch)} · "
                f"递归深度 {_depth + 1} · 已发出请求 {diagnostics.get('request_attempts', 0)} 次"
            ),
            suggestion="无需手动重试，系统正在自动缩小请求范围",
        )
    call_fn = facade_attr("_call_llm_group", _call_llm_group)
    return [
        *call_fn(
            left,
            ai,
            target_length,
            task_id=task_id,
            diagnostics=diagnostics,
            _depth=_depth + 1,
        ),
        *call_fn(
            right,
            ai,
            target_length,
            task_id=task_id,
            diagnostics=diagnostics,
            _depth=_depth + 1,
        ),
    ]
