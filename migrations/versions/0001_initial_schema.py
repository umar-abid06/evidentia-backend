"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-23

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "health_guides",
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("region_code", sa.String(length=10), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "generating",
                "ready",
                "failed",
                name="generationstatus_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_guides")),
        sa.UniqueConstraint(
            "country_code",
            "region_code",
            "language",
            "version",
            name=op.f("uq_health_guides_country_code"),
        ),
    )
    op.create_index("ix_health_guides_status", "health_guides", ["status"], unique=False)
    op.create_table(
        "llm_calls",
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Double(), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("policy_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index("ix_llm_calls_document", "llm_calls", ["document_id"], unique=False)
    op.create_index(
        "ix_llm_calls_stage_created", "llm_calls", ["stage", "created_at"], unique=False
    )
    op.create_table(
        "policies",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("insurer_name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policies")),
        sa.UniqueConstraint("code", name=op.f("uq_policies_code")),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "customer",
                "reviewer",
                "admin",
                name="userrole_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("preferred_language", sa.String(length=5), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "audit_events",
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_entity", "audit_events", ["entity_type", "entity_id"], unique=False
    )
    op.create_table(
        "card_products",
        sa.Column("issuer_name", sa.String(length=200), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("scheme", sa.String(length=40), nullable=True),
        sa.Column("funding_type", sa.String(length=20), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name=op.f("fk_card_products_policy_id_policies"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_products")),
        sa.UniqueConstraint(
            "issuer_name", "product_name", "country_code", name=op.f("uq_card_products_issuer_name")
        ),
    )
    op.create_index(
        "ix_card_products_country_active",
        "card_products",
        ["country_code", "is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_card_products_policy_id"), "card_products", ["policy_id"], unique=False
    )
    op.create_table(
        "documents",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column(
            "intent",
            sa.Enum(
                "full_policy",
                "amendment",
                name="documentintent_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "uploading",
                "pending",
                "processing",
                "completed",
                "failed",
                name="documentstatus_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "review_status",
            sa.Enum(
                "pending",
                "needs_review",
                "approved",
                "rejected",
                name="reviewstatus_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("needs_ocr", sa.Boolean(), nullable=False),
        sa.Column("extraction_model", sa.String(length=100), nullable=True),
        sa.Column("extraction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("overall_confidence", sa.Double(), nullable=True),
        sa.Column("review_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("conflicts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name=op.f("fk_documents_policy_id_policies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name=op.f("fk_documents_reviewed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["users.id"],
            name=op.f("fk_documents_uploaded_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_documents_storage_key")),
    )
    op.create_index(op.f("ix_documents_policy_id"), "documents", ["policy_id"], unique=False)
    op.create_index("ix_documents_review_status", "documents", ["review_status"], unique=False)
    op.create_index("ix_documents_status", "documents", ["status"], unique=False)
    op.create_index(
        "uq_documents_active_sha256",
        "documents",
        ["sha256"],
        unique=True,
        postgresql_where=sa.text("status <> 'failed' AND review_status <> 'rejected'"),
    )
    op.create_table(
        "prompt_overrides",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_prompt_overrides_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_overrides")),
    )
    op.create_index(op.f("ix_prompt_overrides_key"), "prompt_overrides", ["key"], unique=False)
    op.create_index(
        "uq_prompt_overrides_active",
        "prompt_overrides",
        ["key"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_system_settings_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_settings")),
    )
    op.create_table(
        "trips",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("destination_country", sa.String(length=2), nullable=False),
        sa.Column("destination_region", sa.String(length=10), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("health_guide_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["health_guide_id"],
            ["health_guides.id"],
            name=op.f("fk_trips_health_guide_id_health_guides"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_trips_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trips")),
    )
    op.create_index(op.f("ix_trips_user_id"), "trips", ["user_id"], unique=False)
    op.create_table(
        "bin_mappings",
        sa.Column("bin", sa.String(length=8), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("card_product_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "cache",
                "lookup",
                "manual",
                "admin",
                name="matchsource_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["card_product_id"],
            ["card_products.id"],
            name=op.f("fk_bin_mappings_card_product_id_card_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("bin", "country_code", name=op.f("pk_bin_mappings")),
    )
    op.create_table(
        "bin_reviews",
        sa.Column("bin", sa.String(length=8), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "ambiguous",
                "not_found",
                "manual_choice",
                name="binreviewreason_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "resolved",
                "dismissed",
                name="binreviewstatus_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lookup_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_card_product_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name=op.f("fk_bin_reviews_resolved_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_card_product_id"],
            ["card_products.id"],
            name=op.f("fk_bin_reviews_resolved_card_product_id_card_products"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bin_reviews")),
    )
    op.create_index(
        "uq_bin_reviews_open",
        "bin_reviews",
        ["bin", "country_code"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "document_pages",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_pages_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_pages")),
        sa.UniqueConstraint(
            "document_id", "page_number", name=op.f("uq_document_pages_document_id")
        ),
    )
    op.create_table(
        "jetlag_plans",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=True),
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("schema_version", sa.String(length=30), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "generating",
                "ready",
                "failed",
                name="generationstatus_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            name=op.f("fk_jetlag_plans_trip_id_trips"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_jetlag_plans_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jetlag_plans")),
    )
    op.create_index(op.f("ix_jetlag_plans_user_id"), "jetlag_plans", ["user_id"], unique=False)
    op.create_table(
        "policy_versions",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "ready",
                "failed",
                "archived",
                name="versionstatus_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("record", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedding_version", sa.String(length=20), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding_calls", sa.Integer(), nullable=False),
        sa.Column("publish_duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_id"],
            ["users.id"],
            name=op.f("fk_policy_versions_approved_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name=op.f("fk_policy_versions_policy_id_policies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            name=op.f("fk_policy_versions_source_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_versions")),
        sa.UniqueConstraint(
            "policy_id", "version_number", name=op.f("uq_policy_versions_policy_id")
        ),
    )
    op.create_index(
        "uq_policy_versions_one_ready",
        "policy_versions",
        ["policy_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ready'"),
    )
    op.create_table(
        "user_cards",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("card_product_id", sa.Uuid(), nullable=False),
        sa.Column("bin", sa.String(length=8), nullable=False),
        sa.Column("nickname", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "match_source",
            sa.Enum(
                "cache",
                "lookup",
                "manual",
                "admin",
                name="matchsource_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("match_confidence", sa.Double(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["card_product_id"],
            ["card_products.id"],
            name=op.f("fk_user_cards_card_product_id_card_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_cards_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_cards")),
        sa.UniqueConstraint(
            "user_id", "card_product_id", "bin", name=op.f("uq_user_cards_user_id")
        ),
    )
    op.create_index(op.f("ix_user_cards_user_id"), "user_cards", ["user_id"], unique=False)
    op.create_table(
        "coverage_translations",
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["policy_versions.id"],
            name=op.f("fk_coverage_translations_policy_version_id_policy_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coverage_translations")),
        sa.UniqueConstraint(
            "policy_version_id", "language", name=op.f("uq_coverage_translations_policy_version_id")
        ),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_key", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "chunk_type",
            sa.Enum(
                "coverage",
                "overview",
                name="chunktype_enum",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("domain", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("customer_facing", sa.Boolean(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(1536), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedding_version", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["policy_versions.id"],
            name=op.f("fk_knowledge_chunks_policy_version_id_policy_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        sa.UniqueConstraint(
            "policy_version_id", "chunk_key", name=op.f("uq_knowledge_chunks_policy_version_id")
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        op.f("ix_knowledge_chunks_policy_id"), "knowledge_chunks", ["policy_id"], unique=False
    )
    op.create_index(
        "ix_knowledge_chunks_version_category",
        "knowledge_chunks",
        ["policy_version_id", "category"],
        unique=False,
    )
    op.create_table(
        "policy_changes",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("field_path", sa.String(length=300), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_policy_changes_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_policy_changes_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name=op.f("fk_policy_changes_policy_id_policies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["policy_versions.id"],
            name=op.f("fk_policy_changes_policy_version_id_policy_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_changes")),
    )
    op.create_index(
        "ix_policy_changes_policy_created",
        "policy_changes",
        ["policy_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_policy_changes_policy_created", table_name="policy_changes")
    op.drop_table("policy_changes")
    op.drop_index("ix_knowledge_chunks_version_category", table_name="knowledge_chunks")
    op.drop_index(op.f("ix_knowledge_chunks_policy_id"), table_name="knowledge_chunks")
    op.drop_index(
        "ix_knowledge_chunks_embedding_hnsw",
        table_name="knowledge_chunks",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("knowledge_chunks")
    op.drop_table("coverage_translations")
    op.drop_index(op.f("ix_user_cards_user_id"), table_name="user_cards")
    op.drop_table("user_cards")
    op.drop_index(
        "uq_policy_versions_one_ready",
        table_name="policy_versions",
        postgresql_where=sa.text("status = 'ready'"),
    )
    op.drop_table("policy_versions")
    op.drop_index(op.f("ix_jetlag_plans_user_id"), table_name="jetlag_plans")
    op.drop_table("jetlag_plans")
    op.drop_table("document_pages")
    op.drop_index(
        "uq_bin_reviews_open", table_name="bin_reviews", postgresql_where=sa.text("status = 'open'")
    )
    op.drop_table("bin_reviews")
    op.drop_table("bin_mappings")
    op.drop_index(op.f("ix_trips_user_id"), table_name="trips")
    op.drop_table("trips")
    op.drop_table("system_settings")
    op.drop_index(
        "uq_prompt_overrides_active",
        table_name="prompt_overrides",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(op.f("ix_prompt_overrides_key"), table_name="prompt_overrides")
    op.drop_table("prompt_overrides")
    op.drop_index(
        "uq_documents_active_sha256",
        table_name="documents",
        postgresql_where=sa.text("status <> 'failed' AND review_status <> 'rejected'"),
    )
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_review_status", table_name="documents")
    op.drop_index(op.f("ix_documents_policy_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_card_products_policy_id"), table_name="card_products")
    op.drop_index("ix_card_products_country_active", table_name="card_products")
    op.drop_table("card_products")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("users")
    op.drop_table("policies")
    op.drop_index("ix_llm_calls_stage_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_document", table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index("ix_health_guides_status", table_name="health_guides")
    op.drop_table("health_guides")
