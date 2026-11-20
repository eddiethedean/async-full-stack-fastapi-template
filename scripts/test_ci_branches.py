"""Exercise the exact branch gate and verify safe CI run cancellation."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from yaml import BaseLoader, load

WORKFLOWS = Path(".github/workflows")
PUSH_GATE = "check-push-branch"
SHARED_WORKFLOW = "$/.github/workflows/check-push-branch.yml"


def read_workflow(name: str) -> dict:
    return load((WORKFLOWS / f"{name}.yml").read_text(), Loader=BaseLoader)


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
            # Only an admitted run can cancel an older run on its exact ref.
            assert config["concurrency"] == {
                "group": "${{ github.workflow }}-${{ github.event_name == 'push' && github.run_id || github.ref }}",
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
            assert "github.event_name == 'workflow_dispatch'" in jobs[PUSH_GATE]["if"]
        assert jobs[PUSH_GATE]["with"]["pushed-ref"] == pushed_ref, workflow
        assert jobs[job]["needs"] == PUSH_GATE, workflow
        assert jobs[job]["if"] == (
            f"${{{{ !cancelled() && ({event} != 'push' || "
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


def check_coverage_availability() -> None:
    steps = read_workflow("smokeshow")["jobs"]["smokeshow"]["steps"]
    lookup, *publish = steps
    assert lookup["id"] == "coverage"
    assert lookup["env"]["BACKEND_RUN_ID"] == "${{ github.event.workflow_run.id }}"
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
    assert download["with"]["run-id"] == "${{ github.event.workflow_run.id }}"

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
            "BACKEND_RUN_ID": "87",
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
    assert f"branch={os.environ['PUSH_REF'].removeprefix('refs/heads/')}" in args, args
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
    assert "steps.compare.outputs.matches == 'true'" in cancel["if"]
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
        run(409, 8, event="workflow_dispatch", status="queued"),
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


if __name__ == "__main__":
    check_workflow_gates()
    check_exact_comparison()
    check_monotonic_cancellation()
    check_coverage_availability()
