#!/usr/bin/env python3
"""Tests for mesh_bridge.py - the file-drop door into the agent mesh.

Run from anywhere:  python -m pytest bridge/tests/test_mesh_bridge.py -q
Everything runs against a temporary database and temporary bridge folders;
the live mesh at ~/.agents/coordination is never touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BRIDGE_DIR = Path(__file__).resolve().parents[1]
MESH_DIR = BRIDGE_DIR.parent

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)
sys.path.insert(0, str(BRIDGE_DIR))
sys.path.insert(0, str(MESH_DIR))

import agent_mesh
import mesh_bridge
import os


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated db + bridge directories."""
    db = tmp_path / "mesh.sqlite3"
    for name in ("INBOX", "RECEIPTS", "PROCESSED", "OUTBOX"):
        monkeypatch.setattr(mesh_bridge, name, tmp_path / name.lower())
    return {"db": db, "tmp": tmp_path}


def drop(env, request_id, verb, args, extra=None):
    body = {"bridge_version": "1.0", "request_id": request_id, "verb": verb, "args": args}
    body.update(extra or {})
    mesh_bridge.INBOX.mkdir(parents=True, exist_ok=True)
    path = mesh_bridge.INBOX / (request_id + ".json")
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def apply_all(env):
    return mesh_bridge.apply_inbox(env["db"], 50, False)


def receipt(request_id):
    path = mesh_bridge.RECEIPTS / (request_id + ".json")
    assert path.exists(), "expected receipt for " + request_id
    return json.loads(path.read_text(encoding="utf-8"))


def native_mesh(env):
    return agent_mesh.Mesh(env["db"])


def register_native_codex(env, agent_id="codex:native1"):
    mesh = native_mesh(env)
    try:
        mesh.hello(agent_id, "codex", "native shell sibling", "C:\\", 3600, "active",
                   session="native-session-1")
    finally:
        mesh.close()
    return agent_id


def test_hello_send_export_roundtrip(env):
    codex = register_native_codex(env)
    drop(env, "r01-hello", "hello",
         {"agent": "claude-cowork", "runtime": "cowork", "ttl": "4h",
          "task": "file-only sibling; speaks through the bridge"})
    drop(env, "r02-send", "send",
         {"agent": "claude-cowork", "to": codex, "topic": "introduction",
          "kind": "request", "body": "hello from the file-only side"})
    drop(env, "r03-export", "inbox-export", {"agent": "claude-cowork"})
    assert apply_all(env) == 0

    assert receipt("r01-hello")["ok"] is True
    assert receipt("r02-send")["ok"] is True
    assert receipt("r03-export")["ok"] is True

    mesh = native_mesh(env)
    try:
        mail = mesh.inbox(codex)
        assert len(mail) == 1
        assert mail[0]["sender_agent"] == "claude-cowork"
        assert mail[0]["body"] == "hello from the file-only side"
        row = mesh.conn.execute(
            "SELECT runtime FROM agents WHERE agent_id='claude-cowork'").fetchone()
        assert row["runtime"] == "cowork"
    finally:
        mesh.close()

    exports = list(mesh_bridge.OUTBOX.glob("claude-cowork__*.json"))
    assert exports, "inbox-export must write an outbox json"
    payload = json.loads(exports[0].read_text(encoding="utf-8"))
    assert payload["agent"] == "claude-cowork"
    assert "digest" in payload
    # processed files moved out of the inbox
    assert not list(mesh_bridge.INBOX.glob("*.json"))


def test_reply_reaches_file_agent_via_export(env):
    codex = register_native_codex(env)
    drop(env, "r10-hello", "hello", {"agent": "claude-cowork", "runtime": "cowork"})
    assert apply_all(env) == 0
    mesh = native_mesh(env)
    try:
        mesh.send(codex, "claude-cowork", "re-introduction", "welcome, sibling", "answer")
    finally:
        mesh.close()
    drop(env, "r11-export", "inbox-export", {"agent": "claude-cowork"})
    assert apply_all(env) == 0
    exports = sorted(mesh_bridge.OUTBOX.glob("claude-cowork__*.json"))
    payload = json.loads(exports[-1].read_text(encoding="utf-8"))
    bodies = [m["body"] for m in payload["messages"]]
    assert "welcome, sibling" in bodies


def test_hello_rejects_shell_runtimes(env):
    drop(env, "r20-hello", "hello", {"agent": "sneaky", "runtime": "codex"})
    assert apply_all(env) == 0
    rec = receipt("r20-hello")
    assert rec["ok"] is False and rec["exit_code"] == 3


def test_cannot_speak_for_native_agent(env):
    codex = register_native_codex(env)
    drop(env, "r30-send", "send",
         {"agent": codex, "to": codex, "topic": "spoof", "body": "impersonation attempt"})
    assert apply_all(env) == 0
    rec = receipt("r30-send")
    assert rec["ok"] is False and rec["exit_code"] == 3
    mesh = native_mesh(env)
    try:
        assert mesh.inbox(codex) == []
    finally:
        mesh.close()


