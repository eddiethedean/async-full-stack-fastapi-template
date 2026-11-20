"""Verify CI branch gates, deployment secret scope, and report handling."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from yaml import BaseLoader, load

WORKFLOWS = Path(".github/workflows")
PUSH_GATE = "check-push-branch"
SHARED_WORKFLOW = "$/.github/workflows/check-push-branch.yml"


def read_workflow(name: str) -> dict:
    return load((WORKFLOWS / f"{name}.yml").read_text(), Loader=BaseLoader)


def check_deploy_secret_scope() -> None:
    deploy = read_workflow("deploy-docker-compose")["jobs"]["deploy"]
    assert all("secrets." not in value for value in deploy["env"].values())

    required = {"SECRET_KEY", "FIRST_SUPERUSER_PASSWORD", "POSTGRES_PASSWORD"}
    expected = {
        "Start database": {"POSTGRES_PASSWORD"},
        "Stop the previous email worker and migrate": required | {"EMAIL_OUTBOX_KEYS"},
        "Start application": required | {"SMTP_PASSWORD", "EMAIL_OUTBOX_KEYS"},
    }
    step_envs = {step["name"]: step.get("env", {}) for step in deploy["steps"]}
    assert expected.keys() <= step_envs.keys()
    for step, env in step_envs.items():
        assert {key: value for key, value in env.items() if "secrets." in value} == {
            key: f"${{{{ secrets.{key} }}}}" for key in expected.get(step, ())
        }, step

    for step in ("Build", "Pull supporting service images", "Start database"):
        env = step_envs[step]
        for key in required - expected.get(step, set()):
            assert env[key] == "changethis", (step, key)
        assert env["SMTP_PASSWORD"] == env["EMAIL_OUTBOX_KEYS"] == "", step
    assert (
        step_envs["Stop the previous email worker and migrate"]["SMTP_PASSWORD"] == ""
    )


def check_draft_release_gate() -> None:
    jobs = read_workflow("create-draft-release")["jobs"]
    gate = jobs[PUSH_GATE]
    assert gate["uses"] == SHARED_WORKFLOW
    assert gate["with"] == {
        "pushed-ref": "${{ format('refs/heads/{0}', github.event.pull_request.base.ref) }}"
    }
    assert "github.event.pull_request.merged == true" in gate["if"]
    assert "contains(github.event.pull_request.labels.*.name, 'release')" in gate["if"]
    assert "permissions" not in gate
    draft = jobs["create-draft-release"]
    assert draft["needs"] == PUSH_GATE
    assert draft["if"] == "needs.check-push-branch.outputs.matches == 'true'"


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
    ):
        # The API-only recipe removes both the frontend and its workflow.
        if workflow == "playwright" and not has_browser:
            continue
        config = read_workflow(workflow)
        jobs = config["jobs"]
        pushed_ref = "${{ github.event.ref }}"
        if workflow in {"dependency-audit", "playwright"}:
            pushed_ref = (
                "${{ github.event_name == 'push' && github.event.ref || github.ref }}"
            )
        if cancels:
            isolate_run = "github.event_name == 'push'"
            if workflow in {"dependency-audit", "playwright"}:
                isolate_run = (
                    f"({isolate_run} || github.event_name == 'workflow_dispatch')"
                )
            # Workflow-level groups cannot compare ref names case-sensitively.
            # Only an admitted run can cancel an older run on its exact ref.
            assert config["concurrency"] == {
                "group": "${{ github.workflow }}-${{ "
                + isolate_run
                + " && github.run_id || github.ref }}",
                "cancel-in-progress": "true",
            }, workflow
            assert jobs[PUSH_GATE]["permissions"] == {"actions": "write"}, workflow
            assert jobs[PUSH_GATE]["with"]["cancel-older"] == "true", workflow
            assert all("concurrency" not in item for item in jobs.values()), workflow
        else:
            assert "permissions" not in jobs[PUSH_GATE], workflow
            assert "cancel-older" not in jobs[PUSH_GATE]["with"], workflow

        assert jobs[PUSH_GATE]["uses"] == SHARED_WORKFLOW, workflow
        if workflow in {"dependency-audit", "playwright"}:
            assert config["run-name"] == (
                "${{ github.event_name == 'workflow_dispatch' "
                "&& format('Manual {0} CI on {1}', github.ref_type, github.ref) || '' }}"
            )
            assert jobs[PUSH_GATE]["if"] == (
                "github.event_name == 'workflow_dispatch' || "
                "(github.event_name == 'push' && github.event.ref == "
                "format('refs/heads/{0}', github.event.repository.default_branch))"
            )
        assert jobs[PUSH_GATE]["with"]["pushed-ref"] == pushed_ref, workflow
        assert jobs[job]["needs"] == PUSH_GATE, workflow
        assert jobs[job]["if"] == (
            "${{ !cancelled() && (github.event_name != 'push' || "
            f"needs.{PUSH_GATE}.outputs.matches == 'true') }}}}"
        ), workflow


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


def check_playwright_report_extraction() -> None:
    if not (WORKFLOWS / "playwright.yml").is_file():
        return  # The API-only recipe removes the browser workflow.

    steps = read_workflow("playwright")["jobs"]["merge-playwright-reports"]["steps"]
    download = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    assert download["with"].get("skip-decompress") == "true"
    assert download["with"]["path"] == "${{ runner.temp }}/playwright-blob-archives"
    assert download["with"].get("merge-multiple", "false") == "false"
    assert download["with"].get("digest-mismatch", "error") == "error"
    extract = next(step for step in steps if step.get("name") == "Extract blob reports")
    merge = next(step for step in steps if step.get("name") == "Merge into HTML Report")
    assert steps.index(download) < steps.index(extract) < steps.index(merge)

    for layout in (
        (),
        ("artifact",),
        ("blob-report-1/artifact", "blob-report-2/artifact"),
    ):
        with TemporaryDirectory() as directory:
            fixture = Path(directory)
            archives = fixture / "playwright-blob-archives"
            reports = fixture / "frontend" / "all-blob-reports"
            reports.parent.mkdir()
            expected = {}
            for index, archive_name in enumerate(layout, start=1):
                archive = archives / archive_name
                archive.parent.mkdir(parents=True, exist_ok=True)
                report_name = f"report-{index}.zip"
                data = f"report contents for shard {index}".encode()
                with ZipFile(archive, "w") as wrapped:
                    wrapped.writestr(report_name, data)
                expected[report_name] = data

            subprocess.run(
                ["bash", "-c", extract["run"]],
                cwd=fixture,
                env={**os.environ, "RUNNER_TEMP": directory},
                check=True,
            )
            assert {
                report.name: report.read_bytes() for report in reports.glob("*")
            } == expected


def check_report_uploads() -> None:
    backend_steps = read_workflow("test-backend")["jobs"]["test-backend"]["steps"]
    backend_upload = next(
        step for step in backend_steps if step.get("name") == "Store coverage files"
    )
    assert backend_upload["with"].get("if-no-files-found") == "error"
    assert "if" not in backend_upload  # Keep the default success condition.

    if not (WORKFLOWS / "playwright.yml").is_file():
        return  # The API-only recipe removes the browser workflow.
    browser_steps = read_workflow("playwright")["jobs"]["test-playwright"]["steps"]
    browser_tests = next(
        step for step in browser_steps if step.get("name") == "Run Playwright tests"
    )
    test_outcome = f"steps.{browser_tests['id']}.outcome"
    blob_upload = next(
        step
        for step in browser_steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert blob_upload["if"] == "${{ !cancelled() }}"
    assert blob_upload["with"]["if-no-files-found"] == (
        "${{ " + test_outcome + " == 'success' && 'error' || 'warn' }}"
    )


def check_coverage_publishing_scope() -> None:
    backend = read_workflow("test-backend")
    assert "pull_request" in backend["on"]
    assert backend["permissions"] == {"contents": "read"}
    jobs = backend["jobs"]
    producer = jobs["test-backend"]
    coverage_check = next(
        step for step in producer["steps"] if step.get("name") == "Coverage report"
    )
    assert "if" not in coverage_check
    assert coverage_check["run"] == "uv run coverage report --fail-under=90"
    publisher = jobs.get("publish-coverage")
    assert publisher is not None, "Coverage publishing must use the trusted backend run"
    assert publisher["needs"] == [PUSH_GATE, "test-backend"]
    assert publisher["if"] == (
        "github.event_name == 'push' && "
        "needs.check-push-branch.outputs.matches == 'true' && "
        "needs.test-backend.result == 'success'"
    )
    assert publisher["permissions"] == {
        "actions": "read",
        "contents": "read",
        "statuses": "write",
    }
    assert "permissions" not in producer
    assert not (WORKFLOWS / "smokeshow.yml").exists()


def check_coverage_availability() -> None:
    steps = read_workflow("test-backend")["jobs"]["publish-coverage"]["steps"]
    lookup, *publish = steps
    assert lookup["id"] == "coverage"
    assert lookup["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert all(
        step["if"] == "steps.coverage.outputs.artifact-id != ''" for step in publish
    )
    download = next(
        step
        for step in publish
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    assert (
        download["with"]["artifact-ids"] == "${{ steps.coverage.outputs.artifact-id }}"
    )
    assert download["with"]["run-id"] == "${{ github.run_id }}"
    upload = next(step for step in publish if "smokeshow upload" in step.get("run", ""))
    assert upload["env"]["SMOKESHOW_GITHUB_PR_HEAD_SHA"] == "${{ github.sha }}"

    with TemporaryDirectory() as directory:
        gh = Path(directory) / "gh"
        gh.write_text(
            """#!/usr/bin/env bash
