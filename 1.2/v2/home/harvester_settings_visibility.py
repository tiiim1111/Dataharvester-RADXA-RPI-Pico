#!/usr/bin/env python3
import os, subprocess, sys, time, shutil
from pathlib import Path

MOUNTPOINT   = Path("/mnt/harvester-sd")
SETTINGS_DIR = MOUNTPOINT / "settings"
ENABLE_FILE  = MOUNTPOINT / "config_enable.txt"
GADGET_DIR   = Path("/sys/kernel/config/usb_gadget/harvester_msc")

def run(cmd, env=None, check=False):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=check)
#!/usr/bin/env python3
import os, subprocess, sys, time, shutil
from pathlib import Path

MOUNTPOINT   = Path("/mnt/harvester-sd")
SETTINGS_DIR = MOUNTPOINT / "settings"
ENABLE_FILE  = MOUNTPOINT / "config_enable.txt"
GADGET_DIR   = Path("/sys/kernel/config/usb_gadget/harvester_msc")

def run(cmd, env=None, check=False):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=check)

def mount_opts(mp: Path) -> str:
    r = run(["findmnt", "-no", "OPTIONS", str(mp)])
    return r.stdout.strip()

def is_ro(mp: Path) -> bool:
    opts = mount_opts(mp)
    return "ro" in [o.strip() for o in (opts.split(",") if opts else [])]

def remount(mp: Path, mode: str):
    run(["mount", "-o", f"remount,{mode}", str(mp)])

def gadget_bound_udc() -> str:
    udc = GADGET_DIR / "UDC"
    return udc.read_text().strip() if udc.exists() else ""

def unbind_gadget():
    udc = GADGET_DIR / "UDC"
    if udc.exists():
        try: udc.write_text("\n")
        except Exception as e: print(f"[WARN] Unbind gadget failed: {e}")

def bind_gadget(name: str):
    if not name: return
    udc = GADGET_DIR / "UDC"
    if udc.exists():
        try: udc.write_text(name + "\n")
        except Exception as e: print(f"[WARN] Rebind gadget failed: {e}")

def fstype_of_mount(mp: Path) -> str:
    r = run(["findmnt", "-no", "FSTYPE", str(mp)])
    return r.stdout.strip().lower()

def device_of_mount(mp: Path) -> str:
    r = run(["findmnt", "-no", "SOURCE", str(mp)])
    return r.stdout.strip()

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def mattrib_one(dev: str, hide: bool, path_dos: str, env=None) -> tuple[bool, str]:
    flags = ["+h","+s"] if hide else ["-h","-s"]
    r = run(["mattrib", "-i", dev, *flags, path_dos], env=env)
    ok = (r.returncode == 0)
    msg = r.stderr.strip() or r.stdout.strip()
    return ok, msg

def mattrib_try_variants(dev: str, hide: bool) -> bool:
    """
    Try multiple ways that often fix mtools quirks:
      - ::/settings  and  ::/SETTINGS
      - with and without MTOOLS_SKIP_CHECK=1
    """
    tries = []
    base_env = os.environ.copy()
    skip_env = base_env.copy(); skip_env["MTOOLS_SKIP_CHECK"]="1"

    tries.append(("::/settings", None))
    tries.append(("::/SETTINGS", None))
    tries.append(("::/settings", skip_env))
    tries.append(("::/SETTINGS", skip_env))

    for path, env in tries:
        ok, msg = mattrib_one(dev, hide, path, env=env)
        print(f"[DEBUG] mattrib {('+h +s' if hide else '-h -s')} {path} "
              f"{'(MTOOLS_SKIP_CHECK=1)' if env and env.get('MTOOLS_SKIP_CHECK')=='1' else ''} -> "
              f"{'OK' if ok else 'FAIL'}{(': ' + msg) if msg else ''}")
        if ok:
            return True
    return False

def ensure_rw_then(fn):
    was_ro = is_ro(MOUNTPOINT)
    bound = gadget_bound_udc()
    try:
        if was_ro:
            unbind_gadget(); time.sleep(0.2)
            remount(MOUNTPOINT, "rw"); time.sleep(0.1)
        return fn()
    finally:
        if was_ro:
            remount(MOUNTPOINT, "ro"); time.sleep(0.1)
            bind_gadget(bound)

def visible_target() -> bool:
    return ENABLE_FILE.exists()

def main():
    # Basic diagnostics up front
    if not MOUNTPOINT.exists():
        print(f"[ERR] Mountpoint missing: {MOUNTPOINT}")
        return 2
    if not SETTINGS_DIR.exists():
        print(f"[INFO] {SETTINGS_DIR} not present; nothing to do.")
        return 0

    fs = fstype_of_mount(MOUNTPOINT)
    dev = device_of_mount(MOUNTPOINT)
    print(f"[INFO] fstype={fs or '<unknown>'} device={dev or '<unknown>'} mattrib={'yes' if have('mattrib') else 'no'}")
    want_visible = visible_target()
    print(f"[INFO] target={'UNHIDE' if want_visible else 'HIDE'} (config_enable.txt {'present' if want_visible else 'absent'})")

    if fs not in ("vfat","fat","exfat"):
        print("[INFO] Filesystem is not FAT/exFAT; DOS hidden attributes are unsupported here. Leaving as-is.")
        return 0
    if not have("mattrib"):
        print("[INFO] mtools (mattrib) not installed. Install with: sudo apt install -y mtools")
        return 0

    def work():
        ok = mattrib_try_variants(dev, hide=(not want_visible))
        if ok:
            print(f"[OK] {'Unhid' if want_visible else 'Hid'} settings via DOS attributes.")
            return 0
        else:
            print("[WARN] mtools attribute change failed; falling back.")
            print("[INFO] No attribute method available; leaving as-is.")
            return 0

    rc = ensure_rw_then(work)
    return rc if isinstance(rc, int) else 0

