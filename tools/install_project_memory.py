"""Install project-memory runtime files outside Git and optionally register its task."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


def _copy_verified(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(destination.read_bytes()).digest():
        raise RuntimeError("installed runtime byte verification failed: " + destination.name)


def _prune_runtime_package(package_target: Path, allowed_files: set[Path]) -> None:
    if not package_target.exists():
        return
    allowed_dirs = {
        parent
        for relative in allowed_files
        for parent in relative.parents
        if parent != Path(".")
    }
    installed = sorted(
        package_target.rglob("*"),
        key=lambda path: len(path.relative_to(package_target).parts),
        reverse=True,
    )
    for path in installed:
        relative = path.relative_to(package_target)
        if path.is_symlink():
            path.unlink()
        elif path.is_file() and relative not in allowed_files:
            path.unlink()
        elif path.is_dir() and relative not in allowed_dirs:
            try:
                path.rmdir()
            except OSError:
                pass


def install(source_root: Path, runtime_root: Path, state_root: Path,
            compute_state: Path, python_exe: Path) -> Path:
    source_root, runtime_root, state_root = source_root.resolve(), runtime_root.resolve(), state_root.resolve()
    if any((parent / ".git").exists() for parent in (runtime_root, *runtime_root.parents)):
        raise RuntimeError("runtime root must be outside Git")
    package_source = source_root / "dreaming" / "project_memory"
    package_target = runtime_root / "project_memory"
    package_files = [
        source for source in package_source.rglob("*")
        if source.is_file() and "__pycache__" not in source.parts and "tests" not in source.parts
    ]
    allowed_files = {source.relative_to(package_source) for source in package_files}
    _prune_runtime_package(package_target, allowed_files)
    for source in package_files:
        _copy_verified(source, package_target / source.relative_to(package_source))
    _copy_verified(source_root / "dreaming" / "project_memory_cli.py", runtime_root / "project_memory_cli.py")
    _copy_verified(source_root / "dreaming" / "project_memory_hook.py", runtime_root / "project_memory_hook.py")
    _copy_verified(source_root / "dreaming" / "project_memory_hot_hook.py", runtime_root / "project_memory_hot_hook.py")
    _copy_verified(source_root / "dreaming" / "prompt_archive.py", runtime_root / "prompt_archive.py")
    state_root.mkdir(parents=True, exist_ok=True)
    example = state_root / "projects.example.json"
    _copy_verified(package_source / "project_memory.example.json", example)
    scheduler = runtime_root.parent / "scheduler" / "project_memory"
    scheduler.mkdir(parents=True, exist_ok=True)
    pythonw = python_exe.with_name("pythonw.exe") if os.name == "nt" else python_exe
    launcher = scheduler / "hourly.vbs"
    command = '"%s" "%s" hourly --config "%s"' % (
        pythonw, runtime_root / "project_memory_cli.py", state_root / "projects.json")
    vbs = (
        'Dim sh : Set sh = CreateObject("WScript.Shell")\n'
        'sh.CurrentDirectory = "%s"\n'
        'Dim env : Set env = sh.Environment("PROCESS")\n'
        'env("PROJECT_MEMORY_STATE") = "%s"\n'
        'env("PROJECT_MEMORY_CONFIG") = "%s"\n'
        'env("PROJECT_MEMORY_COMPUTE_STATE") = "%s"\n'
        'Dim rc : rc = sh.Run("%s", 0, True)\n'
        'WScript.Quit rc\n'
    ) % (runtime_root, state_root, state_root / "projects.json", compute_state.resolve(),
         command.replace('"', '""'))
    launcher.write_text(vbs, encoding="ascii", newline="\n")
    return launcher


def register_task(launcher: Path, task_name: str) -> None:
    next_hour = (datetime.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    script = """
$ErrorActionPreference='Stop'
$action=New-ScheduledTaskAction -Execute "$env:WINDIR\\System32\\wscript.exe" -Argument ('//B "{launcher}"')
$trigger=New-ScheduledTaskTrigger -Once -At ([datetime]'{start}') -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal=New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -WakeToRun:$false -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 50)
$task=New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName '{task}' -InputObject $task -Force | Out-Null
""".format(launcher=str(launcher).replace("'", "''"), start=next_hour.isoformat(),
           task=task_name.replace("'", "''"))
    script.encode("ascii")
    fd, raw_path = tempfile.mkstemp(prefix="project-memory-task-", suffix=".ps1")
    os.close(fd)
    script_path = Path(raw_path)
    try:
        script_path.write_text(script, encoding="ascii", newline="\n")
        parse_command = (
            "$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
            "'%s',[ref]$t,[ref]$e);if($e.Count){$e|ForEach-Object{Write-Error $_};exit 2}" %
            str(script_path).replace("'", "''")
        )
        flags = 0x08000000 if os.name == "nt" else 0
        parsed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", parse_command],
            creationflags=flags, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if parsed.returncode:
            raise RuntimeError("scheduled-task script failed parser validation: " + parsed.stderr.strip()[-400:])
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script_path)],
            creationflags=flags, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("scheduled-task registration failed: " + result.stderr.strip()[-400:])
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--compute-state", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--register-task", action="store_true")
    parser.add_argument("--task-name", default="BrainFrameworkProjectMemory")
    args = parser.parse_args(argv)
    launcher = install(args.source_root, args.runtime_root, args.state_root,
                       args.compute_state, args.python)
    if args.register_task:
        register_task(launcher, args.task_name)
    print("project-memory runtime installed and byte-verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
