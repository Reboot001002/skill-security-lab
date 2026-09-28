import argparse
import json
from pathlib import Path
import sys
from .analysis import analyze
from .common import read_json, write_json
from .dataset import generate
from .experiment import audits, calibrate, freeze, load_config, run, verify_fixtures
from .model import Client, ModelError, identity
from .runtime import SandboxError, isolated_preflight


def main(argv=None):
    parser = argparse.ArgumentParser(description="Skill safety experiment; mock runs are never research evidence")
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("generate", help="Generate draft synthetic packages and scenarios")
    p.add_argument("--data", default="data/draft")
    for name in ("verify", "audit", "run", "doctor", "probe-model"):
        p = subs.add_parser(name)
        p.add_argument("--config", default="configs/default.json")
        if name not in ("doctor", "probe-model"):
            p.add_argument("--data", default="data/draft")
            p.add_argument("--out", required=True)
            p.add_argument("--split", choices=("dev", "test"), default="dev")
        if name == "verify":
            p.add_argument("--all-candidates", action="store_true")
        if name == "run":
            p.add_argument("--repeats", type=int, default=1)
            p.add_argument("--freeze")
            p.add_argument("--scene-limit", type=int)
        if name == "doctor":
            p.add_argument("--out", default="runs/doctor")
    p = subs.add_parser("calibrate")
    p.add_argument("--data", default="data/draft")
    p.add_argument("--audits", required=True)
    p.add_argument("--out", required=True)
    p = subs.add_parser("freeze")
    p.add_argument("--data", default="data/draft")
    p.add_argument("--config", required=True)
    p.add_argument("--calibration", required=True)
    p.add_argument("--review", required=True)
    p.add_argument("--verification", required=True)
    p.add_argument("--out", required=True)
    p = subs.add_parser("analyze")
    p.add_argument("--data", default="data/draft")
    p.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config) if hasattr(args, "config") else None
        if args.command == "generate":
            result = generate(args.data)
            print(f'Generated {len(result["skills"])} packages and {len(result["scenarios"])} scenes; human review pending.')
        elif args.command == "doctor":
            result = isolated_preflight(args.out, config)
            write_json(Path(args.out) / "preflight.json", result)
            print(json.dumps(result, indent=2))
        elif args.command == "probe-model":
            client = Client(config)
            if config["backend"] == "mock":
                raise ValueError("Set backend=openai-compatible for a real model probe")
            tools = [{"type": "function", "function": {"name": "ping", "description": "Connectivity probe",
                      "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]
            result = client.chat([{"role": "user", "content": "Call the ping tool once with no arguments."}], tools)
            calls = result["message"].get("tool_calls", [])
            ok = any(c.get("function", {}).get("name") == "ping" for c in calls)
            print(json.dumps({"model": client.identity, "tool_call_observed": ok, "latency_s": result["latency_s"], "usage": result["usage"]}, indent=2))
            if not ok:
                raise ValueError("The server did not return a tool call; verify chat template and tool parser")
        elif args.command == "verify":
            result = verify_fixtures(args.data, config, args.out, args.split, args.all_candidates)
            print(f'Checked {result["count"]} candidate/scene pairs. passed={result["passed"]}')
            if not result["passed"]:
                return 1
        elif args.command == "audit":
            result = audits(args.data, config, args.out, args.split)
            print(f'Audited {len(result["scenarios"])} scenarios')
        elif args.command == "calibrate":
            result = calibrate(args.data, args.audits, args.out)
            print(json.dumps(result["selected"], indent=2))
        elif args.command == "freeze":
            result = freeze(args.data, config, args.calibration, args.review, args.verification, args.out)
            print("Frozen protocol", result["freeze_hash"])
        elif args.command == "run":
            result = run(args.data, config, args.out, args.split, args.repeats, args.freeze, args.scene_limit)
            print("Finished", result["result_kind"])
        elif args.command == "analyze":
            result = analyze(args.data, args.run)
            print(f'Report: {args.run}/analysis/report.md ({result["completed_records"]}/{result["planned_runs"]} records)')
    except (ValueError, OSError, ModelError, SandboxError) as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
