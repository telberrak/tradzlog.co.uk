import runpy
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "ec2"
render = runpy.run_path(str(DEPLOY / "render-caddy.py"))["main"]
HASH = "$2a$14$Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT/5qqR7hx4IjWJPDhjvG"


def run(tmp_path: Path, env: str) -> str:
    env_file = tmp_path / ".env"
    env_file.write_text(env, encoding="utf-8")
    out = tmp_path / "tradzlog.caddy"
    argv = sys.argv
    sys.argv = ["render-caddy.py", str(DEPLOY / "tradzlog.caddy.template"), str(env_file), str(out)]
    try:
        render()
    finally:
        sys.argv = argv
    return out.read_text(encoding="utf-8")


def test_site_routes_api_and_web_behind_basic_auth(tmp_path: Path) -> None:
    site = run(tmp_path, f"TRADZLOG_DOMAIN='tradzlog.co.uk'\nTRADZLOG_BASIC_AUTH_USER='tarik'\nTRADZLOG_BASIC_AUTH_HASH='{HASH}'\n")
    lines = site.splitlines()
    assert "tradzlog.co.uk {" in lines and "www.tradzlog.co.uk {" in lines
    assert f"basic_auth {{\n\t\ttarik {HASH}\n\t}}" in site
    assert "reverse_proxy tradzlog-api:8000" in site and "reverse_proxy tradzlog-web:8001" in site
    # The gate covers the web app only: the API authenticates with bearer tokens on the same header.
    api_block = site[site.index("handle /api/*") : site.index("handle {")]
    assert "basic_auth" not in api_block
    assert site.index("basic_auth") > site.index("handle {")
    assert "redir https://tradzlog.co.uk{uri} permanent" in site
    assert "__TRADZLOG_DOMAIN__" not in site and "__BASIC_AUTH__" not in site


def test_refuses_to_publish_without_gate_or_explicit_public(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="BASIC_AUTH"):
        run(tmp_path, "TRADZLOG_DOMAIN='tradzlog.co.uk'\n")
    site = run(tmp_path, "TRADZLOG_DOMAIN='tradzlog.co.uk'\nTRADZLOG_PUBLIC='true'\n")
    assert "basic_auth" not in site.replace("basic-auth", "")


def test_rejects_bad_domain_and_plain_password(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not a domain"):
        run(tmp_path, f"TRADZLOG_DOMAIN='evil.com {{ }}'\nTRADZLOG_BASIC_AUTH_USER='a'\nTRADZLOG_BASIC_AUTH_HASH='{HASH}'\n")
    with pytest.raises(SystemExit, match="bcrypt"):
        run(tmp_path, "TRADZLOG_DOMAIN='tradzlog.co.uk'\nTRADZLOG_BASIC_AUTH_USER='a'\nTRADZLOG_BASIC_AUTH_HASH='hunter2'\n")
