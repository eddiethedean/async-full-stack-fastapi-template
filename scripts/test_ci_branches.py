"""Check CI branch isolation and execute the shared workflow's ref comparison."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from yaml import BaseLoader, load

WORKFLOWS = Path(".github/workflows")
PUSH_GATE = "check-push-branch"
SHARED_WORKFLOW = "$/.github/workflows/check-push-branch.yml"


def read_workflow(name: str) -> dict:
    return load((WORKFLOWS / f"{name}.yml").read_text(), Loader=BaseLoader)


def check_job_group(job: dict, name: str) -> None:
    assert job["concurrency"] == {
        "group": (
            f"${{{{ github.workflow }}}}-{name}-"
            "${{ needs.check-push-branch.outputs.matches == 'true' && github.ref || github.run_id }}"
        ),
        "cancel-in-progress": "true",
    }, name


def check_workflow_gates() -> None:
    has_browser = (
        Path("frontend/package.json").is_file()
        or (WORKFLOWS / "playwright.yml").is_file()
    )
    for workflow, job, cancels in (
        ("dependency-audit", "audit", True),
        ("playwright", "changes", True),
        ("pre-commit", "pre-commit", False),
        ("test-backend", "test-backend", True),
        ("test-docker-compose", "test-docker-compose", True),
        ("zizmor", "zizmor", False),
        ("smokeshow", "smokeshow", False),
    ):
        # The API-only recipe removes both the frontend and its workflow.
        if workflow == "playwright" and not has_browser:
            continue
        config = read_workflow(workflow)
        jobs = config["jobs"]
        event = "github.event_name"
        pushed_ref = "${{ github.event.ref }}"
        if workflow in {"dependency-audit", "playwright"}:
            pushed_ref = (
                "${{ github.event_name == 'push' && github.event.ref || github.ref }}"
            )
        if workflow == "smokeshow":
            event = "github.event.workflow_run.event"
            pushed_ref = (
                "${{ format('refs/heads/{0}', github.event.workflow_run.head_branch) }}"
            )

        if cancels:
            # Workflow-level groups cannot compare ref names case-sensitively.
            # Give each unchecked push its own group before the gated job joins a ref group.
            assert config["concurrency"] == {
                "group": "${{ github.workflow }}-${{ github.event_name == 'push' && github.run_id || github.ref }}",
                "cancel-in-progress": "true",
            }, workflow
            check_job_group(jobs[job], job)

        assert jobs[PUSH_GATE]["uses"] == SHARED_WORKFLOW, workflow
        if workflow in {"dependency-audit", "playwright"}:
            assert "github.event_name == 'workflow_dispatch'" in jobs[PUSH_GATE]["if"]
        assert jobs[PUSH_GATE]["with"]["pushed-ref"] == pushed_ref, workflow
        assert jobs[job]["needs"] == PUSH_GATE, workflow
        assert jobs[job]["if"] == (
            f"${{{{ !cancelled() && ({event} != 'push' || "
            f"needs.{PUSH_GATE}.outputs.matches == 'true') }}}}"
        ), workflow

        if workflow == "playwright":
            check_job_group(
                jobs["test-playwright"], "test-playwright-${{ matrix.shardIndex }}"
            )
            check_job_group(
                jobs["merge-playwright-reports"], "merge-playwright-reports"
            )


def check_exact_comparison() -> None:
    compare = read_workflow(PUSH_GATE)["jobs"]["check"]["steps"][0]
    assert compare["env"] == {
        "PUSH_REF": "${{ inputs.pushed-ref }}",
        "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
    }
    with TemporaryDirectory() as directory:
        output = Path(directory) / "output"
        for default in ("main", "master", "trunk/main"):
            for ref, matches in (
                (f"refs/heads/{default}", True),
                (f"refs/heads/{default.upper()}", False),
                ("refs/heads/feature/work", False),
                (f"refs/tags/{default}", False),
            ):
                output.unlink(missing_ok=True)
                subprocess.run(
                    ["bash", "-e", "-c", compare["run"]],
                    env={
                        "PUSH_REF": ref,
                        "DEFAULT_BRANCH": default,
                        "GITHUB_OUTPUT": str(output),
                    },
                    check=True,
                )
                assert output.read_text() == f"matches={str(matches).lower()}\n", ref


if __name__ == "__main__":
    check_workflow_gates()
    check_exact_comparison()
