"""Create missing development tables; reject unsupported schema changes."""

import os
import re
import sys

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text

from app.database import Base, engine
from app.models import User  # noqa: F401 - register the model with Base.metadata


def _check_signature(sql: str) -> tuple[str, ...]:
    """Compare our simple predicates despite PostgreSQL reflection formatting.

    PostgreSQL renders IN as = ANY (ARRAY[...]) and adds scalar type casts.
    Keep quoted values intact (including case and spaces). This is deliberately
    not a general SQL equivalence checker; unfamiliar expressions fail closed.
    """
    tokens = []
    # Normalize syntax only outside string literals. A legacy category such as
    # 'Cloud::text' must not become indistinguishable from the valid 'Cloud'.
    for part in re.split(r"('(?:''|[^'])*')", sql):
        if part.startswith("'"):
            tokens.append(part)
            continue
        part = re.sub(
            # Type modifiers can narrow a value or overflow before comparison.
            # Only erase unbounded casts; unfamiliar casts require review.
            r"::(?:character varying|text|numeric)\b(?!\s*\()(?:\[\])?",
            "", part, flags=re.IGNORECASE,
        )
        part = re.sub(
            r"=\s*ANY\s*\(\s*ARRAY\s*\[", " IN (", part, flags=re.IGNORECASE,
        )
        tokens.extend(token.lower() for token in re.findall(
            r">=|<=|<>|!=|[a-zA-Z_]\w*|\d+|[^\s()\[\]]", part,
        ))
    return tuple(tokens)


def _unvalidated_constraints(connection, table) -> set[str]:
    if connection.dialect.name != "postgresql":
        return set()
    # Foreign-key reflection omits NOT VALID, so use the catalog for both kinds.
    # Resolve the quoted relation, not a constraint name shared by other tables.
    relation = connection.dialect.identifier_preparer.format_table(table)
    return set(connection.scalars(text(
        "SELECT conname FROM pg_catalog.pg_constraint "
        "WHERE conrelid = pg_catalog.to_regclass(:relation) "
        "AND contype IN ('c', 'f') AND NOT convalidated"
    ), {"relation": relation}))


def _unvalidated_issue(table, kind, name) -> str:
    return (
        f"{table.fullname}: NOT VALID {kind} constraint {name}; older rows have "
        "not been checked. A reviewed data cleanup and constraint validation "
        "step is required before rerunning setup."
    )


def _supports_automatic_id(connection, inspector, table, column, existing) -> bool:
    """Recognize database-owned generators without executing their expressions."""
    if connection.dialect.name == "postgresql":
        relation = connection.dialect.identifier_preparer.format_table(table)
        generation = connection.execute(text(
            "SELECT a.attidentity, pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS expr, "
            "EXISTS (SELECT 1 FROM pg_catalog.pg_depend dep "
            "JOIN pg_catalog.pg_class seq ON seq.oid = dep.refobjid "
            "WHERE dep.classid = 'pg_catalog.pg_attrdef'::regclass "
            "AND dep.objid = d.oid "
            "AND dep.refclassid = 'pg_catalog.pg_class'::regclass "
            "AND seq.relkind = 'S' "
            "AND (pg_catalog.has_sequence_privilege(seq.oid, 'USAGE') "
            "OR pg_catalog.has_sequence_privilege(seq.oid, 'UPDATE'))) AS usable_sequence "
            "FROM pg_catalog.pg_attribute a "
            "LEFT JOIN pg_catalog.pg_attrdef d "
            "ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "WHERE a.attrelid = pg_catalog.to_regclass(:relation) "
            "AND a.attname = :column AND NOT a.attisdropped"
        ), {"relation": relation, "column": column.name}).mappings().one()
        if generation["attidentity"] in ("a", "d"):
            return True
        # Only a direct nextval(regclass) is established as insert-compatible.
        # Constants, NULL, wrappers and arbitrary functions need human review.
        return bool(
            generation["usable_sequence"]
            and re.fullmatch(
                r"(?:pg_catalog\.)?nextval\('(?:[^']|'')+'::regclass\)",
                generation["expr"] or "",
            )
        )
    if connection.dialect.name == "sqlite":
        quote = connection.dialect.identifier_preparer.quote_identifier
        schema = quote(table.schema or inspector.default_schema_name)
        tables = connection.exec_driver_sql(f"PRAGMA {schema}.table_list").mappings()
        row = next((row for row in tables if row["name"] == table.name), None)
        pk = inspector.get_pk_constraint(table.name, schema=table.schema)
        # INTEGER PRIMARY KEY aliases rowid even without AUTOINCREMENT. A
        # WITHOUT ROWID table or the historical inline PRIMARY KEY DESC case
        # instead has an ordinary key and cannot generate an omitted ID.
        indexes = connection.exec_driver_sql(
            f"PRAGMA {schema}.index_list({quote(table.name)})"
        ).mappings()
        return bool(
            row is not None and not row["wr"]
            and str(existing["type"]).upper() == "INTEGER"
            and pk.get("constrained_columns") == [column.name]
            and not any(index["origin"] == "pk" for index in indexes)
        )
    return False


