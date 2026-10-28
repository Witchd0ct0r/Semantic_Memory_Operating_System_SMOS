from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


def test_dockerfile_runs_non_root_with_offline_model_and_volume() -> None:
    text = _read("Dockerfile")
    assert text.startswith("FROM python:3.12-slim") or "\nFROM python:3.12-slim" in text
    assert re.search(r"^USER smos$", text, re.M), "container must not run as root"
    assert "--uid 10001" in text
    assert "VOLUME /data" in text
    assert "SMOS_EMBEDDING_MODEL_DIR=/opt/smos/models/all-MiniLM-L6-v2" in text
    assert "HF_HUB_OFFLINE=1" in text and "TRANSFORMERS_OFFLINE=1" in text
    assert re.search(r"^COPY models ", text, re.M)
    assert "HEALTHCHECK" in text and "/api/v1/health" in text
    assert 'CMD ["smos-server"]' in text
    assert "ARG TORCH_INDEX_URL" in text


def test_dockerfile_has_no_secrets() -> None:
    text = _read("Dockerfile").lower()
    for word in ("password", "token", "secret"):
        assert word not in text, word


def test_compose_persists_data_and_reads_env_file() -> None:
    text = _read("compose.yaml")
    assert "smos-data:/data" in text
    assert re.search(r"^volumes:\s*\n\s+smos-data:", text, re.M)
    assert "env_file" in text and ".env" in text
    assert "restart: unless-stopped" in text
    assert "8765:8765" in text
    assert "TORCH_INDEX_URL" in text
    assert "privileged" not in text and "docker.sock" not in text


def test_env_example_has_placeholders_only_and_env_is_ignored() -> None:
    example = _read(".env.example")
    assert "SMOS_ADMIN_USER=" in example and "SMOS_ADMIN_PASSWORD=change-me" in example
    assert "SMOS_PUBLIC_URL=" in example
    assert re.search(r"^\.env$", _read(".gitignore"), re.M)


def test_dockerignore_keeps_secrets_and_junk_out_of_the_image() -> None:
    lines = {line.strip() for line in _read(".dockerignore").splitlines()}
    for wanted in (".git", ".env", "tests", ".superpowers", "__pycache__"):
        assert wanted in lines, wanted
    assert "models" not in lines


def test_bundled_model_folder_is_complete() -> None:
    folder = _ROOT / "models" / "all-MiniLM-L6-v2"
    for name in ("modules.json", "config.json", "tokenizer.json", "model.safetensors", "NOTICE.md"):
        assert (folder / name).is_file(), name


def test_build_can_trust_an_extra_ca_bundle() -> None:
    text = _read("Dockerfile")
    assert re.search(r"^COPY certs ", text, re.M)
    assert "/opt/smos/certs/ca-bundle.crt" in text
    assert "PIP_CERT" in text
    assert (_ROOT / "certs" / "README.md").is_file()
    assert re.search(r"^certs/\*\.crt$", _read(".gitignore"), re.M), "local CA certificates must not be committed"
    lines = {line.strip() for line in _read(".dockerignore").splitlines()}
    assert "certs" not in lines


def test_every_pip_step_uses_the_extra_ca_bundle() -> None:
    """pip downloads build tools even for `pip install .`, so each RUN that calls pip needs the bundle."""
    text = _read("Dockerfile")
    steps = re.split(r"\n(?=(?:RUN|COPY|ENV|USER|WORKDIR|VOLUME|EXPOSE|HEALTHCHECK|CMD|FROM|ARG)\b)", text)
    pip_steps = [s for s in steps if s.startswith("RUN") and "pip install" in s]
    assert len(pip_steps) >= 2
    for step in pip_steps:
        assert "PIP_CERT=/opt/smos/certs/ca-bundle.crt" in step, step[:80]
