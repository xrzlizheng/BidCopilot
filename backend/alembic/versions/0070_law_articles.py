"""law articles knowledge base

合同审查升级（Wave 4）：新增 `law_articles` 法条知识库表，作为深审的法条
注入与 legal_basis 引用校验数据源。种子为民法典合同编/招标投标法体系 28 条
要点摘编（官方公文，公有领域；上线前应经法务核对），幂等按
(law_name, article_no_arabic) 跳过已存在行。非租户表，运行时只读。
种子内容调整必须走新迁移（幂等跳过不回填存量库），与 review_templates 同规。

Revision ID: 0070_law_articles
Revises: 0069_contract_review_upgrade
Create Date: 2026-11-07
"""

from collections.abc import Sequence
from hashlib import sha256
from json import dumps
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.modules.contracts.data.law_articles import SEED_LAW_ARTICLES

revision: str = "0070_law_articles"
down_revision: str | None = "0069_contract_review_upgrade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "law_articles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("law_name", sa.String(120), nullable=False),
        sa.Column("article_no", sa.String(20), nullable=False),
        sa.Column("article_no_arabic", sa.Integer(), nullable=False),
        sa.Column("article_text", sa.Text(), nullable=False),
        sa.Column("effective_status", sa.String(20), nullable=False, server_default="current"),
        sa.Column("keywords", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_law_articles_law_name", "law_articles", ["law_name"])
    op.create_unique_constraint("uq_law_articles_law_article", "law_articles", ["law_name", "article_no"])
    _seed_law_articles()


def _seed_law_articles() -> None:
    """幂等种子：按 (law_name, article_no_arabic) 跳过已存在行。"""
    bind = op.get_bind()
    existing = {
        (row[0], row[1])
        for row in bind.execute(sa.text("SELECT law_name, article_no_arabic FROM law_articles"))
    }
    insert_sql = sa.text(
        """
        INSERT INTO law_articles (
            id, law_name, article_no, article_no_arabic, article_text,
            effective_status, keywords, source_url, content_hash
        ) VALUES (
            :id, :law_name, :article_no, :article_no_arabic, :article_text,
            'current', CAST(:keywords AS jsonb), :source_url, :content_hash
        )
        """
    )
    for article in SEED_LAW_ARTICLES:
        key = (article["law_name"], article["article_no_arabic"])
        if key in existing:
            continue
        article_text = article["article_text"]
        bind.execute(
            insert_sql,
            {
                "id": str(uuid4()),
                "law_name": article["law_name"],
                "article_no": article["article_no"],
                "article_no_arabic": article["article_no_arabic"],
                "article_text": article_text,
                "keywords": dumps(article.get("keywords", []), ensure_ascii=False),
                # 指向数据库首页而非具体条文页：避免编造深链；语料核对以法务为准。
                "source_url": article.get("source_url") or "https://flk.npc.gov.cn",
                "content_hash": sha256(article_text.encode("utf-8")).hexdigest(),
            },
        )


def downgrade() -> None:
    op.drop_constraint("uq_law_articles_law_article", "law_articles", type_="unique")
    op.drop_index("ix_law_articles_law_name", table_name="law_articles")
    op.drop_table("law_articles")
