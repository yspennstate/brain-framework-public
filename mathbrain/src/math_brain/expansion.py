"""Document expansion for memory-born cards: the mathematical words of a card's source memory that the card lacks.

A house result enters the store as its memory's description, in the program's own names ("Janson Conjecture 20.1 proved
for every real r >= 1"). A question about it usually describes the objects instead ("the r-th moment of the width of the
k-th generation of a size-conditioned Galton-Watson tree"), and the card, short and named, scores below the prefilter's
floor while the ranker would have put it first on its title phrase. The memory body carries the objects. This picks, for
each current memory-born card, up to K words of its memory that the card does not carry, and writes them beside the
store as <store>.expansion.pkl = {"expansion": {node_id: "word word ..."}, ...}. The prefilter indexes them, the ranker
scores them (math_brain.retrieval.EXPANSION) and the hook's coverage checks read them; the node is never changed, so its
body, digest and rendering stay the card's own.

Which words. Content terms of the memory body (the ranker's tokenizer), no digits, not already in the card, and:
  - in the TITLES of at least TITLE_MIN current claim cards that are not memory-born - the store's mathematical
    vocabulary; a memory narrates how a result was found and audited, and a first cut that took any known word handed
    the cards "fetched", "memo", "copied", "rewritten";
  - in at most MAX_DF_SHARE of the current cards (specific) and in at least 2 (a known word, never a private name);
  - in at most HIS_SHARE of the owner's distinct prompts: a word his own prompts use ("book", "lines", "average",
    "pass") is a hook his work orders would pull;
  - outside two stop lists (process vocabulary, generic research words);
ranked by count in the memory x IDF, top K.

Rebuilt with every prefilter index (math_prefilter.build), so a revised or newly transported card gets its expansion the
next time the index is built and a superseded card's expansion disappears with it. Without the memory directory (an
exported copy of the store) there is no expansion and the index is built exactly as before.
"""
from __future__ import annotations

import collections
import contextlib
import hashlib
import json
import math
import os
import pickle
import re
import sqlite3
import tempfile
import time
from pathlib import Path

def _env_path(name):
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


# The memory directory whose notes the house cards were born from, and the prompt ledger beside it. The caller names
# them (build's memory_dir, or MATHBRAIN_EXPANSION_MEMORY / MATHBRAIN_EXPANSION_PROMPTS); unnamed, build reports
# "unconfigured" in LAST_BUILD. Until 30 Sep one machine's absolute user path was the committed default, and an unset or
# missing directory returned an empty expansion exactly like a store with nothing to expand (account04/B03,
# B03-R79-EXPANSION-UNCONFIGURED-LOOKS-SUCCESSFUL-20260929-01).
MEMORY_DIR = _env_path("MATHBRAIN_EXPANSION_MEMORY")
PROMPTS = _env_path("MATHBRAIN_EXPANSION_PROMPTS")
#: What the last build() found: status unconfigured | source_missing | success_empty | success, the source it read,
#: the sidecar it wrote (or None) and a reason when it built nothing.
LAST_BUILD: dict = {}
K = int(os.environ.get("MATHBRAIN_EXPANSION_K", "20") or 20)
# A sealed build refuses a prompt record it cannot read instead of counting around it (interactive audit B100-05): for
# ordinary serving a quarantined record keeps the build alive, for a sealed generation it would silently change the
# corpus the generation declares.
STRICT = os.environ.get("MATHBRAIN_EXPANSION_STRICT", "0").strip() not in ("0", "", "off", "false")
MAX_DF_SHARE = 0.02
HIS_SHARE = 0.002
TITLE_MIN = 2
VERSION = "expansion.v1"

