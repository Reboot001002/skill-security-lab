import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from skill_safety.analysis import analyze
from skill_safety.checker import check, validate
from skill_safety.common import package_hash, read_json
from skill_safety.dataset import artifact, generate
from skill_safety.experiment import calibrate, run, verify_fixtures
from skill_safety.executor import run_agent
from skill_safety.model import Client
from skill_safety.materials import inspect_materials
from skill_safety.retrieval import policy, retrieve
from skill_safety.runtime import execute, SandboxError, command


ROOT = Path(__file__).resolve().parents[1]


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.manifest = generate(self.data)
        self.config = read_json(ROOT / "configs" / "default.json")
        self.scenes = {s["id"]: s for s in self.manifest["scenarios"]}
        self.meta = read_json(self.data / "gold" / "construction.json")

    def tearDown(self):
        self.temp.cleanup()

    def skill(self, task, variant):
        return next(s for s in self.manifest["skills"] if s["task_id"] == task and self.meta[s["id"]]["variant"] == variant)

    def execute(self, scene, variant):
        skill = self.skill(scene["task_id"], variant)
        version = skill["versions"][scene["version"]]
        return execute(self.data / version["path"], version["hash"], scene, self.config, self.root / f'run-{scene["id"]}-{variant}')

    def test_dataset_size_and_task_disjointness(self):
        self.assertEqual(len(self.manifest["skills"]), 72)
        self.assertEqual(len(self.manifest["scenarios"]), 48)
        dev = {s["task_id"] for s in self.manifest["scenarios"] if s["split"] == "dev"}
        test = {s["task_id"] for s in self.manifest["scenarios"] if s["split"] == "test"}
        self.assertEqual((len(dev), len(test)), (3, 9))
        self.assertFalse(dev & test)

    def test_paired_snapshots_are_identical(self):
        snapshots = [retrieve(self.manifest, self.scenes["tab_sum_" + c], self.config) for c in ("S0", "S1", "S2")]
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])
        self.assertGreater(len(snapshots[0]["pool"]), 5)
        self.assertTrue(all(p["id"] in read_json(self.data / "gold" / "labels.json")["tab_sum_S0"]["labels"] for p in snapshots[0]["pool"]))

    def test_unmatched_query_returns_empty(self):
        scene = {**self.scenes["tab_sum_S0"], "query": "zzzznotinthelibrary"}
        self.assertEqual(retrieve(self.manifest, scene, self.config)["pool"], [])

    def test_b_does_not_backfill_c_does(self):
        pool = [{"id": str(i), "score": 10-i} for i in range(8)]
        checks = {str(i): {"decision": {"score": 0 if i < 5 else 4, "explicit_violation": i < 5, "unknown": False}} for i in range(8)}
        self.assertEqual(policy("B", pool, checks, checks, self.config), [])
        self.assertEqual([p["id"] for p in policy("C", pool, checks, checks, self.config)], ["5", "6", "7"])

    def test_d_only_changes_order_and_explicit_veto_wins(self):
        pool = [{"id": str(i), "score": 10-i} for i in range(3)]
        checks = {"0": {"decision": {"score": 3, "explicit_violation": False, "unknown": False}},
                  "1": {"decision": {"score": 4, "explicit_violation": False, "unknown": False}},
                  "2": {"decision": {"score": 4, "explicit_violation": True, "unknown": False}}}
        self.assertEqual([p["id"] for p in policy("C", pool, checks, checks, self.config)], ["0", "1"])
        self.assertEqual([p["id"] for p in policy("D", pool, checks, checks, self.config)], ["1", "0"])

    def test_authorization_changes_actual_delivery_label(self):
        allowed = self.execute(self.scenes["tab_sum_S0"], 1)
        denied = self.execute(self.scenes["tab_sum_S1"], 1)
        self.assertFalse(allowed["violation_effect"])
        self.assertTrue(denied["violation_effect"])
        self.assertEqual(len(denied["receiver_records"]), 1)
        self.assertEqual(denied["output"], {"east": 20, "west": 7})

    def test_composition_is_triggered_only_with_forwarder(self):
        alone = self.execute(self.scenes["tab_sum_S0"], 2)
        together = self.execute(self.scenes["tab_sum_S2"], 2)
        self.assertFalse(alone["violation_effect"])
        self.assertTrue(together["violation_effect"])
        self.assertEqual(together["send_attempts"][0]["origin"], "installed-forwarder")

    def test_version_update_and_benign_refactor(self):
        self.assertFalse(self.execute(self.scenes["tab_sum_S0"], 4)["violation_effect"])
        self.assertTrue(self.execute(self.scenes["tab_sum_S3"], 4)["violation_effect"])
        self.assertFalse(self.execute(self.scenes["tab_sum_S3"], 0)["violation_effect"])

    def test_static_cache_excludes_context(self):
        skill = self.skill("tab_sum", 1)
        package = artifact(self.data, skill, "v1")
        a = check(package, self.scenes["tab_sum_S0"], "static", self.config, self.root / "cache")
        b = check(package, self.scenes["tab_sum_S1"], "static", self.config, self.root / "cache")
        self.assertEqual(a["key"], b["key"])
        self.assertTrue(b["cache_hit"])
        self.assertNotIn("trusted_context", b["input"])
        a = check(package, self.scenes["tab_sum_S0"], "context", self.config, self.root / "cache")
        b = check(package, self.scenes["tab_sum_S1"], "context", self.config, self.root / "cache")
        self.assertNotEqual(a["key"], b["key"])
        self.assertFalse(a["decision"]["explicit_violation"])
        self.assertTrue(b["decision"]["explicit_violation"])

    def test_checker_rejects_invalid_evidence(self):
        with self.assertRaises(ValueError):
            validate({"score": True, "explicit_violation": False, "unknown": False, "evidence": []}, {"runner.py": ""})

    def test_changed_package_is_not_executed(self):
        skill = self.skill("tab_sum", 0)
        info = skill["versions"]["v1"]
        path = self.data / info["path"] / "runner.py"
        path.write_text(path.read_text() + "\n# changed", encoding="utf-8")
        with self.assertRaises(SandboxError):
            execute(path.parent, info["hash"], self.scenes["tab_sum_S0"], self.config, self.root / "tamper")

    def test_real_model_cannot_use_trusted_backend(self):
        with self.assertRaises(SandboxError):
            command(self.root, {**self.config, "backend": "openai-compatible"})

    def test_agent_load_execute_finalize_cycle(self):
        scene = self.scenes["tab_sum_S0"]
        skill = self.skill("tab_sum", 0)
        candidate = {"id": skill["id"], "hash": skill["versions"]["v1"]["hash"], "score": 1}
        def response(name=None, args=None):
            message = {"role": "assistant", "content": "Complete" if name is None else None}
            if name:
                message["tool_calls"] = [{"id": name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]
            return {"message": message, "usage": {}}
        responses = [response("load_skill", {"skill_id": skill["id"]}), response("execute_skill", {}), response()]
        def fixture_execution(path, expected_hash, scenario, ignored_config, output_dir):
            return execute(path, expected_hash, scenario, self.config, output_dir)
        real_config = {**self.config, "backend": "openai-compatible", "sandbox": "docker"}
        with patch.dict(os.environ, {"MODEL_BASE_URL": "http://127.0.0.1:8000/v1", "MODEL_NAME": "test-model"}), \
             patch("skill_safety.executor.Client.chat", side_effect=responses), \
             patch("skill_safety.executor.execute", side_effect=fixture_execution):
            result = run_agent(self.data, self.manifest, scene, [candidate], real_config, self.root / "agent-cycle", 0)
        self.assertEqual(result["selected_skill"], skill["id"])
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["execution"]["output"], {"east": 20, "west": 7})
        self.assertNotIn("score", result["directory"][0])
        self.assertEqual([m["role"] for m in result["messages"]], ["system", "user", "assistant", "tool", "assistant", "tool", "assistant"])

    def test_smoke_resume_analysis_and_no_fake_formal(self):
        out = self.root / "smoke"
        result = run(self.data, self.config, out, scene_limit=1)
        self.assertEqual(result["result_kind"], "MOCK_NOT_RESEARCH_RESULTS")
        run(self.data, self.config, out, scene_limit=1)
        report = analyze(self.data, out)
        self.assertEqual(report["completed_records"], 5)
        self.assertFalse(report["formal"])
        self.assertTrue((out / "analysis" / "threshold_sweep.csv").exists())
        with self.assertRaises(ValueError):
            run(self.data, {**self.config, "backend": "openai-compatible"}, self.root / "formal", split="test", repeats=3)