def _constraint_mismatches(connection, inspector, table) -> list[str]:
    issues = []
    expected_pk = tuple(column.name for column in table.primary_key.columns)
    existing_pk = tuple(
        inspector.get_pk_constraint(table.name, schema=table.schema).get(
            "constrained_columns"
        ) or ()
    )
    if existing_pk != expected_pk:
        issues.append(
            f"{table.fullname}: missing or incompatible primary key "
            f"(database {', '.join(existing_pk) or 'none'}; "
            f"model {', '.join(expected_pk) or 'none'})"
        )
    unvalidated = _unvalidated_constraints(connection, table)
    unique_columns = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints(table.name, schema=table.schema)
    }
    unique_columns.update(
        tuple(item["column_names"])
        for item in inspector.get_indexes(
            table.name, schema=table.schema, include_auto_indexes=True,
        )
        if item.get("unique")
        and all(item["column_names"])
        and item.get("dialect_options", {}).get("postgresql_where") is None
        and item.get("dialect_options", {}).get("sqlite_where") is None
    )
    expected_unique = [
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    ] + [
        tuple(column.name for column in index.columns)
        for index in table.indexes if index.unique
    ]
    for columns in expected_unique:
        if columns not in unique_columns:
            issues.append(f"{table.fullname}: missing unique guarantee ({', '.join(columns)})")

    foreign_keys = inspector.get_foreign_keys(table.name, schema=table.schema)
    for constraint in table.foreign_key_constraints:
        columns = [element.parent.name for element in constraint.elements]
        target = constraint.elements[0].column.table
        referred = [element.column.name for element in constraint.elements]
        matches = [
            fk for fk in foreign_keys
            if (
                fk["constrained_columns"] == columns
                and fk["referred_table"] == target.name
                and (fk["referred_schema"] or inspector.default_schema_name)
                == (target.schema or inspector.default_schema_name)
                and fk["referred_columns"] == referred
                and fk.get("options", {}).get("ondelete") == constraint.ondelete
            )
        ]
        if not matches:
            issues.append(
                f"{table.fullname}: missing foreign key ({', '.join(columns)}) "
                f"-> {target.fullname}({', '.join(referred)})"
            )
        elif all(fk["name"] in unvalidated for fk in matches):
            issues.append(_unvalidated_issue(table, "foreign key", matches[0]["name"]))

    checks = inspector.get_check_constraints(table.name, schema=table.schema)
    expected_checks = set()
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            expected = str(constraint.sqltext.compile(
                dialect=connection.dialect, compile_kwargs={"literal_binds": True},
            ))
            signature = _check_signature(expected)
            expected_checks.add(signature)
            matches = [
                item for item in checks
                if _check_signature(item["sqltext"]) == signature
            ]
            if not matches:
                issues.append(
                    f"{table.fullname}: missing or incompatible check constraint "
                    f"{constraint.name} ({expected})"
                )
            elif all(item["name"] in unvalidated for item in matches):
                issues.append(_unvalidated_issue(table, "check", matches[0]["name"]))
    for item in checks:
        if _check_signature(item["sqltext"]) not in expected_checks:
            # Existing rows satisfying a legacy rule do not establish that new
            # model-valid values can be saved. Never probe inserts or evaluate
            # arbitrary predicates: unsupported extras require human review.
            issues.append(
                f"{table.fullname}: extra check constraint "
                f"{item['name'] or '<unnamed>'} ({item['sqltext']}) may reject "
                "model-valid inserts or updates; compatibility is not established. "
                "Review this predicate in an explicit, data-preserving development "
                "migration before rerunning setup; no rule was dropped or replaced."
            )
    return issues


