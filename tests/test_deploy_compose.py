import re
from pathlib import Path

import yaml

COMPOSE = yaml.safe_load((Path(__file__).resolve().parents[1] / "deploy" / "ec2" / "compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]


def default_aliases(service: str) -> set[str]:
    networks = SERVICES[service].get("networks") or {}
    return set((networks.get("default") or {}).get("aliases") or [])


def test_shared_network_services_only_reach_backends_by_unique_names() -> None:
    # web and api also sit on Mizan's network, which has its own "postgres": a bare service name
    # there resolves to Mizan's database. Backends must be addressed by TradzLog-only aliases.
    on_edge = [name for name, svc in SERVICES.items() if "edge" in (svc.get("networks") or {})]
    assert set(on_edge) == {"api", "web"}

    env = SERVICES["api"]["environment"]
    db_host = re.search(r"@([^:/]+):5432/", env["DATABASE_URL"]).group(1)
    redis_host = re.search(r"redis://([^:/]+):", env["REDIS_URL"]).group(1)
    assert db_host == "tradzlog-postgres" and db_host in default_aliases("postgres")
    assert redis_host == "tradzlog-redis" and redis_host in default_aliases("redis")
    assert f"redis://{redis_host}:" in " ".join(SERVICES["worker"]["command"])


def test_api_health_gate_needs_the_database() -> None:
    assert "/readyz" in " ".join(SERVICES["api"]["healthcheck"]["test"])


def test_nothing_publishes_host_ports() -> None:
    assert all("ports" not in svc for svc in SERVICES.values())
