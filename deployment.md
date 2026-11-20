# Async Full Stack FastAPI - Deployment

Deploy the project to [FastAPI Cloud](https://fastapicloud.com) with the included GitHub Actions workflow.

## Create the FastAPI Cloud Application

Create an application in FastAPI Cloud and set its [Application Directory](https://fastapicloud.com/docs/builds-and-deployments/application-directory/) to `backend`.

Connect a new PostgreSQL database using the [Neon](https://fastapicloud.com/docs/integrations/neon-integration/) or [Supabase](https://fastapicloud.com/docs/integrations/supabase-integration/) integration. Both integrations configure a `DATABASE_URL` secret automatically. You can also configure `DATABASE_URL` manually for another PostgreSQL provider.

## Configure the Application

### Environment Variables

Add these required [environment variables](https://fastapicloud.com/docs/builds-and-deployments/environment-variables/) to the FastAPI Cloud application:

* `PROJECT_NAME`: The name of the project, used in the API documentation and emails.
* `FIRST_SUPERUSER`: The email address of the first superuser.
* `FRONTEND_HOST`: The public URL of the application, such as the generated `https://your-app.fastapicloud.dev` URL or a custom domain.

To enable emails, add these optional environment variables with values from your email provider:

* `SMTP_HOST`
* `SMTP_USER`
* `EMAILS_FROM_EMAIL`

To enable Sentry, configure `SENTRY_DSN`.

### Secrets

Add these required values and mark them as secrets:

* `SECRET_KEY`: A secret key used to sign security tokens.
* `FIRST_SUPERUSER_PASSWORD`: The password of the first superuser.
* `DATABASE_URL`: The PostgreSQL connection URL, configured automatically when using a database integration.

To enable emails with an authenticated provider, add `SMTP_PASSWORD` as a secret.

You can generate secure values for `SECRET_KEY` and `FIRST_SUPERUSER_PASSWORD` with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Configure Continuous Deployment

After configuring the application and repository secrets, set the repository variable `ENABLE_FASTAPI_CLOUD_DEPLOY` to `true`. The included `.github/workflows/deploy.yml` workflow then builds the frontend, prepares the database, and deploys on pushes to `master`. You can also run it manually from the **Actions** tab.

Log in to FastAPI Cloud and configure the [deploy token](https://fastapicloud.com/docs/advanced-features/deploy-tokens/) and application ID as GitHub repository secrets:

```bash
uv run fastapi login
uv run fastapi cloud setup-ci --secrets-only --app-id <your-app-id>
```

If the GitHub CLI is installed and authenticated, the command configures `FASTAPI_CLOUD_TOKEN` and `FASTAPI_CLOUD_APP_ID` automatically. Otherwise, it prints the values so you can add them in your repository under **Settings** > **Secrets and variables** > **Actions**.

The workflow runs database migrations and creates the first superuser before deploying. In the repository's **Settings** > **Secrets and variables** > **Actions** page, add these repository variables:

* `PROJECT_NAME`
* `FIRST_SUPERUSER`

Add these repository secrets:

* `DATABASE_URL`
* `SECRET_KEY`
* `FIRST_SUPERUSER_PASSWORD`

Use the same values configured in FastAPI Cloud. For `DATABASE_URL`, use the connection URL from your database provider. The database must be reachable from GitHub-hosted runners so the preparation step can connect to it.

The deployment workflow performs these steps:

1. Installs and builds the frontend into `backend/app/frontend`.
2. Runs `backend/scripts/prestart.sh` to apply database migrations and create the first superuser.
3. Deploys the project with `uv run fastapi deploy`.

On the first deployment, the preparation step creates the schema and the
configured first superuser. Check API readiness and sign in from the browser.
If you enabled email, start the worker and verify recovery and invitation
delivery. See [Account email delivery](#account-email-delivery).

## URLs

Replace `your-app.fastapicloud.dev` with the URL of your FastAPI Cloud application.

Application (frontend and API): `https://your-app.fastapicloud.dev`

Interactive API docs: `https://your-app.fastapicloud.dev/docs`

## Docker Compose

For deployment to your own server, see the [Docker Compose deployment guide](./deployment-docker-compose.md).

## Release rehearsal

For later releases of your project, rehearse schema changes on a separate
staging stack with a restored backup. Use synthetic or scrubbed data and
route staging email to a capture service.

1. Record the running version and database revision. Verify that the backup
   can be restored to staging.
2. Apply your project's migrations and run `alembic check`. Verify that
   existing records survive.
3. Start the API, frontend, and email worker. Check API readiness,
   browser and bearer sign-in, and email delivery if enabled.
4. Repeat the verified sequence in production and keep the backup
   and test results.

If validation fails, halt the rollout and isolate the affected service.
A restore discards writes made after the backup. Account for those writes
before restoring production data.

## GitHub Repository Automation

The **Prepare Release** workflow uses the repository's `GITHUB_TOKEN` to open a release pull request. To use it, enable **Allow GitHub Actions to create and approve pull requests** under **Settings** > **Actions** > **General**. Add entries under **Latest Changes** in `release-notes.md` before preparing a release.

The pre-commit workflow reports formatting and generated-client changes for contributors to apply locally.

To publish code coverage with [Smokeshow](https://github.com/samuelcolvin/smokeshow), add `SMOKESHOW_AUTH_KEY` as a repository secret.

## Account email delivery

With email enabled, run `python -m app.email_worker` as a separate long-running
process after preparing the database. On FastAPI Cloud, run the worker on a
host with access to the database and SMTP service. Provide the same
`FRONTEND_HOST`, mail settings, and outbox keys to the API and worker.

For later releases, stop all workers and wait for them to exit before applying
migrations. Start the workers again from the updated project after migration.

Generate a Fernet key using `Fernet.generate_key()` from `cryptography.fernet`
and store it in the secret manager. Set `EMAIL_OUTBOX_KEYS` to a JSON object
mapping version names to keys, and set `EMAIL_OUTBOX_KEY_VERSION` to its active
entry. To rotate, deploy the new key to both processes before making it active,
then keep the previous entry until all jobs using it have completed or expired.
The built-in development key derivation is only for local testing.

Delivery is at least once. In an ambiguous SMTP failure, a message may arrive
twice with the same message ID and the same single-use link. Check the
`email_outbox` delivery status and safe error categories when diagnosing a job.
Only the time-limited encrypted credential is queued, and the worker discards
it on delivery or terminal failure.

Deleting an account cancels its queued email, clears queued credentials, and
prevents further retries. After rendering, the worker locks the outbox row,
checks its lease, and holds the lock through the SMTP attempt and result update.
Changing an account email likewise cancels its queued password recovery and
clears the recovery payload. A new recovery request uses the current address.
If the worker takes the lock first, deletion or the email change waits for
that attempt to finish.

## Recovering legacy bcrypt accounts

If a legacy bcrypt password exceeds 72 bytes in UTF-8, sign-in returns the
standard incorrect-credentials response. Use **Forgot your password?** on the
sign-in page to reset it. If recovery email is disabled, an active superuser
can set a new password in user administration. The new password uses Argon2.

## Authentication rate limits

The API counts login (cookie and bearer), signup, and recovery requests in
PostgreSQL. The default window is 300 seconds. Per-account and per-source
thresholds are 30 and 120 for login, 5 and 30 for signup, and 5 and 30 for recovery.
Override `RATE_LIMIT_WINDOW_SECONDS` or the `RATE_LIMIT_LOGIN_ACCOUNT`,
`RATE_LIMIT_LOGIN_SOURCE`, `RATE_LIMIT_SIGNUP_ACCOUNT`,
`RATE_LIMIT_SIGNUP_SOURCE`, `RATE_LIMIT_RECOVERY_ACCOUNT`, and
`RATE_LIMIT_RECOVERY_SOURCE` settings across the API replicas. The returned
`Retry-After` header tells clients when to retry. When the datastore is
unavailable these endpoints respond with 503.

For requests arriving through a proxy, configure Uvicorn's
`FORWARDED_ALLOW_IPS` with only trusted ingress addresses or CIDRs, and ensure
the ingress strips untrusted forwarded headers. The API uses the client
address the server resolved. With direct internet traffic, leave forwarded
address trust disabled for remote peers. The
`auth.rate_limit.requests` metric reports decisions by action and outcome.

## Health and resource limits

Use `/api/v1/utils/health-check/` for process liveness and
`/api/v1/utils/readiness/` to gate traffic on PostgreSQL availability. The
database readiness timeout defaults to one second. Configure
`DB_READINESS_TIMEOUT_SECONDS` if the ingress or database requires a different
budget. The request body limit defaults to 256 KiB through
`MAX_REQUEST_BODY_BYTES`. Set an explicit route-level limit if adding an upload
endpoint that accepts larger bodies.

The connection allowance for API processes is
`WEB_CONCURRENCY × (DB_POOL_SIZE + DB_MAX_OVERFLOW)` per replica. The default
four workers can use up to 28 connections between them. Budget for the worker,
migration, and diagnostics separately. With default settings the mail worker
sends one message at a time. Use `DB_POOL_TIMEOUT_SECONDS` to bound pool wait.
The request dependency caps competing database sessions per API worker and
releases its session before sending ordinary JSON responses.

Application logs contain a request ID, route pattern, duration, and status.
When Sentry is configured, the API strips request data, exception messages, and
span descriptions before sending error and trace events. Align access logging
with that policy at external ingress. FastAPI can also export native
OpenTelemetry metrics through the standard OTLP environment variables and
`FASTAPI_OTEL_AUTO_CONFIGURE=true`. Opt in only after deciding which tool should
export each signal to avoid duplicate tracing.

Check `python -m app.email_worker --healthcheck` for a recent successful worker
poll. Worker logs include queue depth and the age of the oldest pending
message. A worker restart reclaims expired leases without re-creating accounts
or action tokens. Every five minutes an API worker prunes a bounded batch of
expired authentication records, limit buckets, and email payloads. Terminal
email records and revoked sessions are retained up to seven days after that
batch catches up.
