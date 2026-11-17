# Contributing

Contributions to Async Full Stack FastAPI are welcome.

Use [issues](https://github.com/eddiethedean/async-full-stack-fastapi-template/issues) for bugs and [discussions](https://github.com/eddiethedean/async-full-stack-fastapi-template/discussions) for questions or architectural proposals.

## Development

Follow [development.md](./development.md) to run the backend, frontend, and supporting services.

## Pull Requests

Keep each pull request focused and explain the problem it solves. Include reproduction steps for bug fixes and tests for behavior changes.

Run the affected tests and the pre-commit checks before submitting.

```bash
uv run prek run --all-files
```

Backend tests run from the `backend` directory.

```bash
uv run bash scripts/test.sh
```

When changing dependencies, include the corresponding lockfile update and explain why the dependency is needed. Keep database operations asynchronous and run blocking work outside the event loop.

You may use AI tools. Review the resulting code and be prepared to explain its behavior, tradeoffs, and tests.
