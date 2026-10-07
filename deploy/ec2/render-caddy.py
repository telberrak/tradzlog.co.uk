"""Render tradzlog.caddy from the template and /srv/tradzlog/.env.

Usage: python3 render-caddy.py <template> <env file> <output>

Refuses to write a site without the basic-auth gate unless TRADZLOG_PUBLIC=true, so a missing
setting can never publish the app before it has its own sign-in.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile


def read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key] = value
    return values


def main() -> None:
    template_path, env_path, output_path = sys.argv[1:4]
    env = read_env(env_path)

    domain = env.get("TRADZLOG_DOMAIN", "")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        sys.exit(f"render-caddy: TRADZLOG_DOMAIN is missing or not a domain: {domain!r}")

    user = env.get("TRADZLOG_BASIC_AUTH_USER", "")
    password_hash = env.get("TRADZLOG_BASIC_AUTH_HASH", "")
    if user and password_hash:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", user) or not password_hash.startswith("$2"):
            sys.exit("render-caddy: TRADZLOG_BASIC_AUTH_USER must be a plain name and TRADZLOG_BASIC_AUTH_HASH a bcrypt hash (caddy hash-password)")
        basic_auth = f"basic_auth {{\n\t\t{user} {password_hash}\n\t}}"
    elif env.get("TRADZLOG_PUBLIC") == "true":
        basic_auth = "# Public: no basic-auth gate (TRADZLOG_PUBLIC=true)."
    else:
        sys.exit("render-caddy: set TRADZLOG_BASIC_AUTH_USER and TRADZLOG_BASIC_AUTH_HASH (or TRADZLOG_PUBLIC=true once the app has sign-in)")

    with open(template_path, encoding="utf-8") as handle:
        site = handle.read().replace("__TRADZLOG_DOMAIN__", domain).replace("__BASIC_AUTH__", basic_auth)

    directory = os.path.dirname(output_path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tradzlog.caddy.")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(site)
    os.chmod(tmp, 0o644)
    os.replace(tmp, output_path)
    print(f"render-caddy: wrote {output_path} for {domain}")


if __name__ == "__main__":
    main()
