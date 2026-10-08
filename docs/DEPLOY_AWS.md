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

> **Private beta.** The web app does not have its own sign-in yet: it always shows the first
> registered user. Until it does, Caddy puts a username and password (HTTP basic auth) in front
> of the web app. The deploy refuses to publish the site without it unless you explicitly set
> `TRADZLOG_PUBLIC=true`. The API under `/api` is not gated; it has its own sign-in.

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

The policy only allows reading `/tradzlog/*` settings, reading, writing and deleting objects in
the uploads bucket, and writing backups under `tradzlog/` in the backup bucket.

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
| `TRADZLOG_DOMAIN`          | yes      | `tradzlog.co.uk` (www redirects to it)                                   |
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
aws ssm put-parameter --region eu-west-2 --type SecureString --overwrite --name /tradzlog/TRADZLOG_DOMAIN --value "tradzlog.co.uk"
```

`tradzlog-config` refuses to write anything if a required value is missing, if
`POSTGRES_PASSWORD` is too weak, or if it differs from the password the database was created
with (PostgreSQL keeps the first one). Choose it once.

## 5. DNS

At your DNS provider, add **A** records for `tradzlog.co.uk` and `www.tradzlog.co.uk` pointing
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

The database starts empty and sign-up is invite-only. Register your user through the API (not
gated) with `REGISTRATION_INVITE_CODE`, then open the site with the basic-auth username and password:

```bash
curl -sS https://tradzlog.co.uk/api/auth/register -H "Content-Type: application/json" -d '{"email":"you@example.com","password":"a-strong-password","name":"Your Name","invite_code":"YOUR-INVITE-CODE"}'
```

Create your trading accounts as described in the README (API docs are not exposed publicly;
run the API locally to browse them).

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

Point an uptime monitor at `https://tradzlog.co.uk/api/health` (checks the database too; not behind the gate).
