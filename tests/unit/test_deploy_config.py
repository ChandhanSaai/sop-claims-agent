"""The container and CI files are not run by pytest; these pin the contracts the README states."""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_healthcheck_probes_the_port_the_command_binds():
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    check = next(line for line in lines if line.strip().startswith("CMD python -c"))
    assert "PORT" in check and ":8000/healthz" not in check
    code = re.search(r'python -c "(.*)"$', check.strip())[1]
    # the probe runs as written: with nothing listening on PORT it exits non-zero, not with a SyntaxError
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PORT": "1"}, capture_output=True)
    assert r.returncode != 0 and b"SyntaxError" not in r.stderr


def test_ci_builds_the_image():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "docker build -t sop-claims-agent ." in ci and "push" not in ci.split("docker build")[1]
