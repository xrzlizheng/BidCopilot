from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPrimaryKeyMixin


class SubmissionReceiptAttachment(UUIDPrimaryKeyMixin, TenantMixin, Base):
    """交标回执附件：平台回执截图/PDF、CA 签章凭证、加密上传成功页等人工证据。

    内容落对象存储（与快照同源组件），本表只存引用、大小与 SHA-256，
    以便回执被审计时能证明「附件未被替换」。
    """

    __tablename__ = "submission_receipt_attachments"
    __table_args__ = (
        Index("ix_receipt_attachments_tenant_receipt", "tenant_id", "receipt_id"),
    )

    receipt_id: Mapped[UUID] = mapped_column(
        ForeignKey("submission_receipts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="receipt")
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