def test_live_id_cannot_be_rebound_by_bridge(env):
    """A live native id cannot be re-registered through the bridge even with a
    bridge runtime: the mesh's own session/runtime rebinding check refuses."""
    codex = register_native_codex(env)
    drop(env, "r35-hello", "hello", {"agent": codex, "runtime": "cowork"})
    assert apply_all(env) == 0
    rec = receipt("r35-hello")
    assert rec["ok"] is False and rec["exit_code"] == 3


def test_secret_bearing_request_quarantined(env):
    path = mesh_bridge.INBOX
    path.mkdir(parents=True, exist_ok=True)
    bad = path / "r40-secret.json"
    bad.write_text(json.dumps({
        "request_id": "r40-secret", "verb": "send",
        "args": {"agent": "claude-cowork", "to": "all", "topic": "oops",
                 "body": "token ghp_" + "a" * 30},
    }), encoding="utf-8")
    assert apply_all(env) == 0
    # refused before any parse-dependent handling; receipt records the refusal
    rec = receipt("r40-secret")
    assert rec["ok"] is False
    assert not bad.exists()


def test_unparseable_request_quarantined_not_looping(env):
    path = mesh_bridge.INBOX
    path.mkdir(parents=True, exist_ok=True)
    junk = path / "r50-junk.json"
    junk.write_text("{not json", encoding="utf-8")
    assert apply_all(env) == 0
    assert not junk.exists()
    assert (mesh_bridge.PROCESSED / "r50-junk.json.refused").exists()
    # second run: inbox empty, nothing loops
    assert apply_all(env) == 0


def test_duplicate_request_id_skipped(env):
    drop(env, "r60-hello", "hello", {"agent": "claude-cowork", "runtime": "cowork"})
    assert apply_all(env) == 0
    assert receipt("r60-hello")["ok"] is True
    # same id dropped again: must be skipped, not re-applied
    drop(env, "r60-hello", "hello", {"agent": "claude-cowork", "runtime": "cowork",
                                     "task": "replayed"})
    assert apply_all(env) == 0
    mesh = native_mesh(env)
    try:
        row = mesh.conn.execute(
            "SELECT task FROM agents WHERE agent_id='claude-cowork'").fetchone()
        assert row["task"] != "replayed"
    finally:
        mesh.close()


def test_verb_whitelist(env):
    drop(env, "r70-claim", "claim",
         {"agent": "claude-cowork", "resource": "C:\\anything", "mode": "write"})
    assert apply_all(env) == 0
    rec = receipt("r70-claim")
    assert rec["ok"] is False and "whitelist" in rec["error"]


def test_handoff_kind_not_sendable(env):
    drop(env, "r75-hello", "hello", {"agent": "claude-cowork", "runtime": "cowork"})
    drop(env, "r76-send", "send",
         {"agent": "claude-cowork", "to": "claude-cowork", "topic": "x",
          "kind": "handoff", "body": "{}"})
    assert apply_all(env) == 0
    rec = receipt("r76-send")
    assert rec["ok"] is False


def test_ttl_capped(env):
    drop(env, "r80-hello", "hello",
         {"agent": "claude-cowork", "runtime": "cowork", "ttl": "48h"})
    assert apply_all(env) == 0
    rec = receipt("r80-hello")
    assert rec["ok"] is False and "capped" in rec["error"]


def test_oversized_request_refused(env):
    path = mesh_bridge.INBOX
    path.mkdir(parents=True, exist_ok=True)
    big = path / "r90-big.json"
    big.write_text(json.dumps({
        "request_id": "r90-big", "verb": "send",
        "args": {"agent": "claude-cowork", "to": "all", "topic": "big",
                 "body": "x" * 70000},
    }), encoding="utf-8")
    assert apply_all(env) == 0
    rec = receipt("r90-big")
    assert rec["ok"] is False and "exceeds" in rec["error"]


def test_ack_roundtrip(env):
    codex = register_native_codex(env)
    drop(env, "ra0-hello", "hello", {"agent": "claude-cowork", "runtime": "cowork"})
    assert apply_all(env) == 0
    mesh = native_mesh(env)
    try:
        sent = mesh.send(codex, "claude-cowork", "ping", "please ack", "request")
    finally:
        mesh.close()
    drop(env, "ra1-ack", "ack",
         {"agent": "claude-cowork", "message_ids": [sent["message_id"]]})
    assert apply_all(env) == 0
    assert receipt("ra1-ack")["ok"] is True
    mesh = native_mesh(env)
    try:
        row = mesh.conn.execute("SELECT acked_at FROM messages WHERE message_id=?",
                                (sent["message_id"],)).fetchone()
        assert row["acked_at"] is not None
    finally:
        mesh.close()


