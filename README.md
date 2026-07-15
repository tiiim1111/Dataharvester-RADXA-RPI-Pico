# Dataharvester RADXA/RPI Zero

Scripts and service files for a Data Harvester device workflow using a RADXA/Raspberry Pi style Linux host, USB mass-storage gadget mode, mounted SD storage, network setup, MQTT presence, and server-side data sync.

## Features

- USB mass-storage gadget setup for exposing the harvester SD partition to a host computer.
- Default folder provisioning for device data folders such as `cal_log`, `datalog`, `alarms_data`, `settings`, and `unclassified`.
- Continuous rsync-based file synchronization from the mounted harvester SD card to a remote server.
- Automatic cleanup of unexpected top-level files into `unclassified`.
- MQTT presence publishing so the device can announce availability to an external broker.
- NetworkManager Wi-Fi profile generation from `config.json`, including optional static IP settings.
- Watcher/service helpers for applying folder/config changes.
- Systemd unit files for running the harvester workflows as Linux services.
- V2 scripts and manual for the current HARVESTER-003 style configuration.
- Small Node/Ollama AI Agent learning project under `1.2/AI Agent`.

## Capabilities

- Mounts and refreshes `/mnt/harvester-sd` as the main data source.
- Uses `config.json` for device identity, folder paths, Wi-Fi, MQTT, and server sync settings.
- Syncs selected folders to a remote path such as `/mnt/devices/<device_name>/`.
- Supports password-based rsync via `sshpass` when configured.
- Writes operational logs through systemd/journald and rotating log files where supported.
- Keeps generated/runtime-heavy files out of git, including installers, `node_modules`, logs, and environment files.

## Repository Layout

```text
.
|-- V2/                  Current V2 scripts and config template
|-- home/                Earlier/home deployment script set
|-- systemd/             Service unit files
|-- 1.2/                 Versioned working copy and HARVESTER-003 manual
|-- sample_config.json   Sanitized sample configuration
|-- Dependencies.txt     Package/dependency notes
|-- fstab.txt            Mount reference
`-- Locations.txt        Deployment path notes
```

## Configuration

Use the included `sample_config.json` or `V2/config.json` as a starting point, then set device-specific values on the target machine:

- `device_name`
- `network.ssid`
- `network.password`
- `network.static_ip`, `network.gateway`, `network.subnet` when static IP is required
- `mqtt_connection.host`, `mqtt_connection.username`, `mqtt_connection.password`, `mqtt_connection.topic`
- `server_connection.host`, `server_connection.username`, `server_connection.password`, `server_connection.base_dir`

Do not commit real credentials, Wi-Fi passwords, server passwords, tokens, or production broker details.

## Notes

The repository intentionally excludes `node_modules/` and large installer files such as `OllamaSetup.exe`. Reinstall dependencies locally when needed.
