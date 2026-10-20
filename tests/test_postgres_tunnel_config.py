"""Contrato estático do PostgreSQL MCP acessado pelo túnel SSH."""

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]


def test_chave_do_host_entra_read_only_em_caminho_fixo() -> None:
    mounts = SERVICES["postgres-tunnel"]["volumes"]
    key_mount = next(mount for mount in mounts if isinstance(mount, dict))

    assert key_mount["source"].startswith("${PRIVATE_KEY_PATH:")
    assert key_mount["target"] == "/run/secrets/tunnel_key"
    assert key_mount["read_only"] is True
    assert "/run/secrets/tunnel_key" in SERVICES["postgres-tunnel"]["command"]


def test_postgres_mcp_conecta_pelo_tunel_e_fica_restricted() -> None:
    service = SERVICES["postgres-mcp"]
    uri = service["environment"]["DATABASE_URI"]

    assert "@postgres-tunnel:${TUNNEL_LOCAL_PORT:-5432}/" in uri
    assert "--access-mode=restricted" in service["command"]
    assert "profiles" not in service
    assert "postgres-mcp" in SERVICES["hayhooks"]["depends_on"]


def test_trino_nao_sobe_no_caminho_padrao() -> None:
    assert SERVICES["trino-mcp"]["profiles"] == ["trino"]


def test_defaults_da_aplicacao_sao_postgres() -> None:
    settings = (ROOT / "src/rag/settings/configs.py").read_text(encoding="utf-8")
    query_prompt = (ROOT / "src/rag/prompts/query_system.md").read_text(
        encoding="utf-8"
    )
    explore_prompt = (ROOT / "src/rag/prompts/explore_system.md").read_text(
        encoding="utf-8"
    )

    assert '"http://postgres-mcp:8000/sse"' in settings
    assert '"postgres", description="Dialeto do SQL' in settings
    assert "**PostgreSQL**" in query_prompt
    assert "Dialeto **PostgreSQL**" in explore_prompt
