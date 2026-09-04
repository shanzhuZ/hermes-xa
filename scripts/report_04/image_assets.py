"""04 写报：步骤7发文后的图片资产入库 + 分析回填（复用 image_pipeline）。

4.1.2 第一次核验：stream_steps.start_step5_image_pipeline（全量，Hook 内后台线程）。
步骤8 第二次补图：start_step8_post_media_pipeline（仅 post_media，Hook 内后台线程 + 去重锁）。
post_image_job CLI 仅用于手工补跑，与 Hook 共用同一套 in-process 管线逻辑。
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

STEP8_STEP_KEY = "step8_img_analysis"

# Hook 兜底等待上限（4.1.2 同步线程池）
_HOOK_TIMEOUT_SEC = 90
# 步骤8 post_media 管线默认超时（后台线程内生效，到点即停）
_POST_IMAGE_TIMEOUT_SEC = 600
# 步骤8 发文配图最多下载/分析张数（发现可更多，仅处理前 N 张）
_POST_MEDIA_MAX_IMAGES = 20
# 每分析 N 张回写一次 step8 进度
_STEP8_PROGRESS_EVERY = 5

_POST_IMAGE_JOB_LOCK = threading.Lock()
_POST_IMAGE_JOBS: Dict[str, bool] = {}


def count_stored_images(task_id: str) -> int:
    from collect_01.image_assets import count_stored_images as _count

    return _count(task_id)


def has_stored_images(task_id: str) -> bool:
    return count_stored_images(task_id) > 0


def count_image_analysis_stats(
    task_id: str,
    *,
    source_type: Optional[str] = None,
) -> Dict[str, int]:
    """统计图片总量 / 已分析 / 待办。"""
    from collect_01 import db

    sql = """
        SELECT COUNT(1) AS n,
               SUM(CASE WHEN analyze_status='completed' THEN 1 ELSE 0 END) AS analyzed,
               SUM(CASE WHEN storage_status IN ('pending','downloading') THEN 1 ELSE 0 END) AS pending_store,
               SUM(CASE WHEN storage_status='stored'
                         AND analyze_status IN ('pending','running') THEN 1 ELSE 0 END) AS pending_analyze
        FROM collect_images
        WHERE task_id=%s
    """
    params: tuple = (task_id,)
    if source_type:
        sql += " AND source_type=%s"
        params = (task_id, source_type)
    row = db.fetch_one(sql, params)
    n = int((row or {}).get("n") or 0)
    analyzed = int((row or {}).get("analyzed") or 0)
    return {
        "total": n,
        "analyzed": analyzed,
        "pending": max(0, n - analyzed),
        "pending_store": int((row or {}).get("pending_store") or 0),
        "pending_analyze": int((row or {}).get("pending_analyze") or 0),
    }


def post_media_pipeline_should_skip(task_id: str) -> bool:
    """发文配图达分析上限或已无待办则无需再跑（尚无索引时不跳过，需 discover）。"""
    stats = count_image_analysis_stats(task_id, source_type="post_media")
    if stats["total"] <= 0:
        return False
    if stats["analyzed"] >= _POST_MEDIA_MAX_IMAGES:
        return True
    return stats["pending_store"] == 0 and stats["pending_analyze"] == 0


def touch_step8_image_progress(
    store: Any,
    task_id: str,
    *,
    prefix: str = "图片流分析中",
) -> None:
    """回写 step8 进度（发文配图按上限展示）。"""
    from report_04.gates import get_step_status

    st = get_step_status(task_id, STEP8_STEP_KEY)
    if st in {"completed", "skipped", "failed"}:
        return
    stats = count_image_analysis_stats(task_id)
    pm = count_image_analysis_stats(task_id, source_type="post_media")
    n = stats["total"]
    analyzed = stats["analyzed"]
    pending = stats["pending"]
    if n <= 0:
        return
    cap = _POST_MEDIA_MAX_IMAGES
    pm_done = pm["analyzed"]
    target = min(cap, pm["total"]) if pm["total"] > 0 else 0
    if pm["total"] > cap:
        msg = f"{prefix}（发文配图已分析 {pm_done}/{cap}，发现 {pm['total']}，上限 {cap}）"
    elif target > 0:
        msg = f"{prefix}（发文配图已分析 {pm_done}/{target}）"
    else:
        msg = f"{prefix}（已分析 {analyzed}/{n}）"
    payload = {
        "image_count": n,
        "analyzed": analyzed,
        "pending": pending,
        "post_media_analyzed": pm_done,
        "post_media_total": pm["total"],
        "post_media_cap": cap,
    }
    store.set_step_status(
        task_id,
        STEP8_STEP_KEY,
        "running",
        message=msg,
        payload=payload,
        touch_updated_at=False,
    )


def _reset_post_media_running(task_id: str) -> None:
    try:
        from collect_01 import db

        db.execute(
            """
            UPDATE collect_images
            SET analyze_status='pending', updated_at=CURRENT_TIMESTAMP(3)
            WHERE task_id=%s AND source_type='post_media' AND analyze_status='running'
            """,
            (task_id,),
        )
    except Exception as exc:
        logger.warning("重置 post_media running 失败 task=%s: %s", task_id, exc)


def _run_post_media_pipeline(
    task_id: str,
    *,
    progress_callback: Optional[Callable[[], None]] = None,
) -> Optional[Dict[str, Any]]:
    from image_pipeline.run import process_task

    return process_task(
        task_id=task_id,
        force_analyze=False,
        source_type="post_media",
        progress_callback=progress_callback,
        progress_every=_STEP8_PROGRESS_EVERY,
        max_images=_POST_MEDIA_MAX_IMAGES,
    )


def run_post_media_pipeline_blocking(
    task_id: str,
    *,
    timeout_sec: int = _POST_IMAGE_TIMEOUT_SEC,
    skip_if_stored: bool = True,
    store: Optional[Any] = None,
) -> int:
    """同步跑 post_media 管线（CLI / 手工补跑）。返回进程 exit code 风格：0 成功，2 超时。"""
    if not task_id:
        return 2
    if skip_if_stored and post_media_pipeline_should_skip(task_id):
        logger.info("发文配图已全部处理，跳过 task=%s", task_id)
        if store is not None:
            try:
                store.ensure_step8_image_analysis(task_id)
            except Exception:
                pass
        return 0

    if store is not None:
        touch_step8_image_progress(store, task_id, prefix="发文配图分析中")

    def _on_progress() -> None:
        if store is not None:
            touch_step8_image_progress(store, task_id, prefix="发文配图分析中")

    sec = int(timeout_sec or _POST_IMAGE_TIMEOUT_SEC)
    if sec <= 0:
        sec = _POST_IMAGE_TIMEOUT_SEC

    def _work() -> Optional[Dict[str, Any]]:
        try:
            return _run_post_media_pipeline(task_id, progress_callback=_on_progress)
        except Exception as exc:
            logger.warning("post_media 管线异常 task=%s: %s", task_id, exc)
            return None

    summary: Optional[Dict[str, Any]] = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_work)
            try:
                summary = fut.result(timeout=sec)
            except FuturesTimeout:
                logger.warning(
                    "post_media 管线超时(%ss) task=%s（未分析完留待后续）",
                    sec,
                    task_id,
                )
                _reset_post_media_running(task_id)
                if store is not None:
                    _force_step8_terminal(
                        store,
                        task_id,
                        reason="管线超时",
                        summary=None,
                    )
                return 2
    except Exception as exc:
        logger.warning("post_media 管线线程异常 task=%s: %s", task_id, exc)
        if store is not None:
            _force_step8_terminal(
                store,
                task_id,
                reason=f"管线异常:{exc}",
                summary=None,
            )
        return 1

    if store is not None:
        _finalize_step8_after_pipeline(store, task_id, summary)
    return 0 if summary is not None else 1


def _force_step8_terminal(
    store: Any,
    task_id: str,
    *,
    reason: str,
    summary: Optional[Dict[str, Any]],
) -> None:
    """超时/异常后必须落到终态，避免 hold 永久卡住。"""
    from report_04.gates import get_step_status

    st = get_step_status(task_id, STEP8_STEP_KEY)
    if st in {"completed", "skipped", "failed"}:
        return
    stats = count_image_analysis_stats(task_id)
    pm = count_image_analysis_stats(task_id, source_type="post_media")
    cap = _POST_MEDIA_MAX_IMAGES
    payload = dict(summary) if isinstance(summary, dict) else {}
    payload.update(
        {
            "image_count": stats["total"],
            "analyzed": stats["analyzed"],
            "post_media_analyzed": pm["analyzed"],
            "post_media_total": pm["total"],
            "post_media_cap": cap,
            "terminal_reason": reason[:200],
        }
    )
    if pm["total"] <= 0 and stats["total"] <= 0:
        store.set_step_status(
            task_id,
            STEP8_STEP_KEY,
            "skipped",
            message=f"无图可分析（{reason}）",
            payload=payload,
        )
        return
    if pm["analyzed"] > 0 or stats["analyzed"] > 0:
        store.set_step_status(
            task_id,
            STEP8_STEP_KEY,
            "completed",
            message=(
                f"发文配图分析结束（已分析 {pm['analyzed']} 张，上限 {cap}；{reason}）"
            ),
            payload=payload,
        )
        return
    store.set_step_status(
        task_id,
        STEP8_STEP_KEY,
        "failed",
        message=f"发文配图分析未产出（{reason}）",
        payload=payload,
    )


def _finalize_step8_after_pipeline(
    store: Any,
    task_id: str,
    summary: Optional[Dict[str, Any]],
) -> None:
    from report_04.gates import get_step_status

    stats = count_image_analysis_stats(task_id)
    pm_stats = count_image_analysis_stats(task_id, source_type="post_media")
    st = get_step_status(task_id, STEP8_STEP_KEY)
    if st in {"completed", "skipped", "failed"}:
        return

    cap = _POST_MEDIA_MAX_IMAGES
    payload = dict(summary) if isinstance(summary, dict) else {}
    payload.update(
        {
            "image_count": stats["total"],
            "analyzed": stats["analyzed"],
            "pending": stats["pending"],
            "post_media_analyzed": pm_stats["analyzed"],
            "post_media_total": pm_stats["total"],
            "post_media_cap": cap,
        }
    )

    if pm_stats["total"] <= 0:
        if stats["total"] <= 0:
            store.set_step_status(
                task_id,
                STEP8_STEP_KEY,
                "skipped",
                message="无图可分析，已跳过",
                payload={"discovered": 0},
            )
        else:
            # 仅有 profile：尽量收口 completed
            try:
                if store.ensure_step8_image_analysis(task_id):
                    return
            except Exception:
                pass
            store.set_step_status(
                task_id,
                STEP8_STEP_KEY,
                "completed",
                message=f"无发文配图，沿用主页图分析（已分析 {stats['analyzed']}/{stats['total']}）",
                payload=payload,
            )
        return

    # 达上限或限额内已无待办 → completed（不要求分析完全部发现图）
    quota_done = pm_stats["analyzed"] >= cap
    all_done = pm_stats["pending_store"] == 0 and pm_stats["pending_analyze"] == 0
    if quota_done or (all_done and pm_stats["analyzed"] > 0):
        msg = (
            f"发文配图分析完成（已分析 {pm_stats['analyzed']} 张"
            f"{'，上限 ' + str(cap) if pm_stats['total'] > cap or quota_done else ''}）"
        )
        store.set_step_status(
            task_id,
            STEP8_STEP_KEY,
            "completed",
            message=msg,
            payload=payload,
        )
        return

    analyzed_delta = int((summary or {}).get("analyzed") or 0)
    if analyzed_delta <= 0 and pm_stats["analyzed"] == 0:
        store.set_step_status(
            task_id,
            STEP8_STEP_KEY,
            "failed",
            message=(
                f"发文配图分析未产出结果 "
                f"post_media={pm_stats['analyzed']}/{min(cap, pm_stats['total'])}"
            ),
            payload=payload,
        )
        return

    # 管线本轮已结束：有部分产出也必须终态，禁止停在 running
    store.set_step_status(
        task_id,
        STEP8_STEP_KEY,
        "completed",
        message=(
            f"发文配图分析完成（已分析 {pm_stats['analyzed']}/"
            f"{min(cap, max(pm_stats['total'], 1))}，上限 {cap}）"
        ),
        payload=payload,
    )


def start_step8_post_media_pipeline(
    store: Any,
    task_id: str,
    *,
    timeout_sec: int = _POST_IMAGE_TIMEOUT_SEC,
    skip_if_stored: bool = True,
) -> bool:
    """步骤8：Hook 内后台线程跑 post_media 管线（与 4.1.2 同模型，单任务去重）。"""
    if not task_id or store is None:
        return False

    from report_04.gates import get_step_status

    cur = get_step_status(task_id, STEP8_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return False

    with _POST_IMAGE_JOB_LOCK:
        if _POST_IMAGE_JOBS.get(task_id):
            logger.info("步骤8 post_media 管线已在跑，跳过重复拉起 task=%s", task_id)
            return False
        _POST_IMAGE_JOBS[task_id] = True

    if skip_if_stored and post_media_pipeline_should_skip(task_id):
        with _POST_IMAGE_JOB_LOCK:
            _POST_IMAGE_JOBS.pop(task_id, None)
        try:
            store.ensure_step8_image_analysis(task_id)
        except Exception:
            pass
        logger.info("发文配图已全部处理，步骤8 无需再跑 task=%s", task_id)
        try:
            from report_04.gates import get_step_status
            from report_04.session_continue import maybe_continue_agent_session

            st = get_step_status(task_id, STEP8_STEP_KEY) or ""
            if st in {"completed", "skipped", "failed"}:
                maybe_continue_agent_session(
                    store,
                    task_id,
                    reason="step8_already_done",
                    kind="analysis",
                    force=False,
                )
        except Exception:
            pass
        return False

    store.ensure_step_row(task_id, STEP8_STEP_KEY)
    touch_step8_image_progress(store, task_id, prefix="发文配图分析中")
    try:
        from report_04.thought_progress import emit_system_thinking

        emit_system_thinking(task_id, "【系统·步骤8】发文配图入库与分析中…")
    except Exception:
        pass

    sec = int(timeout_sec or _POST_IMAGE_TIMEOUT_SEC)
    if sec <= 0:
        sec = _POST_IMAGE_TIMEOUT_SEC

    def _work() -> None:
        try:
            run_post_media_pipeline_blocking(
                task_id,
                timeout_sec=sec,
                skip_if_stored=False,
                store=store,
            )
        except Exception as exc:
            logger.exception("步骤8 post_media 管线异常 task=%s", task_id)
            try:
                store.set_step_status(
                    task_id,
                    STEP8_STEP_KEY,
                    "failed",
                    message=str(exc)[:500],
                )
            except Exception:
                pass
        finally:
            with _POST_IMAGE_JOB_LOCK:
                _POST_IMAGE_JOBS.pop(task_id, None)
            try:
                from report_04.gates import get_step_status
                from report_04.thought_progress import emit_system_thinking

                st = get_step_status(task_id, STEP8_STEP_KEY) or ""
                emit_system_thinking(
                    task_id,
                    f"【系统·步骤8】发文配图分析结束（状态={st}）。",
                )
            except Exception:
                pass
            # 管线终态后催 Agent write（hold→write），避免会话一直等
            try:
                from report_04.gates import get_step_status
                from report_04.session_continue import maybe_continue_agent_session

                st = get_step_status(task_id, STEP8_STEP_KEY) or ""
                if st in {"completed", "skipped", "failed"}:
                    maybe_continue_agent_session(
                        store,
                        task_id,
                        reason="step8_image_pipeline_done",
                        kind="analysis",
                        force=False,
                    )
            except Exception as exc:
                logger.warning("step8 结束后催续跑失败 task=%s: %s", task_id, exc)

    threading.Thread(
        target=_work,
        name=f"step8-post-media-{task_id[:8]}",
        daemon=True,
    ).start()
    logger.info("已启动步骤8 post_media 后台管线 task=%s timeout=%ss", task_id, sec)
    return True


def spawn_second_image_pipeline(
    task_id: str,
    *,
    store: Optional[Any] = None,
    timeout_sec: int = _POST_IMAGE_TIMEOUT_SEC,
    skip_if_stored: bool = True,
) -> bool:
    """发文后第二次图片管线：Hook 内后台线程（不再 spawn 独立 cmd 进程）。"""
    if not task_id:
        return False
    if store is None:
        try:
            from report_04.task_store import TaskStore

            store = TaskStore()
        except Exception as exc:
            logger.warning("spawn 第二次图片管线：无法创建 TaskStore task=%s: %s", task_id, exc)
            return False
    return start_step8_post_media_pipeline(
        store,
        task_id,
        timeout_sec=timeout_sec,
        skip_if_stored=skip_if_stored,
    )


def run_image_pipeline_for_report(
    task_id: str,
    *,
    force_analyze: bool = True,
    skip_if_stored: bool = True,
    timeout_sec: Optional[int] = _HOOK_TIMEOUT_SEC,
) -> Optional[Dict[str, Any]]:
    """4.1.2 第一次图片核验使用的同步管线（stream_steps 后台线程内调用）。"""
    if not task_id:
        return None
    from collect_01.image_assets import run_image_pipeline_after_posts

    def _run() -> Optional[Dict[str, Any]]:
        return run_image_pipeline_after_posts(
            task_id,
            force_analyze=force_analyze,
            skip_if_stored=skip_if_stored,
        )

    if timeout_sec is None or timeout_sec <= 0:
        try:
            return _run()
        except Exception as exc:
            logger.warning("04 图片管线失败 task=%s: %s", task_id, exc)
            return None

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run)
            try:
                return fut.result(timeout=timeout_sec)
            except FuturesTimeout:
                logger.warning(
                    "04 图片管线等待超时(%ss)，放弃阻塞 task=%s（不 fail 任务）",
                    timeout_sec,
                    task_id,
                )
                return None
    except Exception as exc:
        logger.warning("04 图片管线异常 task=%s: %s", task_id, exc)
        return None
