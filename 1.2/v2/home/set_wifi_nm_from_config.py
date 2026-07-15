#!/usr/bin/env python3
import json, os, subprocess, sys, uuid
from pathlib import Path

MOUNTPOINT  = Path("/mnt/harvester-sd")
CONFIG_FILE = MOUNTPOINT / "settings" / "config.json"

NM_DIR      = Path("/etc/NetworkManager/system-connections")
NM_FILE     = NM_DIR / "harvester-wifi.nmconnection"

DEFAULT_IFACE = "wlan0"

def run(cmd, check=False):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)

def ensure_nmcli():
    r = run(["nmcli", "--version"])
    if r.returncode != 0:
        print("[ERR] nmcli / NetworkManager is not available on this system.")
        sys.exit(3)

def mask_to_prefix(mask: str) -> int:
    try:
        parts = [int(x) for x in mask.split(".")]
        bits = "".join(f"{p:08b}" for p in parts)
        return bits.count("1")
    except Exception:
        return 24

def detect_wifi_iface() -> str:
    # Prefer nmcli; fall back to wlan0
    r = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])
    for line in r.stdout.splitlines():
        dev, typ, _ = (line.split(":") + ["",""])[:3]
        if typ == "wifi":
            return dev
    return DEFAULT_IFACE

def load_cfg() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        print(f"[ERR] Cannot read {CONFIG_FILE}: {e}")
        sys.exit(2)

def read_existing_uuid(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        for line in path.read_text().splitlines():
            if line.strip().startswith("uuid="):
                return line.split("=",1)[1].strip()
    except Exception:
        pass
    return None

def build_nmconnection_text(uuid_str: str, iface: str, ssid: str, password: str,
                            static_ip: str, gateway: str, subnet: str) -> str:
    ipv4_section = []
    if static_ip:
        prefix = mask_to_prefix(subnet or "255.255.255.0")
        addr1  = f"{static_ip}/{prefix}" + (f",{gateway}" if gateway else "")
        ipv4_section += [
            "[ipv4]",
            "method=manual",
            f"address1={addr1}",
            "dns=8.8.8.8;1.1.1.1;",
            "",
        ]
    else:
        ipv4_section += [
            "[ipv4]",
            "method=auto",
            "",
        ]

    wifi_sec = []
    if password:
        wifi_sec = [
            "[wifi-security]",
            "key-mgmt=wpa-psk",
            f"psk={password}",
            "",
        ]
    # For open networks, omit [wifi-security] section entirely

    return "\n".join([
        "[connection]",
        "id=harvester-wifi",
        f"uuid={uuid_str}",
        "type=wifi",
        "autoconnect=true",
        "autoconnect-priority=100",
        f"interface-name={iface}",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={ssid}",
        "",
        *wifi_sec,
        *ipv4_section,
        "[ipv6]",
        "addr-gen-mode=default",
        "method=ignore",
        "",
        "[proxy]",
        "",
    ])

def safe_write_nm_file(text: str):
    NM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = NM_FILE.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, NM_FILE)
    # NM requires root:root and 600
    try:
        import pwd, grp
        os.chown(NM_FILE, pwd.getpwnam("root").pw_uid, grp.getgrnam("root").gr_gid)
    except Exception:
        pass
    os.chmod(NM_FILE, 0o600)

def cleanup_dupe_files():
    # Remove stale copies like harvester-wifi.nmconnection.XYZ
    for p in NM_DIR.glob("harvester-wifi.nmconnection.*"):
        try:
            p.unlink()
        except Exception:
            pass

def apply_and_activate(iface: str, static_ip: str | None):
    # Make NM reload file, then force this connection up
    run(["nmcli", "con", "reload"])
    # Drop any *other* wifi profiles with same SSID/other IDs if desired (optional)
    # We delete only duplicates of our id to avoid nuking user's profiles:
    r = run(["nmcli", "-t", "-f", "NAME,UUID,TYPE", "con", "show"])
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) != 3: continue
        name, cuuid, ctype = parts
        if ctype == "wifi" and name == "harvester-wifi":
            # keep our current one (by UUID from file); remove others named the same
            file_uuid = read_existing_uuid(NM_FILE)
            if cuuid != file_uuid:
                run(["nmcli", "con", "delete", cuuid])

    # Bring the target profile up first.
    # This is safer than disconnecting/flushing the active Wi-Fi before
    # we know the new config can actually connect.
    up = run(["nmcli", "con", "up", "harvester-wifi", "ifname", iface])

    if up.returncode != 0:
        print(f"[WARN] nmcli up failed: {up.stderr.strip()}")
        return

    # After a successful connect, disconnect other active Wi-Fi profiles on the same iface.
    active = run(["nmcli", "-t", "-f", "NAME,DEVICE,TYPE,STATE", "con", "show", "--active"])
    for line in active.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        name, dev, ctype, state = parts[:4]
        if dev == iface and ctype == "802-11-wireless" and state == "activated" and name != "harvester-wifi":
            run(["nmcli", "con", "down", name])

    # Confirm IP (for static)
    if static_ip:
        chk = run(["ip", "-4", "addr", "show", "dev", iface]).stdout
        if static_ip not in chk:
            print(f"[WARN] {iface} does not show {static_ip} yet.")
        else:
            print(f"[OK] {iface} is now {static_ip}")

def main():
    if os.geteuid() != 0:
        print("Run as root: sudo /usr/bin/python3 /home/radxa/scripts/set_wifi_nm_from_config.py")
        sys.exit(1)

    ensure_nmcli()
    cfg = load_cfg()
    net = cfg.get("network") or {}
    ssid      = (net.get("ssid") or "").strip()
    password  = (net.get("password") or "").strip()
    static_ip = (net.get("static_ip") or "").strip()
    gateway   = (net.get("gateway") or "").strip()
    subnet    = (net.get("subnet") or "").strip()

    if not ssid:
        print("[ERR] 'network.ssid' is required.")
        sys.exit(2)

    iface = detect_wifi_iface()
    old_uuid = read_existing_uuid(NM_FILE)
    uuid_str = old_uuid or str(uuid.uuid4())

    text = build_nmconnection_text(uuid_str, iface, ssid, password, static_ip, gateway, subnet)
    safe_write_nm_file(text)
    cleanup_dupe_files()

    print(f"[INFO] Wrote {NM_FILE} (iface={iface}, static={'yes' if static_ip else 'no'})")
    apply_and_activate(iface, static_ip or None)

    # Helpful output
    print("\n-- status --")
    print(run(["nmcli", "-t", "-f", "NAME,DEVICE,STATE", "con", "show", "--active"]).stdout.strip())
    print(run(["ip", "-4", "addr", "show", "dev", iface]).stdout.strip())
    print(run(["ip", "route"]).stdout.strip())

if __name__ == "__main__":
    main()
