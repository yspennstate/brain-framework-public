#!/usr/bin/env python3
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fok_probe
import brain as deliberate_brain
import spread as legacy_spread
import salience
import spread_recall
import supersession


OLD = "fixture_terminal_recovery_old_2026_07_19"
NEW = "fixture_terminal_recovery_verified_2026_07_19"


def memory_text(slug, description, superseded_by=""):
    route = "superseded_by: %s\n" % superseded_by if superseded_by else ""
    return ("---\n"
            "name: %s\n"
            "description: %s\n"
            "metadata:\n"
            "  type: project\n"
            "  created: 2026-07-19\n"
            "  status: verified\n"
            "%s"
            "---\n\n"
            "Exact terminal recovery structure, crash roster, and safe no-input protocol.\n"
            % (slug, description, route))


class SupersessionProducerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mem = self.root / "memory"
        self.mem.mkdir()
        self.ledger = self.root / "superseded.tsv"
        (self.mem / (OLD + ".md")).write_text(
            memory_text(OLD, "Old exact terminal crash recovery roster", NEW), encoding="utf-8")
        (self.mem / (NEW + ".md")).write_text(
            memory_text(NEW, "Verified exact terminal crash recovery structure"), encoding="utf-8")
        self.ledger.write_text("old\tcorrection\n%s\t%s\n" % (OLD, NEW), encoding="utf-8")
        (self.mem / "MEMORY.md").write_text(
            "# Memory Index\n"
            "- [%s.md](%s.md) - [VERIFIED] Verified exact terminal crash recovery structure.\n"
            "- [%s.md](%s.md) - [PROVISIONAL] Old exact terminal crash recovery roster.\n"
            % (NEW, NEW, OLD, OLD), encoding="utf-8")
        self.old_env = os.environ.get("BRAIN_SUPERSEDED_INDEX")
        os.environ["BRAIN_SUPERSEDED_INDEX"] = str(self.ledger)

    def tearDown(self):
        if self.old_env is None:
            os.environ.pop("BRAIN_SUPERSEDED_INDEX", None)
        else:
            os.environ["BRAIN_SUPERSEDED_INDEX"] = self.old_env
        self.tmp.cleanup()

    def test_routes_frontmatter_ledger_and_fail_open(self):
        frontmatter = supersession.active_supersessions(
            str(self.mem), index_path=str(self.root / "missing.tsv"), include_frontmatter=True)
        self.assertEqual(frontmatter.get(OLD), NEW)

        old_path = self.mem / (OLD + ".md")
        old_path.write_text(memory_text(OLD, "Old exact terminal crash recovery roster"),
                            encoding="utf-8")
        ledger = supersession.active_supersessions(
            str(self.mem), index_path=str(self.ledger), include_frontmatter=False)
        self.assertEqual(ledger.get(OLD), NEW)

        known = {OLD, NEW}
        self.assertEqual(supersession.resolve(OLD, {OLD: "missing"}, known), OLD)
        self.assertEqual(supersession.resolve(OLD, {OLD: NEW, NEW: OLD}, known), OLD)

        long_old = "fixture-long-nested-old"
        hyphen_new = "fixture-hyphen-current"
        (self.mem / (hyphen_new + ".md")).write_text(
            memory_text(hyphen_new, "Hyphenated correction endpoint"), encoding="utf-8")
        padding = "  note: " + ("x" * 6000) + "\n"
        (self.mem / (long_old + ".md")).write_text(
            "---\nname: %s\nmetadata:\n%s  superseded_by: %s\n---\nold\n"
            % (long_old, padding, hyphen_new), encoding="utf-8")
        nested = supersession.active_supersessions(
            str(self.mem), index_path=str(self.root / "missing.tsv"), include_frontmatter=True)
        self.assertEqual(nested.get(long_old), hyphen_new)

    def test_default_inventory_reuses_directory_entries_and_excludes_md_directory(self):
        fake_file_directory = self.mem / "not_a_memory.md"
        fake_file_directory.mkdir()
        original_isfile = supersession.os.path.isfile

        def forbidden_path_probe(_path):
            raise AssertionError(
                "default supersession inventory regressed to one os.path.isfile probe per memory"
            )

        try:
            supersession.os.path.isfile = forbidden_path_probe
            routes = supersession.active_supersessions(
                str(self.mem), index_path=str(self.ledger), include_frontmatter=False)
        finally:
            supersession.os.path.isfile = original_isfile

        self.assertEqual(routes.get(OLD), NEW)
        self.assertNotIn("not_a_memory", routes)

    def test_current_index_collapses_historical_pointer(self):
        lines = supersession.current_index_lines(
            str(self.mem), index_path=str(self.ledger), limit=10)
        joined = "\n".join(lines)
        self.assertIn(NEW, joined)
        self.assertNotIn(OLD, joined)
        self.assertEqual(sum(NEW in line for line in lines), 1)
        self.assertEqual(
            supersession.current_index_lines(str(self.root / "missing-memory-dir")), [])
        missing_cli = subprocess.run(
            [sys.executable, str(HERE / "supersession.py"), "current-index", "--memory-dir",
             str(self.root / "missing-memory-dir")], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(missing_cli.returncode, 0)

    def test_deliberate_resolver_fails_open_on_broken_chain(self):
        mid = "fixture_terminal_recovery_mid_2026_07_19"
        (self.mem / (mid + ".md")).write_text(memory_text(mid, "Intermediate"), encoding="utf-8")
        old_mem = deliberate_brain.MEM
        try:
            deliberate_brain.MEM = str(self.mem)
            self.assertEqual(deliberate_brain._current_slug(
                OLD, {OLD: mid, mid: "missing-endpoint"}), OLD)
        finally:
            deliberate_brain.MEM = old_mem

    def test_salience_keeps_faded_diagnostic_but_excludes_active_views(self):
        scores = salience.score_corpus(str(self.mem))
        old = scores[OLD + ".md"]
        new = scores[NEW + ".md"]
        self.assertEqual(old["superseded_by"], NEW)
        self.assertIn("superseded->faded", old["why"])
        self.assertLess(old["salience"], new["salience"])
        self.assertGreaterEqual(new["salience"], 45)

        sidecars = self.root / "sidecars"
        brain = self.root / "brain"
        sidecars.mkdir()
        brain.mkdir()
        salience.write_sidecars(scores, str(sidecars), str(brain))
        tsv = (sidecars / "salience.tsv").read_text(encoding="utf-8")
        view = (brain / "SALIENCE.md").read_text(encoding="utf-8")
        self.assertIn(OLD, tsv)
        self.assertIn("superseded->faded", tsv)
        self.assertIn(NEW, view)
        self.assertNotIn(OLD, view)

    def test_dream_agenda_routes_old_out_of_every_selector(self):
        out = self.root / "agenda.md"
        env = dict(os.environ)
        env.update({
            "BRAIN_SUPERSEDED_INDEX": str(self.ledger),
            "BRAIN_AGE_AS_OF": "2026-07-19",
            "BRAIN_TIMEZONE": "UTC",
            "DREAM_TYPE": "sws",
            "PYTHONIOENCODING": "utf-8",
        })
        proc = subprocess.run(
            [sys.executable, str(HERE / "dream.py"), str(self.mem), str(out)],
            cwd=str(HERE), env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        agenda = out.read_text(encoding="utf-8")
        self.assertIn(NEW, agenda)
        self.assertNotIn(OLD, agenda)
        self.assertIn("1 real memories", agenda)

    def test_fok_names_only_current_endpoint(self):
        extras = [
            "fixture_terminal_recovery_protocol_alpha_2026_07_19",
            "fixture_terminal_recovery_protocol_beta_2026_07_19",
        ]
        with (self.mem / "MEMORY.md").open("a", encoding="utf-8") as fh:
            for slug in extras:
                (self.mem / (slug + ".md")).write_text(
                    memory_text(slug, "Verified terminal recovery crash protocol"), encoding="utf-8")
                fh.write("- [%s.md](%s.md) - [VERIFIED] Verified terminal recovery crash protocol.\n"
                         % (slug, slug))
        sal_path = self.root / "salience.tsv"
        sal_path.write_text(
            "fname\tsalience\tvalence\tcharge\twhy\n"
            + "\n".join("%s.md\t100\t0\t0\trecent" % slug
                         for slug in [OLD, NEW] + extras) + "\n", encoding="utf-8")

        # The probe ranks through BM25 and records each memory it names per session in KNOW_STATE. Both must be this
        # fixture's: on the live index it finds a live look-alike instead of NEW, and in the live record one passing run
        # silences the next for 30 minutes (both seen 28 Sep). BM25() takes its index path as a default argument bound at
        # import, so the probe is handed a BM25 whose default is an index built from this fixture's memories.
        # The withdrawn memory is made the strongest raw match (only it names the "roots" the prompt asks for), so the
        # probe names NEW only by routing OLD to its correction. With NEW first on its own words the test passed with
        # the routing removed.
        (self.mem / (OLD + ".md")).write_text(
            memory_text(OLD, "Old exact terminal crash recovery roots and safe protocol structure", NEW), encoding="utf-8")
        import bm25
        # Its thresholds are absolute BM25 scores set on a corpus of thousands of memories (fok v2, 24 Sep: top score
        # 30 or more). Among five memories no word is rare and no score can reach them, so 300 unrelated memories stand
        # in for the rest of the corpus; none of them holds a word of the prompt.
        words = ["harbor", "violin", "orchard", "glacier", "lantern", "meadow", "copper", "falcon", "velvet", "canyon",
                 "pepper", "marble", "willow", "compass", "saffron", "thunder", "ribbon", "pebble", "cedar", "quartz"]
        for i in range(300):
            w = [words[(i + 7 * k) % len(words)] for k in range(6)]
            filler = "fixture_filler_%03d_2026_07_19" % i
            (self.mem / (filler + ".md")).write_text(
                "---\nname: %s\ndescription: Notes on %s and %s\nmetadata:\n  type: project\n---\n\n%s\n"
                % (filler, w[0], w[1], " ".join(w)), encoding="utf-8")
        index_path = self.root / "bm25_index.json.gz"
        bm25.build(str(self.mem), str(index_path))
        live_bm25 = bm25.BM25

        class FixtureBM25(live_bm25):
            def __init__(self, path=str(index_path)):
                super().__init__(path)

        old_mem, old_sal, old_state = fok_probe.MEM, fok_probe.SAL, fok_probe.FONK_STATE
        old_know = fok_probe.KNOW_STATE
        old_stdin = sys.stdin
        try:
            bm25.BM25 = FixtureBM25
            fok_probe.MEM = str(self.mem)
            fok_probe.SAL = str(sal_path)
            fok_probe.FONK_STATE = str(self.root / "fonk.json")
            fok_probe.KNOW_STATE = str(self.root / "fok_knowing.json")
            sys.stdin = io.StringIO(json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Which exact verified terminal crash recovery roots and safe protocol structure?",
                "session_id": "fixture-not-a-uuid",
            }))
            capture = io.StringIO()
            with redirect_stdout(capture):
                fok_probe.main()
        finally:
            sys.stdin = old_stdin
            bm25.BM25 = live_bm25
            fok_probe.MEM, fok_probe.SAL, fok_probe.FONK_STATE = old_mem, old_sal, old_state
            fok_probe.KNOW_STATE = old_know
        output = capture.getvalue()
        self.assertIn(NEW, output)
        self.assertNotIn(OLD, output)
        self.assertIn("brain.py recall", output)
        self.assertNotIn("grep the memory dir", output)

    def test_legacy_hand_spread_collapses_old_graph_nodes(self):
        other = "fixture_other_recovery_method_2026_07_19"
        adj = {
            OLD: {NEW: 1.0, other: 3.0},
            NEW: {OLD: 2.0, other: 1.0},
            other: {OLD: 3.0, NEW: 1.0},
        }
        descs = {OLD: "old exact terminal recovery", NEW: "verified terminal recovery", other: "other"}
        hits = legacy_spread.spread(NEW, adj, descs, k=10, superseded={OLD: NEW})
        slugs = [slug for _, slug in hits]
        self.assertNotIn(OLD, slugs)
        self.assertIn(other, slugs)

    def test_related_builder_omits_old_page_and_neighbor(self):
        fake_repo = self.root / "architecture"
        fake_mem = fake_repo / "01_ai_consciousness" / "claude" / "memory"
        fake_mem.mkdir(parents=True)
        (fake_repo / "12_cognitive_architecture" / "dreaming").mkdir(parents=True)
        (fake_repo / "12_cognitive_architecture" / "dreaming" / "superseded.tsv").write_text(
            "old\tcorrection\n%s\t%s\n" % (OLD, NEW), encoding="utf-8")
        (fake_mem / (OLD + ".md")).write_text(
            memory_text(OLD, "Old exact terminal crash recovery roster", NEW), encoding="utf-8")
        shared = "\nCascadia transcript controllers topology manifest exact runner lattice.\n"
        (fake_mem / (NEW + ".md")).write_text(
            memory_text(NEW, "Verified exact terminal crash recovery structure") + shared,
            encoding="utf-8")
        third = "fixture_current_runner_topology_2026_07_19"
        (fake_mem / (third + ".md")).write_text(
            memory_text(third, "Verified exact terminal crash recovery structure Cascadia controllers") + shared,
            encoding="utf-8")
        # The views builder ships beside this tree (framework root) and, in a deployment that keeps
        # its brain in a separate repository, under that repository's root. Take the first that
        # exists; a deployment names its own with BRAIN_VIEWS_BUILDER.
        candidates = [Path(os.environ["BRAIN_VIEWS_BUILDER"])] if os.environ.get("BRAIN_VIEWS_BUILDER") else []
        candidates += [parent / "manifests" / "build_tools" / "build_brain_architecture.py"
                       for parent in (HERE.parents[0], HERE.parents[1])]
        builder = next((c for c in candidates if c.exists()), candidates[-1])
        env = dict(os.environ)
        env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run([sys.executable, str(builder), str(fake_repo)], env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        related = (fake_repo / "01_ai_consciousness" / "brain" / "RELATED.md").read_text(
            encoding="utf-8")
        self.assertIn(NEW, related)
        self.assertNotIn(OLD, related)

    def test_spread_activation_collapses_without_double_counting(self):
        act = {OLD: 1.4, NEW: 1.1, "other": 0.4}
        parent = {OLD: ("bridge_old", 0.8), NEW: ("bridge_new", 0.5)}
        collapsed, parents = spread_recall._collapse_activations(
            act, parent, {OLD: NEW, "bridge_old": "bridge_current"})
        self.assertNotIn(OLD, collapsed)
        self.assertEqual(collapsed[NEW], 1.4)
        self.assertEqual(parents[NEW], ("bridge_current", 0.8))


    def test_schema_rebuild_preserves_ledger_first_partial_route(self):
        # Crash window: route was published, but old frontmatter was not stamped yet.
        (self.mem / (OLD + ".md")).write_text(
            memory_text(OLD, "Old exact terminal crash recovery roster"), encoding="utf-8")
        env = os.environ.copy()
        env["BRAIN_MEM_DIR"] = str(self.mem)
        env["BRAIN_SUPERSEDED_INDEX"] = str(self.ledger)
        env["BRAIN_WRITE_LOCK"] = str(self.root / "memory_write.lock")
        env["BRAIN_SUPERSEDE_CANDIDATES"] = str(self.root / "supersede_candidates.tsv")
        env["BRAIN_SUPERSEDE_JUDGED"] = str(self.root / "supersede_judged.tsv")
        rebuilt = subprocess.run(
            [sys.executable, str(HERE / "schema_supersede.py")],
            capture_output=True, text=True, timeout=15, env=env)
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertIn("%s\t%s" % (OLD, NEW), self.ledger.read_text(encoding="utf-8"))

        stale = "fixture_terminal_recovery_stale_2026_07_19"
        (self.mem / (stale + ".md")).write_text(
            memory_text(stale, "Stale prose endpoint"), encoding="utf-8")
        (self.mem / (OLD + ".md")).write_text(
            memory_text(OLD, "Old roster", NEW) + "superseded by [[%s]]\n" % stale,
            encoding="utf-8")
        conflicted = subprocess.run(
            [sys.executable, str(HERE / "schema_supersede.py")],
            capture_output=True, text=True, timeout=15, env=env)
        ledger_text = self.ledger.read_text(encoding="utf-8")
        self.assertEqual(conflicted.returncode, 0, conflicted.stderr)
        self.assertIn("%s\t%s" % (OLD, NEW), ledger_text)
        self.assertNotIn("%s\t%s" % (OLD, stale), ledger_text)

    def test_remember_repairs_missing_ledger_for_pre_stamped_old(self):
        (self.mem / (NEW + ".md")).unlink()
        self.ledger.unlink()
        old_mem = deliberate_brain.MEM
        old_index = deliberate_brain.SUPERSEDED_INDEX
        old_lock = os.environ.get("BRAIN_WRITE_LOCK")
        try:
            deliberate_brain.MEM = str(self.mem)
            deliberate_brain.SUPERSEDED_INDEX = str(self.ledger)
            os.environ["BRAIN_WRITE_LOCK"] = str(self.root / "memory_write.lock")
            rc = deliberate_brain.remember(
                NEW, "project", "Current exact terminal recovery structure",
                "Current correction.", status="verified",
                evidence="fixture evidence", supersedes=OLD)
            self.assertEqual(rc, 0)
            self.assertIn("%s\t%s" % (OLD, NEW), self.ledger.read_text(encoding="utf-8"))
        finally:
            deliberate_brain.MEM = old_mem
            deliberate_brain.SUPERSEDED_INDEX = old_index
            if old_lock is None:
                os.environ.pop("BRAIN_WRITE_LOCK", None)
            else:
                os.environ["BRAIN_WRITE_LOCK"] = old_lock

if __name__ == "__main__":
    unittest.main()
