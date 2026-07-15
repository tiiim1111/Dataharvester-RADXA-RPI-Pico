#!/usr/bin/env python3
"""
Default folders + config bootstrap/apply (idempotent, MSC-safe).

- Ensures dirs: cal_log, datalog, unclassified, settings, settings/logs
- Ensures settings/config.json matches your schema (only writes if different)
- Creates any extra folders listed in folders.paths
- Moves stray top-level FILES into unclassified/
- Only unbinds gadget + remounts RW if there is an actual change to write

Adds defaults:
- server_connection.base_dir = "/mnt/devices"
- device_name = ""  (blank by default)
- sync.source = "/mnt/harvester-sd"
"""

import os, json, shutil, subprocess, sys, time, logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---- PATHS / CONSTANTS ----
MOUNTPOINT   = Path("/mnt/harvester-sd")
CONFIG_FILE  = MOUNTPOINT / "settings" / "config.json"
LOGS_DIR     = MOUNTPOINT / "settings" / "logs"
GADGET_DIR   = Path("/sys/kernel/config/usb_gadget/harvester_msc")

BASE_FOLDER_PATHS = ["cal_log", "datalog", "unclassified", "settings", "settings/logs", "alarms_data"]
SKIP_NAMES = {"System Volume Information", "$RECYCLE.BIN"}  # don't touch OS metadata

# Logging
PRIMARY_LOG_DIR = Path("/var/log/harvester")
PRIMARY_LOG     = PRIMARY_LOG_DIR / "harvester_default_folders.log"
SECONDARY_LOG   = LOGS_DIR / "harvester_default_folders.log"

log = logging.getLogger("harvester_default_folders")

# ----------------- logging -----------------
def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO); sh.setFormatter(fmt)
    log.addHandler(sh)

    try:
        PRIMARY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(PRIMARY_LOG, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(logging.INFO); fh.setFormatter(fmt)
        log.addHandler(fh)
    except Exception as e:
        log.warning(f"Primary file logging unavailable: {e}")

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        if os.access(LOGS_DIR, os.W_OK):
            fh2 = RotatingFileHandler(SECONDARY_LOG, maxBytes=2_000_000, backupCount=2)
            fh2.setLevel(logging.INFO); fh2.setFormatter(fmt)
            log.addHandler(fh2)
        else:
            log.info("Secondary log path not writable; skipping SD mirror.")
    except Exception as e:
        log.info(f"Secondary log disabled: {e}")

# ----------------- helpers -----------------
def run(cmd, check=True, **kw):
    r = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, **kw)
    if check and r.returncode != 0:
        log.error(f"Command failed rc={r.returncode}: {' '.join(cmd)} | {r.stderr.strip()}")
        r.check_returncode()
    return r

def mount_opts(mp: Path) -> str:
    r = run(["findmnt", "-no", "OPTIONS", str(mp)], check=False)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    out = run(["mount"], check=False).stdout.splitlines()
    for line in out:
        if f" on {mp} " in line and "(" in line and ")" in line:
            return line[line.rfind("(")+1:line.rfind(")")]
    return ""

def is_ro(mp: Path) -> bool:
    opts = mount_opts(mp)
    return "ro" in [o.strip() for o in opts.split(",")] if opts else False

def remount(mp: Path, mode: str):
    run(["mount", "-o", f"remount,{mode}", str(mp)], check=False)

def gadget_bound_udc() -> str:
    udc = GADGET_DIR / "UDC"
    if udc.exists():
        return udc.read_text().strip()
    return ""

def unbind_gadget():
    udc = GADGET_DIR / "UDC"
    if udc.exists():
        try:
            udc.write_text("\n"); log.info("Gadget unbound")
        except Exception as e:
            log.warning(f"Unbind gadget failed: {e}")

