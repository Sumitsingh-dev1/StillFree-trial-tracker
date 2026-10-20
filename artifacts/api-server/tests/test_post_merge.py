"""Exercise the real hook and Python schema setup using disposable databases."""

import os
from datetime import date, datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.database import Base
from app.migrate import schema_mismatches
from app.models import Service, User

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "scripts/post-merge.sh"


@pytest.fixture
def hook_environment(tmp_path):
    """Stub dependency installs, not schema setup; no network or shared DB access."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands"
    pnpm = bin_dir / "pnpm"
    pnpm.write_text(
        '#!/bin/bash\n'
        'echo "pnpm $*" >> "$COMMAND_LOG"\n'
        '[[ "$*" == "install --frozen-lockfile" ]] || exit 90\n'
        'exit "${INSTALL_EXIT:-0}"\n'
    )
    uv = bin_dir / "uv"
    uv.write_text(
        '#!/bin/bash\n'
        'echo "uv $*" >> "$COMMAND_LOG"\n'
        'if [[ "$*" == "sync --frozen" ]]; then exit "${SYNC_EXIT:-0}"; fi\n'
        '[[ "$*" == "run --no-sync python -m app.migrate" ]] || exit 91\n'
        'exec "$TEST_PYTHON" -m app.migrate\n'
    )
    pnpm.chmod(0o755)
    uv.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "TEST_PYTHON": sys.executable,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'isolated.db'}",
    }
    for key in ("NODE_ENV", "REPLIT_ENVIRONMENT", "REPLIT_DEPLOYMENT"):
        env.pop(key, None)
    return env, log


def run_hook(env):
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd="/tmp",  # The hook must locate the repository, not rely on cwd.
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def verify_additive_setup(env):
    engine = create_engine(env["DATABASE_URL"])
    try:
        timestamp_type = (
            "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql"
            else "TIMESTAMP"
        )
        id_type = "SERIAL" if engine.dialect.name == "postgresql" else "INTEGER"
        # Seed an existing Python-owned table and an unrelated legacy table.
        # Leave services missing to verify the hook creates it.
        with engine.begin() as connection:
            connection.execute(text(
                f"CREATE TABLE users (id {id_type} PRIMARY KEY, email VARCHAR(255) "
                "UNIQUE NOT NULL, password_hash VARCHAR(100) NOT NULL, "
                f"created_at {timestamp_type} NOT NULL, legacy_note TEXT)"
            ))
            connection.execute(text(
                "INSERT INTO users VALUES "
                "(42, 'preserved@example.invalid', 'existing-hash', "
                "'2026-01-01 00:00:00+00:00', 'keep this column')"
            ))
            connection.execute(text("CREATE TABLE legacy_data (value TEXT)"))
            connection.execute(text("INSERT INTO legacy_data VALUES ('keep this row')"))
        for attempt in range(2):
            result = run_hook(env)
            assert result.returncode == 0, result.stdout + result.stderr
            assert "StillFree development schema is ready." in result.stdout
            assert {"users", "services", "legacy_data"} <= set(inspect(engine).get_table_names())
            with engine.connect() as connection:
                assert connection.execute(text("SELECT * FROM users")).all() == [
                    (42, "preserved@example.invalid", "existing-hash",
                     datetime(2026, 1, 1, tzinfo=timezone.utc)
                     if engine.dialect.name == "postgresql"
                     else "2026-01-01 00:00:00+00:00", "keep this column")
                ]
                assert connection.execute(text("SELECT * FROM legacy_data")).all() == [
                    ("keep this row",)
                ]
                if attempt == 1:
                    assert connection.execute(text(
                        "SELECT id, user_id, name, price_after_trial, cancel_url "
                        "FROM services"
                    )).all() == [(7, 42, "Existing trial", 99, "https://example.invalid/cancel")]
            if attempt == 0:
                with engine.begin() as connection:
                    connection.execute(text(
                        "INSERT INTO services (id, user_id, name, category, expires_on, "
                        "price_after_trial, cancel_url, created_at) VALUES "
                        "(7, 42, 'Existing trial', 'Other', '2026-11-01', 99, "
                        "'https://example.invalid/cancel', '2026-01-01 00:00:00')"
                    ))
    finally:
        engine.dispose()


def test_hook_preserves_records_and_creates_missing_tables(hook_environment):
    env, log = hook_environment
    verify_additive_setup(env)
    assert log.read_text().splitlines() == [
        "pnpm install --frozen-lockfile",
        "uv sync --frozen",
        "uv run --no-sync python -m app.migrate",
    ] * 2


@pytest.mark.parametrize("key,value", [
    ("REPLIT_DEPLOYMENT", "1"),
])
def test_production_is_rejected_before_any_command(hook_environment, key, value):
    env, log = hook_environment
    result = run_hook({**env, key: value})
    assert result.returncode != 0
    assert "development-only" in result.stderr
    assert not log.exists()


@pytest.mark.parametrize("runner_flags", [
    {"NODE_ENV": "production"},
    {"REPLIT_ENVIRONMENT": "production"},
    {"NODE_ENV": "production", "REPLIT_ENVIRONMENT": "production"},
])
def test_runner_production_flags_allow_safe_development_setup(hook_environment, runner_flags):
    env, log = hook_environment
    verify_additive_setup({**env, **runner_flags})
    assert log.read_text().splitlines() == [
        "pnpm install --frozen-lockfile",
        "uv sync --frozen",
        "uv run --no-sync python -m app.migrate",
    ] * 2


@pytest.mark.parametrize("key,expected", [
    ("INSTALL_EXIT", ["pnpm install --frozen-lockfile"]),
    ("SYNC_EXIT", ["pnpm install --frozen-lockfile", "uv sync --frozen"]),
])
def test_install_failure_stops_before_database_access(hook_environment, key, expected):
    env, log = hook_environment
    assert run_hook({**env, key: "23"}).returncode == 23
    assert log.read_text().splitlines() == expected


def test_schema_failure_is_reported(hook_environment, tmp_path):
    env, _ = hook_environment
    result = run_hook({**env, "DATABASE_URL": f"sqlite:///{tmp_path}/missing/db"})
    assert result.returncode != 0
    assert "development schema is ready" not in result.stdout


def test_empty_database_gets_both_python_tables(hook_environment):
    env, _ = hook_environment
    result = run_hook(env)
    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(env["DATABASE_URL"])
    try:
        assert set(inspect(engine).get_table_names()) == {"users", "services"}
    finally:
        engine.dispose()


@pytest.mark.parametrize("column_definition,expected", [
    ("", "users.password_hash: missing column"),
    ("password_hash INTEGER NOT NULL,", "users.password_hash: incompatible type"),
    ("password_hash VARCHAR(50) NOT NULL,", "users.password_hash: incompatible type"),
    ("password_hash VARCHAR(100),", "users.password_hash: incompatible nullability"),
])
def test_hook_rejects_column_drift_without_changing_data(
    hook_environment, column_definition, expected,
):
    env, _ = hook_environment
    engine = create_engine(env["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, "
                "email VARCHAR(255) UNIQUE NOT NULL, "
                f"{column_definition} created_at TIMESTAMP NOT NULL, legacy_note TEXT)"
            ))
            connection.execute(text(
                "INSERT INTO users VALUES (42, 'preserved@example.invalid', "
                + ("123, " if column_definition else "")
                + "'2026-01-01 00:00:00', 'keep this column')"
            ))
            connection.execute(text("CREATE TABLE legacy_data (value TEXT)"))
            connection.execute(text("INSERT INTO legacy_data VALUES ('keep this row')"))
        before_columns = inspect(engine).get_columns("users")
        with engine.connect() as connection:
            before_rows = connection.execute(text("SELECT * FROM users")).all()
        for _ in range(2):
            result = run_hook(env)
            assert result.returncode != 0
            assert expected in result.stderr
            assert "data-preserving development migration" in result.stderr
            assert "No schema changes were applied" in result.stderr
            assert "StillFree development schema is ready." not in result.stdout
            assert set(inspect(engine).get_table_names()) == {"users", "legacy_data"}
            assert [
                (c["name"], str(c["type"]), c["nullable"]) for c in before_columns
            ] == [
                (c["name"], str(c["type"]), c["nullable"])
                for c in inspect(engine).get_columns("users")
            ]
            with engine.connect() as connection:
                assert connection.execute(text("SELECT * FROM users")).all() == before_rows
                assert connection.execute(text("SELECT * FROM legacy_data")).all() == [
                    ("keep this row",)
                ]
    finally:
        engine.dispose()


def test_direct_schema_command_refuses_deployment(hook_environment):
    env, _ = hook_environment
    result = subprocess.run(
        [sys.executable, "-m", "app.migrate"],
        cwd=ROOT,
        env={**env, "PYTHONPATH": "artifacts/api-server", "REPLIT_DEPLOYMENT": "1"},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "development-only" in result.stderr
    assert "development schema is ready" not in result.stdout
    assert not Path(env["DATABASE_URL"].removeprefix("sqlite:///")).exists()


@pytest.fixture
def isolated_postgresql(hook_environment):
    """Use an entirely separate local cluster, never the workspace connection."""
    if not all(shutil.which(tool) for tool in ("initdb", "pg_ctl")):
        pytest.skip("Local PostgreSQL tools are not installed")
    if os.geteuid() == 0:
        pytest.skip("PostgreSQL cannot run as root")
    with tempfile.TemporaryDirectory(prefix="sf-pg-") as directory:
        data = str(Path(directory) / "data")
        subprocess.run(
            ["initdb", "-D", data, "-U", "stillfree_test", "--no-sync",
             "--auth-local=trust", "--auth-host=reject"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        try:
            subprocess.run(
                ["pg_ctl", "-D", data, "-l", f"{directory}/postgres.log",
                 "-o", f"-F -k {directory} -c listen_addresses=''", "-w", "start"],
                check=True, capture_output=True, text=True, timeout=30,
            )
            env, _ = hook_environment
            isolated_env = {
                **env,
                "DATABASE_URL": (
                    f"postgresql+psycopg://stillfree_test@/postgres?host={directory}"
                ),
            }
            yield isolated_env
        finally:
            subprocess.run(
                ["pg_ctl", "-D", data, "-m", "immediate", "-w", "stop"],
                check=True, capture_output=True, text=True, timeout=30,
            )


def test_hook_preserves_records_on_isolated_postgresql(isolated_postgresql):
    env = isolated_postgresql
    verify_additive_setup(env)
    engine = create_engine(env["DATABASE_URL"])
    try:
        # Reproduce real PostgreSQL drift without touching any shared DB.
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE users ALTER COLUMN password_hash TYPE VARCHAR(50)"
            ))
            connection.execute(text("ALTER TABLE services DROP COLUMN cancel_url"))
            before_users = connection.execute(text("SELECT * FROM users")).all()
            before_services = connection.execute(text("SELECT * FROM services")).all()
        result = run_hook(env)
        assert result.returncode != 0
        assert "users.password_hash: incompatible type" in result.stderr
        assert "services.cancel_url: missing column" in result.stderr
        assert "No schema changes were applied" in result.stderr
        assert "development schema is ready" not in result.stdout
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM users")).all() == before_users
            assert connection.execute(text("SELECT * FROM services")).all() == before_services
            assert connection.execute(text("SELECT * FROM legacy_data")).all() == [
                ("keep this row",)
            ]
        assert "cancel_url" not in {
            column["name"] for column in inspect(engine).get_columns("services")
        }
        assert str(inspect(engine).get_columns("users")[2]["type"]) == "VARCHAR(50)"
    finally:
        engine.dispose()


@pytest.mark.parametrize("change,expected", [
    ("ALTER TABLE users DROP CONSTRAINT users_email_key", "users: missing unique guarantee (email)"),
    ("ALTER TABLE services DROP CONSTRAINT services_user_id_fkey", "services: missing foreign key (user_id)"),
    ("ALTER TABLE services DROP CONSTRAINT services_nonnegative_price", "check constraint services_nonnegative_price"),
    ("ALTER TABLE services DROP CONSTRAINT services_valid_category", "check constraint services_valid_category"),
    # Keeping the expected name is not sufficient if the guarantee is weaker.
    ("ALTER TABLE services DROP CONSTRAINT services_nonnegative_price; "
     "ALTER TABLE services ADD CONSTRAINT services_nonnegative_price "
     "CHECK (price_after_trial >= -1)", "check constraint services_nonnegative_price"),
    ("ALTER TABLE users DROP CONSTRAINT users_email_key; "
     "CREATE UNIQUE INDEX partial_email ON users(email) WHERE id > 42",
     "users: missing unique guarantee (email)"),
    ("ALTER TABLE services DROP CONSTRAINT services_user_id_fkey; "
     "ALTER TABLE services ADD FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
     "services: missing foreign key (user_id)"),
    ("ALTER TABLE services ALTER COLUMN name DROP NOT NULL",
     "services.name: incompatible nullability"),
    ("ALTER TABLE services ALTER COLUMN expires_on DROP NOT NULL",
     "services.expires_on: incompatible nullability"),
    ("ALTER TABLE services ALTER COLUMN user_id DROP NOT NULL",
     "services.user_id: incompatible nullability"),
])
def test_hook_rejects_constraint_drift_without_changing_rows(
    isolated_postgresql, change, expected,
):
    env = isolated_postgresql
    verify_additive_setup(env)
    engine = create_engine(env["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            for statement in change.split("; "):
                connection.execute(text(statement))
            before = {
                table: connection.execute(text(f"SELECT * FROM {table}")).all()
                for table in ("users", "services", "legacy_data")
            }
        for _ in range(2):
            result = run_hook(env)
            assert result.returncode != 0
            assert expected in result.stderr
            assert "No schema changes were applied" in result.stderr
            assert "data-preserving development migration" in result.stderr
            assert "Do not drop tables or reset the database" in result.stderr
            assert "development schema is ready" not in result.stdout
            with engine.connect() as connection:
                for table, rows in before.items():
                    assert connection.execute(text(f"SELECT * FROM {table}")).all() == rows
        # Setup must neither repair the missing safeguard nor alter schema.
        with engine.connect() as connection:
            assert any(expected in issue for issue in schema_mismatches(connection))
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "original_name,legacy_name,definition,violation,repair",
    [
        (
            "services_nonnegative_price", "legacy_unvalidated_price",
            "CHECK (price_after_trial >= 0)",
            "price_after_trial = -1", "price_after_trial = 99",
        ),
        (
            "services_valid_category", "legacy_unvalidated_category",
            "CHECK (category IN ('Cloud', 'Streaming', 'Software/AI', 'Fitness', 'Other'))",
            "category = 'Legacy'", "category = 'Other'",
        ),
        (
            "services_user_id_fkey", "legacy_unvalidated_owner",
            "FOREIGN KEY (user_id) REFERENCES users(id)",
            "user_id = 9999", "user_id = 42",
        ),
    ],
)
def test_postgresql_not_valid_rules_reject_setup_without_changing_older_rows(
    isolated_postgresql, original_name, legacy_name, definition, violation, repair,
):
    env = isolated_postgresql
    engine = create_engine(env["DATABASE_URL"])
    catalog = text(
        "SELECT conname, convalidated, pg_get_constraintdef(oid) "
        "FROM pg_catalog.pg_constraint WHERE conrelid = 'services'::regclass "
        "ORDER BY conname"
    )
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(User.__table__.insert(), {
                "id": 42, "email": "preserved@example.invalid",
                "password_hash": "test-only-hash",
            })
            connection.execute(Service.__table__.insert(), {
                "id": 7, "user_id": 42, "name": "Preserved trial",
                "category": "Other", "expires_on": date(2030, 1, 1),
                "price_after_trial": 99,
            })
            # Only this disposable fixture introduces historical violations.
            connection.execute(text(
                f"ALTER TABLE services DROP CONSTRAINT {original_name}"
            ))
            connection.execute(text(f"UPDATE services SET {violation} WHERE id = 7"))
            connection.execute(text(
                f"ALTER TABLE services ADD CONSTRAINT {legacy_name} "
                f"{definition} NOT VALID"
            ))
            before_rows = {
                table: connection.execute(text(f"SELECT * FROM {table}")).all()
                for table in ("users", "services")
            }
            before_rules = connection.execute(catalog).all()
            assert any(
                name == legacy_name and validated is False
                for name, validated, _ in before_rules
            )
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            issues = schema_mismatches(connection)
            assert any(
                "NOT VALID" in issue and legacy_name in issue for issue in issues
            )
        rejected = run_hook(env)
        assert rejected.returncode != 0
        assert "NOT VALID" in rejected.stderr
        assert legacy_name in rejected.stderr
        assert "reviewed data cleanup and constraint validation" in rejected.stderr
        assert "No schema changes were applied" in rejected.stderr
        assert "development schema is ready" not in rejected.stdout
        with engine.connect() as connection:
            assert connection.execute(catalog).all() == before_rules
            for table, rows in before_rows.items():
                assert connection.execute(text(f"SELECT * FROM {table}")).all() == rows

        # Simulate a separately reviewed cleanup/validation in the fixture only.
        # The setup command above must never perform either of these operations.
        with engine.begin() as connection:
            connection.execute(text(f"UPDATE services SET {repair} WHERE id = 7"))
            connection.execute(text(
                f"ALTER TABLE services VALIDATE CONSTRAINT {legacy_name}"
            ))
        with engine.connect() as connection:
            assert schema_mismatches(connection) == []
        accepted = run_hook(env)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        assert "development schema is ready" in accepted.stdout
    finally:
        engine.dispose()


@pytest.mark.parametrize("table,replacement", [
    pytest.param("services", None, id="trial-key-removed"),
    pytest.param("services", "name", id="trial-key-changed"),
    pytest.param("services", "id, name", id="trial-key-made-composite"),
    pytest.param("users", None, id="account-key-removed"),
    pytest.param("users", "email", id="account-key-changed"),
])
def test_postgresql_primary_key_drift_preserves_existing_rows(
    isolated_postgresql, table, replacement,
):
    env = isolated_postgresql
    engine = create_engine(env["DATABASE_URL"])
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(User.__table__.insert(), {
                "id": 42, "email": "preserved@example.invalid",
                "password_hash": "test-only-hash",
            })
            trial = {
                "id": 7, "user_id": 42, "name": "Preserved trial",
                "category": "Other", "expires_on": date(2030, 1, 1),
                "price_after_trial": 99,
            }
            connection.execute(Service.__table__.insert(), trial)
            if table == "users":
                # Retain the ownership FK using an alternate unique index, so
                # the only model mismatch is the missing/changed primary key.
                connection.execute(text(
                    "ALTER TABLE services DROP CONSTRAINT services_user_id_fkey"
                ))
                connection.execute(text(
                    "CREATE UNIQUE INDEX legacy_user_ids ON users(id)"
                ))
            connection.execute(text(
                f"ALTER TABLE {table} DROP CONSTRAINT {table}_pkey"
            ))
            if replacement:
                connection.execute(text(
                    f"ALTER TABLE {table} ADD CONSTRAINT legacy_primary_key "
                    f"PRIMARY KEY ({replacement})"
                ))
            if table == "users":
                connection.execute(text(
                    "ALTER TABLE services ADD FOREIGN KEY (user_id) REFERENCES users(id)"
                ))
            else:
                # These broken keys actually allow duplicate model identifiers.
                # Use Core inserts, not an ORM identity map that assumes unique IDs.
                connection.execute(Service.__table__.insert(), {
                    **trial, "name": "Duplicate trial identifier",
                })
            before_rows = {
                name: connection.execute(text(
                    f"SELECT * FROM {name} ORDER BY id, {sort_column}"
                )).all()
                for name, sort_column in (("users", "email"), ("services", "name"))
            }
            before_key = inspect(connection).get_pk_constraint(table)
            assert before_key["constrained_columns"] == (
                replacement.split(", ") if replacement else []
            )
        expected = f"{table}: missing or incompatible primary key"
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            issues = schema_mismatches(connection)
            assert len(issues) == 1
            assert expected in issues[0]
            assert f"database {replacement or 'none'}; model id" in issues[0]
        for _ in range(2):
            rejected = run_hook(env)
            assert rejected.returncode != 0
            assert expected in rejected.stderr
            assert "No schema changes were applied" in rejected.stderr
            assert "data-preserving development migration" in rejected.stderr
            assert "development schema is ready" not in rejected.stdout
            with engine.connect() as connection:
                assert inspect(connection).get_pk_constraint(table) == before_key
                for name, sort_column in (("users", "email"), ("services", "name")):
                    assert connection.execute(text(
                        f"SELECT * FROM {name} ORDER BY id, {sort_column}"
                    )).all() == before_rows[name]
    finally:
        engine.dispose()


def test_postgresql_renamed_primary_keys_remain_compatible(isolated_postgresql):
    engine = create_engine(isolated_postgresql["DATABASE_URL"])
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE users RENAME CONSTRAINT users_pkey TO legacy_account_key"
            ))
            connection.execute(text(
                "ALTER TABLE services RENAME CONSTRAINT services_pkey TO legacy_trial_key"
            ))
        with engine.connect() as connection:
            assert schema_mismatches(connection) == []
        result = run_hook(isolated_postgresql)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        engine.dispose()


def test_model_schema_passes_preflight_on_postgresql(isolated_postgresql):
    """SQLAlchemy's unique email index also counts, not just UNIQUE constraints."""
    engine = create_engine(isolated_postgresql["DATABASE_URL"])
    try:
        Base.metadata.create_all(engine)
        for _ in range(2):
            result = run_hook(isolated_postgresql)
            assert result.returncode == 0, result.stdout + result.stderr
    finally:
        engine.dispose()

