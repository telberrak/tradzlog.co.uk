#!/usr/bin/env bash
# One-time setup of TradzLog on the EC2 instance that already runs Mizan (safe to run again).
# Mizan's setup.sh already installed Docker, the AWS CLI, the "deploy" user and Caddy; this only
# adds TradzLog's directory, its settings and backup scripts, and the nightly backup job.
# From your PC:
#   scp -r deploy/ec2 ubuntu@<ip>:tradzlog-ec2
#   ssh ubuntu@<ip> 'sudo bash tradzlog-ec2/install.sh'
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"

for need in docker /snap/bin/aws python3; do
  command -v "$need" >/dev/null 2>&1 || { echo "Missing $need: run Mizan's deploy/ec2/setup.sh first." >&2; exit 1; }
done
id deploy >/dev/null 2>&1 || { echo "No deploy user: run Mizan's deploy/ec2/setup.sh first." >&2; exit 1; }
[ -d /srv/mizan/sites.d ] || { echo "No /srv/mizan/sites.d: TradzLog is served by Mizan's Caddy." >&2; exit 1; }
docker network inspect mizan_default >/dev/null 2>&1 || echo "Note: Docker network mizan_default not found yet; start Mizan before deploying TradzLog."

echo "== /srv/tradzlog"
install -d -o deploy -g deploy /srv/tradzlog
install -m 644 -o deploy -g deploy "$here/compose.yml" "$here/tradzlog.caddy.template" "$here/render-caddy.py" /srv/tradzlog/
install -m 755 -o deploy -g deploy "$here/config.sh" /srv/tradzlog/config.sh

echo "== Settings from AWS Parameter Store (/tradzlog/*)"
install -m 755 "$here/config.sh" /usr/local/bin/tradzlog-config

echo "== Nightly database backups (03:45 UTC, after Mizan's; kept 14 days)"
install -m 755 "$here/backup.sh" /usr/local/bin/tradzlog-backup
cat >/etc/cron.d/tradzlog-backup <<'EOF'
45 3 * * * root /usr/local/bin/tradzlog-backup >>/var/log/tradzlog-backup.log 2>&1
EOF

cat <<EOF

Done. Next (docs/DEPLOY_AWS.md):
  1. Add deploy/ec2/iam-policy.json to the instance's IAM role, and set the metadata hop limit to 2.
  2. Put the settings in Parameter Store under /tradzlog/.
  3. Point the domain's DNS at this server, then deploy from GitHub.
EOF
