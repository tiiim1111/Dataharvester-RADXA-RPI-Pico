#!/usr/bin/env python3
import os
import subprocess, time, sys
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

BACKING_DEV  = "/dev/mmcblk1p1"  # SD partition to export
GADGET_NAME  = "harvester_msc"

CONFIGFS = Path("/sys/kernel/config")
G_ROOT   = CONFIGFS / "usb_gadget" / GADGET_NAME
UDC_DIR  = Path("/sys/class/udc")

ID_VENDOR    = "0x0525"  # NetChip (common/test VID)
ID_PRODUCT   = "0xa4a5"
BCD_USB      = "0x0200"
BCD_DEVICE   = "0x0100"
MANUFACTURER = "VRTSYSTEMS"
PRODUCT      = "Data Harvester (MSC)"
SERIAL       = "HARVESTERMSC001"

# Logging targets
PRIMARY_LOG_DIR = Path("/var/log/harvester")
PRIMARY_LOG     = PRIMARY_LOG_DIR / "harvester_msc.log"
SECONDARY_LOG   = Path("/mnt/harvester-sd/settings/logs/harvester_msc.log")  # used only if writable

log = logging.getLogger("harvester_msc")

def setup_logging():
    log.setLevel(logging.INFO)

    # Console/journal handler (systemd picks up stdout/stderr)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(sh)

    # Primary rotating file handler
    try:
        PRIMARY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(PRIMARY_LOG, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(fh)
    except Exception as e:
        log.warning(f"Could not set primary file logging at {PRIMARY_LOG}: {e}")

    # Optional secondary handler on the SD card (only if parent is writable)
    try:
        parent = SECONDARY_LOG.parent
        if parent.exists() and os.access(parent, os.W_OK):
            fh2 = RotatingFileHandler(SECONDARY_LOG, maxBytes=2_000_000, backupCount=2)
            fh2.setLevel(logging.INFO)
            fh2.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            log.addHandler(fh2)
        else:
            log.info(f"Secondary log {SECONDARY_LOG} not enabled (directory missing or read-only).")
    except Exception as e:
        log.warning(f"Could not set secondary file logging at {SECONDARY_LOG}: {e}")

def run(cmd, check=True, **kw):
    """Run a command and log it at DEBUG level on failure."""
    res = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, **kw)
    if check and res.returncode != 0:
        log.error(f"Command failed: {' '.join(cmd)} | rc={res.returncode} | stderr={res.stderr.strip()}")
        res.check_returncode()
    else:
        if res.returncode != 0:
            log.warning(f"Command nonzero rc={res.returncode}: {' '.join(cmd)} | stderr={res.stderr.strip()}")
    return res

def echo(path: Path, val: str):
    path.write_text(val)

def ensure_module(name):
    try:
        run(["modprobe", name])
        log.info(f"Loaded module: {name}")
    except subprocess.CalledProcessError:
        log.info(f"Module already present or built-in: {name}")

def ensure_configfs():
    ensure_module("libcomposite")
    run(["mount", "-t", "configfs", "none", str(CONFIGFS)], check=False)
    log.info("configfs ready")

def get_mountpoints_for_device(dev: str):
    out = run(["mount"]).stdout.splitlines()
    mps = []
    for line in out:
        parts = line.split()
        if len(parts) >= 3 and parts[0] == dev:
            mps.append((parts[2], parts[5] if len(parts) > 5 else ""))
    return mps

def remount_ro_if_needed(dev: str):
    for mp, opts in get_mountpoints_for_device(dev):
        if "(rw," in opts or opts == "(rw)":
            log.info(f"Remounting {mp} as read-only to allow PC write access")
            run(["mount", "-o", "remount,ro", mp], check=False)
        else:
            log.info(f"Local mount {mp} already read-only")

