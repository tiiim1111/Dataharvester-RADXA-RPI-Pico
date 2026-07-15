#!/usr/bin/env python3
# rsync-based continuous sync (reads server params from config.json)
# + tidy: move top-level outside items into unclassified/

import os, sys, time, subprocess, shutil, signal, json
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

# --------- locations & constants ----------
MOUNTPOINT   = Path("/mnt/harvester-sd")
CONFIG_FILE  = MOUNTPOINT / "settings" / "config.json"

SOURCE_ROOT  = MOUNTPOINT                        # sync only subfolders below
SYNC_FOLDERS = ["cal_log", "datalog", "unclassified", "alarms_data"]

INTERVAL_SECONDS = int(os.environ.get("SYNC_INTERVAL_SECONDS", "10"))
USE_CHECKSUM     = True  # rsync --checksum to catch content changes even if size/mtime match

ALLOWED_TOP = set(SYNC_FOLDERS) | {"settings"}   # keep these at root
SKIP_NAMES  = {"System Volume Information", "$RECYCLE.BIN", "lost+found"}  # never touch

# --------- logging ----------
PRIMARY_LOG_DIR   = Path("/var/log/harvester")
SECONDARY_LOG_DIR = SOURCE_ROOT / "settings" / "logs"
TERTIARY_LOG_DIR  = Path.home() / ".harvester" / "logs"
LOG_FILE          = "harvester_sync.log"

log = logging.getLogger("harvester_sync")

def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".w"; t.write_text("ok"); t.unlink(missing_ok=True)
        return True
    except Exception:
        return False

def _pick_log_dir() -> Path:
    for d in (PRIMARY_LOG_DIR, SECONDARY_LOG_DIR, TERTIARY_LOG_DIR):
        if _writable(d): return d
    return Path.cwd()

