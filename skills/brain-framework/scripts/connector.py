"""Deployment mechanics for the Brain skill. Upstream algorithms stay upstream."""
from __future__ import annotations

import contextlib
import ast
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sqlite3
import stat
import signal
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid

SKILL = Path(__file__).absolute().parent.parent
SCHEMA = 1


class ConnectorError(RuntimeError):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def absolute(path):
    # Resolving a venv interpreter symlink selects the BASE interpreter instead.
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ConnectorError("Refusing to replace symlink: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=".brain-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def profile_path(value=None):
    return absolute(value or os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def state_dir(profile):
    return Path(profile) / "brain-framework"


@contextlib.contextmanager
def lock(profile):
    root = state_dir(profile)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "connector.lock"
    try:
        marker.mkdir()
    except FileExistsError:
        raise ConnectorError("Another deployment operation holds " + str(marker))
    try:
        atomic(marker / "owner.json", {"pid": os.getpid(), "at": now()})
        yield
    finally:
        (marker / "owner.json").unlink(missing_ok=True)
        marker.rmdir()


def source_lock():
    return read_json(SKILL / "references/upstream-lock.json")


def verify_source(root, metadata=None, strict=False):
    metadata = metadata or source_lock()
    root = Path(root)
    bad = []
    for item in metadata["files"]:
        p = root / item["path"]
        if p.is_symlink() or not p.is_file():
            bad.append(item["path"] + ": absent or symlink")
            continue
        raw = p.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if actual != item["sha"]:
            bad.append(item["path"] + ": differs")
    if bad:
        raise ConnectorError("Source integrity failed: " + "; ".join(bad[:12]))
    if strict:
        expected = {entry["path"] for entry in metadata["files"]}
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() or p.is_symlink()}
        if actual != expected:
            raise ConnectorError("Source cache has untracked or missing files")
    return {"revision": metadata["revision"], "verified_files": len(metadata["files"]), "at": now()}


def unpack_source(archive, destination, metadata=None):
    metadata = metadata or source_lock()
    allowed = {row["path"] for row in metadata["files"]}
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.isdir():
                continue
            name = member.name
            if name not in allowed and "/" in name:
                name = name.split("/", 1)[1]  # GitHub codeload prefix
            subdir = metadata.get("subdir", "")
            if subdir:
                # the framework sits in a folder of a larger repository (math-brain-public keeps it in framework/)
                if not name.startswith(subdir):
                    continue
                name = name[len(subdir):]
            if any(name.startswith(prefix) for prefix in metadata.get("excluded_prefixes", [])):
                continue
            path = PurePosixPath(name)
            if (not member.isfile() or name not in allowed or name in seen or path.is_absolute()
                    or ".." in path.parts or member.size > 64 * 1024 * 1024):
                raise ConnectorError("Unexpected archive member: " + member.name)
            seen.add(name)
            out = destination.joinpath(*path.parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as stream:
                out.write_bytes(stream.read())
    if seen != allowed:
        raise ConnectorError("Source archive does not contain the locked tree")
    return verify_source(destination, metadata)


def enclosing_checkout():
    """The repository this skill sits in (<repo>/skills/brain-framework), or None."""
    if SKILL.parent.name == "skills" and (SKILL.parent.parent / "bootstrap" / "install.py").is_file():
        return SKILL.parent.parent
    return None


def acquire_source(profile, supplied=None, network=False):
    metadata = source_lock()
    if not supplied and not network and not (SKILL / "assets/upstream-source.tar.gz").is_file():
        # Shipped inside the public repository: the checkout itself is the source, checked file by file
        # against the lock below exactly as a --source checkout would be.
        supplied = enclosing_checkout()
    cache = state_dir(profile) / "sources" / metadata["revision"]
    if cache.exists():
        verify_source(cache, metadata, strict=True)
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    staged = cache.with_name(cache.name + ".incoming-" + uuid.uuid4().hex[:8])
    try:
        if supplied:
            supplied = absolute(supplied)
            verify_source(supplied, metadata)
            staged.mkdir()
            for entry in metadata["files"]:
                out = staged / entry["path"]
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(supplied / entry["path"], out)
        elif network:
            archive = staged.with_suffix(".tar.gz")
            try:
                repository = metadata.get("repository", "https://github.com/yspennstate/brain-framework-public")
                url = ("https://codeload.github.com/" + repository.rstrip("/").split("github.com/", 1)[1]
                       + "/tar.gz/" + metadata["revision"])
                request = urllib.request.Request(url, headers={"User-Agent": "brain-framework-skill"})
                with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
                    shutil.copyfileobj(response, out)
                unpack_source(archive, staged, metadata)
            finally:
                archive.unlink(missing_ok=True)
        else:
            archive = SKILL / "assets/upstream-source.tar.gz"
            if not archive.is_file() or sha(archive) != metadata["archive_sha256"]:
                raise ConnectorError("Bundled source is missing or damaged, and this skill is not inside a checkout of the "
                                     "locked revision; use --network or a verified --source")
            unpack_source(archive, staged, metadata)
        verify_source(staged, metadata)
        os.replace(staged, cache)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return cache


def active(profile, required=True):
    pointer = state_dir(profile) / "active.json"
    if not pointer.is_file():
        if required:
            raise ConnectorError("No deployment. Run brainctl install --apply first.")
        return None
    deployment = read_json(pointer)
    location = Path(deployment["deployment"])
    allowed = state_dir(profile) / "generations"
    try:
        location.resolve().relative_to(allowed.resolve())
    except ValueError:
        raise ConnectorError("Active deployment is outside this profile")
    return read_json(location)


def runtime_env(deployment):
    env = dict(os.environ)
    # Every child is explicitly bound to one deployment; inherited Brain/test seams cannot redirect it.
    for key in list(env):
        if key.startswith(("BRAIN_", "COMPACT_", "TASK_CHECKLIST_", "OWNER_", "PROJECT_MEMORY_", "INSTINCTS_", "GRAPH_",
                           "ERROR_GRAPH_", "MATHBRAIN_", "MATH_BRAIN_", "MATH_TRANSPORT_", "NETWORK_", "FLEET_", "BRS_", "BRL_", "VERIFY_HOOKS_")) or key in (
                "AGENT_MESH_HOME", "GLOBAL_WORKSPACE_DB", "JOB_CYCLE_STATE", "CS_STATE_DIR", "FILMBRAIN_ROOT", "FILMBRAIN_DB", "PYTHONPATH", "PYTHONHOME"):
            env.pop(key)
    home = str(deployment["home"])
    dreaming = str(Path(home) / ".claude/dreaming")
    env.update({"HOME": home, "USERPROFILE": home, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "BRAIN_MEM_DIR": deployment["memory"],
                "BRAIN_FRAMEWORK_DIR": deployment["source"], "BRAIN_REPO_DIR": str(Path(home) / "brain-repo"),
                "AGENT_MESH_HOME": str(Path(home) / ".agents/coordination"),
                "GLOBAL_WORKSPACE_DB": str(Path(home) / ".agents/coordination/global_workspace.sqlite3"),
                "JOB_CYCLE_STATE": str(Path(home) / ".claude/job_cycle"),
                "TASK_CHECKLIST_HOME": str(Path(home) / "brain-repo/12_cognitive_architecture/task_checklist"),
                "COMPACT_HOME": home, "COMPACT_NODES_ROOT": str(Path(home) / ".claude/brain_graph/compactions"),
                "COMPACT_ANCHOR_DIR": deployment["memory"],
                "COMPACT_NO_SPAWN": "1", "CS_STATE_DIR": str(Path(home) / ".claude/common_sense/state"),
                "HF_HOME": str(Path(home) / ".cache/huggingface"), "TORCH_HOME": str(Path(home) / ".cache/torch"),
                "XDG_CACHE_HOME": str(Path(home) / ".cache"), "XDG_STATE_HOME": str(Path(home) / ".local/state"),
                "PYTHONPATH": os.pathsep.join([dreaming, str(Path(home) / ".claude/brain_graph/bin")]),
                "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                "VECLIB_MAXIMUM_THREADS": "1", "TOKENIZERS_PARALLELISM": "false"})
    if deployment.get("slug"):
        canonical = Path(home) / ".claude/projects" / deployment["slug"] / "memory"
        if absolute(deployment["memory"]) != absolute(canonical):
            raise ConnectorError("Deployment corpus differs from its canonical private-home binding")
        marker = Path(home) / ".claude/dreaming/BRAIN_SLUG"
        if marker.read_text().strip() != deployment["slug"]:
            raise ConnectorError("Installed canonical corpus marker changed")
        # This is the generation's canonical live corpus. The upstream test seam intentionally
        # refuses automatic indexing/vector writes whenever BRAIN_MEM_DIR is set.
        env.pop("BRAIN_MEM_DIR", None)
        env["BRAIN_SLUG"] = deployment["slug"]
    env["PATH"] = str(Path(deployment["python"]).parent) + os.pathsep + env.get("PATH", "")
    helpers = Path(deployment["home"]).parent / "helpers"
    if helpers.is_dir():
        env["PATH"] = str(helpers) + os.pathsep + env["PATH"]
    return env


def wait_owned(process, *, input_text=None, timeout=120, log=None):
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Only this invocation's process group is ours. Never match or kill by a substring.
        if os.name != "nt":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            raise ConnectorError("Timed-out Windows child outlived its parent; native Job Object qualification is required")
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate(timeout=5)
        if log:
            atomic(log, ((stdout or "") + (stderr or "")).encode("utf-8"))
        raise ConnectorError("Timed out; stopped this invocation's owned process tree")
    return stdout, stderr


def main_host_observer_selected(deployment):
    """Scope the new host observer to the explicit main; project diagnostics retain their original path."""
    from brain_binding import resolve
    main = resolve(profile_path(deployment["profile"]))
    return (main["profile"], main["id"]) == (deployment["profile"], deployment["id"])


def run(deployment, argv, *, input_text=None, timeout=120, check=True, log=None, session_id=None):
    env = runtime_env(deployment)
    if session_id:
        env["CODEX_THREAD_ID"] = str(session_id)
        env["CLAUDE_CODE_SESSION_ID"] = str(session_id)
        env["AGENT_MESH_SESSION"] = str(session_id)
    arguments = [str(x) for x in argv]
    # The POSIX runner keeps dream.lock's inode after releasing flock. The
    # pinned wake's marker-age/process scan is a Windows-shaped false alarm.
    # Adapt only that observer in memory; source and all other checks stay pinned.
    wake = str(Path(deployment["source"]) / "hooks/wake_brain.py")
    diagnostics = {str(Path(deployment["home"]) / ".claude/dreaming" / name) for name in ("hook_liveness.py", "brain_status.py")}
    scoped_main = (deployment.get("runtime") == "codex" and (wake in arguments or any(path in arguments for path in diagnostics))
                   and main_host_observer_selected(deployment))
    if os.name == "posix" and wake in arguments:
        index = arguments.index(wake)
        scope = ["--profile", deployment["profile"]] if scoped_main else []
        arguments[index:index + 1] = [str(SKILL / "scripts/posix_wake_adapter.py"), "--source", wake, *scope]
    if scoped_main:
        for mode, name in (("liveness", "hook_liveness.py"), ("status", "brain_status.py")):
            target = str(Path(deployment["home"]) / ".claude/dreaming" / name)
            if target in arguments:
                index = arguments.index(target)
                arguments[index:index + 1] = [str(SKILL / "scripts/codex_health_observer.py"), "--profile", deployment["profile"], "--mode", mode]
    # The original recall client admits only its canonical no-override environment to the
    # resident server. Its installed marker was checked above; source/build hints are irrelevant here.
    for index, argument in enumerate(arguments[:-1]):
        if Path(argument).name == "brain.py" and arguments[index + 1] == "recall" and deployment.get("slug"):
            for key in ("BRAIN_SLUG", "BRAIN_FRAMEWORK_DIR", "BRAIN_REPO_DIR"):
                env.pop(key, None)
    launch = arguments
    if os.name == "posix":
        # Only our child is demoted; foreground/protected processes are untouched.
        increment = max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0))
        if increment:
            launch = ["/usr/bin/nice", "-n", str(increment), *arguments]
    process = subprocess.Popen(launch, cwd=Path(deployment["home"]) / ".claude/dreaming", env=env,
                               stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                               start_new_session=(os.name != "nt"), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    stdout, stderr = wait_owned(process, input_text=input_text, timeout=timeout, log=log)
    result = subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
    if log:
        atomic(log, (result.stdout + result.stderr).encode("utf-8"))
    if check and result.returncode:
        raise ConnectorError("Child failed (%s): %s\n%s" % (result.returncode, " ".join(map(str, argv[:3])), (result.stdout + result.stderr)[-3000:]))
    return result



def native(deployment, tool, args=(), **kwargs):
    home = Path(deployment["home"])
    if tool in {"checklist", "graph-capture"}:
        dreaming = home / ".claude/dreaming"
        mode, target = ("--task-checklist", dreaming / "task_checklist_hook.py") if tool == "checklist" else ("--codex-capture", home / ".claude/brain_graph/bin/extract_codex_events.py")
        return run(deployment, [deployment["python"], "-s", dreaming / "brain_skill_adapter.py", "--profile", deployment["profile"],
                               "--brainctl", Path(deployment["profile"]) / "skills/brain-framework/scripts/brainctl.py", mode, target, *args], **kwargs)
    registry = {"brain": home / ".claude/dreaming/brain.py", "checklist": home / ".claude/dreaming/checklist.py",
                "mesh": home / "brain-framework/mesh/agent_mesh.py", "workspace": home / "brain-framework/workspace/global_workspace/workspace.py",
                "checkpoint": home / ".claude/dreaming/codex_manual_compact.py", "switch": home / ".claude/dreaming/brain_switch.py",
                "dream": home / ".claude/dreaming/run_dream.py", "status": home / ".claude/dreaming/brain_status.py"}
    if tool == "script":
        if not args:
            raise ConnectorError("Supply an original repository-relative Python script path")
        relative = PurePosixPath(args[0])
        allowed = {entry["path"] for entry in source_lock()["files"]}
        if relative.as_posix() not in allowed or relative.suffix != ".py" or relative.is_absolute() or ".." in relative.parts:
            raise ConnectorError("Script is not part of the locked original source")
        mappings = {"dreaming": home / ".claude/dreaming", "brain_graph": home / ".claude/brain_graph",
                    "network": home / ".claude/dreaming/network", "common_sense": home / ".claude/common_sense",
                    "fleet": home / ".claude/fleet", "mesh": home / "brain-framework/mesh", "governor": home / "brain-framework/governor",
                    "workspace": home / "brain-framework/workspace", "tools": home / "brain-framework/tools",
                    "viewer": home / "brain-framework/viewer", "mathbrain": home / "brain-framework/mathbrain",
                    "filmbrain": home / "brain-framework/filmbrain", "coordination": home / "brain-framework/coordination",
                    "delegation": home / "brain-repo/12_cognitive_architecture/delegation",
                    "resilience": home / "brain-repo/12_cognitive_architecture/resilience"}
        target = mappings.get(relative.parts[0], Path(deployment["source"]) / relative.parts[0]).joinpath(*relative.parts[1:])
        if not target.is_file():
            raise ConnectorError("Original script is not installed: " + str(target))
        return run(deployment, [deployment["python"], "-s", target, *args[1:]], **kwargs)
    if tool not in registry:
        raise ConnectorError("Unknown native entry point: " + tool)
    return run(deployment, [deployment["python"], "-s", registry[tool], *args], **kwargs)


def snapshot_files(profile, paths, folder):
    folder = Path(folder)
    folder.mkdir(parents=True)
    records = []
    for number, p in enumerate(paths):
        p = Path(p)
        if p.is_symlink() or (p.exists() and not p.is_file()):
            raise ConnectorError("Configuration is not a regular file: " + str(p))
        record = {"path": str(p), "existed": p.exists()}
        if p.exists():
            copy = folder / str(number)
            shutil.copy2(p, copy)
            record.update({"backup": str(copy), "sha256": sha(p), "mode": stat.S_IMODE(p.stat().st_mode)})
        records.append(record)
    atomic(folder / "manifest.json", records)
    return records


def restore_files(records):
    for record in records:
        path = Path(record["path"])
        if record["existed"]:
            atomic(path, Path(record["backup"]).read_bytes())
            if "mode" in record:
                path.chmod(record["mode"])
        else:
            path.unlink(missing_ok=True)


def skill_digest(root):
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ConnectorError("Skill contains a symlink: " + str(relative))
        if path.is_file() and "__pycache__" not in relative.parts and path.suffix != ".pyc" and path.name != ".DS_Store":
            digest.update(relative.as_posix().encode() + b"\0" + bytes.fromhex(sha(path)))
    return digest.hexdigest()


def copy_skill(profile, receipt=None):
    target = Path(profile) / "skills/brain-framework"
    if target.absolute() == SKILL.absolute():
        if receipt is not None:
            receipt.update({"target": str(target), "unchanged": True, "after_sha256": skill_digest(target)})
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    incoming = target.with_name(".brain-framework-incoming-" + uuid.uuid4().hex[:8])
    shutil.copytree(SKILL, incoming, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", "*.pyc"))
    # The source profile may protect its own files with macOS user flags.
    # A separate package install receives the bytes, without those local flags.
    if hasattr(os, "chflags") and hasattr(stat, "UF_IMMUTABLE"):
        for entry in (incoming, *incoming.rglob("*")):
            flags = entry.stat().st_flags
            if flags & stat.UF_IMMUTABLE:
                os.chflags(entry, flags & ~stat.UF_IMMUTABLE)
    previous = None
    if target.exists():
        before = skill_digest(target)
        previous = state_dir(profile) / "skill-backups" / ("skill-" + uuid.uuid4().hex)
        previous.parent.mkdir(parents=True, exist_ok=True)
        os.replace(target, previous)
    try:
        os.replace(incoming, target)
    except BaseException:
        if previous:
            os.replace(previous, target)
        raise
    if receipt is not None:
        receipt.update({"target": str(target), "backup": str(previous) if previous else None,
                        "before_sha256": before if previous else None, "after_sha256": skill_digest(target)})
    return target


def restore_skill(receipt):
    if not receipt or receipt.get("unchanged"):
        return
    target = Path(receipt["target"])
    backup = Path(receipt["backup"]) if receipt.get("backup") else None
    if target.exists() and skill_digest(target) != receipt["after_sha256"]:
        raise ConnectorError("Installed skill changed after activation; reconcile before rollback")
    if backup and (not backup.is_dir() or skill_digest(backup) != receipt["before_sha256"]):
        raise ConnectorError("Skill backup was modified")
    retired = target.with_name(".brain-framework-retired-" + uuid.uuid4().hex[:8])
    if target.exists():
        os.replace(target, retired)
    try:
        if backup:
            shutil.copytree(backup, target)
    except BaseException:
        if target.exists():
            shutil.rmtree(target)
        if retired.exists():
            os.replace(retired, target)
        raise
    if retired.exists():
        shutil.rmtree(retired)


def venv_python(root):
    return Path(root) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def verify_venv(python):
    result = subprocess.run([str(python), "-c", "import sys,json; print(json.dumps([sys.prefix,sys.base_prefix]))"], capture_output=True, text=True, check=True)
    prefix, base = json.loads(result.stdout)
    if prefix == base:
        raise ConnectorError("Dependency installation must use the venv interpreter without resolving its symlink")


def preflight_python(python):
    result = subprocess.run([str(python), "-c", "import json,sys,venv;print(json.dumps(list(sys.version_info[:3])))"],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ConnectorError("Selected Python cannot run venv: " + str(python))
    version = tuple(json.loads(result.stdout))
    if version < (3, 13) or version == (3, 14, 1):
        raise ConnectorError("Select Python 3.13+ (except 3.14.1); the locked dependencies and upstream tested runtime require it")
    return {"python": str(python), "version": list(version)}


def migrate_state(previous, deployment):
    """Preserve a stable lived snapshot; never copy old code, endpoints or embedding authority as new proof."""
    if not previous:
        return {"previous": None, "copied": []}
    verify_runtime_code(previous)
    old = Path(previous["home"])
    new = Path(deployment["home"])
    old_memory = Path(previous["memory"])
    new_memory = Path(deployment["memory"])
    copied = []
    excluded = {"brain_recall_server", "__pycache__", "embedding_generations", "embedding_runtime", ".pytest_cache", ".cache"}
    ephemeral = {"BRAIN_SLUG", "compute_state.json", "endpoint.json", "spawn.stamp", "server.lock", "dream.lock", "governor.pid", "memory_write.lock"}
    records = []
    for path in old.rglob("*"):
        relative = path.relative_to(old)
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_symlink():
            raise ConnectorError("Review a symlink in the previous runtime before migration: " + str(path))
        if not path.is_file():
            continue
        if str(relative) in previous["runtime_code"]:
            continue
        if path.name in ephemeral or path.name.endswith((".pyc", ".lock", ".pid", ".tmp", "-journal", "-wal", "-shm")) or path.name.startswith(("memory_embeddings", ".embedding")):
            continue
        if "embeddings" in path.name and path.suffix in {".npz", ".json"}:
            continue
        try:
            memory_relative = path.relative_to(old_memory)
        except ValueError:
            memory_relative = None
        target = new_memory / memory_relative if memory_relative is not None else new / relative
        before = sha(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {".sqlite", ".sqlite3", ".db"}:
            # SQLite backup observes a consistent committed view rather than copying a live journal piecemeal.
            try:
                with sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True) as source, sqlite3.connect(target) as destination:
                    source.backup(destination)
                copied.append({"source": str(path), "target": str(target), "method": "sqlite-backup", "sha256": sha(target)})
                continue
            except sqlite3.DatabaseError:
                target.unlink(missing_ok=True)
        config_bindings = {".claude/dreaming/hook_dispatch_list.json", ".claude/CLAUDE.md", ".claude/CLAUDE.brain.md", ".claude/brain_startup.md"}
        rebound = relative.as_posix() in config_bindings
        if rebound:
            raw = path.read_text(encoding="utf-8")
            raw = raw.replace(previous["source"], deployment["source"]).replace(previous["python"], deployment["python"])
            raw = raw.replace(str(old), str(new))
            target.write_text(raw, encoding="utf-8")
        else:
            shutil.copy2(path, target)
        after = sha(path)
        if before != after or (not rebound and sha(target) != before):
            raise ConnectorError("Lived state changed during migration; keep the old generation active and retry: " + str(path))
        records.append((path, before))
        copied.append({"source": str(path), "target": str(target), "method": "configuration-rebind" if rebound else "exact-copy",
                       "source_sha256": before, "sha256": sha(target)})
    # Recheck all copied durable files at the acceptance boundary, not only at each earlier sample.
    if any(sha(path) != digest for path, digest in records):
        raise ConnectorError("Lived state changed before migration completed; candidate remains inactive")
    if not (old / ".claude/dreaming/HOOK_DISPATCH_ENABLED").exists():
        (new / ".claude/dreaming/HOOK_DISPATCH_ENABLED").unlink(missing_ok=True)
    result = {"previous": previous["id"], "at": now(), "copied": copied,
              "not_reused_as_authority": ["recall-server endpoints", "embedding activation/coverage", "compute freshness", "verification receipts"]}
    atomic(new.parent / "migration.json", result)
    return result


def initialize_identity(deployment):
    """Fill only fresh bootstrap templates with facts known from this installation."""
    memory = Path(deployment["memory"])
    migrated = bool(deployment.get("migration", {}).get("previous"))
    source = Path(deployment["source"])
    identity = ("You are the continuous collaborator attached to this Brain profile. Resume from its authorized "
                "memories and current evidence. Protect the user's attention, the machine's responsiveness and the "
                "truth of reported results. This installation began at " + deployment["created_at"] + ". "
                "Its source revision is " + deployment["source_revision"] + ".\n\n"
                "Related: [[relationship_our_work_together]], [[values_what_we_stand_for]], [[method_write_like_a_human]], "
                "[[method_think_creatively]], [[shared_language]].\n")
    relationship = ("The partner label supplied to this installation is " + deployment["partner"] + ". "
                    "Work within their current requests and explicit scope. Preserve durable corrections and verified "
                    "preferences as the collaboration develops. No source deployment's private relationship or lived "
                    "history was imported.\n\n**Why:** continuity comes from this profile's real evidence.\n"
                    "**How to apply:** read this at wake and update it when the relationship changes. "
                    "Related: [[identity_who_you_are]], [[values_what_we_stand_for]].\n")
    for slug, desc, body in [("identity_who_you_are", "Identity and scope of this installed continuous collaborator", identity),
                             ("relationship_our_work_together", "The authorized partner and scope of this private profile", relationship)]:
        target = memory / (slug + ".md")
        template = source / "bootstrap/templates/seed_memories" / (slug + ".md")
        if migrated and target.read_bytes() != template.read_bytes():
            continue  # Preserve an authored identity/relationship; only untouched blanks are initialized.
        text = "---\nname: " + slug + "\ndescription: " + desc + "\nmetadata:\n  type: user\n  status: verified\n---\n\n" + body
        atomic(target, text.encode())
    module = ast.parse((source / "bootstrap/install.py").read_text())
    seed_values = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "MEM_SEEDS" for target in node.targets))
    def initialize_blank(name, raw):
        target = memory / name
        if not migrated or (target.exists() and target.read_bytes() == seed_values.get(name, "").encode()):
            atomic(target, raw)
    initialize_blank("AUTOBIOGRAPHY.md", ("# Installation history\n\nThis runtime generation was built at " + deployment["created_at"]
                     + " from upstream revision " + deployment["source_revision"] + ". Existing private history, if any, was preserved by migration.\n").encode())
    initialize_blank("MODEL_OF_PARTNER.md", ("# Partner model\n\nPartner label: " + deployment["partner"]
                     + ". Follow current explicit requests, retain their scope words, and learn preferences only from authorized evidence.\n").encode())
    initialize_blank("STATE_OF_MIND.md", b"**Focus:** Verify this Brain's source, native machinery and receiver behavior.\n**Worry:** Installation receipts alone do not establish complete embedding.\n**Satisfaction:** The original source has been verified.\n")


def prepare_wake_sources(deployment):
    home = Path(deployment["home"])
    dreaming = home / ".claude/dreaming"
    # Original self-care compares these deployed canonical mirrors byte-for-byte.
    canonical = home / "brain-repo/12_cognitive_architecture/dreaming"
    canonical.mkdir(parents=True, exist_ok=True)
    for name in ("brain.py", "bm25.py", "spread_recall.py", "codex_wake.py", "brain_node_documents.py", "brain_selfcare.py",
                 "codex_brain_wake_guard.py", "codex_stop_guard.py", "codex_warning_acceptance.py", "hook_liveness.py", "instincts.py"):
        shutil.copyfile(dreaming / name, canonical / name)
    shutil.copyfile(home / ".claude/wake_brain.ps1", canonical.parent / "wake_brain.ps1")
    learned = dreaming / "instincts_learned.json"
    if not learned.exists():
        # The original compiler accepts absence at import, but its receipt hashes this file,
        # and explicitly rejects []. Seed a real, provisional integration lesson with provenance;
        # never invent personal experience, silently discard existing lessons, or forge a receipt.
        slug = "connector_bootstrap_overlay_contract"
        native(deployment, "brain", ["remember", slug, "--type", "feedback", "--status", "provisional",
                                     "--desc", "Fresh Brain reflex compilation requires a valid authored overlay",
                                     "--evidence", str(Path(deployment["source"]) / "dreaming/instincts.py"),
                                     "--body", "Connector-authored bootstrap lesson, not this user's lived experience. "
                                     "At the pinned source revision, instincts.py accepts a missing authored overlay during import "
                                     "but emit_compile_receipt hashes it, and the parser rejects an empty list. This cold-start "
                                     "integration failure was reproduced in the connector's fresh-install tests. Preserve real "
                                     "authored data; do not clear it or fabricate a healthy compile receipt. This hint remains "
                                     "provisional and non-enforcing until domain precision is measured."])
        atomic(learned, [{"id": "connector_bootstrap_overlay_contract", "mode": "guide", "validity": "low",
                          "trigger": r"\b(?:instincts_learned\.json|emit_compile_receipt)\b",
                          "gut": "An authored-overlay compile needs real data and a matching receipt. Preserve the authored overlay; diagnose an absent or empty file instead of trusting a green exit.",
                          "sting": "The pinned original compiler cannot publish its receipt when the fresh bootstrap supplied no overlay.",
                          "source": slug, "status": "provisional", "origin": "connector bootstrap seed; not personal history",
                          "evidence": {"source_revision": deployment["source_revision"], "compiler_sha256": sha(dreaming / "instincts.py")}}])
    native(deployment, "script", ["dreaming/instincts.py"], timeout=180,
           log=home.parent / "instinct-compilation.log")


def commands_from_template(deployment):
    source = Path(deployment["source"])
    home = Path(deployment["home"])
    dreaming = home / ".claude/dreaming"
    tokens = {"{{PYTHON}}": deployment["python"], "{{DREAMING}}": str(dreaming), "{{FLEET}}": str(home / ".claude/fleet"),
              "{{CLAUDE_DIR}}": str(home / ".claude"), "{{USERPROFILE}}": str(home),
              "{{ERROR_GRAPH_HEX}}": str(dreaming / "error_graph").encode().hex(), "{{BRAIN_SLUG}}": deployment["slug"]}
    template = read_json(source / "hooks/hooks.codex.template.json") if deployment["runtime"] == "codex" else read_json(source / "hooks/settings.hooks.flat.template.json")
    handlers = {}
    groups = {}
    disabled = []
    for event, entries in template["hooks"].items():
        groups[event] = []
        for index, group in enumerate(entries):
            ids = []
            for number, spec in enumerate(group.get("hooks", [])):
                key = "%s-%d-%d" % (event, index, number)
                raw = spec.get("commandWindows" if os.name == "nt" else "command", spec.get("command", ""))
                # Parse path-bearing tokens without treating POSIX backslashes as escaping letters.
                if os.name != "nt":
                    raw = raw.replace("\\", "/")
                argv = split_command(raw)
                argv.extend(str(value).replace("\\", "/") if os.name != "nt" else str(value) for value in spec.get("args", []))
                for n, argument in enumerate(argv):
                    for token, value in tokens.items():
                        argument = argument.replace(token, value)
                    argv[n] = argument
                if argv and argv[0] in ("python", "python3", "py"):
                    argv[0] = deployment["python"]
                names = " ".join(argv)
                if os.name != "nt" and argv and argv[0].lower() in {"powershell", "powershell.exe", "pwsh"}:
                    if "wake_brain.ps1" in names:
                        continue  # Original Python wake is registered below with the same two parts.
                    disabled.append({"id": key, "reason": "PowerShell-only handler requires target qualification", "command": raw})
                    continue
                if "push_reminder.py" in names:
                    disabled.append({"id": key, "reason": "Upstream owner's automatic publication policy is not a generic installation requirement", "command": raw})
                    continue
                if "summary_hook.py" in names and not deployment.get("adopt_owner_summary", False):
                    disabled.append({"id": key, "reason": "The upstream owner's dated Stop-summary format is not adopted as this target user's reporting policy", "command": raw})
                    continue
                if "project_memory" in names:
                    disabled.append({"id": key, "reason": "Private signed project configuration and review key are not supplied by upstream", "command": raw})
                    continue
                if "codex_stop_guard.py" in names:
                    argv = [deployment["python"], "-s", str(dreaming / "brain_skill_adapter.py"), "--profile", deployment["profile"],
                            "--brainctl", str(Path(deployment["profile"]) / "skills/brain-framework/scripts/brainctl.py"),
                            "--guard", str(dreaming / "codex_stop_guard.py")]
                elif "task_checklist_hook.py" in names:
                    trailing = argv[2:]
                    argv = [deployment["python"], "-s", str(dreaming / "brain_skill_adapter.py"), "--profile", deployment["profile"],
                            "--brainctl", str(Path(deployment["profile"]) / "skills/brain-framework/scripts/brainctl.py"),
                            "--task-checklist", str(dreaming / "task_checklist_hook.py"), *trailing]
                handlers[key] = {"event": event, "argv": argv, "timeout": spec.get("timeout", 600), "upstream": spec}
                ids.append(key)
            if ids:
                groups[event].append({"matcher": group.get("matcher", "*"), "handlers": ids})
    # The upstream Codex template has no wake registration. Use the original POSIX wake.
    if deployment["runtime"] in {"codex", "claude"}:
        for part in (1, 2):
            key = "SessionStart-wake-" + str(part)
            handlers[key] = {"event": "SessionStart", "argv": [deployment["python"], "-s", str(source / "hooks/wake_brain.py"), "--part", str(part)], "timeout": 120}
            groups.setdefault("SessionStart", []).append({"matcher": "*", "handlers": [key]})
        if deployment["runtime"] == "codex":
            key = "SessionStart-codex-full-wake"
            handlers[key] = {"event": "SessionStart", "argv": [deployment["python"], "-s", str(dreaming / "codex_wake.py")], "timeout": 120, "full_context": True}
            groups["SessionStart"].append({"matcher": "*", "handlers": [key]})
        handlers["SubagentStart-source-contract"] = {"event": "SubagentStart", "builtin": "worker-contract", "timeout": 30}
        groups["SubagentStart"] = [{"matcher": "*", "handlers": ["SubagentStart-source-contract"]}]
    if "common-sense" in deployment["enabled"]:
        for event in ("UserPromptSubmit", "PreToolUse"):
            key = event + "-common-sense"
            argv = [deployment["python"], "-s", str(home / ".claude/common_sense/cs_core.py"), "--all"]
            if deployment["runtime"] == "codex":
                argv.append("--codex")
            handlers[key] = {"event": event, "argv": argv, "timeout": 30}
            groups.setdefault(event, []).append({"matcher": "*", "handlers": [key]})
    # Native session disconnect must precede all memory reads within this bridge.
    handlers["UserPromptSubmit-brain-switch"] = {"event": "UserPromptSubmit", "argv": [deployment["python"], "-s", str(dreaming / "brain_switch.py")], "timeout": 30}
    groups.setdefault("UserPromptSubmit", []).insert(0, {"matcher": "*", "handlers": ["UserPromptSubmit-brain-switch"]})
    return handlers, groups, disabled


def shell_join(argv):
    if os.name == "nt":
        return subprocess.list2cmdline(list(map(str, argv)))
    return shlex.join(list(map(str, argv)))


def split_command(command):
    if os.name != "nt":
        return shlex.split(command, posix=True)
    # Use the host parser rather than shlex(posix=False), which retains quote characters in argv.
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    count = ctypes.c_int()
    argv = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(argv)


def merge_runtime_hooks(profile, deployment, installed_skill):
    target = Path(profile) / "hooks.json" if deployment["runtime"] == "codex" else Path(deployment["target_home"]) / ".claude/settings.json"
    existing = read_json(target) if target.exists() else {}
    if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
        raise ConnectorError("Existing hook configuration has an invalid shape")
    before = target.read_bytes() if target.exists() else None
    previous = active(profile, required=False)
    owned = previous.get("managed_hook_groups", {}) if previous and previous["runtime"] == deployment["runtime"] else {}
    merged = existing.setdefault("hooks", {})
    for event, groups in owned.items():
        current = merged.get(event, [])
        for group in groups:
            if group not in current:
                raise ConnectorError("A managed hook was modified; reconcile it before an upgrade: " + event)
            current.remove(group)
    generated = {}
    for event, groups in deployment["hook_groups"].items():
        generated[event] = []
        for group in groups:
            specs = []
            for key in group["handlers"]:
                spec = {"type": "command", "command": shell_join([deployment["python"], installed_skill / "scripts/hook_bridge.py", "--profile", profile, "--handler", key]),
                        "timeout": deployment["handlers"][key]["timeout"] + 20}
                if deployment["handlers"][key].get("full_context"):
                    spec["additionalContextLimit"] = 0
                specs.append(spec)
            output = {"matcher": group["matcher"], "hooks": specs}
            generated[event].append(output)
    # The observer bypasses this skill's bridge and its runtime interpreter.
    # It remains able to report the loss of either dependency.
    deployment.update({"hook_config": str(target), "managed_hook_groups": generated})
    if deployment["runtime"] == "codex":
        import guardian
        meta = guardian.install(profile, deployment, installed_skill)
        for event, groups in meta["groups"].items():
            generated.setdefault(event, [])[:0] = groups
    for event, groups in generated.items():
        merged.setdefault(event, [])[:0] = groups
    if (target.read_bytes() if target.exists() else None) != before:
        raise ConnectorError("Hook configuration changed during integration")
    atomic(target, existing)
    deployment.update({"hook_config": str(target), "managed_hook_groups": generated, "hook_config_sha256": sha(target)})
    if deployment.get("guardian"):
        guardian.refresh_manifest(profile, deployment, installed_skill)
    return target



def initialize_compaction(deployment):
    hub = "compaction_hub_how_we_compact_and_where_it_is_kept_2026_10_09"
    if not (Path(deployment["memory"]) / (hub + ".md")).exists():
        native(deployment, "brain", ["remember", hub, "--type", "reference", "--status", "verified",
                                     "--desc", "Durable context checkpoint nodes and their private recall anchors",
                                     "--evidence", str(Path(deployment["source"]) / "dreaming/compact_nodes.py"),
                                     "--body", "Connector-authored installation reference to the original compaction machinery. "
                                     "Full summaries stay in this deployment's private compactions directory; privacy-preserving anchors "
                                     "live in its canonical memory corpus and link here. Native manual checkpoints require the actual "
                                     "full session UUID/rollout, matching mesh actor, a full summary with NEXT, and verified hashes/claims. "
                                     "A saved checkpoint preserves context; it does not claim the runtime reduced its context window. "
                                     "This is setup structure, not a record of a user session or a fabricated compaction."])


def create_deployment(profile, *, supplied=None, network=False, semantic=False, runtime="codex", partner="the user", timezone="UTC", python=None, dependencies=True):
    py = absolute(python or sys.executable)
    preflight_python(py)
    profile = profile_path(profile)
    previous = active(profile, required=False)
    source = acquire_source(profile, supplied, network)
    generation = state_dir(profile) / "generations" / (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
    generation.mkdir(parents=True)
    cloned = generation / "source"
    shutil.copytree(source, cloned)
    home = generation / "home"
    home.mkdir()
    setup_env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
                 "PIP_CACHE_DIR": str(state_dir(profile) / "dependency-cache"), "PYTHONNOUSERSITE": "1"}
    setup_env.pop("PYTHONPATH", None)
    setup_env.pop("PYTHONHOME", None)
    environment = generation / "venv"
    subprocess.run([str(py), "-m", "venv", str(environment)], check=True, capture_output=True, env=setup_env)
    py = venv_python(environment)
    verify_venv(py)
    deployment = {"schema": SCHEMA, "created_at": now(), "id": generation.name, "profile": str(profile), "home": str(home),
                  "target_home": str(profile.parent), "source": str(cloned), "source_revision": source_lock()["revision"],
                  "python": str(py), "runtime": runtime, "partner": partner, "timezone": timezone,
                  "enabled": ["core", "graph", "common-sense"] + (["semantic"] if semantic else []), "state": "staging"}
    if dependencies:
        requirements = SKILL / "assets/runtime-requirements.txt"
        packages = [str(py), "-m", "pip", "install", "-r", str(requirements)] if requirements.exists() else [str(py), "-m", "pip", "install", "numpy", "networkx"]
        with (generation / "dependencies.log").open("w") as log:
            result = subprocess.run(packages, stdout=log, stderr=subprocess.STDOUT, timeout=900, env=setup_env)
        if result.returncode:
            raise ConnectorError("Dependency setup failed; inspect " + str(generation / "dependencies.log"))
        if semantic:
            with (generation / "semantic-dependencies.log").open("w") as log:
                result = subprocess.run([str(py), "-m", "pip", "install", "-r", str(SKILL / "assets/semantic-requirements.txt")], stdout=log, stderr=subprocess.STDOUT, timeout=1200, env=setup_env)
            if result.returncode:
                raise ConnectorError("Semantic dependencies failed")
    argv = [str(py), str(cloned / "bootstrap/install.py"), "--home", str(home), "--project-dir", str(home / "project"), "--timezone", timezone, "--partner", partner, "--python", str(py), "--apply"]
    bootstrap_env = setup_env
    if sys.platform == "darwin" and shutil.which("git") == "/usr/bin/git" and not Path("/Library/Developer/CommandLineTools/usr/bin/git").exists() and not Path("/Applications/Xcode.app/Contents/Developer/usr/bin/git").exists():
        # Calling the macOS shim opens the developer-tools installer. Archive bootstrap needs no Git.
        helpers = generation / "helpers"
        helpers.mkdir()
        (helpers / "git").write_text("#!/bin/sh\nexit 127\n")
        (helpers / "git").chmod(0o700)
        bootstrap_env["PATH"] = str(helpers) + os.pathsep + bootstrap_env.get("PATH", "")
    result = subprocess.run(argv, cwd=cloned, env=bootstrap_env, capture_output=True, text=True, timeout=600)
    atomic(generation / "bootstrap.log", (result.stdout + result.stderr).encode())
    if result.returncode:
        raise ConnectorError("Upstream bootstrap failed; inspect " + str(generation / "bootstrap.log"))
    dreaming = home / ".claude/dreaming"
    (dreaming / "embedding_refresh").mkdir(exist_ok=True)
    deployment["slug"] = (dreaming / "BRAIN_SLUG").read_text().strip()
    deployment["memory"] = str(home / ".claude/projects" / deployment["slug"] / "memory")
    # Add organs omitted by bootstrap without altering their source. State remains per deployment.
    for name, destination in [("network", dreaming / "network"), ("common_sense", home / ".claude/common_sense"),
                              ("viewer", home / "brain-framework/viewer"), ("mathbrain", home / "brain-framework/mathbrain"),
                              ("filmbrain", home / "brain-framework/filmbrain"), ("coordination", home / "brain-framework/coordination")]:
        shutil.copytree(cloned / name, destination, dirs_exist_ok=True)
    shutil.copyfile(cloned / "hooks/wake_brain.py", home / ".claude/wake_brain.py")
    shutil.copyfile(SKILL / "scripts/native_adapter.py", dreaming / "brain_skill_adapter.py")
    deployment["migration"] = migrate_state(previous, deployment)
    initialize_identity(deployment)
    initialize_compaction(deployment)
    # Empty ledgers are empty evidence, not invented experiences. Native readers require these files.
    for path, content in [(Path(deployment["memory"]) / "INDEX_ARCHIVE.md", "# Memory archive\n\nNo archived entries yet.\n"),
                          (dreaming / "instinct_fires.tsv", "")]:
        if not path.exists():
            atomic(path, content.encode())
    prepare_wake_sources(deployment)
    handlers, groups, disabled = commands_from_template(deployment)
    deployment.update({"handlers": handlers, "hook_groups": groups, "disabled_hooks": disabled})
    deployment["runtime_code"] = {str(p.relative_to(home)): sha(p) for p in home.rglob("*.py")}
    deployment["canary"] = "connector_installation_" + generation.name.lower().replace("-", "_")
    candidate_path = Path(deployment["memory"]) / (deployment["canary"] + ".md")
    native(deployment, "brain", ["remember", deployment["canary"], "--type", "project", "--status", "verified",
                                "--desc", "Installation provenance for " + generation.name,
                                "--evidence", str(generation / "bootstrap.log"),
                                "--body", "This private deployment candidate uses upstream revision " + deployment["source_revision"]
                                + ". It belongs to profile " + str(profile) + ". Readiness is decided by current verification, not this provenance note."])
    if not candidate_path.is_file() or deployment["source_revision"] not in candidate_path.read_text():
        raise ConnectorError("The upstream memory writer did not leave the expected provenance node")
    run(deployment, [str(py), str(dreaming / "bm25.py"), "build"])
    atomic(generation / "deployment.json", deployment)
    return deployment


def activate(profile, deployment):
    profile = profile_path(profile)
    skill_receipt = {}
    config = profile / "hooks.json" if deployment["runtime"] == "codex" else Path(deployment["target_home"]) / ".claude/settings.json"
    folder = Path(deployment["home"]).parent / "configuration-backup"
    paths = [profile / "AGENTS.md", profile / "AGENTS.override.md", config, state_dir(profile) / "active.json", state_dir(profile) / "DISCONNECTED"]
    if deployment["runtime"] == "codex":
        paths.extend(guardian_paths(profile))
    if deployment["runtime"] == "claude":
        paths.append(Path(deployment["target_home"]) / ".claude/CLAUDE.md")
    records = snapshot_files(profile, paths, folder)
    previous = active(profile, required=False)
    permission = None
    try:
        atomic(state_dir(profile) / "DISCONNECTED", b"Activation in progress.\n")
        installed = copy_skill(profile, skill_receipt)
        merge_runtime_hooks(profile, deployment, installed)
        if deployment["runtime"] == "codex":
            from profile_scope import configure
            permission_records = snapshot_files(profile, [profile / "config.toml"], Path(deployment["home"]).parent / "permission-backup")
            permission = configure(profile)
            deployment["codex_scope"] = {**permission, "owned": permission["added"] or bool(previous and previous.get("codex_scope", {}).get("owned")),
                                         "backup": str(Path(deployment["home"]).parent / "permission-backup/manifest.json")}
        result = subprocess.run([sys.executable, str(installed / "scripts/standing_activation.py"), "enable", "--runtime", deployment["runtime"]], env={**os.environ, "CODEX_HOME": str(profile)}, capture_output=True, text=True)
        if result.returncode:
            raise ConnectorError(result.stderr)
        deployment.update({"state": "active", "previous": str(Path(previous["home"]).parent / "deployment.json") if previous else None,
                           "configuration_backup": str(folder / "manifest.json"), "skill_transaction": skill_receipt, "activated_at": now()})
        atomic(Path(deployment["home"]).parent / "deployment.json", deployment)
        atomic(state_dir(profile) / "active.json", {"deployment": str(Path(deployment["home"]).parent / "deployment.json")})
        (state_dir(profile) / "DISCONNECTED").unlink(missing_ok=True)
        for record in records:
            path = Path(record["path"])
            record["after_sha256"] = sha(path) if path.is_file() else None
            record["after_mode"] = stat.S_IMODE(path.stat().st_mode) if path.is_file() else None
        atomic(folder / "manifest.json", records)
    except BaseException:
        restore_files(records)
        restore_skill(skill_receipt)
        if permission and permission.get("added"):
            if sha(profile / "config.toml") == permission["after_sha256"]:
                restore_files(permission_records)
            else:
                configure(profile, allow=False)
        raise
    return deployment


def guardian_paths(profile):
    root = state_dir(profile) / "guardian"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ConnectorError("Independent guardian root is not an owned directory: " + str(root))
    return [root / name for name in ("guardian.py", "launch.sh", "manifest.json")]


def verify_guardian(deployment):
    """Bridge's check of the independent observer; no admission/delivery claim."""
    profile = Path(deployment["profile"])
    script, launcher, manifest = guardian_paths(profile)
    meta = deployment.get("guardian")
    if not meta or meta.get("schema") != 1:
        raise ConnectorError("Independent guardian is not installed; run brainctl guardian --apply, then review its native hooks")
    if set(meta.get("files", {})) != {str(script), str(launcher)} or meta.get("manifest") != str(manifest):
        raise ConnectorError("Independent guardian ownership metadata is missing or changed")
    for name, expected in meta["files"].items():
        path = Path(name)
        if path.is_symlink() or not path.is_file() or sha(path) != expected:
            raise ConnectorError("Independent guardian executable is missing or changed: " + name)
    if manifest.is_symlink() or not manifest.is_file():
        raise ConnectorError("Independent guardian manifest is unavailable")
    doc = read_json(manifest)
    if doc.get("schema") != 1 or doc.get("profile") != str(profile) or doc.get("guardian_files") != meta["files"]:
        raise ConnectorError("Independent guardian manifest identity is invalid")
    package_script = profile / "skills/brain-framework/scripts/guardian.py"
    if not package_script.is_file() or sha(package_script) != meta["files"][str(script)]:
        raise ConnectorError("Independent guardian package/deployment differs; inspect and explicitly reinstall")
    expected_groups = {event: [{"matcher": ".*", "hooks": [{"type": "command", "command": shell_join(["/bin/sh", launcher, event]), "timeout": 30}]}]
                       for event in ("SessionStart", "UserPromptSubmit", "SubagentStart", "PreToolUse", "PostCompact")}
    if meta.get("groups") != expected_groups:
        raise ConnectorError("Independent guardian registration metadata changed")
    current = read_json(deployment["hook_config"]).get("hooks", {})
    for event, groups in expected_groups.items():
        for group in groups:
            if group not in current.get(event, []) or group not in deployment.get("managed_hook_groups", {}).get(event, []):
                raise ConnectorError("Independent guardian registration is missing or changed: " + event)
    return {"files_verified": 3, "events_registered": len(expected_groups), "owner_delivery": "requires actual native UI evidence"}


def install_guardian(profile, apply=False):
    """Explicit in-place connector upgrade; identity, corpus and permissions stay bound."""
    profile = profile_path(profile)
    deployment = active(profile)
    if deployment["runtime"] != "codex":
        raise ConnectorError("Independent guardian is qualified only for POSIX Codex hooks")
    verify_runtime_code(deployment)
    verify_source(state_dir(profile) / "sources" / deployment["source_revision"], strict=True)
    import brain_binding
    brain_binding.resolve(profile)
    paths = guardian_paths(profile)
    plan = {"profile": str(profile), "deployment": deployment["id"], "writes": [str(p) for p in paths],
            "changes_applied": False, "services_started": [], "automatic_delivery_verified": False,
            "next": "Review the changed native hooks, then qualify actual root, worker and alarm delivery."}
    if not apply:
        return plan
    folder = Path(deployment["home"]).parent / ("guardian-upgrade-" + uuid.uuid4().hex[:8])
    records = snapshot_files(profile, [*paths, Path(deployment["hook_config"]),
        Path(deployment["home"]).parent / "deployment.json", state_dir(profile) / "DISCONNECTED"], folder)
    try:
        atomic(state_dir(profile) / "DISCONNECTED", b"Guardian integration in progress.\n")
        merge_runtime_hooks(profile, deployment, profile / "skills/brain-framework")
        atomic(Path(deployment["home"]).parent / "deployment.json", deployment)
        restore_files([row for row in records if row["path"] == str(state_dir(profile) / "DISCONNECTED")])
    except BaseException:
        restore_files(records)
        raise
    return {**plan, "changes_applied": True, "backup": str(folder / "manifest.json")}


def disable(profile):
    profile = profile_path(profile)
    deployment = active(profile)
    # The bridge observes this before every handler, including hooks already loaded in running sessions.
    atomic(state_dir(profile) / "DISCONNECTED", b"Disconnected by the profile operator.\n")
    native(deployment, "switch", ["off", "--global"])
    if deployment.get("codex_scope", {}).get("owned"):
        from profile_scope import configure
        configure(profile, allow=False)
    result = subprocess.run([sys.executable, str(Path(profile) / "skills/brain-framework/scripts/standing_activation.py"), "disable", "--runtime", deployment["runtime"]], env={**os.environ, "CODEX_HOME": str(profile)}, capture_output=True, text=True)
    if result.returncode:
        raise ConnectorError(result.stderr)
    return {"disconnected": True, "state_preserved": True}


def rollback(profile):
    profile = profile_path(profile)
    deployment = active(profile)
    records = read_json(deployment["configuration_backup"])
    skill_receipt = deployment.get("skill_transaction", {})
    if skill_receipt and not skill_receipt.get("unchanged"):
        if skill_digest(skill_receipt["target"]) != skill_receipt["after_sha256"]:
            raise ConnectorError("Installed skill changed after activation; reconcile before rollback")
        if skill_receipt.get("backup") and skill_digest(skill_receipt["backup"]) != skill_receipt["before_sha256"]:
            raise ConnectorError("Skill backup was modified")
    for record in records:
        if record["existed"] and sha(record["backup"]) != record["sha256"]:
            raise ConnectorError("Configuration backup was modified")
        path = Path(record["path"])
        if path.is_symlink() or (sha(path) if path.is_file() else None) != record.get("after_sha256"):
            raise ConnectorError("Configuration changed after activation; preserve/reconcile it before rollback: " + str(path))
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) != record.get("after_mode"):
            raise ConnectorError("Configuration permissions changed after activation: " + str(path))
    recovery = Path(deployment["home"]).parent / ("rollback-recovery-" + uuid.uuid4().hex[:8])
    current = snapshot_files(profile, [Path(row["path"]) for row in records], recovery)
    current_skill = recovery / "skill"
    if skill_receipt and not skill_receipt.get("unchanged"):
        shutil.copytree(skill_receipt["target"], current_skill)
    disconnected = state_dir(profile) / "DISCONNECTED"
    was_disconnected = disconnected.exists()
    atomic(disconnected, b"Configuration rollback in progress.\n")
    try:
        restore_skill(skill_receipt)
        restore_files(records)
        permission = deployment.get("codex_scope", {})
        if not deployment.get("previous") and permission.get("owned"):
            from profile_scope import configure
            path = profile / "config.toml"
            if path.exists() and sha(path) == permission.get("after_sha256") and permission.get("backup"):
                restore_files(read_json(permission["backup"]))
            else:
                configure(profile, allow=False)
    except BaseException:
        restore_files(current)
        if current_skill.exists():
            target = Path(skill_receipt["target"])
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(current_skill, target)
        raise
    finally:
        if not was_disconnected and not any(Path(row["path"]) == disconnected for row in records):
            disconnected.unlink(missing_ok=True)
    return {"rolled_back": deployment["id"], "previous": deployment.get("previous"), "memory_preserved": deployment["memory"]}


def describe(profile):
    profile = profile_path(profile)
    deployment = active(profile, required=False)
    if not deployment:
        return {"installed": False, "ready": False, "reason": "No active deployment"}
    errors = []
    for name in ("python", "memory", "source"):
        if not Path(deployment[name]).exists():
            errors.append(name + " missing")
    try:
        verify_source(state_dir(profile) / "sources" / deployment["source_revision"], strict=True)
    except ConnectorError as exc:
        errors.append(str(exc))
    receipt_path = Path(deployment["home"]).parent / "verification.json"
    receipt = read_json(receipt_path) if receipt_path.exists() else {}
    disconnected = (state_dir(profile) / "DISCONNECTED").exists()
    current_receiver = False
    try:
        verify_runtime_code(deployment)
        from receiver_probe import validate_receivers
        current_receiver = not errors and not disconnected and bool(validate_receivers(deployment).get("passed"))
    except (ConnectorError, OSError, ValueError, KeyError) as exc:
        errors.append(str(exc))
    # A receipt is an observation, never proof that a changed deployment is still healthy.
    return {"installed": True, "id": deployment["id"], "revision": deployment["source_revision"], "runtime": deployment["runtime"],
            "source": deployment["source"], "memory": deployment["memory"], "profile": str(profile),
            "ready": False, "last_verified": receipt.get("at"), "last_verification_passed": receipt.get("passed", False),
            "receiver_verified": current_receiver, "last_connection_verified": receipt.get("connection_verified", False),
            "last_faculties": receipt.get("faculties", {}), "disconnected": disconnected, "errors": errors,
            "enabled": deployment["enabled"], "disabled_hooks": deployment["disabled_hooks"],
            "next": "Run verify to recompute readiness; new or changed Codex hooks require the runtime's hook review."}


def verify_runtime_code(deployment):
    home = Path(deployment["home"])
    expected = deployment["runtime_code"]
    actual = {str(p.relative_to(home)) for p in home.rglob("*.py")}
    if actual != set(expected):
        raise ConnectorError("Runtime code file set changed")
    changed = [name for name, digest in expected.items() if sha(home / name) != digest]
    if changed:
        raise ConnectorError("Runtime code differs: " + ", ".join(changed[:8]))
    return {"files_verified": len(expected)}


def build_semantic(deployment):
    if "semantic" not in deployment["enabled"]:
        return {"enabled": False, "reason": "Not selected; the lexical lane remains available"}
    folder = Path(deployment["home"]).parent
    dreaming = Path(deployment["home"]) / ".claude/dreaming"
    py = deployment["python"]
    run(deployment, [py, str(dreaming / "fetch_bge_onnx.py")], timeout=600, log=folder / "model-fetch.log")
    run(deployment, [py, str(dreaming / "query_embed.py"), "--selftest"], timeout=120, log=folder / "query-encoder.log")
    staged = dreaming / "memory_embeddings.next.npz"
    run(deployment, [py, str(dreaming / "build_memory_embeddings.py"), deployment["memory"], "--out", str(staged)], timeout=1200, log=folder / "embedding-build.log")
    run(deployment, [py, str(dreaming / "publish_memory_embeddings.py"), "--staged", str(staged), "--mem-dir", deployment["memory"]], timeout=180, log=folder / "embedding-publication.log")
    coverage = run(deployment, [py, str(dreaming / "embedding_coverage.py"), "--mem-dir", deployment["memory"], "--require-complete", "--write-status"], log=folder / "embedding-coverage.log")
    return {"enabled": True, "coverage": coverage.stdout.strip(), "model_log": str(folder / "model-fetch.log")}


def maintain_recall(deployment):
    """Use original incremental builders and publication; no invented rows or weakened coverage gates."""
    dreaming = Path(deployment["home"]) / ".claude/dreaming"
    folder = Path(deployment["home"]).parent
    run(deployment, [deployment["python"], str(dreaming / "bm25.py"), "refresh"], timeout=180,
        log=folder / "recall-maintenance-bm25.log")
    if "semantic" in deployment["enabled"]:
        staged = dreaming / "memory_embeddings.next.npz"
        run(deployment, [deployment["python"], str(dreaming / "build_memory_embeddings.py"), deployment["memory"], "--out", str(staged)],
            timeout=600, log=folder / "recall-maintenance-vectors.log")
        run(deployment, [deployment["python"], str(dreaming / "publish_memory_embeddings.py"), "--staged", str(staged), "--mem-dir", deployment["memory"]],
            timeout=180, log=folder / "recall-maintenance-publication.log")
        coverage = native(deployment, "script", ["dreaming/embedding_coverage.py", "--mem-dir", deployment["memory"], "--require-complete", "--write-status"])
    else:
        coverage = None
    return {"at": now(), "semantic": bool(coverage), "coverage": coverage.stdout.strip() if coverage else "not selected"}


def repair_wiring(profile, apply=False):
    deployment = active(profile)
    verify_runtime_code(deployment)
    verify_source(state_dir(profile) / "sources" / deployment["source_revision"], strict=True)
    path = Path(deployment["hook_config"])
    config = read_json(path) if path.exists() else {"hooks": {}}
    hooks = config.setdefault("hooks", {})
    missing = [(event, group) for event, groups in deployment["managed_hook_groups"].items()
               for group in groups if group not in hooks.get(event, [])]
    # A changed command naming one of our handlers is an owner edit, not a second missing registration.
    conflicts = []
    for event, group in missing:
        identifiers = ["--handler " + key for key in deployment["handlers"]]
        for other in hooks.get(event, []):
            if other in deployment["managed_hook_groups"].get(event, []):
                continue
            if any(any(identifier in spec.get("command", "") for identifier in identifiers) for spec in other.get("hooks", [])):
                conflicts.append(event)
    plan = {"missing_groups": len(missing), "conflicts": sorted(set(conflicts)), "changes_applied": False,
            "next": "Review changed hooks in the native runtime, then run probe-root --apply --with-worker using normal persisted trust."}
    if not apply:
        return plan
    if conflicts:
        raise ConnectorError("Modified owned hook commands require reconciliation before automatic repair: " + ", ".join(sorted(set(conflicts))))
    folder = Path(deployment["home"]).parent / ("wiring-repair-" + uuid.uuid4().hex[:8])
    paths = [path, Path(profile) / "AGENTS.md", Path(profile) / "AGENTS.override.md"]
    if deployment["runtime"] == "claude":
        paths.append(Path(deployment["target_home"]) / ".claude/CLAUDE.md")
    records = snapshot_files(profile, paths, folder)
    try:
        for event, group in missing:
            hooks.setdefault(event, []).append(group)
        atomic(path, config)
        if deployment["runtime"] == "codex":
            from profile_scope import configure
            permission = configure(profile)
            deployment["codex_scope"] = {**deployment.get("codex_scope", {}), **permission,
                                         "owned": permission["added"] or deployment.get("codex_scope", {}).get("owned", False)}
            atomic(Path(deployment["home"]).parent / "deployment.json", deployment)
        result = subprocess.run([sys.executable, str(Path(profile) / "skills/brain-framework/scripts/standing_activation.py"),
                                 "enable", "--runtime", deployment["runtime"]], env={**os.environ, "CODEX_HOME": str(profile)}, capture_output=True, text=True)
        if result.returncode:
            raise ConnectorError(result.stderr)
    except BaseException:
        restore_files(records)
        raise
    return {**plan, "changes_applied": True, "backup": str(folder / "manifest.json"), "automatic_delivery_verified": False}


def verify(deployment, *, integrated=False, hooks=True, smoke=True):
    """Recompute checks through original entry points; a prior receipt cannot turn this green."""
    generation = Path(deployment["home"]).parent
    checks = []
    integrity_ready = True

    def check(name, callback):
        if not integrity_ready:
            checks.append({"name": name, "passed": False, "error": "Unavailable until source and installed code integrity pass"})
            return
        try:
            detail = callback()
            checks.append({"name": name, "passed": True, "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "error": str(exc)[:3000]})

    check("locked upstream source", lambda: verify_source(state_dir(deployment["profile"]) / "sources" / deployment["source_revision"], strict=True))
    check("installed runtime code", lambda: verify_runtime_code(deployment))
    integrity_ready = all(row["passed"] for row in checks)

    def readback():
        node = native(deployment, "brain", ["node", deployment["canary"]])
        if deployment["source_revision"] not in node.stdout:
            raise ConnectorError("Node reader did not return current source provenance")
        recall = native(deployment, "brain", ["recall", "Installation provenance " + deployment["id"]], timeout=120)
        if deployment["canary"] not in recall.stdout:
            raise ConnectorError("Recall did not return the installed provenance node")
        atomic(generation / "recall-readback.log", recall.stdout.encode())
        return {"node": deployment["canary"], "recall_output_sha256": hashlib.sha256(recall.stdout.encode()).hexdigest()}

    check("installation provenance node/recall readback", readback)
    if "semantic" in deployment["enabled"]:
        def semantic():
            dreaming = Path(deployment["home"]) / ".claude/dreaming"
            result = run(deployment, [deployment["python"], str(dreaming / "embedding_coverage.py"), "--mem-dir", deployment["memory"], "--require-complete"], timeout=120)
            encoder = run(deployment, [deployment["python"], str(dreaming / "query_embed.py"), "--selftest"], timeout=120)
            return {"coverage": result.stdout.strip(), "encoder": encoder.stdout.strip()}
        check("semantic artifacts and actual query encoder", semantic)
    if smoke:
        def original_smoke():
            receipt = generation / "upstream-installed-smoke.json"
            result = run(deployment, [deployment["python"], str(Path(deployment["source"]) / "scripts/smoke_framework.py"), "--installed", deployment["home"], "--receipt", str(receipt)], timeout=240, check=False, log=generation / "upstream-installed-smoke.log")
            if not receipt.is_file():
                raise ConnectorError("Smoke runner left no independently readable receipt")
            data = read_json(receipt)
            unexpected = [row for row in data["checks"] if not row["ok"] and row["name"] not in ("math brain: present", "film brain: present")]
            if unexpected or data["passed"] != 16 or data["total"] != 18:
                raise ConnectorError("Upstream installed smoke failed outside its documented optional omissions: " + result.stdout[-2200:])
            return {"passed": data["passed"], "total": data["total"], "omissions": data["checked_but_not_installed"], "receipt_sha256": sha(receipt)}
        check("upstream installed effects and negative controls", original_smoke)
    if integrated:
        if deployment["runtime"] == "codex":
            check("independent guardian files and registrations", lambda: verify_guardian(deployment))
        def configuration():
            current = read_json(deployment["hook_config"]).get("hooks", {})
            for event, groups in deployment["managed_hook_groups"].items():
                if any(group not in current.get(event, []) for group in groups):
                    raise ConnectorError("Managed hook is missing or modified: " + event)
            profile = Path(deployment["profile"])
            if deployment["runtime"] == "codex":
                import tomllib
                settings = tomllib.loads((profile / "config.toml").read_text())
                if str(state_dir(profile)) not in settings.get("sandbox_workspace_write", {}).get("writable_roots", []):
                    raise ConnectorError("Private Brain state root is missing from native writable scope")
            result = subprocess.run([sys.executable, str(profile / "skills/brain-framework/scripts/standing_activation.py"), "status", "--runtime", deployment["runtime"]], env={**os.environ, "CODEX_HOME": str(profile)}, capture_output=True, text=True)
            if result.returncode or "activation is enabled in" not in result.stdout:
                raise ConnectorError(result.stderr or result.stdout)
            return {"events": len(current), "registered_handlers": len(deployment["handlers"])}
        check("effective startup and hook registrations", configuration)
    if hooks:
        def handler_contracts():
            results = []
            payload = {"session_id": "connector-host-probe-" + uuid.uuid4().hex, "cwd": deployment["home"], "prompt": "",
                       "tool_name": "Bash", "tool_input": {"command": "printf brain_installation_probe"}, "tool_response": {"stdout": "brain_installation_probe", "exit_code": 0},
                       "stop_hook_active": True, "source": "startup", "agent_type": "worker", "agent_id": "verification-worker"}
            for key, spec in deployment["handlers"].items():
                if spec.get("builtin"):
                    continue
                data = {**payload, "hook_event_name": spec["event"]}
                result = run(deployment, spec["argv"], input_text=json.dumps(data), timeout=min(90, spec.get("timeout", 600)), check=False, session_id=payload["session_id"])
                results.append({"handler": key, "rc": result.returncode, "stderr": result.stderr[-800:]})
            atomic(generation / "handler-probes.json", results)
            bad = [row for row in results if row["rc"]]
            if bad:
                raise ConnectorError("Installed handler probes failed: " + json.dumps(bad)[:2500])
            return {"handlers_executed": len(results), "receiver_delivery": "Not established by direct handler probes"}
        check("original hook hosts execute", handler_contracts)
    if integrated and hooks and os.name == "posix" and deployment["runtime"] == "codex":
        def current_health():
            if not main_host_observer_selected(deployment):
                return {"scope": "The host audit applies to the designated main receiver; selected project checks run separately"}
            from codex_health_observer import bound_snapshot
            dreaming = Path(deployment["home"]) / ".claude/dreaming"
            result = run(deployment, [deployment["python"], "-B", str(dreaming / "hook_liveness.py")],
                         timeout=180, check=False, log=generation / "bound-hook-liveness.log")
            priority_notice = "nice: setpriority: Operation not permitted"
            diagnostics = result.stderr.splitlines()
            unexpected = [line for line in diagnostics if line.strip() and line != priority_notice]
            if result.returncode not in (0, 2) or unexpected:
                raise ConnectorError("Live diagnostic could not be verified: "
                                     + ("\n".join(unexpected) or result.stdout)[-2200:])
            state = bound_snapshot(deployment, dreaming / ".liveness_status.json")
            failures = [*state["dead"], *state["artifacts"], *state["safety"]]
            if failures or result.returncode != 0:
                raise ConnectorError("Live checks unresolved: " + "; ".join(failures or ["observer returned a nonzero status"]))
            return {"observed_epoch": state["ts"], "stream_notices": state["stale"],
                    "source_owner_notes": state["source_owner_notes"],
                    "execution_notices": [priority_notice] if priority_notice in diagnostics else []}

        check("bound live health and safety", current_health)

    from receiver_probe import validate_receivers
    receiver = generation / "receiver-verification.json"
    receiver_data = validate_receivers(deployment) if integrated else {"passed": False}
    report = {"schema": SCHEMA, "at": now(), "deployment": deployment["id"], "revision": deployment["source_revision"],
              "checks": checks, "passed": all(row["passed"] for row in checks),
              "receiver_verified": bool(receiver_data.get("passed")), "receiver_evidence": str(receiver) if receiver.exists() else None,
              "ready": all(row["passed"] for row in checks) and integrated and bool(receiver_data.get("passed")),
              "limits": ["Direct handler probes prove execution, not native runtime delivery or model behavior.", "Optional faculties require their own admission evidence; a disabled faculty is not claimed working."]}
    # A delivered hook chain does not admit copied or unmeasured faculties.
    report["connector_setup_ready"] = report["passed"] and bool(integrated)
    report["core_receiver_verified"] = report["receiver_verified"]
    report["connection_verified"] = report["ready"]
    from setup_checklist import inspect as inspect_setup
    setup = inspect_setup(Path(deployment["profile"]), observation=report, persist=False)
    report["readiness_gaps"] = [
        {"id": item["id"], "mechanism": item["mechanism"],
         "unchecked": [task["requirement"] for task in item["checks"]
                       if task["scope"] == "admission" and task["status"] == "unchecked"]}
        for item in setup["items"] if item["capability"] in setup["selected_capabilities"]
        and any(task["scope"] == "admission" and task["status"] == "unchecked" for task in item["checks"])
    ]
    report["qualification_only"] = setup["admission"]["qualification_only"]
    report["faculties"] = {}
    for capability in sorted({item["capability"] for item in setup["items"]}):
        selected = capability in setup["selected_capabilities"]
        items = [item for item in setup["items"] if item["capability"] == capability]
        missing = [item["id"] for item in items if any(task["scope"] == "admission" and task["status"] == "unchecked"
                                                       for task in item["checks"])]
        report["faculties"][capability] = {
            "selected": selected,
            "selection_basis": setup["selected_capabilities"].get(capability, []),
            "state": "not-selected" if not selected else "configured-awaiting-admission" if missing else "admitted",
            "unchecked_mechanisms": missing,
        }
    report["unrun_live_checks"] = [name for name, enabled in (("native hook probes and live health", hooks),
                                                              ("original installed smoke", smoke)) if not enabled]
    report["ready"] = report["connection_verified"] and not report["readiness_gaps"] and not report["unrun_live_checks"]
    atomic(generation / "verification.json", report)
    return report
