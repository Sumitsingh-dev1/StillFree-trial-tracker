"""Real scaffold commands must fail before they can contact any database."""

import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "lib/db"
COMMANDS = [
    (ROOT, ["pnpm", "--filter", "@workspace/db", "run", "push"]),
    (ROOT, ["pnpm", "--filter", "@workspace/db", "run", "push-force", "--force"]),
    (DB, ["pnpm", "run", "push"]),
    (DB, ["pnpm", "run", "push-force"]),
    # Load the retained config with the workspace's existing TypeScript runner.
    # This exercises the bypass guard without reinstalling the removed CLI.
    (ROOT, [
        "pnpm", "--filter", "@workspace/scripts", "exec", "tsx",
        str(DB / "drizzle.config.ts"),
    ]),
]
REMOVED_CLI_COMMANDS = [
    (DB, ["pnpm", "exec", "drizzle-kit", "push", "--force"]),
    (ROOT, [
        "pnpm", "--filter", "@workspace/db", "exec", "drizzle-kit",
        "push", "--force", "--config", "./drizzle.config.ts",
    ]),
]


@pytest.mark.parametrize(
    "cwd,command,cli_removed",
    [(cwd, command, False) for cwd, command in COMMANDS]
    + [(cwd, command, True) for cwd, command in REMOVED_CLI_COMMANDS],
)
@pytest.mark.parametrize("configured_database", [False, True], ids=["no-url", "with-url"])
def test_scaffold_write_commands_cannot_reach_database(
    cwd, command, cli_removed, configured_database,
):
    # A real Postgres connection would start by opening this socket. Zero attempts
    # proves these commands cannot issue DDL or mutate users/services, without ever
    # supplying the application database's connection string to a destructive tool.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.1)
        env = {**os.environ}
        env.pop("DATABASE_URL", None)
        if configured_database:
            port = listener.getsockname()[1]
            env["DATABASE_URL"] = f"postgresql://127.0.0.1:{port}/isolated"
        result = subprocess.run(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        if cli_removed:
            assert 'Command "drizzle-kit" not found' in output
        else:
            assert "StillFree scaffold schema writes are disabled" in output
            assert "SQLAlchemy owns the database" in output
            assert "python -m app.migrate" in output
        with pytest.raises(socket.timeout):
            connection, _ = listener.accept()
            connection.close()


def test_scaffold_workspace_exports_are_retained():
    package = json.loads((DB / "package.json").read_text())
    assert package["name"] == "@workspace/db"
    assert package["exports"] == {
        ".": "./src/index.ts",
        "./schema": "./src/schema/index.ts",
    }
    for path in package["exports"].values():
        assert (DB / path).is_file()


def test_drizzle_cli_is_not_a_workspace_dependency():
    manifests = [ROOT / "package.json", ROOT / "scripts/package.json"]
    for directory in ("lib", "artifacts"):
        manifests.extend((ROOT / directory).glob("*/package.json"))
    for manifest in manifests:
        package = json.loads(manifest.read_text())
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            assert "drizzle-kit" not in package.get(section, {}), str(manifest)
    lockfile = (ROOT / "pnpm-lock.yaml").read_text()
    assert "drizzle-kit" not in lockfile
    assert "@esbuild-kit/esm-loader" not in lockfile