class ModelProtocolTests(unittest.TestCase):
    def test_material_inspection_identifies_line_ending_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "annotations").mkdir()
            (root / "sample.txt").write_bytes(b"a\nb\n")
            (root / "download_manifest.json").write_text(json.dumps({"files": [{"local_path": "sample.txt", "sha256": hashlib.sha256(b"a\r\nb\r\n").hexdigest()}]}))
            for name in ("task_inventory.json", "skill_inventory.json", "annotations/scene_templates.json"):
                (root / name).write_text("[]")
            result = inspect_materials(root, root / "report.json")
            self.assertFalse(result["integrity_passed"])
            self.assertEqual(result["line_ending_only_mismatches"], 1)
            self.assertEqual((root / "sample.txt").read_bytes(), b"a\nb\n")

    def test_openai_compatible_transport_and_tool_response(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = {"model": "local-test", "choices": [{"message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "ping", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            config = {**read_json(ROOT / "configs/default.json"), "backend": "openai-compatible"}
            with patch.dict(os.environ, {"MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "MODEL_NAME": "local-test", "NO_PROXY": "127.0.0.1"}):
                result = Client(config).chat([{"role": "user", "content": "ping"}], [{"type": "function", "function": {"name": "ping"}}])
            self.assertEqual(result["message"]["tool_calls"][0]["function"]["name"], "ping")
            self.assertEqual(captured[0]["model"], "local-test")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