def bind_gadget(saved: str):
    if not saved: return
    udc = GADGET_DIR / "UDC"
    if udc.exists():
        try:
            udc.write_text(saved + "\n"); log.info(f"Gadget rebound to {saved}")
        except Exception as e:
            log.warning(f"Rebind gadget failed: {e}")

def default_folder_descriptions() -> dict:
    return {
        "cal_log": "Top-level calibration logs",
        "datalog": "Top-level data logs",
        "unclassified": "Auto-sorted files that were at root",
        "settings": "Configuration & logs for the device",
        "settings/logs": "Script and device logs",
    }

def merge_paths_preserve_order(existing_paths, defaults):
    result, seen = [], set()
    for p in (existing_paths or []):
        if isinstance(p, str):
            s = p.strip()
            if s and s not in seen:
                result.append(s); seen.add(s)
    for p in defaults:
        if p not in seen:
            result.append(p); seen.add(p)
    return result

# ----------------- config build (idempotent) -----------------
def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}

def canonical_dump(d: dict) -> str:
    # stable ordering so we can compare safely
    return json.dumps(d, indent=2, sort_keys=True)

def target_config_from(existing: dict | None) -> dict:
    """
    Build the desired config from existing, migrating to your schema:
      - version 1.0.0
      - folders: descriptions + paths[] + note
      - sync block (source defaults to /mnt/harvester-sd)
      - network block with note
      - server_connection (adds base_dir="/mnt/devices")
      - mqtt_connection with topic
      - device_name (top-level, default "")
    Only sets created_at on first creation; preserves updated_at if present.
    """
    cfg = existing.copy() if isinstance(existing, dict) else {}

    # legacy rename: server -> server_connection
    if "server" in cfg and "server_connection" not in cfg:
        cfg["server_connection"] = cfg.pop("server")

    # version & timestamps
    cfg.setdefault("version", "1.0.0")
    if existing is None:
        cfg.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    # preserve any existing updated_at; don't auto-change here

    # device_name (top-level)
    cfg.setdefault("device_name", "")  # blank by default

    # folders (descriptions + paths + note)
    folders = cfg.get("folders", {})
    desc = default_folder_descriptions()
    for k, v in desc.items():
        folders.setdefault(k, v)
    paths = folders.get("paths", [])
    if not isinstance(paths, list): paths = []
    folders["paths"] = merge_paths_preserve_order(paths, BASE_FOLDER_PATHS)
    folders.setdefault("note", "Add folders here and restart (or let watcher apply) to take effect.")
    cfg["folders"] = folders

    # sync (default to mount root, not just datalog)
    sync = cfg.get("sync", {})
    sync.setdefault("source", "/mnt/harvester-sd")
    sync.setdefault("server_url", "")
    auth = sync.get("auth", {})
    auth.setdefault("user", ""); auth.setdefault("token", "")
    sync["auth"] = auth
    sync.setdefault("enabled", True)
    cfg["sync"] = sync

    # network
    net = cfg.get("network", {})
    net.setdefault("ssid", ""); net.setdefault("password", "")
    net.setdefault("static_ip", ""); net.setdefault("gateway", ""); net.setdefault("subnet", "")
    net.setdefault("note", "If using DHCP, leave static_ip, gateway, and subnet blank.")
    cfg["network"] = net

    # server_connection (now with base_dir default)
    sc = cfg.get("server_connection", {})
    sc.setdefault("host",""); sc.setdefault("username",""); sc.setdefault("password","")
    sc.setdefault("base_dir","/mnt/devices")  # << added default
    cfg["server_connection"] = sc

    # mqtt_connection (with topic)
    mq = cfg.get("mqtt_connection", {})
    mq.setdefault("host",""); mq.setdefault("port",1883)
    mq.setdefault("username",""); mq.setdefault("password",""); mq.setdefault("topic","")
    cfg["mqtt_connection"] = mq

    return cfg

