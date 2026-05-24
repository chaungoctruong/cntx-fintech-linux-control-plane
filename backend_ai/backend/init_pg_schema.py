import logging
import time

import psycopg2
from app.settings import settings

log = logging.getLogger("schema_bootstrap")


CONTROL_PLANE_SCALE_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "idx_broker_accounts_mt5_identity_active",
        "ON broker_accounts(LOWER(TRIM(broker)), LOWER(TRIM(server)), TRIM(login), user_id, id) "
        "WHERE is_active = TRUE AND status <> 'disconnected'",
    ),
    (
        "idx_account_login_reservations_account_latest",
        "ON account_login_reservations(account_id, requested_at DESC, id DESC)",
    ),
    (
        "idx_account_login_reservations_user_account_latest",
        "ON account_login_reservations(user_id, account_id, requested_at DESC, id DESC)",
    ),
    (
        "idx_account_login_reservations_runner_active",
        "ON account_login_reservations(runner_id, status, updated_at DESC, id DESC) "
        "WHERE status IN ('pending', 'dispatched', 'verified')",
    ),
    (
        "idx_account_login_reservations_runner_slot_active",
        "ON account_login_reservations(runner_id, slot_id, updated_at DESC, id DESC) "
        "WHERE status IN ('pending', 'dispatched', 'verified')",
    ),
    (
        "idx_runner_nodes_status_heartbeat",
        "ON runner_nodes(status, last_heartbeat_at DESC)",
    ),
    (
        "idx_runner_slots_runner_status_slot",
        "ON runner_slots(runner_id, status, slot_id)",
    ),
    (
        "idx_runner_slots_runner_current_account",
        "ON runner_slots(runner_id, current_account_id) WHERE current_account_id IS NOT NULL",
    ),
    (
        "idx_account_slot_bindings_sticky_release",
        "ON account_slot_bindings(last_used_at, updated_at, id) "
        "WHERE is_current = TRUE AND is_sticky = TRUE AND binding_state = 'sticky'",
    ),
    (
        "idx_bot_deployments_account_latest",
        "ON bot_deployments(account_id, updated_at DESC, id DESC)",
    ),
    (
        "idx_bot_deployments_runner_active",
        "ON bot_deployments(runner_id, desired_state, status, updated_at DESC) "
        "WHERE desired_state = 'running' "
        "AND status IN ('start_requested', 'starting', 'running', 'stop_requested')",
    ),
    (
        "idx_execution_commands_runner_delivery",
        "ON execution_commands(runner_id, delivery_status, updated_at DESC, id DESC)",
    ),
    (
        "idx_execution_events_runner_type_latest",
        "ON execution_events(runner_id, event_type, created_at DESC, id DESC)",
    ),
    (
        "idx_execution_events_runner_slot_latest",
        "ON execution_events(runner_id, slot_id, created_at DESC, id DESC) WHERE slot_id IS NOT NULL",
    ),
    (
        "idx_runtime_logs_runner_latest",
        "ON runtime_logs(runner_id, created_at DESC, id DESC)",
    ),
    (
        "idx_runtime_logs_trace_id",
        "ON runtime_logs(trace_id) WHERE trace_id IS NOT NULL",
    ),
)


def _schema_print(message: str) -> None:
    if any(getattr(handler, "_cntx_marker", "") for handler in logging.getLogger().handlers):
        log.info("%s", message)
    else:
        print(message, flush=True)


def _schema_bootstrap_verbose() -> bool:
    return bool(getattr(settings, "SCHEMA_BOOTSTRAP_VERBOSE", False))


class _SchemaBootstrapLog:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.steps: list[str] = []

    def connection(self) -> None:
        if _schema_bootstrap_verbose():
            _schema_print(f"🚀 Đang kết nối tới PostgreSQL ({settings.POSTGRES_HOST}:{settings.POSTGRES_PORT})...")

    def step(self, label: str) -> None:
        self.steps.append(str(label))
        if _schema_bootstrap_verbose():
            _schema_print(f"📦 Đang khởi tạo [{label}]...")

    def success(self) -> None:
        elapsed_ms = int((time.time() - self.started_at) * 1000)
        _schema_print(f"✅ Schema bootstrap OK: steps={len(self.steps)} elapsed_ms={elapsed_ms}")

    def error(self, exc: Exception) -> None:
        _schema_print(f"❌ Lỗi khi khởi tạo Database: {exc}")


