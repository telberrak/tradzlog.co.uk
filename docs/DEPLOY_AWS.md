# Deploying TradzLog on AWS (alongside Mizan)

TradzLog runs on the same EC2 instance as Mizan (t4g.small, eu-west-2), as its own Docker Compose
project in `/srv/tradzlog`. Screenshots go to a private S3 bucket. Everything Mizan already set up
is reused: Docker, the AWS CLI, the `deploy` user, Caddy for HTTPS, Parameter Store for settings,
and GitHub Actions for deploys.

```text
                    ┌──────────────────── EC2 (t4g.small) ─────────────────────┐
 https://tradzlog → │ Mizan's Caddy ──(mizan_default network)──┬─ tradzlog-web    │
                    │   sites.d/tradzlog.caddy                  └─ tradzlog-api   │
                    │                                                           │
                    │ TradzLog's private network: postgres · redis · worker     │
                    └───────────────────────────────┬───────────────────────────┘
                                                    └─ S3: tradzlog-uploads-… (private)
```

- Mizan's files are never edited. TradzLog only adds `/srv/mizan/sites.d/tradzlog.caddy`, the
  extension point Mizan's Caddyfile provides for other sites.
- TradzLog has its own PostgreSQL and Redis. Only `web` and `api` join Mizan's network, so
  TradzLog cannot reach Mizan's database and Mizan cannot reach TradzLog's.
- No TradzLog port is published on the host; all traffic goes through Caddy.
- Memory: TradzLog's containers are capped at about 1.2 GB in total and use roughly 450 MB
  when idle. Watch `docker stats` and `free -m` for the first week; if swap is in steady use,
  move the instance to t4g.medium (4 GB, about $13 a month more).