def automatic_id_snapshot(engine):
    """Read rows, ID metadata and sequence state, never nextval/setval."""
    with engine.connect() as connection:
        sequences = connection.scalars(text(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'S' AND n.nspname = current_schema() "
            "ORDER BY c.relname"
        )).all() if engine.dialect.name == "postgresql" else []
        quote = connection.dialect.identifier_preparer.quote_identifier
        return {
            "rows": {
                table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
                for table in ("users", "services")
            },
            "columns": {
                table: [{**column, "type": str(column["type"])}
                        for column in inspect(connection).get_columns(table)]
                for table in ("users", "services")
            },
            "sequences": {
                name: connection.exec_driver_sql(
                    f"SELECT last_value, is_called FROM {quote(name)}"
                ).one()
                for name in sequences
            },
        }
@pytest.fixture(params=["sqlite", "postgresql"])
def legacy_column_environment(request, hook_environment):
    if request.param == "postgresql":
        return request.getfixturevalue("isolated_postgresql")
    return hook_environment[0]


def verify_legacy_column(env, table, definition, *, allowed, generated=False):
    """Check real reflection, hook behavior, row preservation, and ORM inserts."""
    engine = create_engine(env["DATABASE_URL"])
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text(
                f"ALTER TABLE {table} ADD COLUMN legacy_value {definition}"
            ))
            connection.execute(text("CREATE TABLE legacy_data (value TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO legacy_data VALUES ('keep this row')"))
            for target, columns, values in (
                ("users", "id, email, password_hash, created_at",
                 "42, 'preserved@example.invalid', 'existing-hash', '2026-01-01'"),
                ("services", "id, user_id, name, category, expires_on, created_at",
                 "7, 42, 'Existing trial', 'Other', '2026-11-01', '2026-01-01'"),
            ):
                if target == table and not generated:
                    columns += ", legacy_value"
                    values += ", 91"
                connection.execute(text(f"INSERT INTO {target} ({columns}) VALUES ({values})"))
            before_rows = {
                target: connection.execute(text(f"SELECT * FROM {target}")).all()
                for target in ("users", "services", "legacy_data")
            }

        def columns_snapshot():
            return {
                target: [
                    {**column, "type": str(column["type"])}
                    for column in inspect(engine).get_columns(target)
                ]
                for target in before_rows
            }

        before_columns = columns_snapshot()
        expected = f"{table}.legacy_value: extra NOT NULL column"
        for _ in range(2):
            with engine.connect() as connection:
                issues = schema_mismatches(connection)
                if allowed:
                    assert issues == []
                else:
                    assert len(issues) == 1
                    assert expected in issues[0]
            result = run_hook(env)
            if allowed:
                assert result.returncode == 0, result.stdout + result.stderr
                assert "StillFree development schema is ready." in result.stdout
            else:
                assert result.returncode != 0
                assert expected in result.stderr
                assert "blocks model inserts" in result.stderr
                assert "No schema changes were applied" in result.stderr
                assert "data-preserving development migration" in result.stderr
                assert "Do not drop tables or reset the database" in result.stderr
                assert "development schema is ready" not in result.stdout
            assert set(inspect(engine).get_table_names()) == set(before_rows)
            assert columns_snapshot() == before_columns
            with engine.connect() as connection:
                for target, rows in before_rows.items():
                    assert connection.execute(text(f"SELECT * FROM {target}")).all() == rows

        def insert_models():
            with Session(engine) as session:
                session.add(User(
                    id=43, email="new@example.invalid", password_hash="new-hash",
                ))
                session.flush()
                session.add(Service(
                    id=8, user_id=43, name="New trial", category="Other",
                    expires_on=date(2026, 11, 1),
                ))
                session.commit()

        if allowed:
            insert_models()
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 2
                assert connection.execute(text("SELECT count(*) FROM services")).scalar_one() == 2
        else:
            with pytest.raises(IntegrityError):
                insert_models()
            with engine.connect() as connection:
                for target, rows in before_rows.items():
                    assert connection.execute(text(f"SELECT * FROM {target}")).all() == rows
    finally:
        engine.dispose()


