#!/usr/bin/env python3
"""Measure scoped SDK cleanup on a disposable GitHub-hosted Ubuntu 24 runner.

Never intended for a developer workstation or self-hosted runner. No package
removal, Docker pruning, swap changes, source download or build is performed.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


# Fixed paths only: callers cannot supply deletion targets.
TARGETS = (
    "/usr/local/lib/android",
    "/usr/share/dotnet",
    "/usr/local/.ghcup",
    "/opt/ghc",
    "/usr/share/swift",
    "/usr/local/share/powershell",
    "/opt/az",
    "/opt/hostedtoolcache",
)


def check_runner(env):
    expected = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux", "ImageOS": "ubuntu24",
                "CONFIRM_EPHEMERAL_CLEANUP": "yes"}
    for key, value in expected.items():
        if env.get(key) != value:
            raise ValueError(f"Refusing cleanup: {key} must be {value!r}")


def validate_target(path, protected):
    if str(path) not in TARGETS or path.is_symlink():
        raise ValueError(f"Unapproved or symlink target: {path}")
    if path.resolve() != path:
        raise ValueError(f"Target has symlink ancestors: {path}")
    if not path.is_dir() or path.is_mount():
        raise ValueError(f"Target is not an ordinary directory: {path}")
    for item in protected:
        if item == path or item.is_relative_to(path):
            raise ValueError(f"Target contains a protected path: {path}")


def measure(path):
    usage = shutil.disk_usage(path)
    return {"path": str(path), "total_bytes": usage.total,
            "used_bytes": usage.used, "free_bytes": usage.free}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        check_runner(os.environ)
        workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
        report = args.report.absolute()
        if report.exists() or report.is_symlink() or not report.parent.is_dir():
            raise ValueError("Report must be a new file in an existing directory")
        protected = [workspace, Path(sys.executable).resolve(), report.resolve()]
        present = []
        # Validate all targets before removing any; absent paths are logged.
        for target in TARGETS:
            path = Path(target)
            if os.path.lexists(path):
                validate_target(path, protected)
                present.append(path)
        docker = subprocess.run(["docker", "info", "--format", "{{.DockerRootDir}}"],
                                check=True, capture_output=True, text=True).stdout.strip()
        docker_path = Path(docker).resolve(strict=True)
        for path in present:
            validate_target(path, protected + [docker_path])
        result = {"schema_version": 1, "runner_image": os.environ.get("ImageVersion"),
                  "before": measure(workspace), "docker_before": measure(docker_path),
                  "absent": [p for p in TARGETS if not os.path.lexists(p)], "removed": [],
                  "status": "running", "policy": "SDK directories only; keep swap, packages and Docker images"}
        try:
            for path in present:
                before = shutil.disk_usage(workspace).free
                print(f"Removing approved ephemeral SDK directory: {path}", flush=True)
                subprocess.run(["sudo", "-n", "rm", "-rf", "--one-file-system", "--", str(path)], check=True)
                if os.path.lexists(path):
                    raise ValueError(f"Target remains after cleanup: {path}")
                recovered = shutil.disk_usage(workspace).free - before
                result["removed"].append({"path": str(path), "free_bytes_delta": recovered})
            result["status"] = "success"
        finally:
            result["after"] = measure(workspace)
            result["docker_after"] = measure(docker_path)
            result["recovered_bytes"] = result["after"]["free_bytes"] - result["before"]["free_bytes"]
            report.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2), flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            gib = 2**30
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write("## Scoped hosted-runner cleanup\n\n")
                stream.write(f"Before: {result['before']['free_bytes']/gib:.2f} GiB free\n\n")
                stream.write(f"After: {result['after']['free_bytes']/gib:.2f} GiB free\n\n")
                stream.write(f"Recovered: {result['recovered_bytes']/gib:.2f} GiB\n\n")
                stream.write("No sources or SDK image pulled; no build or release. "
                             "60 GiB is an initial guard, not a measured build requirement.\n")
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"hosted-disk-preflight: {error}\n")


if __name__ == "__main__":
    main()
