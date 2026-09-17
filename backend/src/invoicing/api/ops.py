"""运维端点（运维兜底，设计 §9）：全部 admin-only；MCP 面不暴露任何 ops 工具。"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.config import settings
from invoicing.db import get_db
from invoicing.models import User
from invoicing.models.ops import OpsAlert, TaskRun
from invoicing.security import require_role
from invoicing.schemas.ops import (
    BackupOut,
    OpsAlertListResponse,
    OpsAlertOut,
    OpsStatusOut,
    TaskRunListResponse,
    TaskRunOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ops", tags=["ops"])

# 设计 §9：手动触发白名单（班表任务全集）
_RUNNABLE = {"mailbox_poll", "review_predict", "monthly_health", "audit_retention",
             "ops_check", "ops_backup", "receipt_classify"}
_VERSION = "0.1.0"  # 与 main.health 保持一致


def _write_ops_audit(db: Session, action: str, user_id: int, detail: dict) -> None:
    """失败路径审计：审计自身失败只记日志，绝不吞掉主流程异常。"""
    try:
        write_audit(db, action, user_id=user_id, detail=detail)
        db.commit()
    except Exception:
        logger.exception("运维审计写入失败 action=%s", action)


@router.get("/status", response_model=OpsStatusOut)
def ops_status(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    from invoicing.models.fields import utcnow
    from invoicing.ops.checks import run_all_checks
    from invoicing.ops.backup import list_backups
    from invoicing.ops.instrumentation import PROCESS_STARTED_AT
    from invoicing.ops.metrics import collect_metrics, storage_usage

    backups = list_backups()
    return OpsStatusOut(
        version=_VERSION,
        uptime_seconds=int((utcnow() - PROCESS_STARTED_AT).total_seconds()),
        checks=run_all_checks(),
        metrics=collect_metrics(),
        storage=storage_usage(),
        last_backup=BackupOut(**backups[0]) if backups else None,
    )


@router.get("/tasks", response_model=TaskRunListResponse)
def list_task_runs(
    task_name: str | None = None,
    outcome: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    q = db.query(TaskRun)
    if task_name:
        q = q.filter(TaskRun.task_name == task_name)
    if outcome:
        q = q.filter(TaskRun.outcome == outcome)
    total = q.count()
    items = q.order_by(TaskRun.started_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return TaskRunListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/tasks/{name}/run")
def run_task(name: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    if name not in _RUNNABLE:
        raise HTTPException(status_code=400, detail=f"不支持手动触发: {name}")
    from invoicing.ops.instrumentation import run_manual

    try:
        run_manual(name)  # 同步阻塞执行；结果由 task_runs 记录
    except Exception as exc:
        # 失败也落审计（FRD 审计完整可追溯）；审计自身失败只记日志，不吞原异常
        _write_ops_audit(db, "OPS_TASK_RUN", user.id,
                         {"task": name, "outcome": "error", "error": type(exc).__name__})
        raise HTTPException(status_code=500, detail=f"任务执行失败: {type(exc).__name__}") from exc
    write_audit(db, "OPS_TASK_RUN", user_id=user.id, detail={"task": name})
    db.commit()
    return {"task": name, "status": "done"}


@router.get("/alerts", response_model=OpsAlertListResponse)
def list_alerts(
    severity: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    q = db.query(OpsAlert)
    if severity:
        q = q.filter(OpsAlert.severity == severity)
    total = q.count()
    items = q.order_by(OpsAlert.fired_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return OpsAlertListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/backups", response_model=list[BackupOut])
def list_ops_backups(_: User = Depends(require_role("admin"))):
    from invoicing.ops.backup import list_backups

    return [BackupOut(**b) for b in list_backups()]


@router.post("/backups/run")
def run_backup(db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    from invoicing.ops.backup import create_backup

    try:
        tar_path = create_backup()
    except Exception as exc:
        # 失败也落审计（FRD 审计完整可追溯）；审计自身失败只记日志，不吞原异常
        _write_ops_audit(db, "OPS_BACKUP_RUN", user.id,
                         {"outcome": "error", "error": type(exc).__name__})
        raise HTTPException(status_code=500, detail=f"备份失败: {type(exc).__name__}") from exc
    write_audit(db, "OPS_BACKUP_RUN", user_id=user.id, detail={"file": tar_path.name})
    db.commit()
    return {"name": tar_path.name}


@router.get("/backups/{name}/download")
def download_backup(name: str, db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin"))):
    from invoicing.ops.backup import BACKUP_NAME_RE, list_backups

    if not BACKUP_NAME_RE.match(name) or name not in {b["name"] for b in list_backups()}:
        raise HTTPException(status_code=404, detail="备份不存在")
    write_audit(db, "OPS_BACKUP_DOWNLOAD", user_id=user.id, detail={"file": name})
    db.commit()
    return FileResponse(Path(settings.ops_backup_dir) / name, filename=name)  # FileResponse 流式，不整读内存


@router.get("/logs/tail")
def tail_log(lines: int = Query(200, ge=1, le=2000),
             _: User = Depends(require_role("admin"))):
    log_file = Path(settings.log_dir) / "invoicing.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    buf: list[str] = []
    with open(log_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            buf.append(line)
            if len(buf) > lines:
                buf.pop(0)
    return {"lines": buf}


@router.get("/logs/download")
def download_log(db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    log_file = Path(settings.log_dir) / "invoicing.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    write_audit(db, "OPS_LOG_DOWNLOAD", user_id=user.id)
    db.commit()
    return FileResponse(log_file, filename="invoicing.log")
