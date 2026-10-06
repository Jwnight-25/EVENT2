"""One local supervisor; each heavy job runs in a fresh spawned process."""

import fcntl
import multiprocessing as mp
import os
import signal
import time

# Set before importing NumPy / Torch in child processes.
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(name, "1")

from sqlalchemy import select
from .config import DATA_DIR
from .db import SessionLocal, now, engine
from .entities import Job


def finish(job_id, status, error=None):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.status, job.error, job.finished_at = status, error, now()
        job.progress = 100 if status == "succeeded" else job.progress
        db.commit()


def execute_job(job_id):
    from .research import train_job, prediction_job, Cancelled, BudgetExceeded

    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            kind, payload = job.kind, job.payload
        if kind == "training":
            train_job(job_id)
        elif kind == "prediction":
            prediction_job(job_id)
        elif kind == "ai":
            from .integrations import analyze_prediction

            analyze_prediction(payload["prediction_id"])
        else:
            raise ValueError("未知任务类型")
        with SessionLocal() as db:
            cancelled = db.get(Job, job_id).cancel_requested
        finish(job_id, "cancelled" if cancelled else "succeeded")
    except Cancelled:
        finish(job_id, "cancelled")
    except BudgetExceeded:
        finish(job_id, "failed", "计算预算已用尽；未发布本次未完成的模型")
    except Exception as exc:
        finish(job_id, "failed", f"{type(exc).__name__}: {str(exc)[:800]}")


def claim_job():
    with SessionLocal() as db:
        query = select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
        if engine.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job = db.scalar(query)
        if not job:
            return None
        job.status, job.started_at, job.heartbeat_at, job.stage = "running", now(), now(), "启动计算进程"
        db.commit()
        return job.id, job.kind, job.payload


def recover():
    with SessionLocal() as db:
        for job in db.scalars(select(Job).where(Job.status == "running")):
            job.status, job.error, job.finished_at = "failed", "工作进程中断，请查看已有结果后重新提交", now()
        db.commit()


def main(once=False):
    lock = open(DATA_DIR / "worker.lock", "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("已有工作进程运行，请勿重复启动")
    pg_lock = None
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text

        pg_lock = engine.connect()
        if not pg_lock.scalar(text("SELECT pg_try_advisory_lock(20261005)")):
            raise SystemExit("该数据库已有工作进程")
    recover()
    context = mp.get_context("spawn")
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("EVENT2 worker ready", flush=True)
    while not stopping:
        claimed = claim_job()
        if not claimed:
            if once:
                break
            time.sleep(1)
            continue
        identifier, kind, payload = claimed
        budget = (
            payload.get("request", {}).get("time_budget_seconds", 300) + 90 if kind == "training" else 600
        )
        process = context.Process(target=execute_job, args=(identifier,))
        process.start()
        deadline = time.monotonic() + budget
        while process.is_alive() and not stopping and time.monotonic() < deadline:
            process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()
            finish(identifier, "failed", "工作进程停止或超过任务总预算")
        else:
            with SessionLocal() as db:
                if db.get(Job, identifier).status == "running":
                    finish(identifier, "failed", f"计算进程异常退出：{process.exitcode}")
        if once:
            break
    if pg_lock:
        pg_lock.close()
    lock.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process at most one queued job")
    main(once=parser.parse_args().once)
