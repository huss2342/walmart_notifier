"""Safety invariants for the recommended Docker runtime."""

from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_unauthenticated_container_bind_is_published_only_on_host_loopback():
    """The server's container exception is safe only with this port mapping."""
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    ports_block = compose.split("    ports:\n", 1)[1].split("    volumes:\n", 1)[0]

    assert 'CONTAINER_LOOPBACK_ONLY: "true"' in compose
    assert ports_block.strip() == '- "127.0.0.1:8787:8787"'


def test_runtime_secrets_and_state_are_excluded_from_the_build_context():
    dockerignore = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert dockerignore == ["**", "!src/", "!src/**"]
