#!/usr/bin/env python3
"""triage.py - read-only report of the mesh mail backlog (why comms feel stuck).

Reads the mesh sqlite through a mode=ro URI (never writes). Splits unacked mail by kind, by
recipient liveness (LIVE agents who still owe an ack vs DEAD-LETTER mail addressed to
offline/expired identities that can never ack it), per-live-agent delinquency, top unacked
senders (broadcast pressure), and delivered-but-unacked vs never-delivered. The actionable
backlog is the live-agent slice; dead letters are janitorial and only a sweep clears them.

  python triage.py            # human report
  python triage.py --json     # machine JSON (a contract the owner console can consume)
"""
import os, sys, json, sqlite3, datetime

DB = os.path.join(os.path.expanduser("~"), ".agents", "coordination", "agent_mesh.sqlite3")


def _conn():
    uri = "file:" + DB.replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect():
    now = _now_iso()
    c = _conn()
    c.row_factory = sqlite3.Row
    live = set(r[0] for r in c.execute(
        "SELECT agent_id FROM agents WHERE status NOT IN ('done','offline') AND lease_until > ?", (now,)))
    known = set(r[0] for r in c.execute("SELECT agent_id FROM agents"))
    rows = c.execute("SELECT sender_agent,recipient,kind,created_at,delivered_at,acked_at "
                     "FROM messages").fetchall()
    c.close()
    total = len(rows)
    unacked = [r for r in rows if not r["acked_at"]]
    by_kind, per_live, per_sender = {}, {}, {}
    live_n = dead_n = never_delivered = 0
    for r in unacked:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        if not r["delivered_at"]:
            never_delivered += 1
        per_sender[r["sender_agent"]] = per_sender.get(r["sender_agent"], 0) + 1
        if r["recipient"] in live:
            live_n += 1
            d = per_live.setdefault(r["recipient"], {"count": 0, "oldest": r["created_at"]})
            d["count"] += 1
            if r["created_at"] < d["oldest"]:
                d["oldest"] = r["created_at"]
        else:
            dead_n += 1
    return {
        "generated": now, "total_messages": total, "unacked": len(unacked),
        "acked": total - len(unacked), "live_agents": len(live), "known_identities": len(known),
        "unacked_by_kind": dict(sorted(by_kind.items(), key=lambda x: -x[1])),
        "live_unacked": live_n, "dead_letter_unacked": dead_n,
        "never_delivered": never_delivered, "delivered_unacked": len(unacked) - never_delivered,
        "per_live_agent": dict(sorted(per_live.items(), key=lambda x: -x[1]["count"])),
        "top_unacked_senders": dict(sorted(per_sender.items(), key=lambda x: -x[1])[:8]),
    }


def report(d):
    L = ["MESH MAIL BACKLOG  (generated %s)" % d["generated"],
         "  total=%d  acked=%d  unacked=%d  (ack rate %.0f%%)" %
         (d["total_messages"], d["acked"], d["unacked"],
          100.0 * d["acked"] / max(1, d["total_messages"])),
         "  live agents=%d  known identities=%d" % (d["live_agents"], d["known_identities"]),
         "  unacked split: LIVE-agent (actionable)=%d  DEAD-letter (unackable)=%d" %
         (d["live_unacked"], d["dead_letter_unacked"]),
         "  delivery: never-delivered=%d  delivered-but-unacked=%d" %
         (d["never_delivered"], d["delivered_unacked"]),
         "  unacked by kind: " + ", ".join("%s=%d" % kv for kv in d["unacked_by_kind"].items()),
         "\n  live agents who owe acks (the REAL backlog):"]
    if d["per_live_agent"]:
        for a, v in d["per_live_agent"].items():
            L.append("    %-42s %4d unread  (oldest %s)" % (a[:42], v["count"], v["oldest"]))
    else:
        L.append("    (none - every live agent is caught up)")
    L.append("\n  top unacked senders (broadcast pressure):")
    for a, n in d["top_unacked_senders"].items():
        L.append("    %-42s %4d" % (a[:42], n))
    return "\n".join(L)


def main():
    d = collect()
    print(json.dumps(d, indent=2) if "--json" in sys.argv else report(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
