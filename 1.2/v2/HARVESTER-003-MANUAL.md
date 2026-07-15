# Harvester-003 Deployment Manual

This document captures the exact setup used on `Harvester-003` so it can be replicated to more devices.

## 1. Initial Setup

sudo apt update
sudo mkdir scripts
cd scripts
sudo apt install -y \
  network-manager \
  rsync sshpass openssh-client \
  inotify-tools \
  python3-paho-mqtt \
  kmod util-linux iproute2 \
  exfatprogs

sudo systemctl enable --now sys-kernel-config.mount

### 1.1 Install ZeroTier
```bash
curl -s https://install.zerotier.com | sudo bash
```

### 1.2 Join ZeroTier Network
```bash
sudo zerotier-cli join 8286ac0e47dbef77
```

## 2. SD Mount Setup

### 2.1 Add to `/etc/fstab`
sudo nano /etc/fstab
Add this line:
```fstab
/dev/mmcblk0p3   /mnt/harvester-sd   exfat   defaults,nofail,uid=1000,gid=1000,umask=000   0 0
```

### 2.2 Create mountpoint and reboot
```bash
sudo mkdir -p /mnt/harvester-sd
sudo reboot
```

## 3. USB Gadget Mode Setup

This is for Raspberry Pi Zero 2 W.

Important:
- Use the `USB` micro-USB port for data/OTG.
- The `PWR IN` port is power only.
- `PWR IN` cannot be converted into a data port by software.

### 3.1 Edit `/boot/firmware/config.txt`
sudo nano /boot/firmware/config.txt
Use:
```ini
[all]
dtoverlay=dwc2,dr_mode=peripheral
```

Important note:
- `dtoverlay=dwc2,dr_mode=peripheral` must be under `[all]`.
- Putting it under `[cm5]` will not apply on Pi Zero 2 W.

### 3.2 Edit `/boot/firmware/cmdline.txt`
sudo nano /boot/firmware/cmdline.txt
Ensure the line contains:
```text
modules-load=dwc2
```

Example:
```text
console=serial0,115200 console=tty1 root=PARTUUID=1ef0e27a-02 rootfstype=ext4 fsck.repair=yes rootwait modules-load=dwc2 ds=nocloud;i=rpi-imager-1774987248694 cfg80211.ieee80211_regdom=PH
```

### 3.3 Reboot and verify
```bash
sudo reboot
```

After reboot:
```bash
ls /sys/class/udc
lsmod | grep dwc2
```

Expected:
- `ls /sys/class/udc` should show a UDC entry
- `dwc2` should be loaded

## 4. Script Deployment Path

All active scripts should be placed in:
```bash
/home/radxa/scripts
```

## 5. `harvester_msc.py`

Purpose:
- Export `/dev/mmcblk0p3` as USB mass storage to PC

Run manually for testing:
```bash
sudo python3 /home/radxa/scripts/harvester_msc.py
```

Expected use:
- Run as `root`
- Usually started by systemd, not by regular user

## 6. `harvester-msc.service`

Create:
```bash
sudo nano /etc/systemd/system/harvester-msc.service
```

Contents:
```ini
[Unit]
Description=USB Mass Storage Gadget (PC RW, Radxa RO)
After=local-fs.target sys-kernel-config.mount
Wants=sys-kernel-config

[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 /home/radxa/scripts/harvester_msc.py
ExecStop=/bin/sh -c 'G=/sys/kernel/config/usb_gadget/harvester_msc; [ -e "$G/UDC" ] && echo > "$G/UDC" || true'
RemainAfterExit=yes
TimeoutStartSec=40s

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable harvester-msc.service
sudo systemctl start harvester-msc.service
sudo systemctl status harvester-msc.service --no-pager
```

## 7. `harvester_msc_default_folders.py`

Purpose:
- Create default folders on `/mnt/harvester-sd`
- Create/update `settings/config.json`
- Restore expected folder structure if missing

Default folders:
- `cal_log`
- `datalog`
- `unclassified`
- `settings`
- `settings/logs`
- `alarms_data`

## 8. `harvester-default-folders.service`

Create:
```bash
sudo nano /etc/systemd/system/harvester-default-folders.service
```

