---
name: Schema reflection limits
description: Cross-dialect reflection pitfalls when comparing database safeguards.
---

SQLite reflection can omit inline UNIQUE constraints from both the default constraint list and index list; include automatic indexes when checking uniqueness.

**Why:** A valid existing inline UNIQUE email column was reported as missing its guarantee during development setup, while PostgreSQL reflected it correctly.

**How to apply:** Test existing handwritten schemas as well as model-created schemas. PostgreSQL reflects simple IN checks as ANY/ARRAY expressions with explicit casts, so compare the guarantee rather than its name or raw SQL spelling.

Keep legacy-expression inspection read-only; never execute defaults or CHECK
predicates, or probe model inserts against the application database, to infer
compatibility.

**Why:** Functions in defaults or CHECK rules can advance PostgreSQL sequences,
and those effects may survive a rolled-back transaction. Preserving existing
data takes priority over proving arbitrary SQL expressions safe.

**How to apply:** Use reflected metadata and conservative static classification.
Use disposable databases for insert experiments. Detected incompatibilities
require an explicit, reviewed migration, never automatic legacy-column repair.

Do not rely on reflected foreign-key options to determine PostgreSQL validation
status.

**Why:** SQLAlchemy exposes `NOT VALID` for reflected checks but omits it from
foreign-key reflection. Matching the predicate or reference alone therefore
does not establish a guarantee for older rows.

**How to apply:** Read PostgreSQL catalog validation state scoped to the actual
relation, not globally by constraint name. Require a validated equivalent model
guarantee; report a reviewed cleanup/validation step rather than scanning,
repairing, or automatically validating application records.