if __name__ == "__main__":
    sys.exit(main())

def mount_opts(mp: Path) -> str:
    r = run(["findmnt", "-no", "OPTIONS", str(mp)])
    return r.stdout.strip()

def is_ro(mp: Path) -> bool:
    opts = mount_opts(mp)
    return "ro" in [o.strip() for o in (opts.split(",") if opts else [])]

def remount(mp: Path, mode: str):
    run(["mount", "-o", f"remount,{mode}", str(mp)])

def gadget_bound_udc() -> str:
    udc = GADGET_DIR / "UDC"
    return udc.read_text().strip() if udc.exists() else ""

def unbind_gadget():
    udc = GADGET_DIR / "UDC"
    if udc.exists():
        try: udc.write_text("\n")
        except Exception as e: print(f"[WARN] Unbind gadget failed: {e}")

def bind_gadget(name: str):
    if not name: return
    udc = GADGET_DIR / "UDC"
    if udc.exists():
        try: udc.write_text(name + "\n")
        except Exception as e: print(f"[WARN] Rebind gadget failed: {e}")

def fstype_of_mount(mp: Path) -> str:
    r = run(["findmnt", "-no", "FSTYPE", str(mp)])
    return r.stdout.strip().lower()

def device_of_mount(mp: Path) -> str:
    r = run(["findmnt", "-no", "SOURCE", str(mp)])
    return r.stdout.strip()

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def mattrib_one(dev: str, hide: bool, path_dos: str, env=None) -> tuple[bool, str]:
    flags = ["+h","+s"] if hide else ["-h","-s"]
    r = run(["mattrib", "-i", dev, *flags, path_dos], env=env)
    ok = (r.returncode == 0)
    msg = r.stderr.strip() or r.stdout.strip()
    return ok, msg

def mattrib_try_variants(dev: str, hide: bool) -> bool:
    """
    Try multiple ways that often fix mtools quirks:
      - ::/settings  and  ::/SETTINGS
      - with and without MTOOLS_SKIP_CHECK=1
    """
    tries = []
    base_env = os.environ.copy()
    skip_env = base_env.copy(); skip_env["MTOOLS_SKIP_CHECK"]="1"

    tries.append(("::/settings", None))
    tries.append(("::/SETTINGS", None))
    tries.append(("::/settings", skip_env))
    tries.append(("::/SETTINGS", skip_env))

    for path, env in tries:
        ok, msg = mattrib_one(dev, hide, path, env=env)
        print(f"[DEBUG] mattrib {('+h +s' if hide else '-h -s')} {path} "
              f"{'(MTOOLS_SKIP_CHECK=1)' if env and env.get('MTOOLS_SKIP_CHECK')=='1' else ''} -> "
              f"{'OK' if ok else 'FAIL'}{(': ' + msg) if msg else ''}")
        if ok:
            return True
    return False

def ensure_rw_then(fn):
    was_ro = is_ro(MOUNTPOINT)
    bound = gadget_bound_udc()
    try:
        if was_ro:
            unbind_gadget(); time.sleep(0.2)
            remount(MOUNTPOINT, "rw"); time.sleep(0.1)
        return fn()
    finally:
        if was_ro:
            remount(MOUNTPOINT, "ro"); time.sleep(0.1)
            bind_gadget(bound)

def visible_target() -> bool:
    return ENABLE_FILE.exists()

def main():
    # Basic diagnostics up front
    if not MOUNTPOINT.exists():
        print(f"[ERR] Mountpoint missing: {MOUNTPOINT}")
        return 2
    if not SETTINGS_DIR.exists():
        print(f"[INFO] {SETTINGS_DIR} not present; nothing to do.")
        return 0

    fs = fstype_of_mount(MOUNTPOINT)
    dev = device_of_mount(MOUNTPOINT)
    print(f"[INFO] fstype={fs or '<unknown>'} device={dev or '<unknown>'} mattrib={'yes' if have('mattrib') else 'no'}")
    want_visible = visible_target()
    print(f"[INFO] target={'UNHIDE' if want_visible else 'HIDE'} (config_enable.txt {'present' if want_visible else 'absent'})")

    if fs not in ("vfat","fat","exfat"):
        print("[INFO] Filesystem is not FAT/exFAT; DOS hidden attributes are unsupported here. Leaving as-is.")
        return 0
    if not have("mattrib"):
        print("[INFO] mtools (mattrib) not installed. Install with: sudo apt install -y mtools")
        return 0

    def work():
        ok = mattrib_try_variants(dev, hide=(not want_visible))
        if ok:
            print(f"[OK] {'Unhid' if want_visible else 'Hid'} settings via DOS attributes.")
            return 0
        else:
            print("[WARN] mtools attribute change failed; falling back.")
            print("[INFO] No attribute method available; leaving as-is.")
            return 0

    rc = ensure_rw_then(work)
    return rc if isinstance(rc, int) else 0

if __name__ == "__main__":
    sys.exit(main())
