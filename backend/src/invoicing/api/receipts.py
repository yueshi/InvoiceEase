"""银行回单 API（数字员工 P3/R1）：上传/列表/配对/凭证草稿导出。"""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import BankReceipt, ReceiptUpload, User
from invoicing.reports import receipts_to_csv
from invoicing.security import get_current_user, require_role
from invoicing.workers.queue import enqueue_receipt_parse_sync

router = APIRouter(prefix="/receipts", tags=["receipts"])

_FINANCE = ("finance_staff", "finance_manager", "admin")

_MONTH_PATTERN = r"^\d{4}-\d{2}$"
_QUARTER_PATTERN = r"^\d{4}-Q[1-4]$"
_YEAR_PATTERN = r"^\d{4}$"


def _receipt_out(r: BankReceipt) -> dict:
    return {
        "id": r.id,
        "file_url": r.file_url,
        "file_type": r.file_type,
        "trade_date": str(r.trade_date) if r.trade_date else None,
        "counterparty_name": r.counterparty_name,
        "amount": f"{r.amount:.2f}" if r.amount is not None else None,
        "abstract": r.abstract,
        "direction": r.direction,
        "needs_review": r.needs_review,
        "quality_issues": r.quality_issues,
        "bank_code": r.bank_code,
        "page_no": r.page_no,
        "anchor": r.anchor,
        "paired_invoice_id": r.paired_invoice_id,
        "status": r.status,
        "created_at": r.created_at,
    }


@router.post("/upload")
def upload_receipt(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_FINANCE)),
):
    """回单上传（PDF/图片，异步解析 R1.1）：存档 → 建批次记录 → 入队 → 立即返回。

    解析（分块规则+LLM 兜底）在后台执行，上传请求不等 LLM（一次可含多张回单，
    同步解析可达分钟级，前端 15s 超时的根因）。同一文件重复上传 409。
    """
    import hashlib
    from uuid import uuid4

    from invoicing.fetch.filters import classify_attachment
    from invoicing.storage import get_storage

    data = file.file.read()
    kind = classify_attachment(file.filename or "receipt", "", data)
    if kind not in ("PDF", "IMAGE"):
        raise HTTPException(422, "仅支持 PDF 或图片格式的回单")

    file_hash = hashlib.sha256(data).hexdigest()
    existing = db.query(ReceiptUpload).filter(ReceiptUpload.file_hash == file_hash).first()
    if existing is not None:
        state = "解析中" if existing.status == "parsing" else (
            f"已入库 {existing.receipt_count} 张" if existing.status == "parsed" else "解析失败"
        )
        raise HTTPException(409, f"该回单文件已上传过（批次 #{existing.id}，{state}），请勿重复上传")

    key = f"tenant-default/receipts/{uuid4().hex}-{file.filename or 'receipt'}"
    get_storage().put(key, data, "application/octet-stream")
    up = ReceiptUpload(
        file_hash=file_hash,
        file_url=key,
        file_type=kind,
        user_id=user.id,
        status="parsing",
    )
    db.add(up)
    db.commit()
    enqueue_receipt_parse_sync(up.id)
    return {"upload_id": up.id, "status": "parsing"}


