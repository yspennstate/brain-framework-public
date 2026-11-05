import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server import BrainHTTPServer, BrainPaths, LiveBrain, extract_file_paths


class LiveBrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app_dir = root / "app"
        self.app_dir.mkdir()
        self.memory_dir = root / "memory"
        self.memory_dir.mkdir()
        self.activation_dir = root / "graph_fire"
        self.activation_dir.mkdir()
        self.network_db = root / "net.sqlite3"
        self.mesh_db = root / "mesh.sqlite3"
        self.spread_edges = root / "spread_edges.tsv"
        self.fire_log = self.activation_dir / "gf_shadow_fires.tsv"
        self.instincts = root / "instincts.tsv"

        (self.memory_dir / "alpha_memory.md").write_text(
            "---\n"
            'name: alpha_memory\n'
            'description: "Alpha is the directly activated test memory."\n'
            "---\n\n"
            "Alpha carries durable information and links to [[beta_memory]].\n",
            encoding="utf-8",
        )
        (self.memory_dir / "beta_memory.md").write_text(
            "---\nname: beta_memory\ndescription: Beta neighbor.\n---\n\nBeta body.\n",
            encoding="utf-8",
        )
        self.spread_edges.write_text(
            "a\tb\tweight\nalpha_memory\tbeta_memory\t3.5\n",
            encoding="utf-8",
        )
        self.instincts.write_text(
            "id\tmode\tvalidity\ttrigger\tgut\tsting\tsource\tcharge\n"
            "green_not_trusted\tfire\thigh\tx\tVerify the real behavior.\t"
            "A status light lied.\talpha_memory\t0.9\n",
            encoding="utf-8",
        )
        (self.activation_dir / ".abuf_session0123456.json").write_text(
            json.dumps(
                {
                    "nodes": {"alpha_memory": 5.5},
                    "danger": 0.4,
                    "ts": time.time(),
                }
            ),
            encoding="utf-8",
        )
        observed = r"C:\\Users\\example\\active.py"
        self.fire_log.write_text(
            f"{time.time()}\tsession01234\tgreen_not_trusted\t"
            f'{{"command":"Get-Content -LiteralPath \\"{observed}\\""}}\n',
            encoding="utf-8",
        )
        self._create_network_db()
        self._create_mesh_db()

        self.paths = BrainPaths(
            app_dir=self.app_dir,
            network_db=self.network_db,
            memory_dir=self.memory_dir,
            spread_edges=self.spread_edges,
            activation_dir=self.activation_dir,
            fire_log=self.fire_log,
            instincts_tsv=self.instincts,
            mesh_db=self.mesh_db,
        )
        self.brain = LiveBrain(self.paths)

    def tearDown(self):
        self.temp.cleanup()

    def _create_network_db(self):
        connection = sqlite3.connect(self.network_db)
        connection.executescript(
            """
            CREATE TABLE nodes(
              id TEXT PRIMARY KEY, kind TEXT, label TEXT, df INTEGER, idf REAL
            );
            CREATE TABLE edges(
              src TEXT, dst TEXT, kind TEXT, w REAL, n INTEGER, updated REAL,
              PRIMARY KEY(src,dst,kind)
            );
            CREATE TABLE episodes(
              id TEXT PRIMARY KEY, ts REAL, prompt_node TEXT, pattern TEXT, norm REAL
            );
            CREATE TABLE node_content(id TEXT PRIMARY KEY, content TEXT);
            """
        )
        connection.executemany(
            "INSERT INTO nodes VALUES(?,?,?,?,?)",
            [
                ("mem:alpha_memory", "mem", "", 4, 2.2),
                ("mem:beta_memory", "mem", "", 3, 2.5),
                ("kw:alpha", "kw", "alpha", 5, 1.8),
            ],
        )
        connection.executemany(
            "INSERT INTO edges VALUES(?,?,?,?,?,?)",
            [
                ("mem:alpha_memory", "kw:alpha", "co", 2.0, 3, time.time()),
                ("kw:alpha", "mem:beta_memory", "seq", 1.0, 1, time.time()),
            ],
        )
        connection.execute(
            "INSERT INTO episodes VALUES(?,?,?,?,?)",
            ("episode-1", time.time(), "mem:alpha_memory", "{}", 1.0),
        )
        connection.execute(
            "INSERT INTO node_content VALUES(?,?)",
            ("mem:alpha_memory", "Alpha content"),
        )
        connection.commit()
        connection.close()

    def _create_mesh_db(self):
        connection = sqlite3.connect(self.mesh_db)
        connection.executescript(
            """
            CREATE TABLE agents(
              agent_id TEXT PRIMARY KEY, runtime TEXT, session_key TEXT,
              parent_agent TEXT, campaign_id TEXT, cwd TEXT, task TEXT,
              status TEXT, started_at TEXT, last_seen TEXT, lease_until TEXT,
              ttl_seconds INTEGER, metadata_json TEXT
            );
            CREATE TABLE claims(
              claim_id TEXT PRIMARY KEY, agent_id TEXT, resource TEXT,
              canonical_resource TEXT, resource_kind TEXT, mode TEXT,
              purpose TEXT, acquired_at TEXT, renewed_at TEXT, expires_at TEXT,
              ttl_seconds INTEGER, released_at TEXT
            );
            """
        )
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        future = (now + timedelta(hours=1)).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        connection.execute(
            "INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "claude-session0",
                "claude",
                "session0123456",
                "",
                None,
                r"C:\Users\example",
                "Test the live viewer",
                "working",
                now_text,
                now_text,
                future,
                3600,
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "claim-1",
                "claude-session0",
                r"C:\Users\example\claimed.md",
                r"c:\users\example\claimed.md",
                "path",
                "write",
                "Test claim",
                now_text,
                now_text,
                future,
                3600,
                None,
            ),
        )
        connection.commit()
        connection.close()

    def test_extracts_windows_paths_from_truncated_jsonish_text(self):
        text = (
            r'{"command":"Get-Content C:\\Users\\example\\one.md; '
            r'python C:\\Users\\example\\two.py"}'
        )
        self.assertEqual(
            extract_file_paths(text),
            [r"C:\Users\example\one.md", r"C:\Users\example\two.py"],
        )

    def test_live_state_combines_activation_reflex_agent_and_files(self):
        state = self.brain.build_state(layer="live")
        node_ids = {node["id"] for node in state["nodes"]}
        self.assertIn("mem:alpha_memory", node_ids)
        self.assertIn("mem:beta_memory", node_ids)
        self.assertIn("reflex:green_not_trusted", node_ids)
        self.assertIn("agent:claude-session0", node_ids)
        self.assertIn(r"file:C:\Users\example\active.py", node_ids)
        self.assertIn(r"file:C:\Users\example\claimed.md", node_ids)
        self.assertGreaterEqual(state["metrics"]["active_files"], 2)
        hot_file = next(
            node
            for node in state["nodes"]
            if node["id"] == r"file:C:\Users\example\active.py"
        )
        self.assertTrue(hot_file["hot"])

    def test_association_view_includes_typed_nodes(self):
        state = self.brain.build_state(layer="association", query="alpha")
        node_ids = {node["id"] for node in state["nodes"]}
        self.assertIn("mem:alpha_memory", node_ids)
        self.assertIn("kw:alpha", node_ids)

    def test_memory_detail_is_real_and_path_traversal_is_rejected(self):
        detail = self.brain.node_detail("mem:alpha_memory")
        self.assertIn("directly activated", detail["description"])
        self.assertEqual(detail["neighbors"][0]["id"], "mem:beta_memory")
        rejected = self.brain.node_detail("mem:../alpha_memory")
        self.assertEqual(rejected["type"], "unknown")

    def test_http_surface_is_local_and_returns_csp(self):
        server = BrainHTTPServer(("127.0.0.1", 0), self.brain)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/state?layer=live"
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.load(response)
                csp = response.headers.get("Content-Security-Policy")
            self.assertEqual(payload["app"], "brain-live")
            self.assertIn("default-src 'self'", csp)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
