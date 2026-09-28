"""Inspect upstream material manifests without importing or executing any source."""
import hashlib
from pathlib import Path
from .common import read_json, safe_child, write_json


def inspect_materials(source, output):
    source = Path(source)
    manifest = read_json(source / "download_manifest.json")
    entries = list(manifest["files"])
    extra = source / "external_assets_manifest.json"
    if extra.exists():
        entries.extend(read_json(extra))
    checks = []
    for entry in entries:
        relative = entry["local_path"].replace("\\", "/")
        path = safe_child(source, relative)
        raw = path.read_bytes() if path.is_file() else None
        actual = hashlib.sha256(raw).hexdigest() if raw is not None else None
        expected = entry.get("sha256")
        line_endings_only = False
        if raw is not None and actual != expected and b"\x00" not in raw:
            lf = raw.replace(b"\r\n", b"\n")
            line_endings_only = any(hashlib.sha256(v).hexdigest() == expected for v in (lf, lf.replace(b"\n", b"\r\n")))
        checks.append({"path": relative, "exists": path.is_file(), "sha256_matches": actual == entry.get("sha256"),
                       "line_endings_only": line_endings_only, "expected_sha256": entry.get("sha256"), "actual_sha256": actual})
    tasks = read_json(source / "task_inventory.json")
    skills = read_json(source / "skill_inventory.json")
    scenes = read_json(source / "annotations" / "scene_templates.json")
    missing = []
    for scene in scenes:
        fields = [k for k in ("authorization", "data_policy", "installed_skill_set", "skill_version_mapping", "expected_candidate_labels", "runtime_evidence") if scene.get(k) is None]
        if fields:
            missing.append({"scene_id": scene["scene_id"], "missing": fields})
    report = {"source": str(source), "inspection_only": True, "executed_upstream_code": False,
              "download_files": len(checks), "integrity_passed": bool(checks) and all(c["sha256_matches"] for c in checks),
              "exact_byte_matches": sum(c["sha256_matches"] for c in checks),
              "line_ending_only_mismatches": sum(c["line_endings_only"] for c in checks),
              "tasks": len(tasks), "skill_entries": len(skills), "scenes": len(scenes),
              "runtime_verified_tasks": sum(bool(t.get("runtime_verified")) for t in tasks),
              "missing_scene_fields": missing, "file_checks": checks,
              "ready_for_this_runner": False,
              "required_adaptation": ["Create per-task input and output adapters and preserve upstream validators outside the agent sandbox.",
                                      "Complete independent authorization, composition and version labels.",
                                      "Create same-capability alternatives and freeze development/test splits.",
                                      "Adapt any risk sample to synthetic data and local-only receivers before execution.",
                                      "Implement an explicit runner adapter; raw upstream task folders are not the generated fixture schema."]}
    write_json(output, report)
    return report