@pytest.mark.parametrize("table", ["users", "services"])
@pytest.mark.parametrize("definition,allowed,generated", [
    ("INTEGER NOT NULL", False, False),
    ("INTEGER NOT NULL DEFAULT NULL", False, False),
    ("INTEGER NOT NULL DEFAULT (NULL)", False, False),
    ("INTEGER", True, False),
    ("INTEGER DEFAULT NULL", True, False),
    ("INTEGER NOT NULL DEFAULT 0", True, False),
    ("INTEGER NOT NULL DEFAULT (7 + 2)", True, False),
    ("TEXT NOT NULL DEFAULT 'NULL'", True, False),
    ("TEXT NOT NULL DEFAULT ''", True, False),
    ("INTEGER GENERATED ALWAYS AS (id + 100) STORED NOT NULL", True, True),
])
def test_extra_legacy_columns(
    legacy_column_environment, table, definition, allowed, generated,
):
    verify_legacy_column(
        legacy_column_environment, table, definition, allowed=allowed, generated=generated,
    )


@pytest.mark.parametrize("table", ["users", "services"])
@pytest.mark.parametrize("definition,allowed", [
    ("INTEGER GENERATED ALWAYS AS IDENTITY", True),
    ("INTEGER GENERATED BY DEFAULT AS IDENTITY", True),
    ("SERIAL NOT NULL", True),
    ("INTEGER NOT NULL DEFAULT NULL::integer", False),
])
def test_postgresql_legacy_identity_and_cast_defaults(
    isolated_postgresql, table, definition, allowed,
):
    verify_legacy_column(
        isolated_postgresql, table, definition, allowed=allowed, generated=allowed,
    )


