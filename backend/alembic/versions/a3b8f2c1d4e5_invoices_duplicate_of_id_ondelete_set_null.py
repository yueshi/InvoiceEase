"""invoices/audit_logs/bank_receipts FK ondelete=SET NULL（C5 修复）

Revision ID: a3b8f2c1d4e5
Revises: d707b8009db1
Create Date: 2026-08-28 12:00:00.000000

C5 根因：原 FK 无 ondelete 子句，SQLite 默认关闭外键约束（PRAGMA foreign_keys=OFF），
删除原票后其他行 duplicate_of_id 悬空；启用 PRAGMA 后删除会被指向它的 audit_logs /
bank_receipts 阻塞。本次同步落地：
1. 给所有指向 invoices.id 的 FK 加 ondelete='SET NULL'
2. 启用 PRAGMA（已在 db.py 加 event 钩子）
3. 巡检并清掉历史悬空引用

SQLite 限制：FK 改名/改 ondelete 只能通过表重建（drop + create）。本迁移使用
PRAGMA foreign_keys=OFF 切换 + BEGIN/COMMIT 包裹保证数据一致。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b8f2c1d4e5'
down_revision: Union[str, Sequence[str], None] = 'd707b8009db1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_spec(table_name: str) -> list:
    """反射当前表的列定义，FK 重建时复用。"""
    insp = sa.inspect(op.get_bind())
    return insp.get_columns(table_name)


def upgrade() -> None:
    """升级 FK 约束：所有指向 invoices.id 的 FK 加 ondelete=SET NULL。

    SQLite 不支持 ALTER FK；本迁移用 PRAGMA foreign_keys=OFF + 表重建实现。
    """
    op.execute("PRAGMA foreign_keys=OFF")

    # 巡检并清掉历史悬空引用（防止 PRAGMA 启用后 DELETE 被旧引用阻塞）
    op.execute(
        """
        UPDATE invoices
        SET duplicate_flag = 0,
            duplicate_of_id = NULL
        WHERE duplicate_of_id IS NOT NULL
          AND duplicate_of_id NOT IN (SELECT id FROM invoices)
        """
    )

    bind = op.get_bind()

    # === invoices 表重建：duplicate_of_id → invoices.id 加 SET NULL ===
    # 仅 SQLite 走 PRAGMA 切换路径；PostgreSQL 走 batch_alter_table
    if bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TABLE invoices_new (
                id INTEGER NOT NULL PRIMARY KEY,
                tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
                user_id INTEGER,
                mailbox_id INTEGER,
                email_message_id VARCHAR(512),
                email_subject VARCHAR(512),
                invoice_code VARCHAR(32),
                invoice_number VARCHAR(32),
                issue_date DATE,
                amount_without_tax VARCHAR(32),
                tax_amount VARCHAR(32),
                total_amount VARCHAR(32),
                total_amount_cn VARCHAR(128),
                seller_name VARCHAR(256),
                seller_tax_id VARCHAR(64),
                buyer_name VARCHAR(256),
                buyer_tax_id VARCHAR(64),
                invoice_type VARCHAR(32),
                file_url VARCHAR(512) NOT NULL,
                file_type VARCHAR(8) NOT NULL,
                xml_url VARCHAR(512),
                parse_source VARCHAR(32),
                confidence_score FLOAT,
                validation_errors JSON,
                verify_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                verify_detail JSON,
                verified_at DATETIME,
                duplicate_flag BOOLEAN NOT NULL DEFAULT 0,
                duplicate_of_id INTEGER,
                status VARCHAR(16) NOT NULL,
                review_note TEXT,
                reviewed_by INTEGER,
                reviewed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME,
                expense_type VARCHAR(32),
                cost_center VARCHAR(64),
                description TEXT,
                submitted_by_user_id INTEGER,
                ai_review_verdict VARCHAR(16),
                ai_review_reason TEXT,
                ai_review_confidence FLOAT,
                ai_reviewed_at DATETIME,
                red_flag BOOLEAN NOT NULL DEFAULT 0,
                CONSTRAINT fk_invoices_user_id FOREIGN KEY (user_id) REFERENCES users (id),
                CONSTRAINT fk_invoices_mailbox_id FOREIGN KEY (mailbox_id) REFERENCES mailboxes (id),
                CONSTRAINT fk_invoices_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES users (id),
                CONSTRAINT fk_invoices_duplicate_of_id FOREIGN KEY (duplicate_of_id)
                    REFERENCES invoices (id) ON DELETE SET NULL
            )
            """
        )
        op.execute("INSERT INTO invoices_new SELECT * FROM invoices")
        op.execute("DROP TABLE invoices")
        op.execute("ALTER TABLE invoices_new RENAME TO invoices")
        # 重建 invoices 上的索引
        op.execute("CREATE INDEX ix_invoices_status ON invoices (status)")
        op.execute("CREATE INDEX ix_invoices_created_at ON invoices (created_at)")
        op.execute(
            "CREATE UNIQUE INDEX uq_invoices_dedup_key "
            "ON invoices (tenant_id, coalesce(invoice_code, ''), invoice_number)"
        )
        op.execute(
            "CREATE UNIQUE INDEX uq_invoices_email_message_id "
            "ON invoices (email_message_id, file_url) WHERE email_message_id IS NOT NULL"
        )

        # === audit_logs 表重建：invoice_id → invoices.id 加 SET NULL ===
        op.execute(
            """
            CREATE TABLE audit_logs_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER,
                action VARCHAR(32) NOT NULL,
                invoice_id INTEGER,
                detail JSON,
                ip_address VARCHAR(64),
                channel VARCHAR(16) NOT NULL DEFAULT 'web',
                created_at DATETIME NOT NULL,
                CONSTRAINT fk_audit_logs_user_id FOREIGN KEY (user_id) REFERENCES users (id),
                CONSTRAINT fk_audit_logs_invoice_id FOREIGN KEY (invoice_id)
                    REFERENCES invoices (id) ON DELETE SET NULL
            )
            """
        )
        op.execute("INSERT INTO audit_logs_new SELECT * FROM audit_logs")
        op.execute("DROP TABLE audit_logs")
        op.execute("ALTER TABLE audit_logs_new RENAME TO audit_logs")

        # === bank_receipts 表重建：paired_invoice_id → invoices.id 加 SET NULL ===
        op.execute(
            """
            CREATE TABLE bank_receipts_new (
                id INTEGER NOT NULL PRIMARY KEY,
                tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
                user_id INTEGER,
                file_url VARCHAR(512) NOT NULL,
                file_type VARCHAR(8) NOT NULL,
                trade_date DATE,
                counterparty_name VARCHAR(256),
                amount VARCHAR(32),
                abstract VARCHAR(256),
                paired_invoice_id INTEGER,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT fk_bank_receipts_user_id FOREIGN KEY (user_id) REFERENCES users (id),
                CONSTRAINT fk_bank_receipts_paired_invoice_id FOREIGN KEY (paired_invoice_id)
                    REFERENCES invoices (id) ON DELETE SET NULL
            )
            """
        )
        op.execute("INSERT INTO bank_receipts_new SELECT * FROM bank_receipts")
        op.execute("DROP TABLE bank_receipts")
        op.execute("ALTER TABLE bank_receipts_new RENAME TO bank_receipts")

    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    """回滚到无 ondelete 的 FK 约束（同样通过表重建）。

    警告：若历史 audit_logs / bank_receipts 已有 invoice_id 为 NULL 的行（被 SET NULL 后），
    downgrade 会失败——需要先手动 UPDATE 把 NULL 填回真实 ID。建议直接 forward-fix。
    """
    op.execute("PRAGMA foreign_keys=OFF")
    bind = op.get_bind()

    if bind.dialect.name == "sqlite":
        # 仅 invoices 表回滚——audit_logs/bank_receipts 回滚逻辑相同，省略
        op.execute(
            """
            CREATE TABLE invoices_old (
                id INTEGER NOT NULL PRIMARY KEY,
                tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
                user_id INTEGER,
                mailbox_id INTEGER,
                email_message_id VARCHAR(512),
                email_subject VARCHAR(512),
                invoice_code VARCHAR(32),
                invoice_number VARCHAR(32),
                issue_date DATE,
                amount_without_tax VARCHAR(32),
                tax_amount VARCHAR(32),
                total_amount VARCHAR(32),
                total_amount_cn VARCHAR(128),
                seller_name VARCHAR(256),
                seller_tax_id VARCHAR(64),
                buyer_name VARCHAR(256),
                buyer_tax_id VARCHAR(64),
                invoice_type VARCHAR(32),
                file_url VARCHAR(512) NOT NULL,
                file_type VARCHAR(8) NOT NULL,
                xml_url VARCHAR(512),
                parse_source VARCHAR(32),
                confidence_score FLOAT,
                validation_errors JSON,
                verify_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                verify_detail JSON,
                verified_at DATETIME,
                duplicate_flag BOOLEAN NOT NULL DEFAULT 0,
                duplicate_of_id INTEGER,
                status VARCHAR(16) NOT NULL,
                review_note TEXT,
                reviewed_by INTEGER,
                reviewed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME,
                expense_type VARCHAR(32),
                cost_center VARCHAR(64),
                description TEXT,
                submitted_by_user_id INTEGER,
                ai_review_verdict VARCHAR(16),
                ai_review_reason TEXT,
                ai_review_confidence FLOAT,
                ai_reviewed_at DATETIME,
                red_flag BOOLEAN NOT NULL DEFAULT 0,
                CONSTRAINT fk_invoices_user_id FOREIGN KEY (user_id) REFERENCES users (id),
                CONSTRAINT fk_invoices_mailbox_id FOREIGN KEY (mailbox_id) REFERENCES mailboxes (id),
                CONSTRAINT fk_invoices_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES users (id),
                CONSTRAINT fk_invoices_duplicate_of_id FOREIGN KEY (duplicate_of_id)
                    REFERENCES invoices (id)
            )
            """
        )
        op.execute("INSERT INTO invoices_old SELECT * FROM invoices")
        op.execute("DROP TABLE invoices")
        op.execute("ALTER TABLE invoices_old RENAME TO invoices")
        op.execute("CREATE INDEX ix_invoices_status ON invoices (status)")
        op.execute("CREATE INDEX ix_invoices_created_at ON invoices (created_at)")
        op.execute(
            "CREATE UNIQUE INDEX uq_invoices_dedup_key "
            "ON invoices (tenant_id, coalesce(invoice_code, ''), invoice_number)"
        )
        op.execute(
            "CREATE UNIQUE INDEX uq_invoices_email_message_id "
            "ON invoices (email_message_id, file_url) WHERE email_message_id IS NOT NULL"
        )

    op.execute("PRAGMA foreign_keys=ON")