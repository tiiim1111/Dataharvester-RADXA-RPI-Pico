#!/usr/bin/env python3
"""
Harvester_present.py
Reads mqtt_connection from /mnt/harvester-sd/settings/config.json and publishes a
presence payload to the configured topic. Retries until publish confirmed (QoS1).

Payload example:
{
  "name": "USB-DATA-001",
  "target_folder": "/device001",
  "timestamp": "2025-09-18 19:18:48"
}
"""

import json
import socket
import sys
import time
import threading
import subprocess
from datetime import datetime
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import os

# ====== Paths / defaults ======
MOUNTPOINT   = Path("/mnt/harvester-sd")
CONFIG_FILE  = MOUNTPOINT / "settings" / "config.json"

DEFAULT_NAME          = "USB-DATA-001"
DEFAULT_TARGET_FOLDER = "/device001"

# Log locations (pick first writable)
PRIMARY_LOG_DIR   = Path("/var/log/harvester")
SECONDARY_LOG_DIR = MOUNTPOINT / "settings" / "logs"
TERTIARY_LOG_DIR  = Path.home() / ".harvester" / "logs"

LOG_BASENAME = "harvester_present.log"
STATUS_NAME  = "present_last.json"

# Retry/backoff
RETRY_BASE_SEC = 3
RETRY_MAX_SEC  = 60

log = logging.getLogger("harvester_present")

# ---------------- logging helpers ----------------
def os_access_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".w"
        with open(probe, "w") as f:
            f.write("ok")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False

def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO); sh.setFormatter(fmt)
    log.addHandler(sh)

    chosen = None
    for d in (PRIMARY_LOG_DIR, SECONDARY_LOG_DIR, TERITIARY_LOG_DIR if 'TERITIARY_LOG_DIR' in globals() else TERtiary_log_dir_fallback()):
        if os_access_writable(d):
            chosen = d
            break
    if not chosen:
        chosen = Path.cwd()

    try:
        logfile = chosen / LOG_BASENAME
        fh = RotatingFileHandler(logfile, maxBytes=1_000_000, backupCount=2)
        fh.setLevel(logging.INFO); fh.setFormatter(fmt)
        log.addHandler(fh)
        log.info(f"Logging to {logfile}")
    except Exception as e:
        log.warning(f"File logging unavailable: {e}")

    return chosen  # status JSON goes here too

def TERtiary_log_dir_fallback():
    # tiny helper to avoid NameError above on first eval
    return TERTIARY_LOG_DIR

def write_status(status_dir: Path, state: str, payload: dict, details: str = "", broker: dict | None = None):
    try:
        out = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "state": state,
            "payload": payload,
            "broker": broker or {},
            "details": details,
        }
        (status_dir / STATUS_NAME).write_text(json.dumps(out, indent=2))
    except Exception:
        pass

# ---------------- config & payload ----------------
def refresh_view():
    # best-effort; ignore failures if not root
    subprocess.run(["mount", "-o", "remount,ro", str(MOUNTPOINT)],
                   text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def read_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"{CONFIG_FILE} not found")
    return json.loads(CONFIG_FILE.read_text())

def effective_device_name(cfg: dict, fallback: str) -> str:
    name = (cfg.get("device_name") or "").strip()
    return name if name else fallback