def seed_sqlite_check_schema(connection, table, extra_checks):
    """Create handwritten-style CHECK formatting in a disposable file only."""
    connection.exec_driver_sql("PRAGMA foreign_keys = ON")
    for model_table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(model_table).compile(dialect=connection.dialect)).rstrip()
        if model_table.name == table:
            assert ddl.endswith(")")
            ddl = ddl[:-1] + f",\n{extra_checks}\n)"
        connection.exec_driver_sql(ddl)
        for index in model_table.indexes:
            index.create(connection)
    connection.execute(User.__table__.insert(), {
        "id": 42, "email": "preserved@example.invalid",
        "password_hash": "test-only-hash",
    })
    connection.execute(Service.__table__.insert(), {
        "id": 7, "user_id": 42, "name": "Existing trial",
        "category": "Other", "expires_on": date(2030, 1, 1),
        "price_after_trial": 99,
    })
    connection.exec_driver_sql(
        "CREATE TABLE legacy_data (value INTEGER CHECK (\nvalue <= 100\n))"
    )
    connection.exec_driver_sql("INSERT INTO legacy_data VALUES (42)")


def sqlite_schema_and_rows(connection):
    # Raw DDL preserves unnamed constraints and multiline formatting, unlike
    # comparing reflected names alone. Include indexes and unrelated tables.
    schema = connection.exec_driver_sql(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).all()
    rows = {
        table: connection.exec_driver_sql(
            f"SELECT * FROM {table} ORDER BY rowid"
        ).all()
        for table in ("users", "services", "legacy_data")
    }
    return schema, rows


