#!/usr/bin/env bash
# Writes /srv/tradzlog/.env from AWS Systems Manager Parameter Store: every parameter under
# /tradzlog/ becomes a setting (/tradzlog/JWT_SECRET → JWT_SECRET). Parameter Store is the only
# place to change settings; this file is replaced on every deploy, so never edit it by hand.
#
# Installed by install.sh as /usr/local/bin/tradzlog-config and run by each deploy as the deploy
# user, with the instance's IAM role (read-only on /tradzlog/*). Same approach as mizan-config.
#
# Safety: it stops without changing anything if a required setting is missing, or if
# POSTGRES_PASSWORD differs from the one the database was created with.
set -euo pipefail

prefix="${TRADZLOG_SSM_PATH:-/tradzlog/}"
env_file="${TRADZLOG_ENV_FILE:-/srv/tradzlog/.env}"
aws="${AWS_CLI:-/snap/bin/aws}"

region="${AWS_REGION:-}"
if [ -z "$region" ]; then
  imds_token="$(curl -sf -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
  region="$(curl -sf -H "X-aws-ec2-metadata-token: $imds_token" http://169.254.169.254/latest/meta-data/placement/region)"
fi

errors="$(mktemp)"
trap 'rm -f "$errors"' EXIT
if ! params="$("$aws" ssm get-parameters-by-path --region "$region" --path "$prefix" --recursive --with-decryption --output json 2>"$errors")"; then
  echo "tradzlog-config: Parameter Store could not be read (does the instance role allow $prefix*?): $(tr '\n' ' ' <"$errors")" >&2
  exit 1
fi

render='
import json, os, re, sys, tempfile

env_file, prefix, region = sys.argv[1], sys.argv[2], sys.argv[3]
settings = {p["Name"][len(prefix):]: p["Value"] for p in json.load(sys.stdin)["Parameters"]}

bad = [name for name in settings if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name)]
if bad:
    sys.exit("tradzlog-config: parameter names must look like VARIABLE_NAMES: " + ", ".join(prefix + b for b in bad))
missing = [name for name in ("TRADZLOG_DOMAIN", "POSTGRES_PASSWORD", "JWT_SECRET", "S3_BUCKET") if not settings.get(name)]
if missing:
    sys.exit("tradzlog-config: missing in Parameter Store: " + ", ".join(prefix + m for m in missing) + ". Nothing was changed.")
if not re.fullmatch(r"[A-Za-z0-9_-]{16,}", settings["POSTGRES_PASSWORD"]):
    sys.exit("tradzlog-config: POSTGRES_PASSWORD must be 16+ letters, digits, - or _ (it goes into a database URL). Nothing was changed.")
if len(settings["JWT_SECRET"]) < 32:
    sys.exit("tradzlog-config: JWT_SECRET must be at least 32 characters. Nothing was changed.")

SQ = chr(39)
BS = chr(92)

current = {}
if os.path.exists(env_file):
    for line in open(env_file, encoding="utf-8"):
        line = line.rstrip("\n")
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in (SQ, "\""):
                value = value[1:-1]
            current[key] = value

running = current.get("POSTGRES_PASSWORD")
if running and running != settings["POSTGRES_PASSWORD"] and os.environ.get("TRADZLOG_ALLOW_PASSWORD_CHANGE") != "1":
    sys.exit("tradzlog-config: POSTGRES_PASSWORD in Parameter Store differs from the one the database uses. Nothing was changed.")

# The running version is managed by the deploy, not Parameter Store.
settings["TRADZLOG_TAG"] = current.get("TRADZLOG_TAG") or settings.get("TRADZLOG_TAG") or "latest"
settings.setdefault("AWS_REGION", region)

def quote(value):
    if SQ not in value and "\n" not in value:
        return SQ + value + SQ
    escaped = "".join(BS + ch if ch in (BS, "\"", "$") else ch for ch in value).replace("\n", BS + "n")
    return "\"" + escaped + "\""

lines = [f"# Written by tradzlog-config from AWS Parameter Store ({prefix}*). Do not edit: it is replaced on every deploy."]
lines += [f"{key}={quote(value)}" for key, value in sorted(settings.items())]
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(env_file) or ".", prefix=".env.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    os.fchmod(f.fileno(), 0o600)
    f.write("\n".join(lines) + "\n")
os.replace(tmp, env_file)
print(f"tradzlog-config: {len(settings) - 1} settings from {prefix} written to {env_file} (values not shown)")
'
python3 -c "$render" "$env_file" "$prefix" "$region" <<<"$params"