def schema_mismatches(connection) -> list[str]:
    """Read-only preflight of model guarantees in tables that already exist.

    Compare dialect-rendered types so reflected aliases (e.g. PostgreSQL
    TIMESTAMP and SQLAlchemy DateTime) agree, including length/precision and
    timezone. This is conservative: differences require human review, not DDL.
    Extra legacy tables/columns are never changed, but required extra columns
    must support inserts that omit them. Extra checks on model tables require
    review unless equivalent to a model check. This is not a full migration planner:
    default expressions are not executed or proven safe. Automatic primary-key
    generation is checked conservatively; other model defaults and non-unique
    indexes are not compared.
    """
    inspector = inspect(connection)
    issues = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name, schema=table.schema):
            continue
        actual = {
            column["name"]: column
            for column in inspector.get_columns(table.name, schema=table.schema)
        }
        for name, existing in actual.items():
            if name in table.columns or existing["nullable"]:
                continue
            # Generated/identity values need not have a reflected default.
            if existing.get("computed") is not None or existing.get("identity") is not None:
                continue
            default = existing.get("default")
            # A literal NULL is not a usable default for a NOT NULL column.
            # Ignore parentheses/space and PostgreSQL casts, not quoted strings
            # such as 'NULL'. Never evaluate an arbitrary legacy SQL expression.
            normalized = re.sub(r"[\s()]", "", str(default))
            if default is None or re.match(r"^NULL(?:::|$)", normalized, re.IGNORECASE):
                issues.append(
                    f"{table.fullname}.{name}: extra NOT NULL column without a "
                    "usable server default or generated value blocks model inserts"
                )
        for column in table.columns:
            label = f"{table.fullname}.{column.name}"
            if column.name not in actual:
                issues.append(f"{label}: missing column")
                continue
            existing = actual[column.name]
            if (
                column is table.autoincrement_column
                and not _supports_automatic_id(
                    connection, inspector, table, column, existing,
                )
            ):
                issues.append(
                    f"{label}: missing or unsupported automatic ID generation "
                    "blocks model inserts that omit the primary key; a reviewed, "
                    "data-preserving development migration is required"
                )
            expected_type = column.type.compile(dialect=connection.dialect).upper()
            actual_type = existing["type"].compile(dialect=connection.dialect).upper()
            # SQLite accepts both spellings for SQLAlchemy's datetime storage.
            if connection.dialect.name == "sqlite":
                expected_type = {"TIMESTAMP": "DATETIME"}.get(expected_type, expected_type)
                actual_type = {"TIMESTAMP": "DATETIME"}.get(actual_type, actual_type)
            if expected_type != actual_type:
                issues.append(
                    f"{label}: incompatible type (database {actual_type}; "
                    f"model {expected_type})"
                )
            # SQLite reflects INTEGER PRIMARY KEY as nullable; don't flag that.
            if not column.primary_key and column.nullable != existing["nullable"]:
                issues.append(
                    f"{label}: incompatible nullability "
                    f"(database nullable={existing['nullable']}; "
                    f"model nullable={column.nullable})"
                )
        issues.extend(_constraint_mismatches(connection, inspector, table))
    return issues


def main() -> None:
    if os.environ.get("REPLIT_DEPLOYMENT") == "1":
        sys.exit("Schema setup is development-only; published deployments are refused.")
    with engine.begin() as connection:
        issues = schema_mismatches(connection)
        if issues:
            sys.exit(
                "StillFree development schema is incompatible:\n"
                + "\n".join(f"  - {issue}" for issue in issues)
                + "\nNo schema changes were applied. create_all only creates missing "
                "tables; it cannot update existing columns or constraints. Prepare and review an "
                "explicit, data-preserving development migration, then rerun "
                "scripts/post-merge.sh. Do not drop tables or reset the database. "
                "Production migrations are outside this command."
            )
        Base.metadata.create_all(bind=connection)
    print("StillFree development schema is ready.")


if __name__ == "__main__":
    main()
