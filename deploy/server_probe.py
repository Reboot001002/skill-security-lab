"""Read-only deployment preflight. Never dumps environment variables or credentials."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def run(argv):
    if not shutil.which(argv[0]):
        return {"available": False}
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return {"available": True, "exit_code": result.returncode,
                "stdout": result.stdout.strip()[:4000],
                "stderr": result.stderr.strip()[:1000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "error": type(exc).__name__}


def probe(target):
    requested = Path(target).expanduser().absolute()
    existing = requested
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    usage = shutil.disk_usage(existing)
    report = {
        "platform": platform.system(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "python_310_or_newer": sys.version_info >= (3, 10),
        "venv_module_available": importlib.util.find_spec("venv") is not None,
        "requested_directory": str(requested),
        "existing_parent": str(existing),
        "parent_writable_hint": os.access(existing, os.W_OK),
        "disk_free_gib": round(usage.free / 1024 ** 3, 2),
        "cpu_count": os.cpu_count(),
        "gpu": run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader"]),
        "pip": run([sys.executable, "-m", "pip", "--version"]),
        "isolation_tools_present": {
            name: shutil.which(name) is not None
            for name in ("docker", "podman", "bwrap", "unshare")
        },
        "persistence_verified": False,
        "runtime_isolation_verified": False,
        "notes": [
            "Tool presence does not establish usable isolation.",
            "A container alone does not establish a safe boundary for skill execution.",
            "Confirm storage persistence using the server provider settings.",
            "This script does not install packages, start services, or call a model."
        ]
    }
    if platform.system() == "Linux":
        try:
            memory = Path("/proc/meminfo").read_text()
            for line in memory.splitlines():
                if line.startswith("MemTotal:"):
                    report["host_visible_memory_gib"] = round(
                        int(line.split()[1]) / 1024 ** 2, 2)
        except OSError:
            pass
        report["cgroup_limits"] = {}
        for name, path in {
            "memory_max": "/sys/fs/cgroup/memory.max",
            "cpu_max": "/sys/fs/cgroup/cpu.max",
            "memory_limit_v1": "/sys/fs/cgroup/memory/memory.limit_in_bytes"
        }.items():
            try:
                report["cgroup_limits"][name] = Path(path).read_text().strip()
            except OSError:
                pass
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=".", help="Intended installation directory")
    args = parser.parse_args()
    print(json.dumps(probe(args.target), ensure_ascii=False, indent=2))