def _table_columns(cur, table_name: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    return [str(row[0]) for row in (cur.fetchall() or [])]


def _prepare_runner_nodes_table(cur) -> None:
    columns = _table_columns(cur, "runner_nodes")
    if not columns:
        return

    # Legacy runtime used a different runner_nodes shape (node_id/ip_address/...).
    # Keep a backup table name so the control-plane schema can be created cleanly.
    if "runner_id" not in columns and "node_id" in columns:
        backup_name = f"runner_nodes_legacy_{int(time.time())}"
        _schema_print(
            "⚠️ Phát hiện bảng [runner_nodes] legacy "
            f"({', '.join(columns)}). Đổi tên sang [{backup_name}] trước khi tạo schema control plane mới..."
        )
        cur.execute(f'ALTER TABLE runner_nodes RENAME TO "{backup_name}"')


def _create_control_plane_scale_indexes(cur, *, concurrently: bool = False) -> None:
    create_keyword = "CREATE INDEX CONCURRENTLY IF NOT EXISTS" if concurrently else "CREATE INDEX IF NOT EXISTS"
    for index_name, index_body in CONTROL_PLANE_SCALE_INDEXES:
        cur.execute(f"{create_keyword} {index_name} {index_body};")


def init_postgres_schema():
    tracker = _SchemaBootstrapLog()
    tracker.connection()
    
    try:
        conn = psycopg2.connect(
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            database=settings.POSTGRES_DB
        )
        conn.autocommit = True
        cur = conn.cursor()

        _prepare_runner_nodes_table(cur)

        tracker.step("audit_logs")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_json JSONB,
                result TEXT NOT NULL,
                created_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_tg ON audit_logs(telegram_id, created_at);
        """)

        tracker.step("bot_catalog")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_catalog (
                bot_code TEXT PRIMARY KEY,
                bot_name TEXT NOT NULL,
                strategy TEXT,
                tags JSONB DEFAULT '{"tags":[]}',
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL
            );
        """)
        # Migration: bảng cũ có thể thiếu status/superseded_by (CREATE IF NOT EXISTS không thêm cột)
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ACTIVE';")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS superseded_by TEXT;")
        cur.execute("UPDATE bot_catalog SET status = CASE WHEN enabled = TRUE THEN 'ACTIVE' ELSE 'RETIRED' END WHERE status IS NULL OR status = '';")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_catalog_enabled ON bot_catalog(enabled);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_catalog_status ON bot_catalog(status);")

        tracker.step("external_connect_handshakes")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS external_connect_handshakes (
                session_id TEXT PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                state_secret TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                broker_account_id TEXT,
                broker_metadata JSONB,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ech_tg_updated ON external_connect_handshakes(telegram_id, updated_at DESC);
        """)

        tracker.step("broker_connections_ctrader")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS broker_connections_ctrader (
                id BIGSERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'ctrader',
                broker_name TEXT NOT NULL DEFAULT 'default',
                ctid_user_id BIGINT NULL,
                ctid_account_id BIGINT NULL,
                scope TEXT NOT NULL CHECK (scope IN ('accounts', 'trading')),
                status TEXT NOT NULL CHECK (status IN ('connected', 'disconnected', 'error')) DEFAULT 'connected',
                auth_source TEXT NOT NULL DEFAULT 'telegram_miniapp',
                label TEXT NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_error TEXT NULL,
                connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                disconnected_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cur.execute("ALTER TABLE broker_connections_ctrader ALTER COLUMN broker_name SET DEFAULT 'default';")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_broker_connections_ctrader_telegram_id ON broker_connections_ctrader (telegram_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_broker_connections_ctrader_status ON broker_connections_ctrader (status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_broker_connections_ctrader_account ON broker_connections_ctrader (ctid_account_id);")
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_connections_ctrader_active_broker
            ON broker_connections_ctrader (telegram_id, provider, broker_name)
            WHERE disconnected_at IS NULL;
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_connections_ctrader_active_account
            ON broker_connections_ctrader (telegram_id, broker_name, ctid_account_id)
            WHERE disconnected_at IS NULL AND ctid_account_id IS NOT NULL;
            """
        )

        tracker.step("broker_oauth_tokens_ctrader")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS broker_oauth_tokens_ctrader (
                id BIGSERIAL PRIMARY KEY,
                broker_connection_id BIGINT NOT NULL REFERENCES broker_connections_ctrader(id) ON DELETE CASCADE,
                token_type TEXT NULL,
                scope TEXT NOT NULL CHECK (scope IN ('accounts', 'trading')),
                access_token_encrypted TEXT NOT NULL,
                refresh_token_encrypted TEXT NULL,
                expires_at TIMESTAMPTZ NULL,
                last_refreshed_at TIMESTAMPTZ NULL,
                refresh_fail_count INTEGER NOT NULL DEFAULT 0,
                last_refresh_error TEXT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_broker_oauth_tokens_ctrader_connection_id ON broker_oauth_tokens_ctrader (broker_connection_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_broker_oauth_tokens_ctrader_expires_at ON broker_oauth_tokens_ctrader (expires_at);")
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_oauth_tokens_ctrader_active
            ON broker_oauth_tokens_ctrader (broker_connection_id)
            WHERE is_active = TRUE;
            """
        )

        tracker.step("bot_runs_ctrader")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_runs_ctrader (
                id BIGSERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL,
                broker_connection_id BIGINT NULL REFERENCES broker_connections_ctrader(id) ON DELETE SET NULL,
                ctid_account_id BIGINT NULL,
                strategy_key TEXT NOT NULL DEFAULT 'gold_default_v1',
                desired_state TEXT NOT NULL CHECK (desired_state IN ('stopped', 'running')) DEFAULT 'stopped',
                runtime_state TEXT NOT NULL CHECK (
                    runtime_state IN (
                        'stopped',
                        'waiting_connection',
                        'waiting_trading_scope',
                        'waiting_account',
                        'running',
                        'error'
                    )
                ) DEFAULT 'stopped',
                config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                next_action TEXT NULL CHECK (
                    next_action IN (
                        'connect_broker',
                        'reconnect_with_trading_scope',
                        'set_ctid_account_id',
                        'start_bot',
                        'stop_bot',
                        'reconnect_broker',
                        'inspect_error'
                    )
                ),
                last_error TEXT NULL,
                last_started_at TIMESTAMPTZ NULL,
                last_stopped_at TIMESTAMPTZ NULL,
                last_heartbeat_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cur.execute("ALTER TABLE bot_runs_ctrader ADD COLUMN IF NOT EXISTS ctid_account_id BIGINT NULL;")
        cur.execute(
            """
            UPDATE bot_runs_ctrader b
            SET ctid_account_id = c.ctid_account_id
            FROM broker_connections_ctrader c
            WHERE b.broker_connection_id = c.id
              AND b.ctid_account_id IS NULL
              AND c.ctid_account_id IS NOT NULL;
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_runs_ctrader_telegram_id ON bot_runs_ctrader (telegram_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_runs_ctrader_account_id ON bot_runs_ctrader (ctid_account_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_runs_ctrader_runtime_state ON bot_runs_ctrader (runtime_state);")
        cur.execute("DROP INDEX IF EXISTS uq_bot_runs_ctrader_telegram;")
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_runs_ctrader_account
            ON bot_runs_ctrader (telegram_id, ctid_account_id)
            WHERE ctid_account_id IS NOT NULL;
            """
        )

        tracker.step("bot_events_ctrader")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_events_ctrader (
                id BIGSERIAL PRIMARY KEY,
                bot_run_id BIGINT NOT NULL REFERENCES bot_runs_ctrader(id) ON DELETE CASCADE,
                telegram_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_ctrader_bot_run_id ON bot_events_ctrader (bot_run_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_ctrader_telegram_id ON bot_events_ctrader (telegram_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_ctrader_created_at ON bot_events_ctrader (created_at DESC);")

        tracker.step("users")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                telegram_id TEXT NOT NULL UNIQUE,
                username TEXT NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
        """)
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_webhooks (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                secret_hex TEXT NOT NULL,
                event_filter JSONB NOT NULL DEFAULT '[]'::jsonb,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_delivered_at TIMESTAMPTZ NULL,
                last_error TEXT NULL,
                fail_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_user_webhooks_user_id ON user_webhooks(user_id);
        """)

        tracker.step("audit_logs_extensions")
        cur.execute("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_id BIGINT NULL;")
        cur.execute("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS account_id BIGINT NULL;")
        cur.execute("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS deployment_id BIGINT NULL;")
        cur.execute("ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS trace_id TEXT NULL;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_account_id ON audit_logs(account_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_deployment_id ON audit_logs(deployment_id);")

        tracker.step("miniapp_terms_consents")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS miniapp_terms_consents (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                telegram_id TEXT NOT NULL,
                consent_version TEXT NOT NULL,
                accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ip_address TEXT NULL,
                user_agent TEXT NULL,
                source TEXT NOT NULL DEFAULT 'miniapp',
                partner_id TEXT NULL,
                token_id TEXT NULL,
                checkbox_1 BOOLEAN NOT NULL DEFAULT FALSE,
                checkbox_2 BOOLEAN NOT NULL DEFAULT FALSE,
                checkbox_3 BOOLEAN NOT NULL DEFAULT FALSE,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(telegram_id, consent_version)
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_miniapp_terms_consents_user_version
            ON miniapp_terms_consents(user_id, consent_version, accepted_at DESC);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_miniapp_terms_consents_partner
            ON miniapp_terms_consents(partner_id, token_id)
            WHERE partner_id IS NOT NULL OR token_id IS NOT NULL;
        """)

        tracker.step("ai_logs")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_logs (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
                created_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_logs_user_created ON ai_logs(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_logs_status_created ON ai_logs(status, created_at DESC);
        """)

        tracker.step("ai_chat_memory_cache")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_messages (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'chat',
                status TEXT NOT NULL DEFAULT 'done',
                source TEXT NOT NULL DEFAULT 'executor',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_user_created
            ON ai_chat_messages(user_id, created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_hash
            ON ai_chat_messages(content_hash);
            CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_mode_created
            ON ai_chat_messages(mode, created_at DESC);

            CREATE TABLE IF NOT EXISTS ai_chat_answer_cache (
                id BIGSERIAL PRIMARY KEY,
                scope TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'chat',
                question_hash TEXT NOT NULL,
                normalized_question TEXT NOT NULL,
                sample_question TEXT NOT NULL,
                answer TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'executor',
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                hit_count BIGINT NOT NULL DEFAULT 0,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                last_hit_at BIGINT NULL,
                UNIQUE(scope, mode, question_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_ai_chat_answer_cache_scope_updated
            ON ai_chat_answer_cache(scope, mode, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_chat_answer_cache_hash
            ON ai_chat_answer_cache(question_hash);
            CREATE INDEX IF NOT EXISTS idx_ai_chat_answer_cache_enabled_updated
            ON ai_chat_answer_cache(enabled, updated_at DESC);
        """)

        tracker.step("ai_platform_knowledge")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_platform_knowledge_sources (
                id BIGSERIAL PRIMARY KEY,
                source_key TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL DEFAULT 'manual',
                title TEXT NOT NULL,
                url TEXT NULL,
                trust_level INTEGER NOT NULL DEFAULT 50 CHECK(trust_level >= 0 AND trust_level <= 100),
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                last_ingested_at BIGINT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_sources_enabled
            ON ai_platform_knowledge_sources(enabled, trust_level DESC, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_sources_type
            ON ai_platform_knowledge_sources(source_type, enabled);

            CREATE TABLE IF NOT EXISTS ai_platform_knowledge_chunks (
                id BIGSERIAL PRIMARY KEY,
                source_key TEXT NOT NULL REFERENCES ai_platform_knowledge_sources(source_key) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NULL,
                content TEXT NOT NULL,
                normalized_content TEXT NOT NULL,
                trust_level INTEGER NOT NULL DEFAULT 50 CHECK(trust_level >= 0 AND trust_level <= 100),
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                UNIQUE(source_key, content_hash)
            );
            ALTER TABLE ai_platform_knowledge_chunks
                ADD COLUMN IF NOT EXISTS embedding_json JSONB NULL,
                ADD COLUMN IF NOT EXISTS embedding_model TEXT NULL,
                ADD COLUMN IF NOT EXISTS embedding_dim INTEGER NULL,
                ADD COLUMN IF NOT EXISTS embedding_updated_at BIGINT NULL;
            CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_enabled
            ON ai_platform_knowledge_chunks(enabled, trust_level DESC, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_source
            ON ai_platform_knowledge_chunks(source_key, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_hash
            ON ai_platform_knowledge_chunks(content_hash);
            CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_embedding_model
            ON ai_platform_knowledge_chunks(embedding_model, embedding_updated_at DESC)
            WHERE embedding_json IS NOT NULL;
        """)
        cur.execute("""
            DO $$
            BEGIN
                BEGIN
                    CREATE EXTENSION IF NOT EXISTS vector;
                EXCEPTION WHEN OTHERS THEN
                    RAISE NOTICE 'pgvector extension unavailable, semantic retrieval will use JSON fallback only: %', SQLERRM;
                END;

                IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                    EXECUTE 'ALTER TABLE ai_platform_knowledge_chunks ADD COLUMN IF NOT EXISTS embedding vector(1024)';
                    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_ai_platform_knowledge_chunks_pgvector_model ON ai_platform_knowledge_chunks(embedding_model, embedding_updated_at DESC) WHERE embedding IS NOT NULL';
                END IF;
            END $$;
        """)

        tracker.step("ai_training_pipeline")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_training_examples (
                id BIGSERIAL PRIMARY KEY,
                example_key TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL DEFAULT 'chat',
                source_ref TEXT NULL,
                user_id TEXT NULL,
                scope TEXT NOT NULL DEFAULT 'platform',
                mode TEXT NOT NULL DEFAULT 'chat',
                prompt TEXT NOT NULL,
                completion TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                completion_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'approved', 'rejected', 'exported', 'trained')),
                quality_score NUMERIC(5,4) NOT NULL DEFAULT 0,
                safety_status TEXT NOT NULL DEFAULT 'safe'
                    CHECK(safety_status IN ('safe', 'redacted', 'blocked')),
                skip_reason TEXT NULL,
                redaction_count INTEGER NOT NULL DEFAULT 0,
                reviewer_id TEXT NULL,
                reviewed_at BIGINT NULL,
                exported_at BIGINT NULL,
                trained_at BIGINT NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_training_examples_status_updated
            ON ai_training_examples(status, updated_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_training_examples_mode_status
            ON ai_training_examples(mode, status, quality_score DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_training_examples_prompt_hash
            ON ai_training_examples(prompt_hash);
            CREATE INDEX IF NOT EXISTS idx_ai_training_examples_source_created
            ON ai_training_examples(source, created_at DESC);

            CREATE TABLE IF NOT EXISTS ai_training_exports (
                id BIGSERIAL PRIMARY KEY,
                export_key TEXT NOT NULL UNIQUE,
                format TEXT NOT NULL DEFAULT 'jsonl',
                output_path TEXT NOT NULL,
                checksum TEXT NOT NULL,
                example_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'created',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_training_exports_created
            ON ai_training_exports(created_at DESC);

            CREATE TABLE IF NOT EXISTS ai_model_versions (
                id BIGSERIAL PRIMARY KEY,
                model_key TEXT NOT NULL UNIQUE,
                base_model TEXT NOT NULL,
                adapter_path TEXT NULL,
                dataset_export_key TEXT NULL,
                status TEXT NOT NULL DEFAULT 'candidate'
                    CHECK(status IN ('candidate', 'staging', 'active', 'retired', 'failed')),
                metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at BIGINT NOT NULL,
                activated_at BIGINT NULL,
                retired_at BIGINT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_model_versions_status_created
            ON ai_model_versions(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS ai_model_eval_runs (
                id BIGSERIAL PRIMARY KEY,
                run_key TEXT NOT NULL UNIQUE,
                model_key TEXT NOT NULL,
                dataset_export_key TEXT NULL,
                eval_type TEXT NOT NULL DEFAULT 'dataset_static',
                status TEXT NOT NULL DEFAULT 'created'
                    CHECK(status IN ('created', 'running', 'completed', 'failed')),
                example_count INTEGER NOT NULL DEFAULT 0,
                score NUMERIC(6,5) NOT NULL DEFAULT 0,
                pass_threshold NUMERIC(6,5) NOT NULL DEFAULT 0.8,
                passed BOOLEAN NOT NULL DEFAULT FALSE,
                metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at BIGINT NOT NULL,
                completed_at BIGINT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_model_eval_runs_model_created
            ON ai_model_eval_runs(model_key, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_model_eval_runs_dataset_created
            ON ai_model_eval_runs(dataset_export_key, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_model_eval_runs_status_created
            ON ai_model_eval_runs(status, created_at DESC);
        """)

        tracker.step("bot_catalog_extensions")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS display_name TEXT;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'other';")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS version TEXT DEFAULT '0.1.0';")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS profile_class TEXT DEFAULT 'normal';")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS runtime_entry TEXT;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS required_params JSONB DEFAULT '[]'::jsonb;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS risk_profile JSONB DEFAULT '{}'::jsonb;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS indicator_requirements JSONB DEFAULT '[]'::jsonb;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS strategy_tags JSONB DEFAULT '[]'::jsonb;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS resource_hints JSONB DEFAULT '{}'::jsonb;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS supports_demo BOOLEAN NOT NULL DEFAULT TRUE;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS supports_live BOOLEAN NOT NULL DEFAULT TRUE;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS default_config_path TEXT NULL;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS runtime_env JSONB DEFAULT '{}'::jsonb;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS checksum TEXT NULL;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS source_path TEXT NULL;")
        cur.execute("ALTER TABLE bot_catalog ADD COLUMN IF NOT EXISTS metadata_json JSONB DEFAULT '{}'::jsonb;")
        cur.execute("UPDATE bot_catalog SET display_name = COALESCE(NULLIF(display_name, ''), bot_name) WHERE display_name IS NULL OR display_name = '';")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_catalog_profile_class ON bot_catalog(profile_class);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_catalog_language ON bot_catalog(language);")

        tracker.step("bot_versions")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_versions (
                id BIGSERIAL PRIMARY KEY,
                bot_code TEXT NOT NULL REFERENCES bot_catalog(bot_code) ON DELETE CASCADE,
                version TEXT NOT NULL,
                checksum TEXT NULL,
                source_path TEXT NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_versions_code_version
                ON bot_versions(bot_code, version);
        """)

        tracker.step("broker_accounts")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS broker_accounts (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                broker TEXT NOT NULL,
                server TEXT NOT NULL,
                login TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'connected'
                    CHECK (status IN ('pending_login', 'connected', 'login_failed', 'disconnected')),
                label TEXT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_error TEXT NULL,
                login_requested_at TIMESTAMPTZ NULL,
                verified_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_accounts_user_identity
                ON broker_accounts(user_id, broker, server, login);
            CREATE INDEX IF NOT EXISTS idx_broker_accounts_user_id ON broker_accounts(user_id);
            CREATE INDEX IF NOT EXISTS idx_broker_accounts_status ON broker_accounts(status);
        """)
        cur.execute("ALTER TABLE broker_accounts ADD COLUMN IF NOT EXISTS login_requested_at TIMESTAMPTZ NULL;")
        cur.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'broker_accounts'
                      AND column_name = 'verification_requested_at'
                ) THEN
                    EXECUTE 'UPDATE broker_accounts SET login_requested_at = COALESCE(login_requested_at, verification_requested_at) WHERE login_requested_at IS NULL';
                END IF;
            END $$;
        """)
        # Risk policy per account: JSONB de moi field optional (daily_loss_limit_usd,
        # daily_loss_limit_percent, auto_stop_on_breach, ...). UI co the noi rong sau ma
        # khong can migration moi.
        cur.execute("""
            ALTER TABLE broker_accounts
                DROP CONSTRAINT IF EXISTS broker_accounts_status_check;
        """)
        cur.execute("""
            UPDATE broker_accounts
            SET status = CASE
                WHEN status = 'pending_verification' THEN 'pending_login'
                WHEN status = 'verification_failed' THEN 'login_failed'
                ELSE status
            END
            WHERE status IN ('pending_verification', 'verification_failed');
        """)
        cur.execute("""
            ALTER TABLE broker_accounts
                ADD CONSTRAINT broker_accounts_status_check
                CHECK (status IN ('pending_login', 'connected', 'login_failed', 'disconnected'));
        """)
        cur.execute("ALTER TABLE broker_accounts ADD COLUMN IF NOT EXISTS risk_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb;")
        cur.execute("ALTER TABLE broker_accounts ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_broker_accounts_sort_order ON broker_accounts(user_id, sort_order, id);")

        tracker.step("tradingview_signal_subscriptions")
        # Subscription map: which broker_account follows which TradingView
        # signal. Backend uses this for fan-out dispatch on /public/tradingview/
        # broadcast: 1 signal -> SELECT subscribers -> batch publish.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tradingview_signal_subscriptions (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                signal_id TEXT NOT NULL,
                bot_code TEXT NULL,
                volume_override DOUBLE PRECISION NULL,
                priority INTEGER NOT NULL DEFAULT 50,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (account_id, signal_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tv_sig_subs_signal_enabled
                ON tradingview_signal_subscriptions(signal_id) WHERE enabled = TRUE;
            CREATE INDEX IF NOT EXISTS idx_tv_sig_subs_account
                ON tradingview_signal_subscriptions(account_id);
            ALTER TABLE tradingview_signal_subscriptions
                ADD COLUMN IF NOT EXISTS bot_code TEXT NULL;
            CREATE INDEX IF NOT EXISTS idx_tv_sig_subs_bot_code
                ON tradingview_signal_subscriptions(bot_code) WHERE bot_code IS NOT NULL;
        """)

        tracker.step("account_credentials_encrypted")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS account_credentials_encrypted (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                password_encrypted TEXT NOT NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_account_credentials_account_id
                ON account_credentials_encrypted(account_id);
        """)

        tracker.step("runner_nodes")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runner_nodes (
                id BIGSERIAL PRIMARY KEY,
                runner_id TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                host TEXT NULL,
                status TEXT NOT NULL DEFAULT 'online'
                    CHECK (status IN ('online', 'degraded', 'offline', 'draining')),
                supported_profiles JSONB NOT NULL DEFAULT '[]'::jsonb,
                capability_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                capabilities_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                max_slots INTEGER NOT NULL DEFAULT 1,
                last_registered_at TIMESTAMPTZ NULL,
                last_heartbeat_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_runner_nodes_status ON runner_nodes(status);
            CREATE INDEX IF NOT EXISTS idx_runner_nodes_last_heartbeat ON runner_nodes(last_heartbeat_at);
        """)
        cur.execute("ALTER TABLE runner_nodes ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;")

        tracker.step("runner_slots")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runner_slots (
                id BIGSERIAL PRIMARY KEY,
                runner_id TEXT NOT NULL REFERENCES runner_nodes(runner_id) ON DELETE CASCADE,
                slot_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready'
                    CHECK (status IN ('ready', 'allocated', 'degraded', 'broken', 'disabled')),
                allowed_profile_classes JSONB NOT NULL DEFAULT '[]'::jsonb,
                current_account_id BIGINT NULL REFERENCES broker_accounts(id) ON DELETE SET NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_heartbeat_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_runner_slots_runner_slot
                ON runner_slots(runner_id, slot_id);
            CREATE INDEX IF NOT EXISTS idx_runner_slots_status ON runner_slots(status);
            CREATE INDEX IF NOT EXISTS idx_runner_slots_current_account_id ON runner_slots(current_account_id);
        """)
        cur.execute("""
            CREATE OR REPLACE FUNCTION enforce_runner_slot_node_cap()
            RETURNS trigger AS $$
            DECLARE
                slot_number INTEGER;
            BEGIN
                IF COALESCE(NULLIF(SUBSTRING(NEW.slot_id FROM '([0-9]+)$'), ''), '') <> '' THEN
                    slot_number := CAST(SUBSTRING(NEW.slot_id FROM '([0-9]+)$') AS INTEGER);
                    IF slot_number > 12 THEN
                        NEW.status := 'disabled';
                        NEW.current_account_id := NULL;
                        NEW.metadata_json := jsonb_strip_nulls(
                            COALESCE(NEW.metadata_json, '{}'::jsonb)
                            || jsonb_build_object(
                                'disabled_by_node_slot_cap', TRUE,
                                'node_slot_cap', 12,
                                'disabled_reason', 'node_slot_cap_12',
                                'available_for_new_account', FALSE,
                                'control_plane_state', 'disabled',
                                'current_control_plane_state', 'disabled'
                            )
                        );
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            DROP TRIGGER IF EXISTS trg_enforce_runner_slot_node_cap ON runner_slots;
            CREATE TRIGGER trg_enforce_runner_slot_node_cap
            BEFORE INSERT OR UPDATE ON runner_slots
            FOR EACH ROW
            EXECUTE FUNCTION enforce_runner_slot_node_cap();
        """)

        tracker.step("account_login_reservations")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS account_login_reservations (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                runner_id TEXT NOT NULL REFERENCES runner_nodes(runner_id) ON DELETE CASCADE,
                slot_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'dispatched', 'verified', 'failed', 'expired', 'released', 'claimed', 'cancelled')),
                command_id TEXT NULL,
                trace_id TEXT NULL,
                redis_stream_id TEXT NULL,
                last_error TEXT NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                dispatched_at TIMESTAMPTZ NULL,
                completed_at TIMESTAMPTZ NULL,
                expires_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_account_login_reservations_account_latest
                ON account_login_reservations(account_id, requested_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_account_login_reservations_status_expiry
                ON account_login_reservations(status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_account_login_reservations_runner_slot
                ON account_login_reservations(runner_id, slot_id, updated_at DESC, id DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_account_login_reservations_active_account
                ON account_login_reservations(account_id)
                WHERE status IN ('pending', 'dispatched', 'verified');
            CREATE UNIQUE INDEX IF NOT EXISTS uq_account_login_reservations_active_slot
                ON account_login_reservations(runner_id, slot_id)
                WHERE status IN ('pending', 'dispatched', 'verified');
        """)

        tracker.step("account_slot_bindings")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS account_slot_bindings (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                runner_id TEXT NOT NULL REFERENCES runner_nodes(runner_id) ON DELETE CASCADE,
                slot_id TEXT NOT NULL,
                binding_state TEXT NOT NULL DEFAULT 'sticky'
                    CHECK (binding_state IN ('sticky', 'active', 'released', 'broken')),
                is_sticky BOOLEAN NOT NULL DEFAULT TRUE,
                is_current BOOLEAN NOT NULL DEFAULT TRUE,
                last_used_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_account_slot_bindings_account_id ON account_slot_bindings(account_id);
            CREATE INDEX IF NOT EXISTS idx_account_slot_bindings_runner_slot ON account_slot_bindings(runner_id, slot_id);
            CREATE INDEX IF NOT EXISTS idx_account_slot_bindings_sticky_release
                ON account_slot_bindings(last_used_at, updated_at, id)
                WHERE is_current = TRUE AND is_sticky = TRUE AND binding_state = 'sticky';
            CREATE UNIQUE INDEX IF NOT EXISTS uq_account_slot_bindings_current_account
                ON account_slot_bindings(account_id) WHERE is_current = TRUE;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_account_slot_bindings_current_slot
                ON account_slot_bindings(runner_id, slot_id) WHERE is_current = TRUE;
        """)

        tracker.step("bot_deployments")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_deployments (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                bot_code TEXT NOT NULL REFERENCES bot_catalog(bot_code) ON DELETE RESTRICT,
                bot_name TEXT NOT NULL,
                profile_class TEXT NOT NULL DEFAULT 'normal',
                mode TEXT NOT NULL DEFAULT 'live' CHECK (mode IN ('live', 'paper')),
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'start_requested', 'starting', 'running', 'stop_requested', 'stopped', 'failed', 'blocked', 'queued')),
                desired_state TEXT NOT NULL DEFAULT 'stopped'
                    CHECK (desired_state IN ('running', 'stopped')),
                intent_seq INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT FALSE,
                runner_id TEXT NULL REFERENCES runner_nodes(runner_id) ON DELETE SET NULL,
                slot_id TEXT NULL,
                binding_id BIGINT NULL REFERENCES account_slot_bindings(id) ON DELETE SET NULL,
                config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                health_status TEXT NULL,
                last_error TEXT NULL,
                last_runner_recovery_reason TEXT NULL,
                last_runner_recovery_at TIMESTAMPTZ NULL,
                runner_recovery_first_seen_at TIMESTAMPTZ NULL,
                runner_recovery_last_seen_at TIMESTAMPTZ NULL,
                runner_recovery_attempt_count INTEGER NOT NULL DEFAULT 0,
                runner_recovery_window_started_at TIMESTAMPTZ NULL,
                runner_recovery_cooldown_until TIMESTAMPTZ NULL,
                runner_recovery_in_flight BOOLEAN NOT NULL DEFAULT FALSE,
                runner_recovery_in_flight_since TIMESTAMPTZ NULL,
                runner_recovery_last_command_id TEXT NULL,
                runner_recovery_last_command_at TIMESTAMPTZ NULL,
                trace_id TEXT NULL,
                started_at TIMESTAMPTZ NULL,
                stopped_at TIMESTAMPTZ NULL,
                last_heartbeat_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_bot_deployments_account_id ON bot_deployments(account_id);
            CREATE INDEX IF NOT EXISTS idx_bot_deployments_user_id ON bot_deployments(user_id);
            CREATE INDEX IF NOT EXISTS idx_bot_deployments_status ON bot_deployments(status);
            CREATE INDEX IF NOT EXISTS idx_bot_deployments_runner_slot ON bot_deployments(runner_id, slot_id);
        """)
        cur.execute("ALTER TABLE bot_deployments ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';")
        cur.execute(
            "ALTER TABLE bot_deployments ADD COLUMN IF NOT EXISTS intent_seq INTEGER NOT NULL DEFAULT 0;"
        )
        cur.execute("""
            ALTER TABLE bot_deployments
                ADD COLUMN IF NOT EXISTS last_runner_recovery_reason TEXT NULL,
                ADD COLUMN IF NOT EXISTS last_runner_recovery_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS runner_recovery_first_seen_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS runner_recovery_last_seen_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS runner_recovery_attempt_count INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS runner_recovery_window_started_at TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS runner_recovery_cooldown_until TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS runner_recovery_in_flight BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS runner_recovery_in_flight_since TIMESTAMPTZ NULL,
                ADD COLUMN IF NOT EXISTS runner_recovery_last_command_id TEXT NULL,
                ADD COLUMN IF NOT EXISTS runner_recovery_last_command_at TIMESTAMPTZ NULL;
        """)
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'ck_bot_deployments_mode'
                      AND conrelid = 'bot_deployments'::regclass
                ) THEN
                    ALTER TABLE bot_deployments
                        ADD CONSTRAINT ck_bot_deployments_mode
                        CHECK (mode IN ('live', 'paper'));
                END IF;
            END $$;
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_deployments_active_account
                ON bot_deployments(account_id)
                WHERE status IN ('start_requested', 'starting', 'running', 'stop_requested');
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_deployments_active_user
                ON bot_deployments(user_id)
                WHERE status IN ('start_requested', 'starting', 'running', 'stop_requested');
        """)

        tracker.step("execution_commands")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS execution_commands (
                id BIGSERIAL PRIMARY KEY,
                command_id TEXT NOT NULL UNIQUE,
                command_type TEXT NOT NULL
                    CHECK (command_type IN ('RESERVE_OR_LOGIN_SLOT', 'START_BOT', 'STOP_BOT', 'UPDATE_BOT_CONFIG', 'PLACE_ORDER', 'MODIFY_ORDER', 'CLOSE_ORDER', 'SYNC_STATE')),
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                deployment_id BIGINT NULL REFERENCES bot_deployments(id) ON DELETE CASCADE,
                bot_id TEXT NOT NULL,
                runner_id TEXT NOT NULL REFERENCES runner_nodes(runner_id) ON DELETE CASCADE,
                slot_id TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 50,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                delivery_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (delivery_status IN ('pending', 'queued', 'dispatched', 'acknowledged', 'failed')),
                queue_name TEXT NOT NULL,
                redis_stream_id TEXT NULL,
                trace_id TEXT NULL,
                last_error TEXT NULL,
                dispatched_at TIMESTAMPTZ NULL,
                acknowledged_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_execution_commands_account_id ON execution_commands(account_id);
            CREATE INDEX IF NOT EXISTS idx_execution_commands_deployment_id ON execution_commands(deployment_id);
            CREATE INDEX IF NOT EXISTS idx_execution_commands_delivery_status ON execution_commands(delivery_status);
            CREATE INDEX IF NOT EXISTS idx_execution_commands_trace_id ON execution_commands(trace_id);
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_commands_trace_identity
                ON execution_commands(account_id, deployment_id, command_type, trace_id)
                WHERE trace_id IS NOT NULL;
        """)
        cur.execute("ALTER TABLE execution_commands ALTER COLUMN deployment_id DROP NOT NULL;")
        cur.execute("""
            ALTER TABLE execution_commands
                DROP CONSTRAINT IF EXISTS execution_commands_command_type_check;
        """)
        cur.execute("""
            ALTER TABLE execution_commands
                ADD CONSTRAINT execution_commands_command_type_check
                CHECK (command_type IN (
                    'RESERVE_OR_LOGIN_SLOT',
                    'START_BOT', 'STOP_BOT', 'UPDATE_BOT_CONFIG',
                    'PLACE_ORDER', 'MODIFY_ORDER', 'CLOSE_ORDER', 'SYNC_STATE'
                ));
        """)

        tracker.step("execution_events")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS execution_events (
                id BIGSERIAL PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL
                    CHECK (event_type IN ('HEARTBEAT', 'LOGIN_SLOT_VERIFIED', 'LOGIN_SLOT_FAILED', 'LOGIN_SLOT_RELEASED', 'BOT_STARTED', 'BOT_STOP_REQUESTED', 'BOT_WORKER_STOPPED', 'BOT_STOPPED', 'SIGNAL_EXECUTOR_PREPARING', 'SIGNAL_EXECUTOR_READY', 'SIGNAL_EXECUTOR_STOPPING', 'SIGNAL_EXECUTOR_STOPPED', 'BOT_LISTENING', 'ORDER_SENT', 'ORDER_FILLED', 'ORDER_REJECTED', 'POSITION_UPDATED', 'SLOT_DEGRADED', 'SLOT_BROKEN', 'RUNTIME_LOG', 'SLOT_STATE_CHANGED', 'SLOT_TERMINAL_KILL_BEGIN', 'SLOT_TERMINAL_KILL_DONE', 'COMMAND_REJECTED')),
                account_id BIGINT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                deployment_id BIGINT NULL REFERENCES bot_deployments(id) ON DELETE CASCADE,
                bot_id TEXT NULL,
                runner_id TEXT NOT NULL REFERENCES runner_nodes(runner_id) ON DELETE CASCADE,
                slot_id TEXT NULL,
                command_id TEXT NULL,
                severity TEXT NOT NULL DEFAULT 'info'
                    CHECK (severity IN ('info', 'warning', 'error')),
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                trace_id TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_execution_events_account_id ON execution_events(account_id);
            CREATE INDEX IF NOT EXISTS idx_execution_events_deployment_id ON execution_events(deployment_id);
            CREATE INDEX IF NOT EXISTS idx_execution_events_runner_id ON execution_events(runner_id);
            CREATE INDEX IF NOT EXISTS idx_execution_events_created_at ON execution_events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_execution_events_command_id ON execution_events(command_id);
            CREATE INDEX IF NOT EXISTS idx_execution_events_trace_id ON execution_events(trace_id);
        """)
        cur.execute("""
            ALTER TABLE execution_events
                ADD COLUMN IF NOT EXISTS command_id TEXT NULL;
        """)
        cur.execute("""
            ALTER TABLE execution_events
                DROP CONSTRAINT IF EXISTS execution_events_event_type_check;
        """)
        cur.execute("""
            ALTER TABLE execution_events
                ADD CONSTRAINT execution_events_event_type_check
                CHECK (event_type IN (
                    'HEARTBEAT',
                    'LOGIN_SLOT_VERIFIED', 'LOGIN_SLOT_FAILED', 'LOGIN_SLOT_RELEASED',
                    'BOT_STARTED',
                    'BOT_STOP_REQUESTED', 'BOT_WORKER_STOPPED', 'BOT_STOPPED',
                    'SIGNAL_EXECUTOR_PREPARING', 'SIGNAL_EXECUTOR_READY',
                    'SIGNAL_EXECUTOR_STOPPING', 'SIGNAL_EXECUTOR_STOPPED',
                    'BOT_LISTENING',
                    'ORDER_SENT', 'ORDER_FILLED', 'ORDER_REJECTED',
                    'POSITION_UPDATED', 'SLOT_DEGRADED', 'SLOT_BROKEN',
                    'RUNTIME_LOG', 'SLOT_STATE_CHANGED',
                    'SLOT_TERMINAL_KILL_BEGIN', 'SLOT_TERMINAL_KILL_DONE',
                    'COMMAND_REJECTED'
                ));
        """)

        tracker.step("execution_audit")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS execution_audit (
                id BIGSERIAL PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                command_id TEXT NULL,
                trace_id TEXT NULL,
                account_id BIGINT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                deployment_id BIGINT NULL REFERENCES bot_deployments(id) ON DELETE CASCADE,
                runner_id TEXT NULL REFERENCES runner_nodes(runner_id) ON DELETE SET NULL,
                slot_id TEXT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                audit_status TEXT NOT NULL DEFAULT 'recorded',
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                source_stream_id TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_execution_audit_account_id ON execution_audit(account_id);
            CREATE INDEX IF NOT EXISTS idx_execution_audit_deployment_id ON execution_audit(deployment_id);
            CREATE INDEX IF NOT EXISTS idx_execution_audit_command_id ON execution_audit(command_id);
            CREATE INDEX IF NOT EXISTS idx_execution_audit_trace_id ON execution_audit(trace_id);
            CREATE INDEX IF NOT EXISTS idx_execution_audit_created_at ON execution_audit(created_at DESC);
        """)

        tracker.step("account_state_snapshots")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS account_state_snapshots (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                deployment_id BIGINT NULL REFERENCES bot_deployments(id) ON DELETE SET NULL,
                runner_id TEXT NULL REFERENCES runner_nodes(runner_id) ON DELETE SET NULL,
                slot_id TEXT NULL,
                connection_status TEXT NOT NULL DEFAULT 'pending_login',
                pnl NUMERIC NULL,
                balance NUMERIC NULL,
                equity NUMERIC NULL,
                free_margin NUMERIC NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                heartbeat_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_account_state_snapshots_account
                ON account_state_snapshots(account_id);
            CREATE INDEX IF NOT EXISTS idx_account_state_snapshots_deployment_id ON account_state_snapshots(deployment_id);
        """)

        tracker.step("position_snapshots")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS position_snapshots (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                deployment_id BIGINT NULL REFERENCES bot_deployments(id) ON DELETE CASCADE,
                position_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NULL,
                volume NUMERIC NULL,
                entry_price NUMERIC NULL,
                mark_price NUMERIC NULL,
                pnl NUMERIC NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_position_snapshots_identity
                ON position_snapshots(account_id, deployment_id, position_key);
            CREATE INDEX IF NOT EXISTS idx_position_snapshots_account_id ON position_snapshots(account_id);
        """)

        tracker.step("runtime_logs")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runtime_logs (
                id BIGSERIAL PRIMARY KEY,
                account_id BIGINT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                deployment_id BIGINT NULL REFERENCES bot_deployments(id) ON DELETE CASCADE,
                runner_id TEXT NULL REFERENCES runner_nodes(runner_id) ON DELETE SET NULL,
                slot_id TEXT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                trace_id TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_logs_account_id ON runtime_logs(account_id);
            CREATE INDEX IF NOT EXISTS idx_runtime_logs_deployment_id ON runtime_logs(deployment_id);
            CREATE INDEX IF NOT EXISTS idx_runtime_logs_created_at ON runtime_logs(created_at DESC);
        """)

        # Composite/scale indexes phụ thuộc runtime_logs nên phải tạo sau khi bảng tồn tại
        # (trước đây gọi sớm ở giữa init -> fail trên DB mới vì runtime_logs chưa được tạo).
        tracker.step("control_plane_scale_indexes")
        _create_control_plane_scale_indexes(cur)

        tracker.step("runner_bot_state_records")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runner_bot_state_records (
                id BIGSERIAL PRIMARY KEY,
                bot_id TEXT NOT NULL,
                schema_name TEXT NOT NULL DEFAULT 'gsalgo_backend_state.v1',
                operation TEXT NOT NULL,
                record_type TEXT NOT NULL,
                record_key TEXT NOT NULL,
                account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
                deployment_id BIGINT NOT NULL REFERENCES bot_deployments(id) ON DELETE CASCADE,
                runner_id TEXT NOT NULL REFERENCES runner_nodes(runner_id) ON DELETE CASCADE,
                slot_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'recorded',
                symbol TEXT NULL,
                side TEXT NULL,
                realized_pnl NUMERIC NULL,
                occurred_at TIMESTAMPTZ NULL,
                closed_at TIMESTAMPTZ NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_runner_bot_state_record
                ON runner_bot_state_records(bot_id, record_type, account_id, deployment_id, record_key);
            CREATE INDEX IF NOT EXISTS idx_runner_bot_state_context
                ON runner_bot_state_records(account_id, deployment_id, bot_id, record_type, status);
            CREATE INDEX IF NOT EXISTS idx_runner_bot_state_runner
                ON runner_bot_state_records(runner_id, slot_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_runner_bot_state_pnl
                ON runner_bot_state_records(account_id, deployment_id, bot_id, record_type, occurred_at DESC)
                WHERE realized_pnl IS NOT NULL;
        """)

        tracker.step("billing_subscriptions")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_subscriptions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'inactive',
                renews_at TIMESTAMPTZ NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_billing_subscriptions_user_id ON billing_subscriptions(user_id);
        """)

        tracker.success()
        
        cur.close()
        conn.close()

    except Exception as e:
        tracker.error(e)
        raise RuntimeError(f"init_postgres_schema_failed: {e}") from e

if __name__ == "__main__":
    init_postgres_schema()
