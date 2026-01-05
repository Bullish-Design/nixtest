#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
import fcntl

CONFIG_PATH = Path("/etc/devenv-harness/config.json")


@dataclass(frozen=True)
class HarnessConfig:
    slots: int
    container_prefix: str
    state_dir: Path
    artifacts_dir_default: str


def load_config() -> HarnessConfig:
    data = json.loads(CONFIG_PATH.read_text())
    return HarnessConfig(
        slots=int(data["slots"]),
        container_prefix=str(data["containerPrefix"]),
        state_dir=Path(data["stateDir"]),
        artifacts_dir_default=str(data.get("artifactsDirDefault", "./artifacts")),
    )


def is_root() -> bool:
    return os.geteuid() == 0


def with_root(cmd: list[str]) -> list[str]:
    if is_root():
        return cmd
    return ["sudo", "--"] + cmd


def slot_paths(cfg: HarnessConfig, slot: int) -> tuple[str, Path, Path, Path]:
    name = f"{cfg.container_prefix}{slot}"
    slot_dir = cfg.state_dir / "slots" / str(slot)
    work_dir = slot_dir / "work"
    lock_path = slot_dir / "slot.lock"
    return name, slot_dir, work_dir, lock_path


class SlotLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.fd = None

    def try_lock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.lock_path, "a+")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            self.fd.close()
            self.fd = None
            return False

    def unlock(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            self.fd.close()
            self.fd = None


async def run_cmd(cmd: list[str], *, cwd: Path | None = None, log_file: Path | None = None) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "ab") as f:
                f.write(line)
        else:
            sys.stdout.buffer.write(line)
            sys.stdout.flush()
    return await proc.wait()


async def rsync_project(src: Path, dst: Path, log: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)

    cmd = with_root([
        "rsync",
        "-a",
        "--delete",
        "--safe-links",
        "--exclude", ".direnv/",
        "--exclude", "result",
        "--exclude", ".git/objects/",
        f"{src}/",
        f"{dst}/",
    ])
    rc = await run_cmd(cmd, log_file=log)
    if rc != 0:
        raise RuntimeError(f"rsync failed with exit code {rc}")


async def container_start(name: str, log: Path) -> None:
    rc = await run_cmd(with_root(["nixos-container", "start", name]), log_file=log)
    if rc != 0:
        raise RuntimeError(f"failed to start container {name}")


async def container_stop(name: str, log: Path) -> None:
    await run_cmd(with_root(["nixos-container", "stop", name]), log_file=log)


async def container_run(name: str, shell_cmd: str, log: Path) -> int:
    inner = f"cd /work && {shell_cmd}"
    cmd = with_root(["nixos-container", "run", name, "--", "bash", "-lc", inner])
    return await run_cmd(cmd, log_file=log)


async def acquire_slot(cfg: HarnessConfig, wait: bool) -> tuple[int, SlotLock]:
    while True:
        for slot in range(1, cfg.slots + 1):
            _, _, _, lock_path = slot_paths(cfg, slot)
            lk = SlotLock(lock_path)
            if lk.try_lock():
                return slot, lk
        if not wait:
            raise RuntimeError("no free slots available")
        await asyncio.sleep(0.2)


def run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"


async def main() -> int:
    p = argparse.ArgumentParser(prog="devenv-harness")
    p.add_argument("--project", default=".", help="Project root to snapshot into the slot workspace")
    p.add_argument("--artifacts", default=None, help="Artifacts output directory (default from config)")
    p.add_argument("--cmd", action="append", required=True, help="Command to run in the container (repeatable)")
    p.add_argument("--no-wait", action="store_true", help="Fail immediately if no slot is available")
    p.add_argument("--keep-workdir", action="store_true", help="Do not wipe workdir after run (debug)")
    args = p.parse_args()

    cfg = load_config()
    artifacts_root = Path(args.artifacts or cfg.artifacts_dir_default).resolve()
    project = Path(args.project).resolve()

    slot, lk = await acquire_slot(cfg, wait=not args.no_wait)
    name, slot_dir, work_dir, _ = slot_paths(cfg, slot)
    rid = run_id()

    out_dir = artifacts_root / name / rid
    out_dir.mkdir(parents=True, exist_ok=True)

    lifecycle_log = out_dir / "lifecycle.log"
    rsync_log = out_dir / "rsync.log"

    try:
        if work_dir.exists():
            await run_cmd(with_root(["rm", "-rf", str(work_dir)]), log_file=lifecycle_log)
        work_dir.mkdir(parents=True, exist_ok=True)

        await rsync_project(project, work_dir, rsync_log)

        await container_start(name, lifecycle_log)

        for idx, cmd in enumerate(args.cmd, start=1):
            cmd_log = out_dir / f"cmd-{idx:02d}.log"
            rc = await container_run(name, cmd, cmd_log)
            if rc != 0:
                (out_dir / "FAILED").write_text(
                    f"Command {idx} failed: {cmd}\nexit={rc}\n"
                )
                return rc

        (out_dir / "OK").write_text("success\n")
        return 0

    finally:
        await container_stop(name, lifecycle_log)
        if not args.keep_workdir:
            await run_cmd(with_root(["rm", "-rf", str(work_dir)]), log_file=lifecycle_log)
        lk.unlock()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