def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO); sh.setFormatter(fmt)
    log.addHandler(sh)

    d = _pick_log_dir()
    try:
        fh = RotatingFileHandler(d / LOG_FILE, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(logging.INFO); fh.setFormatter(fmt)
        log.addHandler(fh)
        log.info(f"Logging to {d/LOG_FILE}")
    except Exception as e:
        log.warning(f"File logging unavailable: {e}")
    return d

# --------- helpers ----------
def which(cmd: str) -> str | None:
    return shutil.which(cmd)

def rsync_available() -> bool:
    return which("rsync") is not None

def make_rsync_cmd(base_args: list[str], password: str | None, port: int) -> list[str]:
    ssh = f"ssh -p {port} -o StrictHostKeyChecking=no"
    if password:
        sshpass = which("sshpass")
        if not sshpass:
            log.error("sshpass not found but a password was provided. Install it: sudo apt install -y sshpass")
            return []
        return [sshpass, "-p", password, "rsync"] + base_args + ["-e", ssh]
    else:
        return ["rsync"] + base_args + ["-e", ssh]

def read_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        log.error(f"Cannot read {CONFIG_FILE}: {e}")
        return {}

def effective_device_name(cfg: dict) -> str:
    cfg_name = (cfg.get("device_name") or "").strip()
    if cfg_name:
        return cfg_name
    env_name = (os.environ.get("DEVICE_NAME") or "").strip()
    return env_name if env_name else "USB-DATA-001"

def is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False

def refresh_view():
    """RO remount to make host-side MSC edits visible locally. Safe; requires root."""
    if not is_root():
        if not getattr(refresh_view, "_warned", False):
            log.info("Tip: run as root to refresh SD view; without it, recent MSC edits may be missed.")
            refresh_view._warned = True
        return
    subprocess.run(["mount", "-o", f"remount,ro", str(MOUNTPOINT)],
                   text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def mount_opts(mp: Path) -> str:
    try:
        r = subprocess.run(["findmnt", "-no", "OPTIONS", str(mp)], text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    # fallback parsing of `mount`
    try:
        out = subprocess.run(["mount"], text=True, stdout=subprocess.PIPE, check=False).stdout.splitlines()
        for line in out:
            if f" on {mp} " in line and "(" in line and ")" in line:
                return line[line.rfind("(")+1:line.rfind(")")]
    except Exception:
        pass
    return ""

def is_ro(mp: Path) -> bool:
    opts = mount_opts(mp)
    return "ro" in [o.strip() for o in opts.split(",")] if opts else False

def tidy_root_to_unclassified():
    """
    Move any top-level files/folders at /mnt/harvester-sd that are not in ALLOWED_TOP
    into /mnt/harvester-sd/unclassified. Skips hidden dotfiles and SKIP_NAMES.
    Requires the filesystem to be writable (we do NOT remount RW here).
    """
    if is_ro(MOUNTPOINT):
        log.info("Root FS is read-only; skipping tidy to unclassified.")
        return

    uncls = MOUNTPOINT / "unclassified"
    try:
        uncls.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning(f"Cannot create 'unclassified' directory: {e}")
        return

    moved_count = 0
    try:
        for item in MOUNTPOINT.iterdir():
            name = item.name
            if name in ALLOWED_TOP or name in SKIP_NAMES:
                continue
            if name.startswith("."):
                continue
            # move into unclassified
            dest = uncls / name
            if dest.exists():
                stem, suf = dest.stem, dest.suffix
                ts = time.strftime("%Y%m%d-%H%M%S")
                dest = uncls / f"{stem}_{ts}{suf}"
            try:
                shutil.move(str(item), str(dest))
                moved_count += 1
                log.info(f"Tidied: moved '{name}' -> 'unclassified/{dest.name}'")
            except Exception as e:
                log.warning(f"Failed to move '{name}' to unclassified: {e}")
    except Exception as e:
        log.warning(f"Tidy scan error: {e}")

    if moved_count:
        try: os.sync()
        except Exception: pass
        log.info(f"Tidy complete: moved {moved_count} item(s) into 'unclassified'.")

_stop = False
def handle_signal(signum, frame):
    global _stop
    _stop = True

# --------- main loop ----------
def main():
    setup_logging()

    if not rsync_available():
        log.error("rsync is not installed. Install it: sudo apt install -y rsync")
        return 2

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not _stop:
        if not SOURCE_ROOT.exists():
            log.warning(f"Source root missing: {SOURCE_ROOT}; retrying...")
            time.sleep(5)
            continue

        # See host-side changes & tidy root (if writable)
        refresh_view()
        tidy_root_to_unclassified()

        cfg = read_config()

        # --- server creds from config.json
        sc        = cfg.get("server_connection") or {}
        host      = (sc.get("host") or "").strip()
        user      = (sc.get("username") or "").strip()
        password  = (sc.get("password") or "").strip() or None
        port      = int(sc.get("port") or 22)
        base_dir  = (sc.get("base_dir") or "/mnt/devices").strip().rstrip("/")
        dev_name  = effective_device_name(cfg)
        dest_root = f"{base_dir}/{dev_name}"

        log.info(
            "server_connection: host=%s port=%s user=%s password=%s base_dir=%s device_name=%s",
            host or "<none>", port, user or "<none>", "<set>" if password else "<empty>", base_dir, dev_name
        )
        log.info("dest_root: %s | source_root: %s | folders=%s | interval_seconds=%s | checksum=%s",
                 dest_root, SOURCE_ROOT, SYNC_FOLDERS, INTERVAL_SECONDS, USE_CHECKSUM)

        if not host or not user:
            log.warning("Missing host/username in config.json; will retry…")
            time.sleep(5)
            continue

        # for folder in SYNC_FOLDERS:
        #     src_dir = SOURCE_ROOT / folder
        #     if not src_dir.exists():
        #         continue

        #     dest_dir = f"{dest_root}/{folder}"
        #     dest     = f"{user}@{host}:{dest_dir}/"

        #     base_args = [
        #         "-a", "-z",
        #         "-v", "--itemize-changes",
        #         "--protect-args",
        #         "--no-owner", "--no-group", "--no-perms",
        #         "--omit-dir-times",
        #         "--exclude=~$*",  # skip temp/locked files
        #         "--rsync-path", f"mkdir -p '{dest_dir}' && rsync",
        #     ]
        #     if USE_CHECKSUM:
        #         base_args.append("--checksum")

        #     cmd = make_rsync_cmd(base_args, password, port)
        #     if not cmd:
        #         time.sleep(INTERVAL_SECONDS)
        #         break

        #     full_cmd = cmd + [str(src_dir) + "/", dest]
        #     log.info(f"[{folder}] rsync -> {dest}")
        #     try:
        #         res = subprocess.run(full_cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        #         if res.returncode != 0:
        #             log.error(f"rsync failed (rc={res.returncode}) for {folder}: {res.stderr.strip()}")
        #         else:
        #             if res.stdout.strip():
        #                 log.info(f"rsync output:\n{res.stdout.strip()[:2000]}")
        #     except Exception as e:
        #         log.error(f"rsync invocation error for {folder}: {e}")
        
        for folder in SYNC_FOLDERS:
            src_dir = SOURCE_ROOT / folder
            if not src_dir.exists():
                continue

            dest_dir    = f"{dest_root}/{folder}"
            backup_dir  = f"{dest_root}/.backups/{folder}"
            dest        = f"{user}@{host}:{dest_dir}/"

            # rsync will:
            # - keep latest version in dest_dir
            # - move overwritten/changed files into backup_dir
            base_args = [
                "-a", "-z",
                "-v", "--itemize-changes",
                "--protect-args",
                "--no-owner", "--no-group", "--no-perms",
                "--omit-dir-times",
                "--exclude=~$*",      # skip temp/locked files

                # backup/versioning behavior:
                "--backup",
                "--backup-dir", backup_dir,

                # ensure both dest_dir and backup_dir exist on remote:
                "--rsync-path", f"mkdir -p '{dest_dir}' '{backup_dir}' && rsync",
            ]

            # If you want default rsync behavior (size+mtime), you can drop checksum:
            # if USE_CHECKSUM:
            #     base_args.append("--checksum")

            cmd = make_rsync_cmd(base_args, password, port)
            if not cmd:
                time.sleep(INTERVAL_SECONDS)
                break

            full_cmd = cmd + [str(src_dir) + "/", dest]
            log.info(f"[{folder}] rsync -> {dest}")
            try:
                res = subprocess.run(full_cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if res.returncode != 0:
                    log.error(f"rsync failed (rc={res.returncode}) for {folder}: {res.stderr.strip()}")
                else:
                    if res.stdout.strip():
                        log.info(f"rsync output:\n{res.stdout.strip()[:2000]}")
            except Exception as e:
                log.error(f"rsync invocation error for {folder}: {e}")

        slept = 0
        while slept < INTERVAL_SECONDS and not _stop:
            time.sleep(1); slept += 1

    log.info("harvester_sync exiting on signal.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
