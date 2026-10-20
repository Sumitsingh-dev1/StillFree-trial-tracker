"""Guard publishing configuration without starting a scheduler or using a database."""

from pathlib import Path
import runpy
import tomllib

import uvicorn


ROOT = Path(__file__).resolve().parents[3]


def artifact_config(slug):
    path = ROOT / "artifacts" / slug / ".replit-artifact" / "artifact.toml"
    return tomllib.loads(path.read_text())


def test_web_registration_owns_the_only_python_service():
    source = artifact_config("api-server")
    web = artifact_config("stillfree")
    assert source["kind"] == "api"  # Immutable registration retained.
    assert source["services"] == []
    assert web["kind"] == "web"
    assert web["previewPath"] == "/"
    assert len(web["services"]) == 1
    service = web["services"][0]
    assert service["paths"] == ["/"]
    assert service["development"]["run"] == "pnpm --filter @workspace/api-server run dev"
    production = service["production"]
    assert "serve" not in production  # Dynamic Python, not the bootstrap's static SPA.
    assert "rewrites" not in production
    assert production["health"]["startup"]["path"] == "/api/healthz"
    assert production["build"]["args"] == [
        "sh", "-c", "pnpm install --frozen-lockfile && uv sync --frozen",
    ]
    assert production["run"]["args"] == [
        "uv", "run", "--no-sync", "python", "artifacts/api-server/run.py",
    ]
    assert production["run"]["env"]["NODE_ENV"] == "production"
    assert int(production["run"]["env"]["PORT"]) == service["localPort"]
    assert tomllib.loads((ROOT / ".replit").read_text())["deployment"]["deploymentTarget"] == "vm"


def test_entrypoint_runs_one_worker_on_the_assigned_port(monkeypatch):
    calls = []
    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    runpy.run_path(str(ROOT / "artifacts/api-server/run.py"), run_name="__main__")
    assert calls == [(("app.main:app",), {
        "host": "0.0.0.0", "port": 9123, "workers": 1, "reload": False,
    })]