@router.get("/uploads")
def list_receipt_uploads(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """最近回单上传批次状态（前端轮询解析进度）。"""
    rows = db.query(ReceiptUpload).order_by(ReceiptUpload.id.desc()).limit(20).all()
    return [
        {
            "id": u.id,
            "status": u.status,
            "receipt_count": u.receipt_count,
            "error": u.error,
            "created_at": u.created_at,
            "parsed_at": u.parsed_at,
        }
        for u in rows
    ]


@router.get("")
def list_receipts(
    month: str | None = Query(None, pattern=_MONTH_PATTERN),
    quarter: str | None = Query(None, pattern=_QUARTER_PATTERN),
    year: str | None = Query(None, pattern=_YEAR_PATTERN),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """回单列表：month=YYYY-MM / quarter=YYYY-QN / year=YYYY；都不传 = 全部时间。"""
    from invoicing.reports import receipts_in_period

    try:
        rows = receipts_in_period(db, month=month, quarter=quarter, year=year)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return [_receipt_out(r) for r in rows]


@router.get("/unmatched")
def unmatched_receipts(
    month: str | None = Query(None, pattern=_MONTH_PATTERN),
    quarter: str | None = Query(None, pattern=_QUARTER_PATTERN),
    year: str | None = Query(None, pattern=_YEAR_PATTERN),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """无票费用提示（R2）：未配对回单清单（年/季/月；都不传 = 全部时间）。"""
    from invoicing.reports import receipts_in_period

    try:
        rows = receipts_in_period(db, month=month, quarter=quarter, year=year)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return [_receipt_out(r) for r in rows if r.paired_invoice_id is None]


@router.get("/{receipt_id}/file")
def get_receipt_file(
    receipt_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """回单原件查看（人工核对/补录用）：inline 预览，浏览器直接打开。"""
    from invoicing.storage import get_storage

    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise HTTPException(404, "回单不存在")
    data = get_storage().get(r.file_url)
    filename = r.file_url.rsplit("/", 1)[-1]
    media = "application/pdf" if r.file_type == "PDF" else "image/jpeg"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/{receipt_id}/page.png")
def get_receipt_page_image(
    receipt_id: int,
    dpi: int = Query(150, ge=72, le=300),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """原件定位：回单所在页渲染为 PNG（前端叠加锚点高亮框，人工核对用）。

    派生数据按 file_hash+页+DPI 缓存于存储；渲染依赖缺失返回 501，
    前端降级为「打开原 PDF 第 N 页#page=N」。
    """
    from invoicing.parse.pdf_text_parser import RenderUnavailable, render_pdf_page_png
    from invoicing.storage import get_storage

    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise HTTPException(404, "回单不存在")
    if r.file_type != "PDF":
        raise HTTPException(422, "仅 PDF 回单支持页面渲染")
    page_no = r.page_no or 1
    storage = get_storage()
    file_key = (r.file_hash or r.file_url.rsplit("/", 1)[-1]).replace("/", "_")
    cache_key = f"tenant-default/receipts/pages/{file_key}/p{page_no}@{dpi}.png"
    try:
        png = storage.get(cache_key)
    except Exception:
        png = None
    if png is None:
        try:
            data = storage.get(r.file_url)
            png = render_pdf_page_png(data, page_no, dpi=dpi)
        except RenderUnavailable as e:
            raise HTTPException(501, f"页面渲染不可用（{e}），请打开原 PDF 第 {page_no} 页") from None
        try:
            storage.put(cache_key, png, "image/png")
        except Exception:
            pass  # 缓存写失败不阻塞返回
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/{receipt_id}/pair")
def pair_receipt(
    receipt_id: int,
    invoice_id: int = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """手动配对（覆盖建议）。"""
    from invoicing.models import Invoice

    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise HTTPException(404, "回单不存在")
    if db.get(Invoice, invoice_id) is None:
        raise HTTPException(404, "发票不存在")
    r.paired_invoice_id = invoice_id
    r.status = "paired"
    db.commit()
    return _receipt_out(r)


@router.post("/{receipt_id}/auto-pair")
def auto_pair_receipt(
    receipt_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """按配对规则（D5：金额相等+户名规范化）自动配对建议并落库。"""
    from invoicing.parse.receipt import suggest_pair

    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise HTTPException(404, "回单不存在")
    suggested = suggest_pair(db, receipt_id)
    if suggested is None:
        r.status = "unmatched"
        db.commit()
        return _receipt_out(r)
    r.paired_invoice_id = suggested
    r.status = "paired"
    db.commit()
    return _receipt_out(r)


@router.get("/export")
def export_receipts(
    month: str | None = Query(None, pattern=_MONTH_PATTERN),
    quarter: str | None = Query(None, pattern=_QUARTER_PATTERN),
    year: str | None = Query(None, pattern=_YEAR_PATTERN),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """凭证草稿 CSV（金蝶/用友通用列；年/季/月；都不传 = 全部）。"""
    try:
        data = receipts_to_csv(db, month=month, quarter=quarter, year=year)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    period = quarter or month or year or "all"
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="receipts-{period}.csv"'},
    )
