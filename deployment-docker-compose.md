# Async Full Stack FastAPI - Docker Compose Deployment

You can deploy the project to your own remote server with Docker Compose. The deployment configuration includes Traefik to handle HTTPS and route incoming traffic to the application.

## Preparation

Start with a fresh Compose project and PostgreSQL database. To try the first
deployment in staging, use a separate Compose project name and an explicit
staging environment file. Verify backend readiness and email delivery if
enabled.

* Have a remote server ready and available.
* Configure DNS records pointing to the server for the application domain and any supporting service subdomains you want to expose, such as `fastapi-project.example.com` and `adminer.fastapi-project.example.com`.
* Install and configure [Docker](https://docs.docker.com/engine/install/) on the remote server (Docker Engine, not Docker Desktop).

## Copy the Code

```bash
rsync -av --exclude=".git/" --filter=":- .gitignore" ./ root@your-server.example.com:/root/code/app/
```

The `--filter=":- .gitignore"` option tells `rsync` to use the same ignore rules as Git, excluding files such as the Python virtual environment.

## Configure the Application

### Environment Variables

Set the application domain, project name, and first superuser email:

```bash
export DOMAIN=fastapi-project.example.com
export PROJECT_NAME="Async Full Stack FastAPI"
export FIRST_SUPERUSER=admin@example.com
```

You can also configure these environment variables as needed:

* `SMTP_HOST`: The SMTP server host from your email provider.
* `SMTP_PORT`: The SMTP server port. Defaults to `587`.
* `SMTP_TLS`: Whether to use STARTTLS. Defaults to `true`.
* `SMTP_SSL`: Whether to use implicit TLS. Defaults to `false`.
* `SMTP_TIMEOUT_SECONDS`: The SMTP connection timeout. Defaults to `15`.
* `SMTP_USER`: The SMTP server user.
* `EMAILS_FROM_EMAIL`: The email account used to send emails.
* `SENTRY_DSN`: The DSN for Sentry.

### Secrets

Generate and set secure values for the database password, token signing key, and first superuser password:

```bash
export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FIRST_SUPERUSER_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

Set `POSTGRES_PASSWORD` to the raw password, including any characters such as
`/` or `%`. Compose passes the same password to PostgreSQL and the application,
which encodes it when building its connection URL. Use Compose files and a
backend image built from the same revision.

Mail is disabled when `SMTP_HOST` or `EMAILS_FROM_EMAIL` is unset. To enable mail,
define both and set `EMAIL_OUTBOX_KEYS` to a JSON object with a Fernet key for
the active `EMAIL_OUTBOX_KEY_VERSION`, which defaults to `v1`. To use an
authenticated provider, also set `SMTP_PASSWORD`.

## Deploy

```bash
cd /root/code/app/
export BACKEND_IMAGE="asf-backend:release-$(date -u +%Y%m%dT%H%M%SZ)"
docker compose --env-file /dev/null -f compose.yml -f compose.deploy.yml build backend
bash scripts/scan-production-image.sh "$BACKEND_IMAGE"
docker compose --env-file /dev/null -f compose.yml -f compose.deploy.yml pull --ignore-buildable
docker compose --env-file /dev/null -f compose.yml -f compose.deploy.yml up -d --wait --no-build --pull never db
bash scripts/migrate-compose.sh --env-file /dev/null -f compose.yml -f compose.deploy.yml
docker compose --env-file /dev/null -f compose.yml -f compose.deploy.yml up -d --wait --no-build --pull never
```

The migration command creates the schema and the configured first superuser
on the first deployment. The final command waits for service health checks.
Sign in as the first superuser and verify delivery if email is enabled.

To deploy an image already tested in staging, load it onto the production
host, set `BACKEND_IMAGE` to its tag, and begin with the scan command.

The `compose.deploy.yml` file adds HTTPS and automatic certificate handling to
the shared `compose.yml` configuration. The production commands use the
exported settings and skip the tracked development `.env` file. To load
settings from a deployment environment file, pass its path to `--env-file`.

When upgrading an existing deployment, use its current Compose project name so
Compose reuses the database and certificate volumes. If the name came from
`.env`, export the same `COMPOSE_PROJECT_NAME` before running the production
commands with `--env-file /dev/null`.

The backend Docker image builds the frontend, so the server does not need Bun or prebuilt frontend files.

The deployment puts Traefik and the API on a dedicated ingress network. Uvicorn
trusts the Traefik peer on that network when resolving the client address for
[authentication rate limits](./deployment.md#authentication-rate-limits).
Set the `RATE_LIMIT_*` variables in your deployment environment to override
the thresholds. If the ingress subnet overlaps another network on the host,
set `TRAEFIK_INGRESS_SUBNET` and an unused `TRAEFIK_INGRESS_IP` within it.
Set `FORWARDED_ALLOW_IPS` only when you need to customize the trusted proxy
addresses. Include the Traefik ingress address when Traefik serves the API.

Traefik reads Docker service labels through `docker-discovery` on a separate
internal network shared by those two services. Keep
`docker/traefik-discovery.cfg` with the Compose files on the deployment host.
The Docker socket is mounted only in `docker-discovery`, which allows Docker
API reads for version negotiation, container discovery, and event streaming.
Container inspection includes sensitive metadata such as environment variables.
Keep the discovery network private and reserve it for these services.

## Deploy with GitHub Actions

Set the repository variable `ENABLE_DOCKER_COMPOSE_DEPLOY` to `true` after configuring the server and secrets. The included `.github/workflows/deploy-docker-compose.yml` workflow then runs the deployment commands on the server when manually triggered from GitHub Actions.

The workflow gives each build a unique image tag and scans the image on the
deployment host before migrations. The API and worker start from that same
local image.

The workflow keeps Compose's existing project name resolution, including a
`COMPOSE_PROJECT_NAME` in `.env`. The workflow's SMTP settings take precedence
over `.env`, with production transport defaults for unset settings.

Use a self-hosted runner only for a repository whose contributors and workflow code you trust. GitHub recommends using self-hosted runners with private repositories because workflows execute directly on the runner machine.

### Configure Repository Variables and Secrets

In the repository, go to **Settings** > **Secrets and variables** > **Actions** and add these repository variables:

* `DOMAIN`
* `PROJECT_NAME`
* `FIRST_SUPERUSER`

To enable emails, add these optional repository variables:

* `SMTP_HOST`
* `SMTP_PORT`
* `SMTP_TLS`
* `SMTP_SSL`
* `SMTP_TIMEOUT_SECONDS`
* `SMTP_USER`
* `EMAILS_FROM_EMAIL`
* `EMAIL_OUTBOX_KEY_VERSION`

To enable Sentry, add the optional `SENTRY_DSN` repository variable.

The workflow also accepts the optional rate-limit and ingress variables
described above as repository variables.

Add these repository secrets:

* `POSTGRES_PASSWORD`
* `SECRET_KEY`
* `FIRST_SUPERUSER_PASSWORD`

To enable mail, add the `EMAIL_OUTBOX_KEYS` repository secret. To use an
authenticated provider, also add the `SMTP_PASSWORD` repository secret.

### Install a Self-Hosted Runner

On the server, create a dedicated user and grant it access to Docker:

```bash
sudo adduser github
sudo usermod -aG docker github
sudo su - github
```

In the GitHub repository, go to **Settings** > **Actions** > **Runners**, select **New self-hosted runner**, choose Linux, and follow the commands GitHub provides to download, configure, and register the runner. Install it in `/home/github/actions-runner`.

After registering the runner, exit the `github` user session and install the runner as a system service:

```bash
exit
cd /home/github/actions-runner
sudo ./svc.sh install github
sudo ./svc.sh start
sudo ./svc.sh status
```

See GitHub's guides for [adding a self-hosted runner](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners) and [configuring the runner as a service](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application?platform=linux).

### Run the Deployment

When the runner is online, open the repository's **Actions** tab, select **Deploy with Docker Compose**, and select **Run workflow**.

## URLs

Replace `fastapi-project.example.com` with your domain.

Application (frontend and API): `https://fastapi-project.example.com`

Interactive API docs: `https://fastapi-project.example.com/docs`

Adminer: `https://adminer.fastapi-project.example.com`

## Account email delivery

The `email-worker` service uses the backend image. The migration script stops
the previous worker and waits for it to exit before applying migrations. The
last Compose command starts the worker from the new image. Add
`EMAIL_OUTBOX_KEYS` to deployment secrets and `EMAIL_OUTBOX_KEY_VERSION` to
deployment variables when configuring SMTP. Both services read the same
settings. See [account email delivery](./deployment.md#account-email-delivery)
for key setup, rotation, and retry behavior.

The production worker resumes processing queued email after Docker restarts. A
worker explicitly stopped for maintenance stays stopped until you start it.

The base Compose stack builds the unprivileged production image. The development
override selects the development image so local test and code tools are
available. The Compose deployment workflow starts PostgreSQL, runs its
migration step, then waits for the API and worker health checks.

Set `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`,
`DB_READINESS_TIMEOUT_SECONDS`, `MAX_REQUEST_BODY_BYTES`, or `WEB_CONCURRENCY`
in the deployment environment or as repository variables for the deployment
workflow. The Compose probe allows the full API readiness timeout plus one
second for the HTTP request.