Contents:
```ini
[Unit]
Description=Apply default folders and config
After=local-fs.target harvester-msc.service
Requires=harvester-msc.service

[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 /home/radxa/scripts/harvester_msc_default_folders.py

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable harvester-default-folders.service
sudo systemctl restart harvester-default-folders.service
sudo systemctl status harvester-default-folders.service --no-pager
```

sudo reboot

## 9. `harvester_present.py`

Purpose:
- Publish device presence to MQTT
- Uses `mqtt_connection` from `config.json`
- Uses `device_name` from `config.json`
- Falls back to `USB-DATA-001` if `device_name` is blank

Manual test:
```bash
sudo python3 /home/radxa/scripts/harvester_present.py
```

Dependency note:
- Requires valid MQTT settings in `config.json`
- Requires `python3-paho-mqtt`

Install dependency if needed:
```bash
sudo apt install -y python3-paho-mqtt
```

## 10. `harvester-present.service`

Create:
```bash
sudo nano /etc/systemd/system/harvester-present.service
```

Contents:
```ini
[Unit]
Description=Publish harvester presence to MQTT (retry until success, then exit)
Wants=network-online.target
After=network-online.target
RequiresMountsFor=/mnt/harvester-sd

[Service]
Type=simple
User=root
Group=root
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/env python3 /home/radxa/scripts/harvester_present.py
Restart=on-failure
RestartSec=5
Nice=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable harvester-present.service
sudo systemctl start harvester-present.service
sudo systemctl status harvester-present.service --no-pager
```

## 11. `harvester_watcher.py`

Purpose:
- Watches `/mnt/harvester-sd`
- If `config.json` or required folders are missing, runs `harvester_msc_default_folders.py`
- Acts as an automatic self-healing watcher

Manual test:
```bash
sudo python3 /home/radxa/scripts/harvester_watcher.py
```

Normal behavior:
- It stays running
- It may appear idle after startup
- That is normal

## 12. `harvester-watcher.service`

Create:
```bash
sudo nano /etc/systemd/system/harvester-watcher.service
```

Contents:
```ini
[Unit]
Description=Harvester SD card watcher (repair folders/config if missing)
After=local-fs.target harvester-msc.service
Wants=local-fs.target

[Service]
Type=simple
ExecStart=/usr/bin/env python3 /home/radxa/scripts/harvester_watcher.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable harvester-watcher.service
sudo systemctl start harvester-watcher.service
sudo systemctl status harvester-watcher.service --no-pager
```

## 13. `harvester_sync.py`

Purpose:
- Continuously sync data folders from `/mnt/harvester-sd` to remote server via `rsync`
- Syncs:
  - `cal_log`
  - `datalog`
  - `unclassified`
  - `alarms_data`
- Stores older overwritten versions in remote `.backups`

Base used:
- `harvester_sync (4).py` was treated as the latest base version

Important hardcoded value for `Harvester-003`:
```python
SD_DEVICE = "/dev/mmcblk0p3"
```

Manual test:
```bash
sudo python3 /home/radxa/scripts/harvester_sync.py
```

Required packages:
```bash
sudo apt install -y rsync
sudo apt install -y sshpass
```

## 14. `harvester-sync.service`

Create:
```bash
sudo nano /etc/systemd/system/harvester-sync.service
```

Contents:
```ini
[Unit]
Description=Harvester continuous rsync to server
Wants=network-online.target
After=network-online.target
RequiresMountsFor=/mnt/harvester-sd

[Service]
Type=simple
User=root
Group=root
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/env python3 /home/radxa/scripts/harvester_sync.py
Restart=always
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=15
Nice=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable harvester-sync.service
sudo systemctl start harvester-sync.service
sudo systemctl status harvester-sync.service --no-pager
```

## 15. `set_wifi_nm_from_config.py`

Purpose:
- Reads `network` from `config.json`
- Creates NetworkManager profile
- Applies Wi-Fi settings to `wlan0`

Important note:
- Uses `nmcli`
- This works on Raspberry Pi OS Lite if `NetworkManager` is installed and active

Manual test:
```bash
sudo python3 /home/radxa/scripts/set_wifi_nm_from_config.py
```

Safer behavior used in `v2`:
- Do not disconnect current Wi-Fi before proving the new profile can connect

## 16. `harvester-wifi.service`

Create:
```bash
sudo nano /etc/systemd/system/harvester-wifi.service
```