set -eu
test "$*" = 'api --paginate --method GET repos/fixture/starter/actions/runs/87/artifacts -f name=coverage-html'
test "$MOCK_RESPONSE" != 'api-error'
printf '%s\n' "$MOCK_RESPONSE"
"""
        )
        gh.chmod(0o700)
        output = Path(directory) / "output"
        env = {
            "PATH": os.pathsep.join((directory, os.environ.get("PATH", os.defpath))),
            "GH_TOKEN": "fixture",
            "GITHUB_REPOSITORY": "fixture/starter",
            "GITHUB_RUN_ID": "87",
            "GITHUB_OUTPUT": str(output),
        }
        command = ["bash", "-e", "-o", "pipefail", "-c", lookup["run"]]
        for pages, artifact_id in (
            ([{"artifacts": []}], ""),
            (
                [{"artifacts": [{"name": "coverage-html", "id": 11, "expired": True}]}],
                "",
            ),
            (
                [
                    {"artifacts": [{"name": "other", "id": 31, "expired": False}]},
                    {
                        "artifacts": [
                            {"name": "coverage-html", "id": 27, "expired": False}
                        ]
                    },
                ],
                "27",
            ),
        ):
            output.unlink(missing_ok=True)
            subprocess.run(
                command,
                env={**env, "MOCK_RESPONSE": "\n".join(map(json.dumps, pages))},
                check=True,
            )
            assert output.read_text() == f"artifact-id={artifact_id}\n"
        output.unlink()
        result = subprocess.run(
            command, env={**env, "MOCK_RESPONSE": "api-error"}, check=False
        )
        assert result.returncode and not output.exists()


def check_playwright_report_availability() -> None:
    if not (WORKFLOWS / "playwright.yml").is_file():
        return  # The API-only recipe removes the browser workflow.
    steps = read_workflow("playwright")["jobs"]["merge-playwright-reports"]["steps"]
    check = next((step for step in steps if step.get("id") == "blob-reports"), None)
    assert check is not None, "Playwright must check for blob reports before merging"
    assert check["env"]["SHARD_RESULT"] == "${{ needs.test-playwright.result }}"
    for name in ("Merge into HTML Report", "Upload HTML report"):
        step = next(step for step in steps if step.get("name") == name)
        assert steps.index(step) > steps.index(check)
        assert step["if"] == "steps.blob-reports.outputs.available == 'true'"

    for shard_result, files, exit_code, output in (
        ("failure", (), 0, ""),
        ("failure", ("note.txt",), 0, ""),
        ("failure", ("report-1.zip",), 0, "available=true\n"),
        ("success", ("report-1.zip", "report-2.zip"), 0, "available=true\n"),
        ("success", (), 1, ""),
    ):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "frontend/all-blob-reports"
            if files:
                reports.mkdir(parents=True)
                for filename in files:
                    (reports / filename).touch()
            github_output = root / "output"
            github_output.touch()
            result = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", check["run"]],
                cwd=root,
                env={"GITHUB_OUTPUT": str(github_output), "SHARD_RESULT": shard_result},
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == exit_code, result
            assert github_output.read_text() == output, result


# Serve only workflow metadata, the run list and cancellation responses. Any
# unexpected request fails locally instead of falling through to the real API.
MOCK_GH = """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
assert args[0] == "api", args
endpoint = next(arg.lstrip("/") for arg in args if arg.lstrip("/").startswith("repos/"))
prefix = f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/"
assert endpoint.startswith(prefix), endpoint
endpoint = endpoint.removeprefix(prefix)
if endpoint == f"runs/{os.environ['GITHUB_RUN_ID']}" and ".workflow_id" in args:
    print(101)