@pytest.mark.parametrize("table,extra_checks,label", [
    pytest.param(
        "services", "CONSTRAINT legacy_limit CHECK (price_after_trial <= 100)",
        "legacy_limit", id="named-price-limit",
    ),
    pytest.param(
        "services", "CHECK (category IN ('Cloud', 'Other'))",
        "<unnamed>", id="unnamed-category-limit",
    ),
    pytest.param(
        "services", "CHECK (\n    price_after_trial\n    <= 100\n)",
        "<unnamed>", id="unnamed-multiline-price-limit",
    ),
    pytest.param(
        "services",
        'CONSTRAINT "old trial limit" CHECK (\n    length(name)\n    <= 20\n)',
        "old trial limit", id="quoted-multiline-name-limit",
    ),
    pytest.param(
        "users", "CHECK (\n    length(email)\n    <= 100\n)",
        "<unnamed>", id="account-with-no-model-checks",
    ),
])
def test_sqlite_extra_checks_refuse_setup_without_changing_rows_or_rules(
    hook_environment, monkeypatch, table, extra_checks, label,
):
    env, _ = hook_environment
    engine = create_engine(env["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            seed_sqlite_check_schema(connection, table, extra_checks)
            before = sqlite_schema_and_rows(connection)
        expected = f"{table}: extra check constraint {label}"
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only = ON")
            try:
                issues = schema_mismatches(connection)
                assert len(issues) == 1
                assert expected in issues[0]
                assert "model-valid inserts or updates" in issues[0]
            finally:
                connection.exec_driver_sql("PRAGMA query_only = OFF")

        from app import migrate

        def forbidden_ddl(*args, **kwargs):
            pytest.fail("Unsupported SQLite CHECK reached automatic DDL")

        with monkeypatch.context() as patch:
            patch.setattr(migrate, "engine", engine)
            patch.setattr(Base.metadata, "create_all", forbidden_ddl)
            with pytest.raises(SystemExit, match="extra check constraint"):
                migrate.main()
        for _ in range(2):
            result = run_hook(env)
            assert result.returncode != 0
            assert expected in result.stderr
            assert "compatibility is not established" in result.stderr
            assert "data-preserving development migration" in result.stderr
            assert "No schema changes were applied" in result.stderr
            assert "no rule was dropped or replaced" in result.stderr
            assert "development schema is ready" not in result.stdout
            with engine.connect() as connection:
                assert sqlite_schema_and_rows(connection) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("equivalent_checks", [
    pytest.param(
        "CONSTRAINT differently_named_price CHECK ((price_after_trial >= 0))",
        id="renamed-equivalent-price",
    ),
    pytest.param(
        "CHECK (\n    price_after_trial >= 0\n),\n"
        "CHECK (\n    CATEGORY IN (\n"
        "    'Cloud', 'Streaming', 'Software/AI', 'Fitness', 'Other'\n"
        "    )\n)",
        id="unnamed-multiline-equivalent-predicates",
    ),
])
def test_sqlite_equivalent_checks_and_unrelated_legacy_tables_are_accepted(
    hook_environment, equivalent_checks,
):
    env, _ = hook_environment
    engine = create_engine(env["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            seed_sqlite_check_schema(connection, "services", equivalent_checks)
            for category_id, category in enumerate(
                ("Cloud", "Streaming", "Software/AI", "Fitness", "Other"),
            ):
                for price_id, price in enumerate((None, 0, 1000)):
                    connection.execute(Service.__table__.insert(), {
                        "id": 100 + category_id * 3 + price_id,
                        "user_id": 42, "name": "Model-valid trial",
                        "category": category, "expires_on": date(2030, 1, 1),
                        "price_after_trial": price,
                    })
            before = sqlite_schema_and_rows(connection)
        with engine.connect() as connection:
            assert schema_mismatches(connection) == []
        for _ in range(2):
            result = run_hook(env)
            assert result.returncode == 0, result.stdout + result.stderr
            assert "development schema is ready" in result.stdout
            with engine.connect() as connection:
                assert sqlite_schema_and_rows(connection) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("table,predicate", [
    ("services", "price_after_trial <= 100"),
    ("services", "category IN ('Cloud', 'Other')"),
    # Cast-like text inside a string is a different value, not SQL formatting.
    ("services", "category IN ('Cloud::text', 'Streaming', 'Software/AI', 'Fitness', 'Other')"),
    ("services", "length(name) <= 20"),
    ("services", "price_after_trial IS NOT NULL"),
    ("services", "(price_after_trial::numeric(3, 0)) >= 0"),
    # Tables without any expected model CHECKs must also be inspected.
    ("users", "length(email) <= 100"),
])
def test_extra_checks_refuse_setup_without_ddl_or_data_changes(
    isolated_postgresql, monkeypatch, table, predicate,
):
    env = isolated_postgresql
    engine = create_engine(env["DATABASE_URL"])
    catalog = text(
        "SELECT c.oid, c.conrelid, c.conname, c.convalidated, "
        "pg_get_constraintdef(c.oid) FROM pg_catalog.pg_constraint c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' ORDER BY c.oid"
    )
    try:
        # For the users case, leave services absent: refusal must also prevent
        # create_all from creating a genuinely missing model table.
        if table == "users":
            User.__table__.create(engine)
        else:
            Base.metadata.create_all(engine)
        with engine.begin() as connection:
            # Apply the legacy rule before seeding compatible records. Neither
            # historical violations nor missing model checks explain refusal.
            connection.execute(text(
                f"ALTER TABLE {table} ADD CONSTRAINT legacy_limit CHECK ({predicate})"
            ))
            connection.execute(User.__table__.insert(), {
                "id": 42, "email": "preserved@example.invalid",
                "password_hash": "test-only-hash",
            })
            if table == "services":
                connection.execute(Service.__table__.insert(), {
                    "id": 7, "user_id": 42, "name": "Existing trial",
                    "category": "Other", "expires_on": date(2030, 1, 1),
                    "price_after_trial": 99,
                })
            connection.execute(text(
                "CREATE TABLE legacy_data (value INTEGER CHECK (value <= 100))"
            ))
            connection.execute(text("INSERT INTO legacy_data VALUES (42)"))
            before_rows = {
                name: connection.execute(text(f"SELECT * FROM {name}")).all()
                for name in inspect(connection).get_table_names()
            }
            before_rules = connection.execute(catalog).all()
            before_tables = inspect(connection).get_table_names()

        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            issues = schema_mismatches(connection)
            assert len(issues) == 1
            assert f"{table}: extra check constraint legacy_limit" in issues[0]
            assert "model-valid inserts or updates" in issues[0]

        # Observe entry to automatic DDL, even when create_all would be a no-op.
        # Keep the real CLI and hook exercised below as well.
        from app import migrate

        def forbidden_ddl(*args, **kwargs):
            pytest.fail("Incompatible extra CHECK reached automatic DDL")

        with monkeypatch.context() as patch:
            patch.setattr(migrate, "engine", engine)
            patch.setattr(Base.metadata, "create_all", forbidden_ddl)
            with pytest.raises(SystemExit, match="extra check constraint legacy_limit"):
                migrate.main()

        for _ in range(2):
            result = run_hook(env)
            assert result.returncode != 0
            assert f"{table}: extra check constraint legacy_limit" in result.stderr
            assert "compatibility is not established" in result.stderr
            assert "data-preserving development migration" in result.stderr
            assert "No schema changes were applied" in result.stderr
            assert "no rule was dropped or replaced" in result.stderr
            assert "development schema is ready" not in result.stdout
            with engine.connect() as connection:
                assert inspect(connection).get_table_names() == before_tables
                assert connection.execute(catalog).all() == before_rules
                for name, rows in before_rows.items():
                    assert connection.execute(text(f"SELECT * FROM {name}")).all() == rows
    finally:
        engine.dispose()


def test_postgresql_setup_never_executes_a_legacy_check_function(
    isolated_postgresql, monkeypatch,
):
    env = isolated_postgresql
    engine = create_engine(env["DATABASE_URL"])
    catalog = text(
        "SELECT conname, convalidated, pg_get_constraintdef(oid) "
        "FROM pg_catalog.pg_constraint "
        "WHERE conrelid IN ('users'::regclass, 'services'::regclass) "
        "ORDER BY conrelid, conname"
    )

    def snapshot():
        with engine.connect() as connection:
            return {
                "rows": {
                    table: connection.execute(text(
                        f"SELECT * FROM {table} ORDER BY id"
                    )).all()
                    for table in ("users", "services")
                },
                "constraints": connection.execute(catalog).all(),
                "function": connection.scalar(text(
                    "SELECT pg_get_functiondef("
                    "'public.legacy_check_guard(numeric)'::regprocedure)"
                )),
                "calls": tuple(connection.execute(text(
                    "SELECT last_value, is_called FROM public.legacy_check_audit"
                )).one()),
            }

    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE FUNCTION public.legacy_check_guard(amount numeric)
                RETURNS boolean LANGUAGE plpgsql VOLATILE AS $$
                BEGIN
                    RETURN amount IS NULL OR amount <= 100;
                END;
                $$
            """))
            connection.execute(text(
                "ALTER TABLE services ADD CONSTRAINT legacy_function_limit "
                "CHECK (public.legacy_check_guard(price_after_trial))"
            ))
            connection.execute(User.__table__.insert(), {
                "id": 42, "email": "preserved@example.invalid",
                "password_hash": "test-only-hash",
            })
            connection.execute(Service.__table__.insert(), {
                "id": 7, "user_id": 42, "name": "Compatible historical trial",
                "category": "Other", "expires_on": date(2030, 1, 1),
                "price_after_trial": 99,
            })
            connection.execute(text("CREATE SEQUENCE public.legacy_check_audit"))
            connection.execute(text("""
                CREATE OR REPLACE FUNCTION public.legacy_check_guard(amount numeric)
                RETURNS boolean LANGUAGE plpgsql VOLATILE AS $$
                BEGIN
                    PERFORM nextval('public.legacy_check_audit'::regclass);
                    RAISE EXCEPTION 'legacy CHECK execution sentinel';
                END;
                $$
            """))

        # Prove the guard is armed before taking the baseline. Sequence advances
        # survive rollback, so even a caught/rolled-back compatibility probe is
        # observable during the preflight and hook checks that follow.
        with pytest.raises(DBAPIError, match="legacy CHECK execution sentinel"):
            with engine.begin() as connection:
                connection.execute(text("SELECT public.legacy_check_guard(99)"))
        before = snapshot()
        assert before["calls"] == (1, True)

        with engine.connect() as connection:
            issues = schema_mismatches(connection)
            assert len(issues) == 1
            assert "services: extra check constraint legacy_function_limit" in issues[0]
            assert "legacy_check_guard" in issues[0]
            assert "compatibility is not established" in issues[0]
        assert snapshot() == before

        from app import migrate

        def forbidden_ddl(*args, **kwargs):
            pytest.fail("Legacy function CHECK reached automatic DDL")

        with monkeypatch.context() as patch:
            patch.setattr(migrate, "engine", engine)
            patch.setattr(Base.metadata, "create_all", forbidden_ddl)
            with pytest.raises(SystemExit, match="extra check constraint"):
                migrate.main()
        assert snapshot() == before

        for _ in range(2):
            result = run_hook(env)
            assert result.returncode != 0
            assert "services: extra check constraint legacy_function_limit" in result.stderr
            assert "legacy_check_guard" in result.stderr
            assert "data-preserving development migration" in result.stderr
            assert "No schema changes were applied" in result.stderr
            assert "legacy CHECK execution sentinel" not in result.stderr
            assert "development schema is ready" not in result.stdout
            assert snapshot() == before
    finally:
        engine.dispose()


def test_equivalent_constraint_names_and_valid_trials_are_accepted(isolated_postgresql):
    verify_additive_setup(isolated_postgresql)
    engine = create_engine(isolated_postgresql["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE services RENAME CONSTRAINT services_nonnegative_price "
                "TO preserved_price_check"
            ))
            # Additional equivalent checks are safe, regardless of their names.
            connection.execute(text(
                "ALTER TABLE services ADD CONSTRAINT duplicate_price "
                "CHECK ((price_after_trial >= 0::numeric))"
            ))
            connection.execute(text(
                "ALTER TABLE services ADD CONSTRAINT duplicate_category CHECK "
                "(category = ANY (ARRAY['Cloud', 'Streaming', 'Software/AI', "
                "'Fitness', 'Other']::text[]))"
            ))
            # Unrelated legacy tables are outside model preflight.
            connection.execute(text(
                "ALTER TABLE legacy_data ADD CHECK (length(value) <= 20)"
            ))
            for category_id, category in enumerate(
                ("Cloud", "Streaming", "Software/AI", "Fitness", "Other"),
            ):
                for price_id, price in enumerate((None, 0, 1000)):
                    connection.execute(text(
                        "INSERT INTO services "
                        "(id, user_id, name, category, expires_on, price_after_trial, created_at) "
                        "VALUES (:id, 42, 'Valid', :category, '2026-11-01', :price, "
                        "'2026-01-01 00:00:00')"
                    ), {
                        "id": 100 + category_id * 3 + price_id,
                        "category": category, "price": price,
                    })
        result = run_hook(isolated_postgresql)
        assert result.returncode == 0, result.stdout + result.stderr
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES "
                "(43, 'preserved@example.invalid', 'hash', '2026-01-01 00:00:00')"
            ))
        assert error.value.orig.sqlstate == "23505"
    finally:
        engine.dispose()


@pytest.mark.parametrize("field,value,sqlstate", [
    ("price_after_trial", -1, "23514"),
    ("category", "Unknown", "23514"),
    ("category", None, "23502"),
    ("name", None, "23502"),
    ("expires_on", None, "23502"),
    ("user_id", None, "23502"),
    ("user_id", 999, "23503"),
])
def test_postgresql_rejects_invalid_trials(isolated_postgresql, field, value, sqlstate):
    verify_additive_setup(isolated_postgresql)
    engine = create_engine(isolated_postgresql["DATABASE_URL"])
    try:
        values = {
            "user_id": 42, "name": "Invalid trial", "category": "Other",
            "expires_on": "2026-11-01", "price_after_trial": 0,
            "created_at": "2026-01-01 00:00:00",
        }
        values[field] = value
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO services "
                "(user_id, name, category, expires_on, price_after_trial, created_at) "
                "VALUES (:user_id, :name, :category, :expires_on, :price_after_trial, :created_at)"
            ), values)
        assert error.value.orig.sqlstate == sqlstate
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM services")).scalar_one() == 1
    finally:
        engine.dispose()

@pytest.mark.parametrize("key", [
    "INTEGER PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT",
])
def test_sqlite_rowid_generates_omitted_ids(hook_environment, key):
    env, _ = hook_environment
    engine = create_engine(env["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f"CREATE TABLE users (id {key}, email VARCHAR(255) UNIQUE NOT NULL, "
                "password_hash VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL)"
            ))
        Base.metadata.create_all(engine)
        seed_automatic_id_records(engine)
        before = automatic_id_snapshot(engine)
        for _ in range(2):
            with engine.connect() as connection:
                assert schema_mismatches(connection) == []
            result = run_hook(env)
            assert result.returncode == 0, result.stdout + result.stderr
            assert automatic_id_snapshot(engine) == before
        with Session(engine) as session:
            user = User(email="new@example.invalid", password_hash="new-hash")
            session.add(user)
            session.flush()
            service = Service(
                user_id=user.id, name="New trial", category="Other",
                expires_on=date(2026, 11, 1),
            )
            session.add(service)
            session.commit()
            assert user.id == 43
            assert service.id == 8
    finally:
        engine.dispose()

def seed_automatic_id_records(engine):
    with Session(engine) as session:
        session.add(User(
            id=42, email="preserved@example.invalid", password_hash="existing-hash",
        ))
        session.flush()
        session.add(Service(
            id=7, user_id=42, name="Existing trial", category="Other",
            expires_on=date(2026, 11, 1),
        ))
        session.commit()

@pytest.mark.parametrize("table", ["users", "services"])
@pytest.mark.parametrize("default", [
    None, "NULL", "123",
    "nextval('{table}_id_seq'::regclass) + 1",
    "unreviewed_id()",
])
def test_postgresql_unsupported_automatic_ids_are_read_only(
    isolated_postgresql, table, default,
):
    env = isolated_postgresql
    engine = create_engine(env["DATABASE_URL"])
    try:
        Base.metadata.create_all(engine)
        seed_automatic_id_records(engine)
        with engine.begin() as connection:
            # If setup ever executes an unknown default this function advances
            # a sequence, even if the transaction subsequently rolls back.
            connection.execute(text(
                "CREATE SEQUENCE default_probe_seq START WITH 900"
            ))
            connection.execute(text(
                "CREATE FUNCTION unreviewed_id() RETURNS integer LANGUAGE SQL "
                "AS $$ SELECT nextval('default_probe_seq')::integer $$"
            ))
            action = (
                "DROP DEFAULT" if default is None
                else f"SET DEFAULT {default.format(table=table)}"
            )
            connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN id {action}"))
        before = automatic_id_snapshot(engine)
        expected = f"{table}.id: missing or unsupported automatic ID generation"
        for _ in range(2):
            with engine.connect() as connection:
                assert schema_mismatches(connection) == [
                    expected + " blocks model inserts that omit the primary key; "
                    "a reviewed, data-preserving development migration is required"
                ]
            result = run_hook(env)
            assert result.returncode != 0
            assert expected in result.stderr
            assert "No schema changes were applied" in result.stderr
            assert "data-preserving development migration" in result.stderr
            assert "Do not drop tables or reset the database" in result.stderr
            assert "development schema is ready" not in result.stdout
            assert automatic_id_snapshot(engine) == before
        if default in (None, "NULL"):
            # Reproduce the actual omitted-ID failure only in this disposable
            # cluster, after proving setup itself did not execute any defaults.
            with Session(engine) as session, pytest.raises(IntegrityError):
                if table == "users":
                    session.add(User(
                        email="new@example.invalid", password_hash="new-hash",
                    ))
                else:
                    session.add(Service(
                        user_id=42, name="New trial", category="Other",
                        expires_on=date(2026, 11, 1),
                    ))
                session.flush()
            assert automatic_id_snapshot(engine) == before
    finally:
        engine.dispose()

@pytest.mark.parametrize("key,suffix", [
    ("INTEGER PRIMARY KEY", " WITHOUT ROWID"),
    ("INTEGER PRIMARY KEY DESC", ""),
])
def test_sqlite_non_rowid_keys_require_review(hook_environment, key, suffix):
    env, _ = hook_environment
    engine = create_engine(env["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f"CREATE TABLE users (id {key}, email VARCHAR(255) UNIQUE NOT NULL, "
                "password_hash VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL)"
                + suffix
            ))
        Base.metadata.create_all(engine)
        seed_automatic_id_records(engine)
        before = automatic_id_snapshot(engine)
        for _ in range(2):
            result = run_hook(env)
            assert result.returncode != 0
            assert "users.id: missing or unsupported automatic ID generation" in result.stderr
            assert "No schema changes were applied" in result.stderr
            assert automatic_id_snapshot(engine) == before
    finally:
        engine.dispose()

@pytest.mark.parametrize("generation", ["serial", "identity-always", "identity-default"])
def test_postgresql_automatic_ids_accept_serial_and_identity(
    isolated_postgresql, generation,
):
    env = isolated_postgresql
    engine = create_engine(env["DATABASE_URL"])
    try:
        Base.metadata.create_all(engine)
        seed_automatic_id_records(engine)
        with engine.begin() as connection:
            for table in ("users", "services"):
                if generation != "serial":
                    mode = "ALWAYS" if generation == "identity-always" else "BY DEFAULT"
                    connection.execute(text(
                        f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT"
                    ))
                    # Preserve the old SERIAL sequence and its state, but free
                    # the conventional name for the new identity sequence.
                    connection.execute(text(
                        f"ALTER SEQUENCE {table}_id_seq "
                        f"RENAME TO {table}_previous_serial_seq"
                    ))
                    connection.execute(text(
                        f"ALTER TABLE {table} ALTER COLUMN id ADD GENERATED {mode} "
                        "AS IDENTITY"
                    ))
                else:
                    # Names/ownership are not requirements for a usable default.
                    connection.execute(text(
                        f"ALTER SEQUENCE {table}_id_seq OWNED BY NONE"
                    ))
                    connection.execute(text(
                        f'ALTER SEQUENCE {table}_id_seq RENAME TO "{table} unusual seq"'
                    ))
        before = automatic_id_snapshot(engine)
        for _ in range(2):
            with engine.connect() as connection:
                assert schema_mismatches(connection) == []
            result = run_hook(env)
            assert result.returncode == 0, result.stdout + result.stderr
            assert automatic_id_snapshot(engine) == before
        # Both ORM inserts omit the ID, including GENERATED ALWAYS identity.
        with Session(engine) as session:
            user = User(email="new@example.invalid", password_hash="new-hash")
            session.add(user)
            session.flush()
            service = Service(
                user_id=user.id, name="New trial", category="Other",
                expires_on=date(2026, 11, 1),
            )
            session.add(service)
            session.commit()
            assert user.id not in (None, 42)
            assert service.id not in (None, 7)
    finally:
        engine.dispose()