Contents:
```ini
[Unit]
Description=Apply Wi-Fi from config.json to NetworkManager
Wants=network-pre.target NetworkManager.service
After=network-pre.target NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /home/radxa/scripts/set_wifi_nm_from_config.py
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable harvester-wifi.service
sudo systemctl start harvester-wifi.service
sudo systemctl status harvester-wifi.service --no-pager
```

## 17. `config.json` Required Values

These values must be reviewed per device.

### 17.1 Device identity
```json
"device_name": "Harvester-003"
```

If blank:
- `harvester_present.py` falls back to `USB-DATA-001`
- `harvester_sync.py` also falls back to `USB-DATA-001`

### 17.2 Network section
Example:
```json
"network": {
  "gateway": "192.168.0.1",
  "note": "If using DHCP, leave static_ip, gateway, and subnet blank.",
  "password": "WIFI_PASSWORD",
  "ssid": "WIFI_SSID",
  "static_ip": "192.168.0.184",
  "subnet": "255.255.0.0"
}
```

Note:
- If using DHCP, leave `static_ip`, `gateway`, and `subnet` blank

### 17.3 MQTT section
Example:
```json
"mqtt_connection": {
  "host": "MQTT_BROKER_HOST",
  "password": "MQTT_PASSWORD",
  "port": 1883,
  "topic": "USB_DEVICES",
  "username": "MQTT_USERNAME"
}
```

### 17.4 Server sync section
Example:
```json
"server_connection": {
  "base_dir": "/mnt/devices",
  "host": "SYNC_SERVER_HOST",
  "password": "SYNC_SERVER_PASSWORD",
  "username": "SYNC_SERVER_USERNAME"
}
```

## 18. Hardcoded Values To Review Per Device

### 18.1 In `harvester_sync.py`
```python
SD_DEVICE = "/dev/mmcblk0p3"
```

### 18.2 In `config.json`
Change per device as needed:
- `device_name`
- `network.ssid`
- `network.password`
- `network.static_ip`
- `network.gateway`
- `network.subnet`
- `server_connection.host`
- `server_connection.username`
- `server_connection.password`
- `server_connection.base_dir`
- `mqtt_connection.host`
- `mqtt_connection.port`
- `mqtt_connection.username`
- `mqtt_connection.password`
- `mqtt_connection.topic`

## 19. Validation Checklist

### 19.1 USB gadget check
```bash
ls /sys/class/udc
sudo systemctl status harvester-msc.service --no-pager
```

### 19.2 Mount check
```bash
mount | grep harvester-sd
ls /mnt/harvester-sd
```

### 19.3 Wi-Fi check
```bash
nmcli device status
nmcli connection show --active
ip -4 addr show dev wlan0
ip route
hostname -I
```

### 19.4 MQTT presence check
```bash
sudo systemctl status harvester-present.service --no-pager
```

### 19.5 Sync check
```bash
sudo systemctl status harvester-sync.service --no-pager
journalctl -u harvester-sync.service --no-pager -n 50
```

## 20. Emergency Recovery

Put this section at the end of the final manual.

### 20.1 Recover keyboard access on Pi Zero 2 W

If USB gadget mode prevents keyboard use:

Edit `/boot/firmware/config.txt`:
```ini
dtoverlay=dwc2,dr_mode=host
```

Edit `/boot/firmware/cmdline.txt` and remove:
```text
modules-load=dwc2
```

Then reboot.

Important:
- Use `USB` port for keyboard/data
- `PWR IN` is power only

### 20.2 Switch back to gadget mode later
Edit `/boot/firmware/config.txt`:
```ini
dtoverlay=dwc2,dr_mode=peripheral
```

Edit `/boot/firmware/cmdline.txt` and add back:
```text
modules-load=dwc2
```

Then reboot.

### 20.3 Recover Wi-Fi manually
```bash
nmcli device wifi list
sudo nmcli device wifi connect "YOUR_WIFI_NAME" password "YOUR_PASSWORD"
nmcli device status
hostname -I
```

### 20.4 Recover SSH
On device:
```bash
hostname -I
nmcli device status
systemctl status ssh --no-pager
sudo systemctl enable --now ssh
ss -ltnp | grep :22
```

### 20.5 Notes
- `connection refused` usually means the IP is reachable but SSH is not listening
- `wlan0 disconnected` means Wi-Fi exists but is not currently connected
- `wlan0` was not removed by the reviewed scripts

## 21. Notes To Append

Add future device-specific notes here as they come up.