def test_oversized_does_not_clobber_previous_receipt(env):
    """Regression for the 2026-07-11 gate rejection: the v1.0 error handler
    read `req` from the previous loop iteration, so an oversized file that
    failed before parsing (1) got no receipt of its own and (2) overwrote the
    PREVIOUS request's receipt with ok=False. Both must be fixed together."""
    drop(env, "rc0-hello", "hello", {"agent": "claude-cowork", "runtime": "cowork"})
    big = mesh_bridge.INBOX / "rc9-big.json"
    big.write_text(json.dumps({
        "request_id": "rc9-big", "verb": "send",
        "args": {"agent": "claude-cowork", "to": "all", "topic": "big",
                 "body": "x" * 70000},
    }), encoding="utf-8")
    assert apply_all(env) == 0
    # the earlier, valid request keeps its ok=True receipt untouched
    good = receipt("rc0-hello")
    assert good["ok"] is True and good["request_id"] == "rc0-hello"
    # the oversized request gets its OWN refusal receipt
    rec = receipt("rc9-big")
    assert rec["ok"] is False and "exceeds" in rec["error"]
    assert not big.exists()


def test_oversized_without_visible_id_quarantined(env):
    """If the request_id is not inside the first 4 KiB of an oversized file,
    the bounded-prefix hint finds nothing: quarantine receiptless, never parse."""
    mesh_bridge.INBOX.mkdir(parents=True, exist_ok=True)
    big = mesh_bridge.INBOX / "rd0-buried.json"
    # keys in insertion order: the giant body precedes the id, pushing it
    # far past the 4 KiB prefix window
    big.write_text(json.dumps({
        "verb": "send",
        "args": {"agent": "claude-cowork", "to": "all", "topic": "big",
                 "body": "x" * 70000},
        "request_id": "rd0-buried",
    }), encoding="utf-8")
    assert apply_all(env) == 0
    assert not big.exists()
    assert (mesh_bridge.PROCESSED / "rd0-buried.json.refused").exists()
    assert not (mesh_bridge.RECEIPTS / "rd0-buried.json").exists()


def test_symlinked_request_refused_unread(env, tmp_path):
    """Containment: a symlink in the inbox must be quarantined without its
    target ever being read, even when the target is a perfectly valid request."""
    target = tmp_path / "outside-the-inbox.json"
    target.write_text(json.dumps({
        "request_id": "re0-viasymlink", "verb": "hello",
        "args": {"agent": "claude-cowork", "runtime": "cowork"},
    }), encoding="utf-8")
    mesh_bridge.INBOX.mkdir(parents=True, exist_ok=True)
    link = mesh_bridge.INBOX / "re0-link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks on this platform/user")
    assert apply_all(env) == 0
    assert not link.exists()
    assert (mesh_bridge.PROCESSED / "re0-link.json.refused").exists()
    # the valid payload behind the link was never applied or receipted
    assert not (mesh_bridge.RECEIPTS / "re0-viasymlink.json").exists()
    mesh = native_mesh(env)
    try:
        row = mesh.conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id='claude-cowork'").fetchone()
        assert row is None
    finally:
        mesh.close()


def test_export_json_carries_untrusted_warning(env):
    """Raw JSON exports must carry the same untrusted-origin warning the .md
    rendering has, so a consumer of either format sees the caveat."""
    drop(env, "rf0-hello", "hello", {"agent": "claude-cowork", "runtime": "cowork"})
    drop(env, "rf1-export", "inbox-export", {"agent": "claude-cowork"})
    assert apply_all(env) == 0
    exports = sorted(mesh_bridge.OUTBOX.glob("claude-cowork__*.json"))
    payload = json.loads(exports[-1].read_text(encoding="utf-8"))
    assert "untrusted" in payload.get("warning", "")


def test_refusal_replay_does_not_rewrite_receipt(env):
    """A replayed bad request with an already-receipted id is quarantined
    without rewriting the original receipt (receipts are write-once)."""
    drop(env, "rg0-hello", "hello", {"agent": "sneaky", "runtime": "codex"})
    assert apply_all(env) == 0
    first = receipt("rg0-hello")
    assert first["ok"] is False
    drop(env, "rg0-hello", "hello", {"agent": "sneaky", "runtime": "codex"})
    assert apply_all(env) == 0
    second = receipt("rg0-hello")
    assert second["receipt_written_at"] == first["receipt_written_at"]


def test_dry_run_touches_nothing(env):
    drop(env, "rb0-hello", "hello", {"agent": "claude-cowork", "runtime": "cowork"})
    assert mesh_bridge.apply_inbox(env["db"], 50, True) == 0
    assert list(mesh_bridge.INBOX.glob("*.json"))       # still pending
    assert not (mesh_bridge.RECEIPTS / "rb0-hello.json").exists()
