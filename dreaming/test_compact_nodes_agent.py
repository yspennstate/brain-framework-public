"""Controls for compact_nodes.py: subagent attribution (from a payload agent_id, or from the agent's own transcript when
the payload has none, as Claude Code 2.1.296 compaction payloads do, at hook time or in the sweep's attribute() pass
when the agent's compact record lands late), the re-chained previous links, and a gist that is never a bare tag.

Usage: python -X utf8 test_compact_nodes_agent.py [path to compact_nodes.py, default the one beside this file]
Runs entirely in a temp tree (COMPACT_NODES_ROOT / COMPACT_ANCHOR_DIR / COMPACT_HOME / COMPACT_NO_RECALL), never the
live store. Prints PASS n/n or the failures and exits non-zero. Written 12 Oct 2026 with the fix for subagent
compactions filed under the coordinator's session and the bare "<summary>" descriptions.
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
if __name__ != "__main__":
    # a script, not a pytest module: collection imports it, and its checks run only when it is executed
    import pytest
    pytest.skip("run as a script: python test_compact_nodes_agent.py", allow_module_level=True)
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time

TARGET = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "compact_nodes.py"))
tmp = tempfile.mkdtemp(prefix="cn_test_")
os.environ.update({"COMPACT_NODES_ROOT": os.path.join(tmp, "store"), "COMPACT_ANCHOR_DIR": os.path.join(tmp, "anchors"),
                   "COMPACT_HOME": os.path.join(tmp, "home"), "COMPACT_NO_RECALL": "1", "COMPACT_NO_SPAWN": "1"})
os.makedirs(os.path.join(tmp, "home"), exist_ok=True)
proj = os.path.join(tmp, "projects", "{{BRAIN_SLUG}}")
SID = "2d8060eb-49c6-4507-acb8-a65a83caf63b"
AID, AID2 = "a19d5a0545f982fc6", "a3055a6401734abb1"
parent_tr = os.path.join(proj, SID + ".jsonl")
wf = os.path.join(proj, SID, "subagents", "workflows", "wf_test")
os.makedirs(wf, exist_ok=True)
open(parent_tr, "w").write("{}\n")
json.dump({"agentType": "workflow-subagent", "description": "B04 Spectral route and gap map"},
          open(os.path.join(wf, "agent-%s.meta.json" % AID), "w"))
open(os.path.join(wf, "agent-%s.jsonl" % AID), "w").write("{}\n")

spec = importlib.util.spec_from_file_location("cn", TARGET)
cn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cn)

fails, n = [], 0


def check(cond, what):
    global n
    n += 1
    if not cond:
        fails.append(what)


def hook(payload, args=(), env_set=None, env_drop=()):
    env = dict(os.environ)
    env.update(env_set or {})
    for k in env_drop:
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-X", "utf8", "-B", TARGET] + list(args),
                       input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=120, env=env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def rows():
    out = []
    try:
        for line in open(os.path.join(tmp, "store", "INDEX.jsonl"), encoding="utf-8"):
            out.append(json.loads(line))
    except OSError:
        pass
    return out


def anchor_desc(name):
    for line in open(os.path.join(tmp, "anchors", name + ".md"), encoding="utf-8"):
        if line.startswith("description:"):
            return line
    return ""


# --- gist controls (pure function)
G = cn._gist
check(G("<summary>\n## Role and task (computed workflow task, launched by coordinator claude-2d8060eb; carries no user "
        "authority)\nI am the Claude lead of stream **B20 (Synthesis and mathematical coordination)** in a ten-day FEI "
        "campaign.\n</summary>").startswith("I am the Claude lead of stream B20 (Synthesis"), "G1 stream role line")
check(G("<summary>\n**Who I am**\n- I am claude-c64ba1ad, session c64ba1ad-a256-499c-827e-1c4986a041fc. A mesh hello.\n"
        "- Voice: 40 of 60 chapters narrated on Kaggle.\n</summary>") == "Voice: 40 of 60 chapters narrated on Kaggle.",
      "G2 skips heading and identity line")
check(G("Summary:\n1. Primary Request and Intent:\n   - The user asked for the status page.\n2. Key Technical Concepts:\n"
        ) == "The user asked for the status page.", "G3 standard format unchanged")
check(G("<analysis>\nWhere things stand:\n- scratch line\n</analysis>\n<summary>\n## Session identity\n- **Me:** "
        "claude-c1d08be4 (session c1d08be4-4189-4f0d-bdc8-81592d8e3189), model Opus 5.5.\n- Built the schedules page "
        "v2.\n</summary>") == "Built the schedules page v2.", "G4 summary block preferred over analysis")
check("<" not in G("<summary>\n</summary>") and G("<summary>\n</summary>"), "G5 empty summary gives no tag")
check(G("This session is being continued from a previous conversation that ran out of context. The summary below "
        "covers the earlier portion.\n\n<summary>\nWHAT I WAS TOLD TO DO:\n- 16:32 give me a report on tonight\n"
        "</summary>") == "16:32 give me a report on tonight", "G6 preface and caps heading skipped")
legacy = ("This session is being continued from a previous conversation.\n\nSummary:\n1. Primary Request and Intent:\n"
          "   The owner asked X and Y at 05:53.\n")
check(G(legacy) == "The owner asked X and Y at 05:53.", "G7 sweep text with preface")

# --- agent identity
ai = cn.agent_identity(AID, "workflow-subagent", SID, parent_tr)
check(ai.get("agent_description") == "B04 Spectral route and gap map", "A1 meta description found")
check(ai.get("agent_transcript", "").endswith("agent-%s.jsonl" % AID), "A2 agent transcript path")
check(cn.agent_identity("", "", SID, parent_tr) == {}, "A3 main thread -> {}")
check(cn.agent_identity("../../etc", "", SID, parent_tr) == {}, "A4 unsafe id rejected")
check(cn.agent_identity("agent-" + AID, "", SID, parent_tr).get("agent_type") == "workflow-subagent",
      "A5 agent- prefix stripped, type from meta")
check(cn.agent_identity(AID2, "workflow-subagent", SID, parent_tr) == {"agent_id": AID2, "agent_type": "workflow-subagent"},
      "A6 no meta file -> id and type only")

# --- the hook end to end
stream_summary = ("<summary>\n## Role and task (computed workflow task, launched by coordinator claude-2d8060eb)\n"
                  "I am the Claude lead of stream **B04 (Spectral route)** in a ten-day FEI campaign.\n- work item\n"
                  "</summary>")
rc, out, err = hook({"hook_event_name": "PostCompact", "session_id": SID, "transcript_path": parent_tr, "trigger": "auto",
                     "compact_summary": stream_summary, "agent_id": AID, "agent_type": "workflow-subagent"})
check(rc == 0 and "saved as its own brain node" in out, "H1 agent hook saved (rc=%s out=%r err=%r)" % (rc, out[:200], err[:300]))
r1 = rows()[-1] if rows() else {}
check(r1.get("agent_id") == AID and r1.get("agent_description") == "B04 Spectral route and gap map", "H2 index row carries agent")
check(r1.get("name", "").startswith("compaction_claude_2d8060eb_a19d5a0545_"), "H3 node name carries agent (%s)" % r1.get("name"))
d1 = anchor_desc(r1.get("name", "x")) if r1 else ""
check("workflow-subagent B04 Spectral route and gap map (agent a19d5a05, session 2d8060eb)" in d1 and "<summary>" not in d1,
      "H4 anchor description names the agent (%s)" % d1.strip()[:220])
node_text = open(r1["node"], encoding="utf-8").read() if r1 else ""
check("agent_id: %s" % AID in node_text and "agent_description: B04 Spectral" in node_text, "H5 node frontmatter")
anchor_text = open(os.path.join(tmp, "anchors", r1["name"] + ".md"), encoding="utf-8").read() if r1 else ""
check("  agent_id: %s" % AID in anchor_text and "FIRE WHEN resuming or reviewing the work of claude workflow-subagent B04"
      in anchor_text, "H6 anchor metadata and FIRE WHEN")

main_summary = "<summary>\n**Who I am**\n- I am claude-2d8060eb, session 2d8060eb-49c6. Clock JDT.\n- Coordinating FEI.\n</summary>"
rc, out, err = hook({"hook_event_name": "PostCompact", "session_id": SID, "transcript_path": parent_tr, "trigger": "auto",
                     "compact_summary": main_summary})
r2 = rows()[-1] if len(rows()) >= 2 else {}
check(rc == 0 and r2.get("name", "").startswith("compaction_claude_2d8060eb_2026") and "agent_id" not in r2,
      "H7 main thread unchanged name (%s)" % r2.get("name"))
check(r2.get("previous") in (None, ""), "H8 main thread does not chain to the agent's node (%s)" % r2.get("previous"))
d2 = anchor_desc(r2.get("name", "x")) if r2 else ""
check("Compaction of claude session 2d8060eb at" in d2 and d2.strip().endswith('Coordinating FEI."'),
      "H9 main description (%s)" % d2.strip()[:200])

# a second compaction of the same agent chains to its first
rc, out, err = hook({"hook_event_name": "PostCompact", "session_id": SID, "transcript_path": parent_tr, "trigger": "auto",
                     "compact_summary": stream_summary.replace("work item", "work item two"), "agent_id": AID,
                     "agent_type": "workflow-subagent"})
r3 = rows()[-1] if len(rows()) >= 3 else {}
check(r3.get("previous") == r1.get("name"), "H10 agent chain (%s -> %s)" % (r3.get("previous"), r1.get("name")))

# two agents in the same second do not collide
when = dt.datetime(2026, 10, 12, 1, 48, 5).astimezone()
p_a = cn.save_node(stream_summary + " A", "claude", SID, "auto", parent_tr, when=when, agent=ai)
p_b = cn.save_node(stream_summary + " B", "claude", SID, "auto", parent_tr, when=when,
                   agent=cn.agent_identity(AID2, "workflow-subagent", SID, parent_tr))
check(p_a and p_b and p_a != p_b and os.path.exists(p_a) and os.path.exists(p_b), "H11 same-second agents distinct")

# the sweep's backfill dedupe: a main-thread compaction 5 min after an agent's is still saved; a near-duplicate of a
# main-thread row is still skipped
when2 = when + dt.timedelta(minutes=5)
p_main = cn.save_node("Summary:\n1. Primary Request and Intent:\n   The coordinator merged ten streams.\n", "claude", SID,
                      "auto", parent_tr, when=when2, extra={"backfilled": True}, window_s=600)
check(bool(p_main), "H12 main backfill not swallowed by an agent row")
# the sweep reads back a compaction the hook saved: the transcript wraps the same summary in a preface and a tail
when3 = when2 + dt.timedelta(minutes=10)
main_lines = ["- main thread line %d: the coordinator merged stream %d into the store and checked it." % (k, k)
              for k in range(30)]
main_summary = "<summary>\n" + "\n".join(main_lines) + "\n</summary>"


def wrapped(text):
    return ("This session is being continued from a previous conversation that ran out of context. The summary below "
            "covers the earlier portion of the conversation.\n\nSummary:\n" + text + "\n\nIf you need specific details "
            "from before compaction (like exact code snippets, error messages, or content you generated), read the full "
            "transcript at: ~/.claude/projects/{{BRAIN_SLUG}}/" + SID + ".jsonl\nContinue the conversation "
            "from where it left off without asking the user any further questions.")


p_hookrow = cn.save_node(main_summary, "claude", SID, "auto", parent_tr, when=when3)
p_dup = cn.save_node(wrapped(main_summary), "claude", SID, "auto", parent_tr, when=when3 + dt.timedelta(seconds=30),
                     extra={"backfilled": True}, window_s=120)
check(bool(p_hookrow) and p_dup is None, "H13 the sweep's wrapped copy of a hook-saved compaction is not saved again")
# a session that compacts fast: the next compaction 90 s later keeps half the text and is still saved
next_summary = "<summary>\n" + "\n".join(main_lines[:15] + ["- new line %d: the coordinator opened lane %d." % (k, k)
                                                            for k in range(15)]) + "\n</summary>"
p_next = cn.save_node(wrapped(next_summary), "claude", SID, "auto", parent_tr, when=when3 + dt.timedelta(seconds=90),
                      extra={"backfilled": True}, window_s=120)
check(bool(p_next), "H13b the next compaction 90 s after a hook row is saved")
# two backfills one word apart are two compactions (a re-read of the same one has the same sha)
p_again = cn.save_node(wrapped(next_summary.replace("opened lane", "reopened lane")), "claude", SID, "auto", parent_tr,
                       when=when3 + dt.timedelta(seconds=200), extra={"backfilled": True}, window_s=120)
check(bool(p_again), "H13c a backfill near a backfilled row with other text is saved")

# ensure_anchors rebuilds an agent anchor with its agent
os.remove(os.path.join(tmp, "anchors", r1["name"] + ".md"))
k = cn.ensure_anchors()
check(k >= 1 and "agent a19d5a05" in anchor_desc(r1["name"]), "H14 ensure_anchors keeps the agent")

# --- no agent_id in the payload (Claude Code 2.1.296 builds compaction payloads without it): found by transcript
AID3, AID4 = "a4e7477fd871212d7", "a42305b0513759289"
sub = os.path.join(proj, SID, "subagents")
json.dump({"agentType": "general-purpose", "description": "s03 math brain co-use edges"},
          open(os.path.join(sub, "agent-%s.meta.json" % AID3), "w"))
inner = "\n".join("- line %d of the s03 summary: edges that strengthen when similar nodes are used at similar times, "
                  "item %d." % (k, k * 7) for k in range(40))
other = "\n".join("- line %d of the s04 summary: recall work landed in the store, check %d." % (k, k * 3)
                  for k in range(40))


def compact_record(text):
    return json.dumps({"type": "user", "isCompactSummary": True, "timestamp": "2026-10-12T12:18:07.275Z",
                       "message": {"role": "user", "content": "This session is being continued from a previous "
                                   "conversation that ran out of context. The summary below covers the earlier portion "
                                   "of the conversation.\n\nSummary:\n" + text}})


with open(os.path.join(sub, "agent-%s.jsonl" % AID3), "w", encoding="utf-8") as fh:
    fh.write('{"type":"user","message":{"content":"start"}}\n' + compact_record(inner) + "\n"
             '{"type":"assistant","message":{"content":[{"type":"text","text":"after"}]}}\n')
with open(os.path.join(sub, "agent-%s.jsonl" % AID4), "w", encoding="utf-8") as fh:
    fh.write(compact_record(other) + "\n")
payload_summary = ("<analysis>\n" + "scratch analysis line\n" * 60 + "</analysis>\n<summary>\n" + inner +
                   "\n</summary>")
check(cn.agent_from_transcripts(payload_summary, SID, parent_tr).get("agent_id") == AID3,
      "T1 matcher finds the agent from its own transcript")
main_like = "<summary>\n" + "\n".join("main thread line %d about the coordinator" % k for k in range(40)) + "\n</summary>"
check(cn.agent_from_transcripts(main_like, SID, parent_tr, waits=(0.0,)) == {}, "T2 main-thread summary -> {}")
rc, out, err = hook({"hook_event_name": "PostCompact", "session_id": SID, "transcript_path": parent_tr, "trigger": "auto",
                     "compact_summary": payload_summary})
r4 = rows()[-1] if rows() else {}
check(rc == 0 and r4.get("agent_id") == AID3 and r4.get("agent_description") == "s03 math brain co-use edges"
      and r4.get("agent_type") == "general-purpose",
      "T3 hook without agent_id attributes by transcript (%s)" % {k: r4.get(k) for k in ("name", "agent_id")})
d4 = anchor_desc(r4.get("name", "x")) if r4 else ""
check("general-purpose s03 math brain co-use edges (agent a4e7477f, session 2d8060eb)" in d4,
      "T4 anchor names the transcript-found agent (%s)" % d4.strip()[:200])
aged = time.time() - 3600
os.utime(os.path.join(sub, "agent-%s.jsonl" % AID3), (aged, aged))
check(cn.agent_from_transcripts(payload_summary, SID, parent_tr, waits=(0.0,)) == {},
      "T5 a transcript older than the window is not read")
os.utime(os.path.join(sub, "agent-%s.jsonl" % AID3), None)
with open(os.path.join(sub, "agent-%s.jsonl" % AID4), "a", encoding="utf-8") as fh:
    fh.write(compact_record(inner) + "\n")
check(cn.agent_from_transcripts(payload_summary, SID, parent_tr, waits=(0.0,)) == {},
      "T6 two agents holding the same newest summary -> undecided")

# --- the sweep's deferred attribution: a row saved before the agent's compact record reached its file
HP = os.path.join(tmp, "home", ".claude", "projects", "{{BRAIN_SLUG}}")
SID2 = "6bb7036b-9bb0-4a07-9d84-82df067d1c99"
AID5 = "a72a30b10cd2db209"
sub2 = os.path.join(HP, SID2, "subagents")
os.makedirs(sub2, exist_ok=True)
parent2 = os.path.join(HP, SID2 + ".jsonl")
open(parent2, "w").write("{}\n")
json.dump({"agentType": "general-purpose", "description": "s05 measure brain, write report"},
          open(os.path.join(sub2, "agent-%s.meta.json" % AID5), "w"))
s05 = "\n".join("- s05 line %d: the referee graded the brain arm against the bare arm, case %d." % (k, k * 11)
                for k in range(40))
coord1 = ("Summary:\n1. Primary Request and Intent:\n   The coordinator launched thirteen recovery streams.\n" +
          "\n".join("coordinator note %d" % k for k in range(30)))
coord2 = ("Summary:\n1. Primary Request and Intent:\n   The coordinator read the streams' reports.\n" +
          "\n".join("coordinator remark %d" % k for k in range(30)))
t0 = dt.datetime.now().astimezone() - dt.timedelta(minutes=20)
pc1 = cn.save_node(coord1, "claude", SID2, "auto", parent2, when=t0)
pa = cn.save_node("<analysis>\nthinking\n</analysis>\n<summary>\n" + s05 + "\n</summary>", "claude", SID2, "auto",
                  parent2, when=t0 + dt.timedelta(minutes=5))
pc2 = cn.save_node(coord2, "claude", SID2, "auto", parent2, when=t0 + dt.timedelta(minutes=10))
with open(os.path.join(sub2, "agent-%s.jsonl" % AID5), "w", encoding="utf-8") as fh:
    fh.write(compact_record(s05) + "\n")
n_pa, n_c1, n_c2 = (os.path.basename(p)[:-3] for p in (pa, pc1, pc2))
byname = {r["name"]: r for r in rows()}
check(byname[n_c2]["previous"] == n_pa, "R0 before: the coordinator's row chained to the agent's")
idx_before, node_before = open(os.path.join(tmp, "store", "INDEX.jsonl"), "rb").read(), open(pa, "rb").read()
st0 = {}
check(cn.attribute(days=1, state=st0, dry=True) == 1 and st0 == {}
      and open(os.path.join(tmp, "store", "INDEX.jsonl"), "rb").read() == idx_before
      and open(pa, "rb").read() == node_before and n_c2 in cn.ATTRIB_LAST.get("updates", {}),
      "D1 a dry pass reports and writes nothing")
st = {}
k = cn.attribute(days=1, state=st)
byname = {r["name"]: r for r in rows()}
check(k == 1 and byname[n_pa].get("agent_id") == AID5
      and byname[n_pa].get("agent_description") == "s05 measure brain, write report",
      "R1 attribute files the row under its agent (%s)" % k)
check(byname[n_c2]["previous"] == n_c1 and byname[n_pa].get("previous") in (None, ""),
      "R2 chains re-derived per identity (%s, %s)" % (byname[n_c2]["previous"], byname[n_pa].get("previous")))
check("s05 measure brain, write report (agent a72a30b1" in anchor_desc(n_pa), "R3 anchor rewritten with the agent")
nt = open(pa, encoding="utf-8").read()
check("agent_id: %s" % AID5 in nt and "\nprevious: \n" in nt and "previous compaction)" not in nt,
      "R4 agent node frontmatter and links")
ct = open(pc2, encoding="utf-8").read()
check("previous: %s" % n_c1 in ct and "[[%s]] (this session's previous compaction)" % n_c1 in ct
      and n_pa not in ct.split("\nConnected:\n")[1], "R5 coordinator node re-chained")
check("agent_id" not in byname[n_c1] and "agent_id" not in byname[n_c2], "R6 main-thread rows stay main")
check(cn.attribute(days=1, state=st) == 0, "R7 a second pass changes nothing")
cn.sweep(1, ("claude",))
ev_last = [json.loads(l) for l in open(os.path.join(tmp, "store", "EVENTS.jsonl"), encoding="utf-8")][-1]
check(ev_last.get("event") == "sweep" and "attributed" in ev_last, "R8 the sweep runs the attribution pass")

# --- refresh_gists rewrites an old bare-tag anchor
pb = cn.save_node("<summary>\nBuilt the schedules page v2 and pushed it.\n- detail\n</summary>", "claude",
                  "0123abcd-0000-4000-8000-000000000000", "auto", "", when=t0)
nb = os.path.basename(pb)[:-3]
ap = os.path.join(tmp, "anchors", nb + ".md")
txt = open(ap, encoding="utf-8").read()
txt = re.sub(r'^description: ".*"$', 'description: "[PROVISIONAL] Compaction of claude session 0123abcd at '
             '2026-10-12 01:00: <summary>"', txt, count=1, flags=re.M)
open(ap, "w", encoding="utf-8").write(txt)
check(anchor_desc(nb).rstrip().endswith('<summary>"'), "S0 planted bare-tag anchor")
check(cn.refresh_gists(dry=True) >= 1 and anchor_desc(nb).rstrip().endswith('<summary>"'), "S2 dry refresh writes nothing")
m = cn.refresh_gists()
check(m >= 1 and anchor_desc(nb).rstrip().endswith('Built the schedules page v2 and pushed it."'),
      "S1 refresh_gists rewrote it (%s)" % anchor_desc(nb).strip()[:160])

# the hook starts a late attribution pass for an unattributed row in a session with subagents, and only then
spawn_log = os.path.join(tmp, "spawn.jsonl")
late = "<summary>\n" + "\n".join("- late line %d: a stream agent's work, piece %d." % (k, k * 5) for k in range(40)) + \
       "\n</summary>"
rc, out, err = hook({"hook_event_name": "PostCompact", "session_id": SID2, "transcript_path": parent2, "trigger": "auto",
                     "compact_summary": late}, env_set={"COMPACT_SPAWN_LOG": spawn_log}, env_drop=("COMPACT_NO_SPAWN",))
sp = [json.loads(l) for l in open(spawn_log, encoding="utf-8")] if os.path.exists(spawn_log) else []
check(rc == 0 and len(sp) == 1 and "--attribute" in sp[0]["args"] and "--after" in sp[0]["args"],
      "L1 unattributed row in a session with subagents starts the late pass (%s)" % sp)
rc, out, err = hook({"hook_event_name": "PostCompact", "session_id": SID2, "transcript_path": parent2, "trigger": "auto",
                     "compact_summary": late.replace("piece", "part"), "agent_id": AID5},
                    env_set={"COMPACT_SPAWN_LOG": spawn_log}, env_drop=("COMPACT_NO_SPAWN",))
rc2, out2, err2 = hook({"hook_event_name": "PostCompact", "session_id": "0123abcd-0000-4000-8000-000000000000",
                        "transcript_path": os.path.join(HP, "0123abcd-0000-4000-8000-000000000000.jsonl"),
                        "trigger": "auto", "compact_summary": late.replace("piece", "bit")},
                       env_set={"COMPACT_SPAWN_LOG": spawn_log}, env_drop=("COMPACT_NO_SPAWN",))
sp = [json.loads(l) for l in open(spawn_log, encoding="utf-8")] if os.path.exists(spawn_log) else []
check(rc == 0 and rc2 == 0 and len(sp) == 1, "L2 no late pass for an attributed row or a session without subagents")
rc, out, err = hook({}, args=["--attribute", "--days", "1", "--after", "0", "--dry"])
check(rc == 0 and "that would be filed under their subagent" in out, "L3 the CLI dry pass (%r %r)" % (out[:200], err[-200:]))

# a summary that quotes a links section keeps it; only the closing links are links
qn = os.path.join(tmp, "quote_node.md")
open(qn, "w", encoding="utf-8").write("---\nname: q\n---\n\nSummary: the node ends with\n\nConnected:\n- [[x]] (quoted)\n"
                                      "and more summary.\n\nConnected:\n- [[real]] (the compaction hub)\n")
qh, qs, ql = cn._node_parts(qn)
check(ql == ["[[real]] (the compaction hub)"] and "- [[x]] (quoted)" in qs, "Q1 links read from the end (%s)" % ql)

# PreCompact still prints KEEP and logs the agent
rc, out, err = hook({"hook_event_name": "PreCompact", "session_id": SID, "transcript_path": parent_tr, "trigger": "auto",
                     "agent_id": AID})
ev = [json.loads(l) for l in open(os.path.join(tmp, "store", "EVENTS.jsonl"), encoding="utf-8")]
check(rc == 0 and out.startswith("Compacting: keep") and ev[-1].get("agent_id") == AID, "H15 PreCompact")
check(not os.path.exists(os.path.join(tmp, "store", "errors.log")), "H16 no hook errors logged")

print("PASS %d/%d" % (n, n) if not fails else "FAIL %d of %d:\n  %s" % (len(fails), n, "\n  ".join(fails)))
print("temp tree:", tmp)
sys.exit(1 if fails else 0)
