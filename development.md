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

To confirm authentication, request `GET /api/v1/users/me` with a browser session
or the bearer token from `POST /api/v1/login/access-token`. To preview password
recovery and other email templates, run `bun run email:dev` from the repository
root and open <http://localhost:3333>.

### Frontend Served by FastAPI

Build the frontend from the `frontend` directory:

```bash
bun run build
```

The build is written to `backend/app/frontend` and served by FastAPI at <http://localhost:8000>. If FastAPI was started before the first build, restart it to serve the bundle. Rebuild the frontend after making frontend changes.

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

## API-only App

If your project will ship the API without the bundled React app, make these
edits from the repository root before starting development. The backend,
PostgreSQL, email worker, and backend tests use the same setup as the full
stack.

1. If you will keep React Email, copy `frontend/biome.json` over
   `packages/react-email/biome.json` to keep the email lint settings
   available without the frontend. Delete `frontend/`, `scripts/generate-client.sh`, and
   `scripts/test-browser.sh`. In `backend/app/main.py`, remove the
   conditional frontend registration, `reject_api_frontend_fallback`,
   the `FRONTEND_DIR` constant, and the `os`, `Path`, `APIRouter`,
   `Depends`, and `HTTPException` imports. Keep the API router registration
   and two blank lines before `class RuntimeFastAPI`. In
   `backend/tests/test_standalone_app.py`, remove the tests
   `test_production_requires_a_frontend_bundle` and
   `test_frontend_fallback_preserves_api_namespace`.
2. In `backend/Dockerfile`, remove the `frontend-build` stage and the
   `COPY --from=frontend-build` instruction. Keep both Python stages
   (`production` and `development`). In `compose.override.yml`, remove
   the `./frontend` watch entry. In `compose.test.yml`, remove the
   `playwright` service and the backend `build.args` entry for
   `VITE_API_URL`; keep the backend's `development` build target.
3. In the root `package.json`, remove the `frontend` workspace and the
   `dev`, `lint`, `test`, and `test:ui` scripts. Keep `packages/*` and
   the `email:dev` and `email:export` scripts to continue editing email
   templates with React Email. Run `bun install` and commit the updated
   `bun.lock`. In `.github/dependabot.yml`, remove the `/frontend` Docker
   directory and the Bun ignore entry for `@hey-api/openapi-ts`. In
   `.github/workflows/pre-commit.yml`, rename the Bun install step to
   reflect the remaining workspace.
4. Remove the `local-biome-check` and `generate-frontend-sdk` hooks from
   `.pre-commit-config.yaml`. Delete `.github/workflows/playwright.yml`.
   If branch protection requires that workflow's status, remove the
   `alls-green-playwright` requirement. To check React Email exports
   after editing them, run `bun run email:export` and review the changes
   under `backend/app/email-templates/`.
5. Remove the **Set up Bun**, **Install frontend dependencies**, and
   **Build frontend** steps from `.github/workflows/deploy.yml`. Remove
   `!backend/app/frontend/` from `.fastapicloudignore`. In the **Verify
   the production runtime** step of
   `.github/workflows/test-docker-compose.yml`, remove the HTML
   navigation loop for `/`, `/login`, and `/items`. Keep the API
   readiness, OpenAPI, and email delivery checks.
6. In `.vscode/launch.json`, remove the **Debug Frontend** launch
   configuration. Keep the backend Python debugger configuration.

Update your project's README and development and deployment docs to
remove the React setup, browser test commands, and links to the deleted
frontend documentation.

`FRONTEND_HOST` still controls the links in welcome and recovery
emails. If you send account emails, set it to the origin of the separate
client that handles those links. For a Docker Compose deployment, update
`FRONTEND_HOST` in both the `backend` and `email-worker` environments in
`compose.deploy.yml`.

If the project will edit the checked-in HTML templates directly, you can
also remove `packages/react-email/`, the root `package.json` and
`bun.lock`, the Bun Dependabot entry, Bun setup and install from the
pre-commit workflow, and the Bun audit steps from
`.github/workflows/dependency-audit.yml`. Keep the templates in
`backend/app/email-templates/` so the worker can deliver account emails.

Verify the API-only copy from the repository root:

```bash
uv run prek run --all-files
bash scripts/test.sh
docker compose -f compose.yml build backend
docker compose run --rm backend bash scripts/prestart.sh
docker compose watch
```

Run the Compose commands with a fresh development database. Open
<http://localhost:8000/docs> and
<http://localhost:8000/api/v1/utils/readiness/> to check the API. If you
kept React Email, run `bun ci`, `bun run --filter emails lint`, and
`bun run email:export` to verify the workspace and template exports.

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
in. The browser test setup routes are excluded from the public client. A fixture
creation request for an existing email returns HTTP 409 and preserves the
existing account. Use a unique email when creating a new fixture.

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

Projects generated from this template run the same audits without inherited
advisory exceptions. Document any temporary exception your own repository
needs and remove it when it expires or a fix becomes available.

## Removing the Items Example

If your new project does not use Items, remove the example before initializing
its database.

1. Remove the `ItemBase` through `ItemsPublic` declarations, the item index,
   and `User.items` from `backend/app/models.py`. Remove the `item` table and
   its index from the initial revision in `backend/app/alembic/versions/`,
   including the table drop in `downgrade()`.
2. Remove the Items router from `backend/app/api/main.py` and delete
   `backend/app/api/routes/items.py`. Remove `create_item` and its item model
   imports from `backend/app/crud.py`.
3. Delete `frontend/src/routes/_layout/items.tsx`,
   `frontend/src/components/Items/`, and
   `frontend/src/components/Pending/PendingItems.tsx`. Remove the Items link
   and its icon import from `frontend/src/components/Sidebar/AppSidebar.tsx`.
   Run `bash scripts/generate-client.sh` to update the API client and run
   `bun run dev` to regenerate the TanStack route tree.
4. Remove the Items-specific backend tests in
   `backend/tests/api/routes/test_items.py` and
   `backend/tests/test_database_schema.py`, along with
   `backend/tests/utils/item.py`. Remove `frontend/tests/items.spec.ts` and its
   item data helpers. Adapt the late-response login tests in
   `frontend/tests/login.spec.ts` to use your own private feature, or remove
   their Items setup and assertions while keeping the session checks. Replace
   `/items` in the Docker Compose smoke test with a route from your project.
5. Run `bash scripts/test.sh` to check schema creation and the account routes
   against a fresh database. Run `bun run --filter frontend build` to check the
   frontend and generated routes.

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

## Optional Editor Agent Skills

After installing the backend dependencies, you can link the FastAPI and
SQLModel skills bundled with those packages. From the repository root, run:

```bash
uv sync --locked --project backend
uvx library-skills install --skill fastapi --skill sqlmodel
```

The installer discovers the skills in the local Python environment and links
them under `.agents/skills/`. Add `--claude` to also link them under
`.claude/skills/`. Run the installer again after moving or recreating your
Python environment. These links belong to your local checkout and are ignored
by Git. Application setup and CI do not require this step.

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
