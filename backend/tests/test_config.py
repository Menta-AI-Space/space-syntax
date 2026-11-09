"""Settings loading — especially that it does not depend on the working directory."""

import os
import subprocess
import sys
from pathlib import Path

from app.config import _BACKEND_ROOT, _ENV_FILES

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_env_file_candidates_are_absolute():
    """Regression: these used to be ".env"/"../.env"/"../../.env", resolved
    against the working directory. Starting the app from anywhere but backend/
    (e.g. `uvicorn --app-dir backend` from the repo root) then found no .env,
    silently fell back to the defaults, and pointed DATABASE_URL at the wrong
    Postgres — every analysis 503'd on a missing roads_clean table.
    """
    assert _ENV_FILES, "expected at least one env file candidate"
    for path in _ENV_FILES:
        assert path.is_absolute(), f"env_file candidate must be absolute: {path}"


def test_backend_env_file_is_a_candidate():
    assert _BACKEND_ROOT / ".env" in _ENV_FILES


def _database_url_from(cwd: Path) -> str:
    """Load Settings in a fresh interpreter started from `cwd`."""
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", "from app.config import settings; print(settings.database_url)"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_database_url_is_identical_regardless_of_cwd(tmp_path):
    """The whole point of anchoring env_file to this file: where the process
    was started from must not change which database the app talks to.
    """
    from_backend = _database_url_from(BACKEND_ROOT)
    from_elsewhere = _database_url_from(tmp_path)

    assert from_backend == from_elsewhere
