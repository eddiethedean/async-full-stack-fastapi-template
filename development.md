# Async Full Stack FastAPI - Development

## Local Development

For local development, run PostgreSQL and Mailpit with Docker Compose, and run the FastAPI and Vite development servers locally.

Start the supporting services:

```bash
docker compose up -d db mailpit
```

Then, from the `backend` directory, install the dependencies and prepare the database:

```bash
uv sync
uv run bash scripts/prestart.sh
```

Start the FastAPI development server:

```bash
AUTH_COOKIE_SECURE=false uv run fastapi dev
```

In another terminal, from the project root, install the frontend dependencies and start the Vite development server:

```bash
bun install
bun run dev
```

Now you can open these URLs:

Frontend development server: <http://localhost:5173>

Backend API: <http://localhost:8000>

Automatic interactive API documentation with Swagger UI: <http://localhost:8000/docs>

Mailpit: <http://localhost:8025>

The frontend development server uses the backend at `http://localhost:8000`, as configured in `frontend/.env`.

Use the same host name for both servers. The browser sends a session cookie to the API, and `localhost` and `127.0.0.1` count as different sites. `AUTH_COOKIE_SECURE=false` is a local HTTP opt-in and requires development mode.

### Frontend Served by FastAPI

Build the frontend from the `frontend` directory:

```bash
bun run build
```

The build is written to `backend/app/frontend` and served by FastAPI at <http://localhost:8000>. Rebuild the frontend after making frontend changes.

When running the built frontend on local HTTP outside Docker, set `AUTH_TRUSTED_ORIGINS='["http://localhost:8000"]'` as well. Compose applies the local HTTP settings in its development override.

On native Windows, async Psycopg requires a selector event loop. `fastapi dev` uses one with reload enabled. When running the server without reload, pass the loop explicitly from `backend`.

```bash
AUTH_COOKIE_SECURE=false uv run uvicorn app.main:app --loop asyncio:SelectorEventLoop
```

## Full Stack with Docker Compose

To run the backend and built frontend in Docker Compose:

```bash
docker compose run --rm backend bash scripts/prestart.sh
docker compose watch
```

Now you can open these URLs:

Application, with the frontend and API served by FastAPI: <http://localhost:8000>

Automatic interactive API documentation with Swagger UI: <http://localhost:8000/docs>

Adminer, database web administration: <http://localhost:8080>

Traefik UI, to see how the routes are being handled by the proxy: <http://localhost:8090>

Mailpit: <http://localhost:8025>

Stop a locally running FastAPI server before starting the Compose backend because both use port `8000`.

**Note**: The first time you start the stack, it might take a minute for all the services to be ready. To monitor it, use `docker compose logs`, or `docker compose logs backend` for the backend service.

## Mailpit