def plan_config_write() -> tuple[bool, str]:
    """
    Returns (needs_write, target_text).
    Only requests a write if content actually differs.
    """
    if CONFIG_FILE.exists():
        try:
            current = json.loads(CONFIG_FILE.read_text())
        except Exception:
            current = {}
        target = target_config_from(current)
        cur_txt = canonical_dump(current)
        tgt_txt = canonical_dump(target)
        return (cur_txt != tgt_txt, tgt_txt)
    else:
        target = target_config_from(None)
        return (True, canonical_dump(target))

def write_config(text: str):
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text)
    os.replace(tmp, CONFIG_FILE)

# ----------------- folders & files -----------------
def desired_dirs_from_config() -> set[str]:
    wanted = set(BASE_FOLDER_PATHS)
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text())
            for p in (data.get("folders", {}).get("paths", []) or []):
                if isinstance(p, str) and p.strip():
                    wanted.add(p.strip())
    except Exception:
        pass
    return wanted

def list_missing_dirs(wanted: set[str]) -> list[Path]:
    return [MOUNTPOINT / rel for rel in sorted(wanted) if not (MOUNTPOINT / rel).exists()]

def list_stray_files() -> list[Path]:
    keep = set(BASE_FOLDER_PATHS) | SKIP_NAMES
    items = []
    for item in MOUNTPOINT.iterdir():
        if item.is_file() and item.name not in keep and not item.name.startswith("."):
            items.append(item)
    return items

def ensure_dirs(paths: list[Path]):
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)
        try: os.chmod(p, 0o777)
        except Exception: pass
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try: os.chmod(LOGS_DIR, 0o777)
    except Exception: pass

def move_loose_files(files: list[Path]) -> int:
    moved = 0
    target = MOUNTPOINT / "unclassified"
    target.mkdir(parents=True, exist_ok=True)
    for item in files:
        try:
            dest = target / item.name
            if dest.exists():
                stem, ext = dest.stem, dest.suffix
                ts = time.strftime("%Y%m%d-%H%M%S")
                dest = target / f"{stem}_{ts}{ext}"
            shutil.move(str(item), str(dest))
            moved += 1
        except Exception as e:
            log.warning(f"Move failed for {item.name}: {e}")
    return moved

# ----------------- APPLY (plan first; only unbind if needed) -----------------
def apply_all():
    if not MOUNTPOINT.exists():
        log.error(f"Mountpoint missing: {MOUNTPOINT}")
        sys.exit(1)

    # Plan (read-only)
    cfg_needs_write, cfg_text = plan_config_write()
    wanted_dirs = desired_dirs_from_config()
    missing = list_missing_dirs(wanted_dirs)
    strays  = list_stray_files()

    if not (cfg_needs_write or missing or strays):
        log.info("No changes needed; skipping maintenance (no unbind/rebind).")
        return

    bound = gadget_bound_udc()
    was_ro = is_ro(MOUNTPOINT)

    try:
        if was_ro:
            log.info("Entering maintenance: unbind gadget + remount RW")
            unbind_gadget(); time.sleep(0.2)
            remount(MOUNTPOINT, "rw"); time.sleep(0.1)

        if cfg_needs_write:
            write_config(cfg_text)
            log.info("Config created/updated")

        if missing:
            ensure_dirs(missing)
            log.info(f"Created directories: {[str(p.relative_to(MOUNTPOINT)) for p in missing]}")

        if strays:
            n = move_loose_files(strays)
            log.info(f"Moved {n} stray file(s) into 'unclassified'")

        try: os.sync()
        except Exception: pass

    finally:
        if was_ro:
            remount(MOUNTPOINT, "ro"); time.sleep(0.1)
            bind_gadget(bound)
            log.info("Restored RO and gadget binding")

# ----------------- main -----------------
def main():
    setup_logging()
    log.info("----- default_folders apply start -----")
    apply_all()
    log.info("----- default_folders apply done -----")

if __name__ == "__main__":
    main()