> **Sign-in and the beta gate.** The web app has its own accounts and sign-in (secure session
> cookie, CSRF protection, per-user data). Caddy can additionally put a username and password
> (HTTP basic auth) in front of the whole web app, for a private beta. The deploy refuses to
> publish the site without that gate unless you explicitly set `TRADZLOG_PUBLIC=true`; see
> [Removing the beta gate](#removing-the-beta-gate). The API under `/api` is never gated.

Run the AWS commands from your PC with the AWS CLI signed in to the account, region eu-west-2.

## 1. S3 bucket for screenshots

Bucket names are global, so add something of your own to the name:

```bash
aws s3api create-bucket --bucket tradzlog-uploads-CHANGE-ME --region eu-west-2 --create-bucket-configuration LocationConstraint=eu-west-2
```

```bash
aws s3api put-public-access-block --bucket tradzlog-uploads-CHANGE-ME --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

```bash
aws s3api put-bucket-encryption --bucket tradzlog-uploads-CHANGE-ME --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

Optional but recommended, so a deleted screenshot can be recovered for 30 days:

```bash
aws s3api put-bucket-versioning --bucket tradzlog-uploads-CHANGE-ME --versioning-configuration Status=Enabled
```

```bash
aws s3api put-bucket-lifecycle-configuration --bucket tradzlog-uploads-CHANGE-ME --lifecycle-configuration '{"Rules":[{"ID":"expire-old-versions","Status":"Enabled","Filter":{},"NoncurrentVersionExpiration":{"NoncurrentDays":30}}]}'
```

The bucket stays private. The app shows each screenshot through a signed link that expires
after an hour, and no CORS configuration is needed.

## 2. Instance permissions

The instance already has Mizan's IAM role (`mizan-server`). Add TradzLog's permissions to the
same role as a separate inline policy:

1. Copy [`deploy/ec2/iam-policy.json`](../deploy/ec2/iam-policy.json) and replace
   `TRADZLOG_UPLOADS_BUCKET` with the bucket from step 1, and `BACKUP_BUCKET` with Mizan's
   backup bucket (or remove that statement if you have none).
2. IAM → Roles → `mizan-server` → Add permissions → Create inline policy → JSON → paste →
   name it `tradzlog`.

The policy only allows reading `/tradzlog/*` settings, listing, reading, writing and deleting
objects in the uploads bucket, and writing backups under `tradzlog/` in the backup bucket.

Listing (`ListTradzlogScreenshots`, added for account deletion in M4) lets the app sweep a deleted
user's whole `attachments/<user id>/` folder. Without it, deletion still removes every screenshot
the database knows about and logs a warning for the sweep. To add it to an existing policy:
IAM → Roles → `mizan-server` → `tradzlog` → Edit → JSON, add the statement, Save.

Backups should not outlive deleted accounts by more than the 30 days the privacy notice promises.
Local copies are kept 14 days; for the S3 copies, add a lifecycle rule on the backup bucket:
S3 → backup bucket → Management → Create lifecycle rule → prefix `tradzlog/` → Expire current
versions after 30 days (and permanently delete noncurrent versions after 1 day if versioning is on).

### Let containers use the role

The app runs in containers, and AWS only answers instance-role requests from one network "hop"
away by default, which containers are not. Raise the limit to 2 (find the instance id under
EC2 → Instances):

```bash
aws ec2 modify-instance-metadata-options --region eu-west-2 --instance-id i-CHANGE-ME --http-tokens required --http-put-response-hop-limit 2
```

This changes nothing for Mizan. The trade-off is that any container on the instance could use
the role, which is the usual setup for Docker on EC2. Keep the role's permissions narrow, as above.

## 3. Install on the server

From the repository on your PC (use the same SSH user you use for Mizan, usually `ubuntu`):

```bash
scp -r deploy/ec2 ubuntu@YOUR-ELASTIC-IP:tradzlog-ec2
```

```bash
ssh ubuntu@YOUR-ELASTIC-IP 'sudo bash tradzlog-ec2/install.sh'
```

This creates `/srv/tradzlog`, installs `tradzlog-config` (settings) and `tradzlog-backup`
(nightly at 03:45 UTC, 15 minutes after Mizan's), and nothing else.

## 4. Settings in Parameter Store

Every setting lives under `/tradzlog/` as a SecureString, the same way as Mizan's `/mizan/`.
Each deploy writes them to `/srv/tradzlog/.env`; never edit that file on the server.

| Parameter                  | Required | Value                                                                    |
| -------------------------- | -------- | ------------------------------------------------------------------------ |
| `TRADZLOG_DOMAIN`          | yes      | `tradzlog.com` (www redirects to it)                                   |
| `POSTGRES_PASSWORD`        | yes      | 16+ letters/digits; set once, see below                                  |
| `JWT_SECRET`               | yes      | 32+ random characters                                                    |
| `S3_BUCKET`                | yes      | the bucket from step 1                                                   |
| `TRADZLOG_BASIC_AUTH_USER` | yes\*    | the private-beta username                                                |
| `TRADZLOG_BASIC_AUTH_HASH` | yes\*    | bcrypt hash of its password (below)                                      |
| `TRADZLOG_PUBLIC`          | no       | `true` only once the web app has its own sign-in; replaces the two above |
| `REGISTRATION_INVITE_CODE` | yes\*\* | code needed to sign up while sign-up is closed (any long random string)  |
| `REGISTRATION_OPEN`        | no       | `true` opens sign-up to everyone (M1); closed by default in production   |
| `BACKUP_S3_BUCKET`         | no       | Mizan's backup bucket; backups go under `tradzlog/`                      |
| `ANTHROPIC_API_KEY`        | no       | AI coaching                                                              |
| `SENTRY_DSN`               | no       | error tracking                                                           |
| `S3_PREFIX`                | no       | key prefix inside the bucket                                             |
| `EMAIL_BACKEND`            | no       | `ses` once SES is set up (see Email below); `log` (default) sends nothing |
| `EMAIL_FROM`               | no       | sender; default `TradzLog <no-reply@TRADZLOG_DOMAIN>`                    |

\* Until the web app has its own sign-in. \*\* While `REGISTRATION_OPEN` is not `true`; without it nobody can sign up.

Generate the secrets on your PC (Git Bash has `openssl`):

```bash
openssl rand -hex 24
```

The password hash, using Caddy itself (it prompts for the password, which is never stored):

```bash
docker run --rm -it caddy:2 caddy hash-password
```

Store each one, for example:

```bash
aws ssm put-parameter --region eu-west-2 --type SecureString --overwrite --name /tradzlog/TRADZLOG_DOMAIN --value "tradzlog.com"
```

`tradzlog-config` refuses to write anything if a required value is missing, if
`POSTGRES_PASSWORD` is too weak, or if it differs from the password the database was created
with (PostgreSQL keeps the first one). Choose it once.

## 5. DNS

At your DNS provider, add **A** records for `tradzlog.com` and `www.tradzlog.com` pointing
at the instance's Elastic IP (the same address Mizan uses). Caddy gets the HTTPS certificates
on the first deploy once DNS resolves.

## 6. GitHub

In the `tradzlog.co.uk` repository → Settings:

1. **Secrets and variables → Actions → Secrets:** `EC2_HOST` (the Elastic IP), `EC2_SSH_KEY`
   and `EC2_KNOWN_HOSTS`. These can be the same values as in Mizan's repository, because both
   deploy as the same `deploy` user.
2. **Environments:** create `production` (add yourself as a required reviewer if you want to
   approve each deploy).
3. **Variables:** `EC2_DEPLOY` = `true` once steps 1–5 are done.

Every push to `main` then runs the tests, builds the image for arm64 and amd64
(`ghcr.io/telberrak/tradzlog`), and deploys it. A deploy:

1. reads the settings from Parameter Store, and checks the Caddy site can be written;
2. pulls the new image, runs database migrations, and starts the containers;
3. if they are not healthy within 3 minutes, starts the previous version again;
4. writes `/srv/mizan/sites.d/tradzlog.caddy` and reloads Caddy (an invalid file keeps the old config);
5. checks the database from inside the stack, and that `https://<domain>/` answers 401 (gated) or 200.

Migrations run before the new version starts. If a deploy is rolled back after a migration ran,
the previous version runs on the newer schema, so keep migrations backwards compatible
(add columns and tables; drop them in a later release).

## 7. First sign-up

Sign-up is invite-only while `REGISTRATION_OPEN` is not `true`. Open `https://tradzlog.com/signup`
(past the beta gate, if it is on), enter your details and the `REGISTRATION_INVITE_CODE`, and you
are signed in. Then add your trading accounts under **Accounts** and your symbols under
**Accounts → Instruments** (`/settings/instruments`).

## Email (Amazon SES)

TradzLog sends welcome, password-reset, password-changed and account-deleted emails. Until
`EMAIL_BACKEND` is `ses`, nothing is sent (the worker logs the subject instead), so set this up
before opening sign-up: without it nobody can reset a forgotten password.

1. **[Browser] Verify the domain.** AWS console, region **Europe (London) eu-west-2** →
   Amazon SES → Configuration → Identities → **Create identity** → Domain → `tradzlog.com` →
   leave *Easy DKIM* (RSA 2048) selected → **Create identity**. The page lists three **CNAME**
   records.
2. **[Browser] DNS.** At your DNS provider, add the three CNAME records exactly as shown (name
   and value). Add one more record so receiving servers know what to do with forged mail:
   - Type **TXT**, name `_dmarc`, value `v=DMARC1; p=none; rua=mailto:YOUR_ADDRESS` (an address
     you read; `p=none` only reports, it rejects nothing).
   Within an hour or so the identity shows **Verified** in SES.
3. **[Browser] Test while still in the sandbox.** New SES accounts can only send to verified
   addresses. Identities → Create identity → Email address → your own address → open the
   confirmation link in that inbox.
4. **[Browser] Permissions.** IAM → Roles → `mizan-server` → `tradzlog` → Edit → JSON → add the
   `TradzlogEmail` statement from [`iam-policy.json`](../deploy/ec2/iam-policy.json), replacing
   `TRADZLOG_DOMAIN` with `tradzlog.com` (twice) → Save. It only allows sending from
   `@tradzlog.com` addresses.
5. **[Browser] Switch it on.** Systems Manager → Parameter Store → Create parameter →
   name `/tradzlog/EMAIL_BACKEND`, type SecureString, value `ses`.
6. **[Server] Apply** (or push any commit): as `deploy` in `/srv/tradzlog`,
   `bash config.sh && docker compose up -d`.
7. **[Browser] Check.** On the sign-in page use *Forgot your password?* with the address from
   step 3; the email should arrive within a minute. If not:
   `docker compose logs --tail 50 worker` on the server shows the SES error.
8. **[Browser] Leave the sandbox.** SES → Account dashboard → **Request production access** →
   Mail type *Transactional*, website `https://tradzlog.com`, and describe the use: account
   emails only (welcome, password reset, security notices), sent to people who signed up, no
   marketing. AWS usually answers within a day. Until then only verified addresses get mail.

Replies to these emails go to `SUPPORT_EMAIL`.

## Removing the beta gate

Once you are happy for visitors to reach the sign-in page directly:

1. Parameter Store: create `/tradzlog/TRADZLOG_PUBLIC` = `true`, and delete
   `/tradzlog/TRADZLOG_BASIC_AUTH_USER` and `/tradzlog/TRADZLOG_BASIC_AUTH_HASH`.
2. Apply it on the server (or push any commit to deploy):

```bash
sudo -u deploy bash -c 'cd /srv/tradzlog && bash config.sh && python3 render-caddy.py tradzlog.caddy.template .env /srv/mizan/sites.d/tradzlog.caddy && docker compose --project-directory /srv/mizan exec -T caddy caddy reload --config /etc/caddy/Caddyfile'
```

Sign-up stays invite-only until you also set `/tradzlog/REGISTRATION_OPEN` = `true` (and run
`docker compose up -d` in `/srv/tradzlog` after `config.sh`, so the apps pick it up).

## Operations

On the server, as `deploy`, in `/srv/tradzlog`:

| Task                    | Command                                                                     |
| ----------------------- | --------------------------------------------------------------------------- |
| Status                  | `docker compose ps`                                                         |
| Logs                    | `docker compose logs -f --tail 100 web api worker`                          |
| Memory                  | `docker stats --no-stream`                                                  |
| Apply changed settings  | `bash config.sh && docker compose up -d`                                    |
| Backup now              | `sudo tradzlog-backup`                                                      |
| Restore a backup        | `gunzip -c /var/backups/tradzlog/FILE.sql.gz \| docker compose exec -T postgres psql -U tradzlog tradzlog` |
| Run a specific version  | set `TRADZLOG_TAG` in `.env` to a commit SHA, then `docker compose up -d`   |

Point an uptime monitor at `https://tradzlog.com/api/health` (checks the database too; not behind the gate).
