"""Preserve TOML while granting/removing only the selected profile's private Brain state root."""
from __future__ import annotations

import copy
import json
import re
import stat
import tomllib

import connector as c


def array_end(text, start):
    depth, quote, triple, escaped, comment = 0, None, False, False, False
    i = start
    while i < len(text):
        ch = text[i]
        if comment:
            if ch == "\n":
                comment = False
        elif quote:
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif triple and text.startswith(quote * 3, i):
                quote, triple = None, False
                i += 2
            elif not triple and ch == quote:
                quote = None
        elif ch == "#":
            comment = True
        elif ch in "\"'":
            quote = ch
            triple = text.startswith(ch * 3, i)
            if triple:
                i += 2
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise c.ConnectorError("Unterminated writable_roots array; keep existing configuration")


def edit_roots(raw, roots):
    text = raw.decode("utf-8")
    before = tomllib.loads(text)
    expected = copy.deepcopy(before)
    expected.setdefault("sandbox_workspace_write", {})["writable_roots"] = roots
    value = json.dumps(roots, ensure_ascii=False)
    table = before.get("sandbox_workspace_write")
    if table is None:
        updated = text.rstrip("\n") + '\n\n[sandbox_workspace_write]\nwritable_roots = ' + value + "\n"
    else:
        header = re.compile(r'''(?m)^\s*\[\s*(?:sandbox_workspace_write|"sandbox_workspace_write"|'sandbox_workspace_write')\s*\][ \t]*(?:\#[^\n]*)?\n''')
        headers = list(header.finditer(text))
        if len(headers) != 1:
            raise c.ConnectorError("Nonstandard/ambiguous sandbox table needs a source-vetted TOML edit; existing bytes preserved")
        start = headers[0].end()
        next_table = re.search(r"(?m)^[ \t]*\[", text[start:])
        end = start + next_table.start() if next_table else len(text)
        section = text[start:end]
        fields = list(re.finditer(r'''(?m)^[ \t]*(?:writable_roots|"writable_roots"|'writable_roots')[ \t]*=[ \t]*''', section))
        if not fields and "writable_roots" not in table:
            updated = text[:start] + "writable_roots = " + value + "\n" + text[start:]
        elif len(fields) == 1 and isinstance(table.get("writable_roots"), list):
            a = start + fields[0].end()
            # TOML permits a newline between '=' and the opening bracket.
            a += len(text[a:]) - len(text[a:].lstrip())
            if text[a:a + 1] != "[":
                raise c.ConnectorError("Expected a TOML writable_roots array")
            b = array_end(text, a)
            updated = text[:a] + value + text[b:]
        else:
            raise c.ConnectorError("Ambiguous writable_roots setting; existing bytes preserved")
    if tomllib.loads(updated) != expected:
        raise c.ConnectorError("Scoped permission edit would change unrelated TOML values")
    return updated.encode("utf-8")


def configure(profile, allow=True):
    path = profile / "config.toml"
    before = path.read_bytes() if path.exists() else b""
    data = tomllib.loads(before.decode("utf-8"))
    current = data.get("sandbox_workspace_write", {}).get("writable_roots", [])
    if not isinstance(current, list) or not all(isinstance(x, str) for x in current):
        raise c.ConnectorError("Existing writable_roots is not an array of paths")
    root = str(c.state_dir(profile))
    present = root in current
    if present == allow:
        return {"root": root, "added": False, "changed": False}
    roots = [*current, root] if allow else [x for x in current if x != root]
    updated = edit_roots(before, roots)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    if (path.read_bytes() if path.exists() else b"") != before:
        raise c.ConnectorError("Native config changed during scoped permission setup; retry without overwriting it")
    c.atomic(path, updated)
    path.chmod(mode)
    return {"root": root, "added": allow and not present, "changed": True,
            "before_sha256": __import__("hashlib").sha256(before).hexdigest(), "after_sha256": c.sha(path),
            "scope": "Only this private Brain state root; no approval, network or whole-filesystem policy change"}