elif endpoint.startswith("workflows/101/runs"):
    status = next(arg.removeprefix("status=") for arg in args if arg.startswith("status="))
    if status == os.environ.get("MOCK_QUERY_FAILURE"):
        sys.exit(1)
    ref_name = os.environ["PUSH_REF"].split("/", 2)[2]
    assert f"branch={ref_name}" in args, args
    runs = json.loads(os.environ["MOCK_RUNS"])
    print(json.dumps({"workflow_runs": [run for run in runs if run["status"] == status]}))
elif endpoint.endswith("/cancel"):
    assert "POST" in args, args
    run_id = endpoint.split("/")[1]
    with open(os.environ["MOCK_CANCEL_LOG"], "a") as log:
        print(run_id, file=log)
    if run_id == os.environ.get("MOCK_CANCEL_FAILURE"):
        sys.exit(1)
elif endpoint == f"runs/{os.environ.get('MOCK_CANCEL_FAILURE')}" and ".status" in args:
    print(os.environ["MOCK_STATUS_AFTER_FAILURE"])
else:
    raise AssertionError(args)
"""


def check_monotonic_cancellation() -> None:
    gate = read_workflow(PUSH_GATE)
    opt_in = gate["on"]["workflow_call"]["inputs"]["cancel-older"]
    assert opt_in["type"] == "boolean"
    assert opt_in["default"] == "false"
    cancel = next(
        step
        for step in gate["jobs"]["check"]["steps"]
        if "inputs.cancel-older" in step.get("if", "")
    )
    assert cancel["if"] == (
        "inputs.cancel-older && (steps.compare.outputs.matches == 'true' || "
        "github.event_name == 'workflow_dispatch')"
    )
    assert cancel["env"]["PUSH_REF"] == "${{ inputs.pushed-ref }}"
    assert cancel["env"]["GH_TOKEN"] == "${{ github.token }}"

    repo = {"id": 33, "full_name": "fixture/starter"}
    branch = "trunk/main"

    def run(run_id: int, number: int, **overrides: object) -> dict:
        return {
            "id": run_id,
            "run_number": number,
            "workflow_id": 101,
            "head_branch": branch,
            "head_repository": repo,
            "event": "push",
            "status": "in_progress",
            **overrides,
        }

    runs = [
        run(401, 1, status="waiting"),
        run(402, 2, status="requested"),
        run(403, 3, status="pending"),
        run(405, 5, head_branch="trunk/Main"),
        run(406, 6, workflow_id=102),
        run(407, 7, head_repository={"id": 34, "full_name": "fixture/fork"}),
        run(408, 8, event="pull_request"),
        run(
            409,
            8,
            event="workflow_dispatch",
            status="queued",
            display_title=f"Manual branch CI on refs/heads/{branch}",
        ),
        run(410, 9),
        run(411, 10),
        run(412, 4, status="completed"),
    ]

    with TemporaryDirectory() as directory:
        bin_dir = Path(directory)
        gh = bin_dir / "gh"
        gh.write_text(MOCK_GH)
        gh.chmod(0o700)
        log = bin_dir / "cancellations"
        env = {
            "PATH": os.pathsep.join(
                (
                    directory,
                    str(Path(sys.executable).parent),
                    os.environ.get("PATH", os.defpath),
                )
            ),
            "GH_TOKEN": "fixture",
            "GITHUB_REPOSITORY": repo["full_name"],
            "GITHUB_REPOSITORY_ID": str(repo["id"]),
            "GITHUB_EVENT_NAME": "push",
            "PUSH_REF": f"refs/heads/{branch}",
            "MOCK_RUNS": json.dumps(runs),
            "MOCK_CANCEL_LOG": str(log),
        }

        def execute(
            run_id: int, number: int, *, succeeds: bool = True, **mock_env: str
        ) -> list[str]:
            log.write_text("")
            result = subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", cancel["run"]],
                env={
                    **env,
                    "GITHUB_RUN_ID": str(run_id),
                    "GITHUB_RUN_NUMBER": str(number),
                    **mock_env,
                },
                capture_output=True,
                text=True,
                check=False,
            )
            assert (result.returncode == 0) == succeeds, result.stderr
            return sorted(log.read_text().splitlines())

        # Run 10 reaches its exact-ref gate first. The late gate on run 9 sees
        # the same list, which deliberately still includes in-flight cancels.
        older_targets = ["401", "402", "403", "409"]
        assert execute(411, 10) == [*older_targets, "410"]
        assert execute(410, 9) == older_targets
        assert (
            execute(
                410, 9, MOCK_CANCEL_FAILURE="409", MOCK_STATUS_AFTER_FAILURE="completed"
            )
            == older_targets
        )
        assert (
            execute(
                410,
                9,
                succeeds=False,
                MOCK_CANCEL_FAILURE="409",
                MOCK_STATUS_AFTER_FAILURE="in_progress",
            )
            == older_targets
        )
        assert execute(410, 9, succeeds=False, MOCK_QUERY_FAILURE="queued") == []

        # GitHub lists branches and tags under the same short head_branch.
        # The run title identifies the exact, case-sensitive ref.
        manual_runs = [
            *runs,
            run(
                413,
                8,
                head_branch="trunk/Main",
                event="workflow_dispatch",
                display_title="Manual branch CI on refs/heads/trunk/Main",
            ),
            run(
                414,
                8,
                head_branch="trunk/Main",
                event="workflow_dispatch",
                display_title="Playwright Tests",
            ),
            run(
                415,
                6,
                head_branch="trunk/Main",
                event="workflow_dispatch",
                display_title="Manual tag CI on refs/tags/trunk/Main",
            ),
            run(
                416,
                7,
                event="workflow_dispatch",
                display_title="Manual tag CI on refs/tags/trunk/main",
            ),
            run(
                417,
                11,
                head_branch="trunk/Main",
                event="workflow_dispatch",
                display_title="Manual tag CI on refs/tags/trunk/Main",
            ),
        ]
        cancelled = execute(
            411,
            10,
            GITHUB_EVENT_NAME="workflow_dispatch",
            PUSH_REF="refs/heads/trunk/Main",
            MOCK_RUNS=json.dumps(manual_runs),
        )
        assert cancelled == ["405", "413"], cancelled
        cancelled = execute(
            411,
            10,
            GITHUB_EVENT_NAME="workflow_dispatch",
            PUSH_REF="refs/tags/trunk/Main",
            MOCK_RUNS=json.dumps(manual_runs),
        )
        assert cancelled == ["415"], cancelled


if __name__ == "__main__":
    check_deploy_secret_scope()
    check_draft_release_gate()
    check_workflow_gates()
    check_playwright_report_extraction()
    check_exact_comparison()
    check_monotonic_cancellation()
    check_report_uploads()
    check_coverage_publishing_scope()
    check_coverage_availability()
    check_playwright_report_availability()