def build_payload(name: str, target_folder: str) -> dict:
    return {
        "name": name,
        "target_folder": target_folder,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

def tcp_probe(host: str, port: int, timeout=5) -> tuple[bool, str]:
    try:
        ip = socket.gethostbyname(host)
    except Exception as e:
        return False, f"DNS resolution failed: {e}"
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        s.close()
        return True, f"TCP OK to {ip}:{port}"
    except Exception as e:
        return False, f"TCP connect failed: {e}"

# ---------------- one attempt ----------------
def attempt_once(status_dir: Path, target_folder: str) -> bool:
    refresh_view()

    # Read config
    try:
        cfg = read_config()
    except Exception as e:
        msg = f"config read error: {e}"
        log.error(msg)
        write_status(status_dir, "error", {}, msg)
        return False

    # device name
    eff_name = effective_device_name(cfg, DEFAULT_NAME)

    # MQTT params
    mq = (cfg.get("mqtt_connection") or {})
    host = (mq.get("host") or "").strip()
    topic = (mq.get("topic") or "").strip()
    port = int(mq.get("port") or 1883)
    username = (mq.get("username") or "").strip()
    password = (mq.get("password") or "").strip()

    broker_info = {"host": host, "port": port, "topic": topic, "username": ("set" if username else "")}

    if not host or not topic:
        msg = f"mqtt_connection.host and mqtt_connection.topic must be set. Got host='{host}', topic='{topic}'"
        log.error(msg)
        write_status(status_dir, "error", {}, msg, broker_info)
        return False

    payload = build_payload(eff_name, target_folder)
    write_status(status_dir, "attempting", payload, "start publish", broker_info)
    log.info(f"Publishing presence to mqtt://{host}:{port} topic='{topic}' payload={payload}")

    # TCP reachability check
    ok, note = tcp_probe(host, port, timeout=5)
    log.info(f"Probe: {note}")
    if not ok:
        write_status(status_dir, "tcp_unreachable", payload, note, broker_info)
        return False

    # Import paho
    try:
        import paho.mqtt.client as mqtt
    except Exception as e:
        msg = f"Missing paho-mqtt (sudo apt install -y python3-paho-mqtt). Error: {e}"
        log.error(msg)
        write_status(status_dir, "error", payload, msg, broker_info)
        return False

    connected = threading.Event()
    published  = threading.Event()
    last_conn_rc = None
    last_disc_rc = None

    def on_connect(client, userdata, flags, rc, properties=None):
        nonlocal last_conn_rc
        last_conn_rc = rc
        log.info(f"on_connect rc={rc} flags={flags}")
        if rc == 0:
            connected.set()
            write_status(status_dir, "connected", payload, f"rc={rc}", broker_info)
        else:
            write_status(status_dir, "connect_error", payload, f"rc={rc}", broker_info)

    def on_disconnect(client, userdata, rc, properties=None):
        nonlocal last_disc_rc
        last_disc_rc = rc
        log.warning(f"on_disconnect rc={rc}")

    def on_publish(client, userdata, mid):
        log.info(f"on_publish mid={mid}")
        published.set()

    client_id = f"harvester-present-{socket.gethostname()}-{os.getpid()}"
    client = mqtt.Client(client_id=client_id, clean_session=True, protocol=mqtt.MQTTv311)
    try:
        client.enable_logger(log)
    except Exception:
        pass

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish

    if username or password:
        client.username_pw_set(username=username or None, password=password or None)

    client.connect_timeout = 10

    # Connect (async handshake)
    try:
        rc = client.connect(host, port=port, keepalive=30)
        if rc != mqtt.MQTT_ERR_SUCCESS:
            msg = f"MQTT connect() returned rc={rc}"
            log.error(msg)
            write_status(status_dir, "connect_error", payload, msg, broker_info)
            return False
    except Exception as e:
        msg = f"Connect exception: {e}"
        log.error(msg)
        write_status(status_dir, "connect_error", payload, msg, broker_info)
        return False

    client.loop_start()

    # Wait for broker ACK (on_connect)
    if not connected.wait(timeout=10):
        msg = f"Connect handshake not confirmed within timeout (last_conn_rc={last_conn_rc})"
        log.error(msg)
        write_status(status_dir, "connect_error", payload, msg, broker_info)
        client.loop_stop(); client.disconnect()
        return False

    # Publish (require QoS1 PUBACK to succeed)
    info = client.publish(topic, json.dumps(payload), qos=1, retain=False)
    success = published.wait(timeout=10)
    if success:
        log.info("Publish OK (QoS1 PUBACK).")
        write_status(status_dir, "published", payload, "QoS1 OK", broker_info)
    else:
        log.error("QoS1 publish not confirmed (PUBACK timeout).")
        write_status(status_dir, "timeout", payload, "QoS1 timeout", broker_info)

    try:
        client.loop_stop()
        client.disconnect()
    except Exception:
        pass

    return bool(success)

# ---------------- main ----------------
def main(name: str = DEFAULT_NAME, target_folder: str = DEFAULT_TARGET_FOLDER):
    status_dir = setup_logging()
    log.info("---- harvester_present start ----")

    # Loop until success
    delay = RETRY_BASE_SEC
    attempt = 1
    while True:
        log.info(f"Attempt #{attempt}")
        ok = attempt_once(status_dir, target_folder)
        if ok:
            log.info("Presence published successfully; exiting.")
            return 0
        log.info(f"Will retry in {delay}s…")
        time.sleep(delay)
        delay = min(delay * 2, RETRY_MAX_SEC)
        attempt += 1

if __name__ == "__main__":
    sys.exit(main())