PROCESS = ("paper papers draft drafts lean repo repos github commit commits pushed push published publish shipped audit "
           "audits audited referee referees reviewer review reviewed session sessions agent agents subagent subagents "
           "memory memories note notes record records ledger file files folder folders script scripts pdf tex latex "
           "owner his him he we our us i me my run runs running ran log logs json csv html md py python code codex "
           "claude opus sonnet gpt fable hook hooks brain dream verified provisional status campaign campaigns task "
           "tasks queue box boxes cpu gpu minutes hours today tonight yesterday morning evening wrote written writing "
           "read reading fixed fix fixes bug bugs error errors mistake mistakes scar scars lesson lessons measured "
           "check checked checks test tests tested version versions section sections page pages email posted post "
           "researchgate arxiv doi journal submitted submission acknowledgment acknowledgement figure figures table "
           "tables appendix supplement supplementary artifact artifacts stands refuted refute refutes refutation "
           "reproduced reproduce refereed campaign's superseded supersedes nobody sits percent predicted certificate "
           "certificates survives prop thm verbatim settled attack sees numerics master wiki wikipedia")
GENERIC = ("open problem problems conjecture conjectures theorem theorems proof proofs prove proved proving solve solved "
           "solving solution solutions result results question questions lemma lemmas true false known famous "
           "literature full fully general case cases work working method methods close closer new old first second "
           "third one two three main key exact exactly also yes no not every any some")


def _terms(text):
    from math_brain import retrieval as R
    return list(R._content_terms(R.index_signals(text or "").lexical_tokens))


def sidecar_path(store_path) -> str:
    return str(store_path) + ".expansion.pkl"


HIS_RULE = "prefix300-cluster-union.v1"
"""How the owner's prompts are counted: prompts sharing their first 300 characters form one cluster (a re-sent ask
whose tail changed is one ask), and a term counts once per cluster if any prompt in the cluster carries it."""


def _tokenizer_identity() -> str:
    """The digest of the tokenizer's source (retrieval.py): counts made by another tokenizer are not reused."""
    from math_brain import retrieval as R

    try:
        return hashlib.sha256(Path(R.__file__).read_bytes()).hexdigest()
    except OSError:
        return "unknown"


def _his_df(cache_path):
    """term -> number of the owner's prompt clusters carrying it, and the number of clusters (HIS_RULE); _his_counts
    also returns the records it refused."""
    df, n, _rejected = _his_counts(cache_path)
    return df, n


def _his_counts(cache_path, strict=None, prompts=None):
    """(term -> clusters carrying it, number of clusters, refused records by reason). With strict (default STRICT) a
    refused record fails the count with ValueError instead of being counted around.

    The count is order-independent: every prompt of a cluster contributes its terms, each term once per cluster.
    Until 29 Sep only a cluster's first prompt was read, so a later prompt's own terms were dropped and the counts
    depended on the order (interactive audit BM-20260929-03). The cache is bound to the prompt file's resolved path
    and the SHA-256 of its bytes, the tokenizer's source digest and HIS_RULE; until 29 Sep it was keyed by size and
    modification time alone, so another file, or the same file replaced, with an equal stamp read the old counts
    (BM-20260929-02). The cache is published atomically under a private temporary name.

    A record that cannot be read as an owner prompt is refused and counted, never guessed at: a line that is not JSON,
    JSON that is not an object, an owner record with no text or with a text that is not a string. Until 30 Sep a
    non-string text reached str.strip and the whole count raised, which dropped the expansion from the index build
    (interactive audit B100-05). Other speakers' records and owner prompts under 12 characters are left out by the
    rule, not refused."""
    strict = STRICT if strict is None else strict
    prompts = Path(prompts) if prompts else PROMPTS
    if prompts is None:
        return {}, 0, {}
    try:
        raw = prompts.read_bytes()
    except OSError:
        return {}, 0, {}
    tokenizer = _tokenizer_identity()
    # an unreadable tokenizer source is no identity: two such environments would share one key, so the cache is neither
    # read nor written then (interactive audit, p. 42)
    cacheable = tokenizer != "unknown"
    key = (VERSION, HIS_RULE, str(prompts.resolve()), hashlib.sha256(raw).hexdigest(), tokenizer)
    if cacheable:
        try:
            with open(cache_path, "rb") as fh:
                cached = pickle.load(fh)
            if cached.get("key") == key:
                rejected = dict(cached.get("rejected") or {})
                if strict and rejected:
                    raise ValueError("prompt records refused in a strict build: %s" % rejected)
                return cached["df"], cached["n"], rejected
        except ValueError:
            raise
        except Exception:
            pass
    clusters: dict[str, set] = {}
    rejected: collections.Counter = collections.Counter()
    for line in raw.decode("utf-8", errors="replace").split("\n"):     # newlines only, as a text-mode read split it
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            rejected["not_json"] += 1
            continue
        if not isinstance(d, dict):
            rejected["not_an_object"] += 1
            continue
        if d.get("origin") != "owner":
            continue
        t = d.get("text")
        if t is None:
            rejected["no_text"] += 1
            continue
        if not isinstance(t, str):
            rejected["text_not_a_string"] += 1
            continue
        t = t.strip()
        if len(t) < 12:
            continue
        clusters.setdefault(t[:300], set()).update(_terms(t[:8000]))
    df = collections.Counter()
    for terms in clusters.values():
        df.update(terms)
    n = len(clusters)
    if cacheable:
        try:
            directory = os.path.dirname(os.path.abspath(str(cache_path)))
            descriptor, temporary = tempfile.mkstemp(prefix=".his_df.", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(descriptor, "wb") as fh:
                    pickle.dump({"key": key, "df": dict(df), "n": n, "rejected": dict(rejected)}, fh,
                                protocol=pickle.HIGHEST_PROTOCOL)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temporary, cache_path)
            except BaseException:
                if os.path.exists(temporary):
                    os.remove(temporary)
                raise
        except Exception:
            pass
    if strict and rejected:
        raise ValueError("prompt records refused in a strict build: %s" % dict(rejected))
    return dict(df), n, dict(rejected)