[Mailpit](https://mailpit.axllent.org) captures emails sent during local development instead of delivering them. The local backend connects to it at `localhost:1025`, and the Compose backend connects to the `mailpit` service. Captured emails are available at <http://localhost:8025>.

## Running Tests

Run `bash scripts/test.sh` from the repository root to test against disposable
PostgreSQL and Mailpit containers. It uses a distinct Compose project, allocates
loopback ports, and removes the test volumes on exit. The regular development
stack can stay up.

To use your own disposable PostgreSQL instance, create a separate database named
`asf_test_<name>`, set `TEST_DATABASE_URL` to that database, then run
`uv run bash scripts/test.sh` from `backend`. The backend test command applies
migrations and initializes test data. Direct `pytest` requires the database to
be migrated first. Use a fresh test database for each run and remove it when
finished.

For browser tests, run one of these commands from the repository root:

```bash
bun run test
bun run test:ui
```

Both commands start a disposable Compose project and remove its volumes on
exit. For UI mode, open <http://127.0.0.1:9323> and run the setup project before
running tests that require sign-in. Playwright verifies test mode before signing
in. The browser test setup routes are excluded from the public client.

The backend command applies the initial Alembic revision to the disposable
database, checks schema drift, and runs the integration tests. The Playwright
workflow checks that regenerating the SDK and React Email exports produces no
tracked changes.

CI audits the locked Python dependencies and checks high and critical Bun
advisories. The built production image is scanned for high and critical issues
with fixes available. The production smoke test also checks that the worker
delivers a recovery email to Mailpit. Any temporary exception should cite the
advisory, affected component, owner, justification, and expiration date in a
repository issue and keep the scanner exception scoped to that advisory.

The [Picomatch exception](https://github.com/advisories/GHSA-fc5q-39xc-vjpf)
belongs to the project maintainer and is tracked in
[issue #12](https://github.com/eddiethedean/async-full-stack-fastapi-template/issues/12).
The affected package is used by the frontend build with repository-defined
patterns. Remove the exception when a fix is available. It expires on
December 18, 2026.

## Frontend branding

Set `VITE_PROJECT_NAME` before building to set the browser tab titles, logo text
and accessible name, and footer branding. Set the optional `VITE_PROJECT_URL`
to your project website. The footer omits the link when the value is blank.

- For the local Vite server or a standalone frontend build, set these values in
  `frontend/.env` or in your shell.
- For Docker Compose, set them in the repository root `.env` or export them
  before building the image. When invoking Docker directly, pass the values
  with `--build-arg`.
- For FastAPI Cloud deployment through GitHub Actions, set them as repository
  variables under **Settings** > **Secrets and variables** > **Actions**.

These values are public and are compiled into the frontend. Rebuild and deploy
after changing them. The backend's `PROJECT_NAME` controls API and email branding.
Replace the placeholder logo mark in
[`Logo.tsx`](./frontend/src/components/Common/Logo.tsx). To add a favicon or
touch icon, place the file in `frontend/public` and reference it in
[`frontend/index.html`](./frontend/index.html).

## Docker Compose Files and Environment Variables

The main `compose.yml` file contains the configuration shared by the whole stack. Docker Compose loads it automatically.

The `compose.override.yml` file adds local development settings, such as mounting the source code as a volume. Docker Compose also loads it automatically and applies it on top of `compose.yml`.

The `compose.deploy.yml` file contains the deployment-specific settings, including HTTPS and automatic certificate handling. It is explicitly combined with `compose.yml` when deploying the application.

The backend reads local settings from the `.env` file. Docker Compose also uses it for variable interpolation and passes the settings each container needs.

After changing variables, make sure you restart the stack:

```bash
docker compose watch
```

## The `.env` File

The tracked `.env` file contains local development defaults, passwords, and other configuration. Its hostnames use `localhost` for processes running on your machine. Docker Compose overrides hostnames such as the database and SMTP server with their Compose service names.

Do not store deployment secrets in `.env`. Configure them as described in the [FastAPI Cloud deployment guide](./deployment.md) or the [Docker Compose deployment guide](./deployment-docker-compose.md).

## Authentication

Browsers sign in at `POST /api/v1/auth/session`. The response sets an HttpOnly cookie and supplies a CSRF token for writes. `GET /api/v1/auth/session` restores the session after reload. Send `X-CSRF-Token` and an allowed `Origin` on cookie-authenticated writes, including `DELETE /api/v1/auth/session` and `POST /api/v1/auth/logout-all`. Adjust `BROWSER_SESSION_EXPIRE_MINUTES` to change the browser session lifetime.

The frontend sends the session ID in `X-Browser-Session` on private requests so a tab can detect when the shared cookie has changed. The ID identifies a session and cannot authenticate a request. Tabs coordinate sign-in and sign-out on the same frontend origin with Web Locks. When Web Locks are unavailable or the API trusts multiple frontend origins, sign-out revokes the session and leaves the unusable cookie to expire or be replaced on the next sign-in.

API clients can still use the OAuth2 bearer login. An explicit Authorization header takes precedence on protected API routes that support both mechanisms. An invalid bearer token returns 401 even when the client has a valid browser cookie. The browser session endpoint itself accepts only cookie credentials.

Set `FRONTEND_HOST` to the deployed frontend's HTTPS origin. Additional trusted origins for CORS and browser writes can be configured as a JSON list in `AUTH_TRUSTED_ORIGINS`. Production cookies are Secure and host only.

## Pre-commit Hooks and Code Linting

The project uses [prek](https://prek.j178.dev/), a modern alternative to [pre-commit](https://pre-commit.com/), for code linting and formatting.

You can find a file `.pre-commit-config.yaml` with configurations at the root of the project.

### Install `prek` to Run Automatically

`prek` is already part of the dependencies of the project.

From the project root, install the Git hook so that `prek` runs automatically before each commit:

```bash
uv run prek install -f
```

The `-f` flag forces the installation, in case there was already a `pre-commit` hook previously installed.

Now whenever you try to commit, for example with:

```bash
git commit
```

`prek` will check and format the code you are about to commit. If it modifies any files, add those files to Git again before committing.

### Run `prek` Manually

You can also run `prek` manually on all files from the project root:

```bash
uv run prek run --all-files
```

## Protected API responses

Protected routes return `401 Unauthorized` for missing or invalid credentials.
Bearer clients receive a `WWW-Authenticate: Bearer` header and can sign in
again. A `403 Forbidden` response means the current account lacks the
required permissions.

## Account emails

Start the `email-worker` service along with the backend and Mailpit for invitations
and password recovery. For host development, start Mailpit through Compose and
run `uv run python -m app.email_worker` from `backend` in another terminal. Set
`FASTAPI_ENV=development` for this local setup. The worker waits for committed
messages, sends them outside the database transaction, and retries temporary
SMTP failures with bounded backoff.

Use **Send invitation link** in the admin add-user dialog to let the recipient
choose their own password. For an expired pending invitation, delete the pending
account and send a fresh invitation. When mail is disabled, direct account
provisioning still works and invitations return an unavailable response.