def cleanup_gadget():
    if not G_ROOT.exists():
        return
    log.info("Cleaning up any existing gadget state")
    udc = G_ROOT/"UDC"
    if udc.exists():
        try:
            echo(udc, "\n")  # unbind
            log.info("Unbound gadget from UDC")
        except Exception as e:
            log.warning(f"Failed to unbind gadget: {e}")
    cfg = G_ROOT/"configs"/"c.1"
    if cfg.exists():
        for it in list(cfg.iterdir()):
            if it.is_symlink():
                it.unlink(missing_ok=True)
    func = G_ROOT/"functions"/"mass_storage.usb0"
    if func.exists():
        try:
            try:
                echo(func/"lun.0"/"file", "\n")
            except Exception:
                pass
            func.rmdir()
        except Exception as e:
            log.warning(f"Failed removing function dir: {e}")
    # best-effort dir cleanup
    for p in [cfg/"strings"/"0x409", cfg, G_ROOT/"strings"/"0x409", G_ROOT]:
        try:
            if p.exists():
                p.rmdir()
        except Exception:
            pass

def wait_for_udc(timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if UDC_DIR.exists():
            udcs = [p.name for p in UDC_DIR.iterdir() if p.is_dir() or p.is_symlink()]
            if udcs:
                log.info(f"Using UDC: {udcs[0]}")
                return udcs[0]
        time.sleep(0.2)
    raise RuntimeError("No UDC found; use the OTG USB-C port and a data-capable cable")

def setup_gadget():
    log.info("Setting up USB MSC gadget")
    ensure_configfs()
    cleanup_gadget()

    G_ROOT.mkdir(parents=True, exist_ok=True)
    echo(G_ROOT/"idVendor",  ID_VENDOR+"\n")
    echo(G_ROOT/"idProduct", ID_PRODUCT+"\n")
    echo(G_ROOT/"bcdUSB",    BCD_USB+"\n")
    echo(G_ROOT/"bcdDevice", BCD_DEVICE+"\n")

    s = G_ROOT/"strings"/"0x409"
    s.mkdir(parents=True, exist_ok=True)
    echo(s/"manufacturer", MANUFACTURER+"\n")
    echo(s/"product",      PRODUCT+"\n")
    echo(s/"serialnumber", SERIAL+"\n")

    cfg = G_ROOT/"configs"/"c.1"
    cfg.mkdir(parents=True, exist_ok=True)
    cs = cfg/"strings"/"0x409"
    cs.mkdir(parents=True, exist_ok=True)
    echo(cs/"configuration","MSC (PC RW, Radxa RO)\n")
    echo(cfg/"MaxPower","250\n")  # 500 mA

    func = G_ROOT/"functions"/"mass_storage.usb0"
    func.mkdir(parents=True, exist_ok=True)
    try:
        echo(func/"stall","1\n")
    except Exception:
        pass

    lun0 = func/"lun.0"
    echo(lun0/"removable","1\n")
    echo(lun0/"cdrom","0\n")
    echo(lun0/"ro","0\n")                   # Host SIDE = read-write
    echo(lun0/"file", BACKING_DEV+"\n")     # export raw partition
    log.info(f"LUN file -> {BACKING_DEV}")

    link = cfg/"mass_storage.usb0"
    if not link.exists():
        link.symlink_to(func)

    udc = wait_for_udc(15)
    echo(G_ROOT/"UDC", udc+"\n")
    log.info("Gadget bound to UDC; MSC is up")

def main():
    setup_logging()
    log.info("----- harvester_msc starting -----")
    if not Path(BACKING_DEV).exists():
        log.error(f"Backing device not found: {BACKING_DEV}")
        print(f"[harvester_msc] ERROR: {BACKING_DEV} not found.")
        sys.exit(1)

    # Ensure any local mount (if present) is RO before handing RW to the PC
    remount_ro_if_needed(BACKING_DEV)
    try:
        setup_gadget()
        log.info(f"Exporting {BACKING_DEV} via USB MSC (PC RW). Local mounts forced RO.")
        print(f"[harvester_msc] Exporting {BACKING_DEV} via USB MSC (PC RW). Local mounts forced RO.")
    except Exception as e:
        log.exception(f"Failed to set up gadget: {e}")
        print(f"[harvester_msc] ERROR: {e}")
        raise
    finally:
        log.info("----- harvester_msc init complete -----")

if __name__ == "__main__":
    try:
        time.sleep(1.0)
        main()
    except Exception as e:
        # Already logged via log.exception; ensure non-zero exit
        sys.exit(1)
