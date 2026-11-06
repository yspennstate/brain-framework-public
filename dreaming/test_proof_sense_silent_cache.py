"""proof_sense keeps the prompts it found silent: a repeat exits early with the same (empty) answer, a firing
prompt is never kept, and an edit to proof_sense.py or math_sense.py empties the cache."""
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
QUIET = "what time is it on the mac and is the film render still going"
PROOF = ("CLAIM: $\\P(X \\ge t) \\le e^{-t^2/2}$.\nARGUMENT:\n1. By Chebyshev's inequality, "
         "$\\P(|S_n - np| \\geq d np) \\le \\exp(-d^2 np/2)$.\n"
         "2. Let $m$ be the largest integer with $\\rho N^{-m} \\ge \\delta$; then $\\rho N^{-m+1} < \\delta$.\n"
         "3. Since the $X_i$ are independent, $\\limsup_n \\mu_n(F) \\le \\mu(F)$ for closed $F$ as $n \\to \\infty$.\n"
         "4. Hence $\\sup_x f(x) \\le \\int f \\, d\\mu$ and the claim follows.")


@pytest.fixture
def hook(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    for f in ("proof_sense.py", "math_sense.py"):
        shutil.copy(os.path.join(HERE, f), code / f)
    home = tmp_path / "home"
    (home / ".claude" / "dreaming").mkdir(parents=True)
    cache = tmp_path / "silent.json"
    counter = [0]

    def run(prompt, session=None, switch=None):
        counter[0] += 1
        if session is None:
            session = "00000000-0000-0000-0000-%012d" % counter[0]
        env = dict(os.environ, HOME=str(home), PROOF_SENSE_SILENT_CACHE=switch or str(cache))
        r = subprocess.run([sys.executable, "-B", "proof_sense.py"], cwd=code, env=env, capture_output=True, text=True,
                           input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt,
                                             "session_id": session}), timeout=120)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def rows():
        return json.loads(cache.read_text())["silent"] if cache.exists() else []

    return run, rows, code, cache


def test_a_silent_prompt_is_kept_and_a_firing_one_never(hook):
    run, rows, _, _ = hook
    assert run(QUIET) == ""
    assert len(rows()) == 1
    assert run(QUIET) == ""
    assert len(rows()) == 1
    first = run(PROOF)
    assert "additionalContext" in first
    assert run(PROOF) == first
    assert len(rows()) == 1


def test_a_kept_verdict_is_served_until_either_file_changes(hook):
    run, rows, code, cache = hook
    run(QUIET)
    d = json.loads(cache.read_text())
    import hashlib
    d["silent"].append(hashlib.sha256(PROOF.encode("utf-8")).hexdigest())   # plant: claim the proof is silent
    cache.write_text(json.dumps(d))
    assert run(PROOF) == ""                                                  # the cache is what answered
    ms = code / "math_sense.py"
    ms.write_text(ms.read_text() + "\n")
    assert "additionalContext" in run(PROOF)                                 # stale cache ignored


def test_only_a_real_session_writes_and_the_switch_turns_it_off(hook):
    run, rows, code, cache = hook
    run(QUIET, session="")
    run(QUIET, session="not-a-uuid")
    assert rows() == []
    run(QUIET, switch="0")
    assert not cache.exists() and not (code / "0").exists()     # "0" means off, not a file named 0
    run(QUIET)
    assert len(rows()) == 1
    (code / "0").write_text(cache.read_text())
    assert run(QUIET, switch="0") == ""
    assert sorted(os.listdir(code)) == ["0", "math_sense.py", "proof_sense.py"]


def test_the_off_switch_reads_nothing(hook):
    run, rows, code, cache = hook
    run(QUIET)
    import hashlib
    d = json.loads(cache.read_text())
    d["silent"].append(hashlib.sha256(PROOF.encode("utf-8")).hexdigest())
    (code / "0").write_text(json.dumps(d))                      # a planted cache under the switch's own name
    assert "additionalContext" in run(PROOF, switch="0")
