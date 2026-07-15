#!/usr/bin/env python3
"""
harvester_watcher.py — keep /mnt/harvester-sd sane.

- Detects missing settings/config.json or required folders
- Calls harvester_msc_default_folders.py only when fixes are needed
- If not root, tries sudo -n to elevate (no prompt); logs a clear hint if sudoers not set
- Uses inotifywait if present, else polls
"""

import os, time, json, shutil, subprocess, sys, logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

MOUNTPOINT   = Path("/mnt/harvester-sd")
CONFIG_FILE  = MOUNTPOINT / "settings" / "config.json"
LOGS_DIR     = MOUNTPOINT / "settings" / "logs"
APPLY_SCRIPT = Path("/home/radxa/harvester_msc_default_folders.py")

BASE_REQUIRED = ["cal_log", "datalog", "unclassified", "settings", "settings/logs"]

PRIMARY_LOG_DIR   = Path("/var/log/harvester")
SECONDARY_LOG_DIR = LOGS_DIR
TERTIARY_LOG_DIR  = Path.home() / ".harvester" / "logs"
LOG_FILE          = "harvester_watcher.log"

DEBOUNCE_SECONDS = 8

log = logging.getLogger("harvester_watcher")

# ---------- logging ----------
def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".w"; t.write_text("ok"); t.unlink(missing_ok=True)
        return True
    except Exception:
        return False

def _pick_log_dir() -> Path:
    for d in (PRIMARY_LOG_DIR, SECONDARY_LOG_DIR, TERITIARY_LOG_DIR := TERTIARY_LOG_DIR):
        if _writable(d): return d
    return Path.cwd()

def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    sh = logging.StreamHandler(sys.stderr); sh.setLevel(logging.INFO); sh.setFormatter(fmt)
    log.addHandler(sh)

    d = _pick_log_dir()
    try:
        fh = RotatingFileHandler(d / LOG_FILE, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(logging.INFO); fh.setFormatter(fmt)
        log.addHandler(fh)
        log.info(f"Logging to {d/LOG_FILE}")
    except Exception as e:
        log.warning(f"File logging unavailable: {e}")

# ---------- helpers ----------
def is_mount_ready() -> bool:
    try:
        return MOUNTPOINT.exists() and os.path.ismount(MOUNTPOINT)
    except Exception:
        return MOUNTPOINT.exists()

def desired_required_dirs() -> set[str]:
    wanted = set(BASE_REQUIRED)
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text())
            for p in (data.get("folders", {}).get("paths", []) or []):
                if isinstance(p, str) and p.strip():
                    wanted.add(p.strip())
    except Exception:
        pass
    return wanted

def missing_dirs(wanted: set[str]) -> list[str]:
    return [rel for rel in sorted(wanted) if not (MOUNTPOINT / rel).exists()]

def needs_fix() -> tuple[bool, dict]:
    details = {"reason": [], "missing_dirs": []}
    if not CONFIG_FILE.exists():
        details["reason"].append("config_missing")
    want = desired_required_dirs()
    miss = missing_dirs(want)
    if miss:
        details["reason"].append("dirs_missing")
        details["missing_dirs"] = miss
    return (bool(details["reason"]), details)

def is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False

_last_apply = 0.0
def maybe_apply():
    global _last_apply
    ok, info = needs_fix()
    if not ok:
        return False
    now = time.time()
    if now - _last_apply < DEBOUNCE_SECONDS:
        log.info(f"Fix needed but debounced ({int(DEBOUNCE_SECONDS - (now - _last_apply))}s). Info: {info}")
        return False
    _last_apply = now

    cmd = ["/usr/bin/env", "python3", str(APPLY_SCRIPT)]
    if not is_root():
        # try non-interactive sudo; if not permitted, we’ll log a helpful hint
        cmd = ["sudo", "-n"] + cmd

    log.info(f"Running default-fix: reasons={info.get('reason')} missing_dirs={info.get('missing_dirs')}")
    res = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        if not is_root() and res.returncode == 1 and "a password is required" in (res.stderr.lower() or ""):
            log.error(
                "Elevation failed: sudo requires a password. "
                "Run once as root: sudo -E /usr/bin/env python3 /home/radxa/harvester_msc_default_folders.py\n"
                "Or run this watcher as a root systemd service (see unit file below), "
                "or grant passwordless sudo for this script:\n"
                "  echo 'radxa ALL=(root) NOPASSWD: /usr/bin/env python3 /home/radxa/harvester_msc_default_folders.py' | sudo tee /etc/sudoers.d/harvester\n"
            )
        else:
            log.error(f"default_folders apply failed rc={res.returncode}: {res.stderr.strip()}")
        if res.stdout.strip():
            log.info(res.stdout.strip())
        return False
    else:
        out = res.stdout.strip()
        if out:
            log.info(f"default_folders output:\n{out}")
        return True

def use_inotify() -> bool:
    return shutil.which("inotifywait") is not None

def watch_inotify():
    log.info("Using inotifywait on /mnt/harvester-sd and /mnt/harvester-sd/settings")
    args = [
        "inotifywait", "-m",
        "-e", "create", "-e", "delete", "-e", "move", "-e", "attrib", "-e", "close_write",
        str(MOUNTPOINT), str(MOUNTPOINT / "settings")
    ]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert p.stdout is not None
    try:
        for line in p.stdout:
            if not is_mount_ready():
                continue
            if "config.json" in line or " DELETE " in line or " MOVED_FROM " in line or " MOVED_TO " in line or " CREATE " in line:
                maybe_apply()
    finally:
        p.terminate()
        try: p.wait(timeout=2)
        except Exception: pass

def watch_poll():
    log.info("inotifywait not found; polling every 5s")
    while True:
        try:
            if is_mount_ready():
                maybe_apply()
        except Exception as e:
            log.warning(f"poll error: {e}")
        time.sleep(5)

def main():
    setup_logging()
    log.info("----- harvester_watcher starting -----")
    if is_mount_ready():
        maybe_apply()
    else:
        log.info("Mount not ready yet; watcher will wait…")

    if use_inotify():
        watch_inotify()
    else:
        watch_poll()

if __name__ == "__main__":
    main()
