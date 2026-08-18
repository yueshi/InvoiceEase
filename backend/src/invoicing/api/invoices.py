# api/invoices.py
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import Role, User
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut, InvoiceUpdate, ReviewRequest
from invoicing.security import get_current_user, require_role
from invoicing.storage import get_storage
from invoicing.workflow import services

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _content_disposition(filename: str, disposition: str = "attachment") -> str:
    """RFC 6266：ASCII 兜底文件名 + UTF-8 filename*（中文原件名不触发 latin-1 编码错误）。"""
    ascii_name = filename.encode("ascii", "ignore").decode().strip() or "download"
    return f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


_MIME_BY_TYPE = {"PDF": "application/pdf", "XML": "text/xml; charset=utf-8", "OFD": "application/octet-stream"}


@router.get("", response_model=InvoiceListResponse)
def list_invoices(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return services.list_invoices(db, user, status, date_from, date_to, keyword, page, page_size)


@router.post("/upload", response_model=InvoiceOut)
def upload_invoice(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """员工交票上传（M3）：仅 PDF/OFD/XML 原件，图片 422 引导走邮箱；user_id 归属上传者。"""
    data = file.file.read()
    return services.upload_invoice(db, user, file.filename or "invoice", data)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice_detail(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return services.get_invoice(db, user, invoice_id)


@router.get("/{invoice_id}/file")
def download_file(
    invoice_id: int,
    kind: str = Query("file", pattern="^(file|xml)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 统一流式返回（本地/S3 两后端同路径）；presigned 直传优化留 Plan D 生产对齐
    inv = services.get_invoice(db, user, invoice_id)
    key = inv.xml_url if kind == "xml" else inv.file_url
    if kind == "xml" and not key:
        from fastapi import HTTPException

        raise HTTPException(404, "该发票无 XML 原件")
    data = get_storage().get(key)
    filename = key.rsplit("/", 1)[-1]
    # PDF 与 XML 支持浏览器内联预览；OFD 走 /preview 渲染端点或下载
    inline = kind == "xml" or (inv.file_type or "") == "PDF"
    media = "text/xml; charset=utf-8" if kind == "xml" else _MIME_BY_TYPE.get(inv.file_type or "", "application/octet-stream")
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": _content_disposition(filename, "inline" if inline else "attachment")},
    )


@router.get("/{invoice_id}/preview")
def preview_file(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """OFD 原件渲染预览（字体转曲矢量渲染 / 内嵌页面图）；PDF/XML 请用 /file 端点内联。"""
    from fastapi import HTTPException

    inv = services.get_invoice(db, user, invoice_id)
    if (inv.file_type or "") != "OFD":
        raise HTTPException(404, "仅 OFD 原件支持渲染预览，PDF/XML 请用下载端点")
    data = get_storage().get(inv.file_url)
    from invoicing.parse.ocr import extract_ofd_page_image
    from invoicing.parse.ofd_render import render_ofd_page_to_png

    img = render_ofd_page_to_png(data) or extract_ofd_page_image(data)
    if img is None:
        raise HTTPException(422, "该 OFD 无法渲染预览，请下载后用本地阅读器查看")
    return Response(content=img, media_type="image/png")


@router.post("/{invoice_id}/review", response_model=InvoiceOut)
def review(
    invoice_id: int,
    body: ReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.review_invoice(db, user, invoice_id, body.action, body.note)


@router.post("/{invoice_id}/verify", response_model=InvoiceOut)
def re_verify(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.re_verify_invoice(db, user, invoice_id)


@router.put("/{invoice_id}", response_model=InvoiceOut)
def update_invoice(
    invoice_id: int,
    body: InvoiceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.update_invoice(db, user, invoice_id, body.model_dump(exclude_unset=True))


@router.post("/{invoice_id}/unblock", response_model=InvoiceOut)
def unblock_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.unblock_invoice(db, user, invoice_id)


@router.delete("/{invoice_id}")
def delete_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_manager.value, Role.admin.value)),
):
    return services.delete_invoice(db, user, invoice_id)


@router.post("/{invoice_id}/ai-review", response_model=InvoiceOut)
def ai_review(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    """按需生成/重算 AI 复核预判（班表任务通常已生成；改字段后可手动重算）。"""
    from fastapi import HTTPException

    from invoicing.audit import write_audit
    from invoicing.models.fields import utcnow
    from invoicing.parse.ai_review import predict_review

    inv = services.get_invoice(db, user, invoice_id)
    verdict = predict_review(inv, db)
    if verdict is None:
        raise HTTPException(422, "预判不可用（LLM 未启用或调用失败），请人工复核")
    inv.ai_review_verdict = verdict.verdict
    inv.ai_review_reason = verdict.reason
    inv.ai_review_confidence = verdict.confidence
    inv.ai_reviewed_at = utcnow()
    write_audit(
        db, action="AI_REVIEW", user_id=user.id, invoice_id=inv.id, channel="web",
        detail={"verdict": verdict.verdict, "reason": verdict.reason, "confidence": verdict.confidence},
    )
    db.commit()
    return inv