LOCK_WAIT_S = 120.0
LOCK_STALE_S = 900.0


@contextlib.contextmanager
def _build_lock(path, wait_s=None, stale_s=None):
    """One expansion build at a time for a sidecar (interactive audit B100-04, 30 Sep 2026). A lock file older than
    stale_s is a builder that died holding it and is taken over; a live one is waited for up to wait_s, then the build
    fails, which the prefilter records and builds without expansion, as for any expansion failure."""
    wait_s = LOCK_WAIT_S if wait_s is None else wait_s
    stale_s = LOCK_STALE_S if stale_s is None else stale_s
    lock = path + ".lock"
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > stale_s:
                    os.remove(lock)
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("another expansion build holds " + lock)
            time.sleep(0.2)
            continue
        try:
            os.write(fd, ("%d %.3f" % (os.getpid(), time.time())).encode("ascii"))
        finally:
            os.close(fd)
        break
    try:
        yield
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _generation(current_nodes) -> str:
    """The store state a build read: the newest creation stamp among its current nodes. The store only grows, so a
    later read never has an older generation."""
    return max((str(getattr(node, "created_at", "") or "") for node in current_nodes), default="")


def _publish(path, payload) -> bool:
    """Write the sidecar under a private temporary name and swap it in, unless the sidecar already there was built from
    a newer store state (a later generation); then leave it and return False. Until 30 Sep every build staged through
    the one name path + ".tmp", so two builds could publish each other's bytes and the second then failed on a missing
    file (interactive audit B100-04)."""
    try:
        with open(path, "rb") as fh:
            current = pickle.load(fh)
        if isinstance(current, dict) and str(current.get("generation") or "") > str(payload.get("generation") or ""):
            return False
    except Exception:
        pass
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary = tempfile.mkstemp(prefix=".expansion.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise
    return True


def build(store_path, current_nodes, memory_dir=None, prompts=None) -> dict:
    """Compute the expansion of every current memory-born card and write the sidecar. Returns {node_id: text}, and says
    in LAST_BUILD what it found: unconfigured (no memory directory named), source_missing (named but absent),
    success_empty (built, no card expanded) or success.

    current_nodes: the store's current revisions (what the index is built from). memory_dir and prompts default to
    MEMORY_DIR and PROMPTS (the prompt ledger defaults to prompts/PROMPTS.jsonl under the memory directory). Raises
    nothing the caller must handle beyond ordinary I/O; math_prefilter.build treats any failure as 'no expansion'."""
    memory = Path(memory_dir) if memory_dir else MEMORY_DIR
    LAST_BUILD.clear()
    LAST_BUILD.update({"status": None, "source": str(memory) if memory else None, "sidecar": None, "cards": 0,
                       "reason": None, "published": False})
    if memory is None:
        LAST_BUILD.update(status="unconfigured",
                          reason="no memory directory: pass memory_dir or set MATHBRAIN_EXPANSION_MEMORY")
        return {}
    if not memory.is_dir():
        LAST_BUILD.update(status="source_missing", reason="the memory directory does not exist")
        return {}
    ledger = Path(prompts) if prompts else (PROMPTS or memory / "prompts" / "PROMPTS.jsonl")
    path = sidecar_path(store_path)
    with _build_lock(path):
        out = _build(store_path, list(current_nodes), path, memory, ledger)
    # sidecar names the file only when this build wrote it: a build from an older store state leaves a newer sidecar in
    # place (B100-04), and that file is not this build's
    LAST_BUILD.update(status="success" if out else "success_empty", cards=len(out),
                      sidecar=path if LAST_BUILD.get("published") else None,
                      reason=None if out else "no current card has a memory source with words to add")
    return out


def _build(store_path, current_nodes, path, memory=None, ledger=None) -> dict:
    memory = memory or MEMORY_DIR
    db = sqlite3.connect("file:%s?mode=ro" % str(store_path).replace("\\", "/"), uri=True)
    try:
        refs = db.execute("select claim_id, source_ref from claim_provenance where source_ref like 'memory:%'").fetchall()
    finally:
        db.close()
    current = {n.id: n for n in current_nodes}
    house = {}
    for cid, ref in refs:
        if cid in current and cid not in house:
            house[cid] = ref[len("memory:"):].split(";")[0].strip()
    if not house:
        return {}
    n_cards = len(current)
    df = collections.Counter()
    title_df = collections.Counter()
    for n in current_nodes:
        df.update(set(_terms((n.title or "") + " " + (n.body or ""))))
        if str(n.kind) == "claim" and n.id not in house:
            title_df.update(set(_terms(n.title)))
    vocab = {t for t, c in title_df.items() if c >= TITLE_MIN and not re.search(r"\d", t)}
    his, n_his, his_rejected = _his_counts(Path(str(store_path)).with_name("expansion_his_df.pkl"), prompts=ledger)
    if n_his:
        his_cap = max(3, HIS_SHARE * n_his)
        vocab = {t for t in vocab if his.get(t, 0) <= his_cap}
    stop = set(_terms(PROCESS)) | set(_terms(GENERIC))
    cap = MAX_DF_SHARE * n_cards
    out = {}
    for cid, slug in house.items():
        try:
            txt = (memory / (slug + ".md")).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parts = txt.split("---")
        body = "---".join(parts[2:]) if len(parts) > 2 else txt
        body = re.sub(r"\[\[[^\]]+\]\]", " ", body)
        node = current[cid]
        own = set(_terms((node.title or "") + " " + (node.body or "")))
        tf = collections.Counter(t for t in _terms(body) if t not in own and t not in stop and t in vocab)
        ranked = sorted(((c * math.log1p(n_cards / (1.0 + df.get(t, 0))), t) for t, c in tf.items()
                         if 2 <= df.get(t, 0) <= cap), reverse=True)
        pick = [t for _, t in ranked[:K]]
        if pick:
            out[cid] = " ".join(pick)
    LAST_BUILD["published"] = _publish(
        path, {"version": VERSION, "n": len(out), "k": K, "max_df_share": MAX_DF_SHARE, "his_share": HIS_SHARE,
               "his_prompts": n_his, "his_rejected": his_rejected, "generation": _generation(current_nodes),
               "expansion": out})
    return out
