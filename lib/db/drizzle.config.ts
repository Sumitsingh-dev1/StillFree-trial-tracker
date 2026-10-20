// Also block direct `pnpm exec drizzle-kit push` calls that bypass package scripts.
// Keep this config discoverable so the CLI fails explicitly, before database access.
import "./disabled-schema-writes.cjs";

export default {};
