# 📦 backup-mayster — WhoAmI

A pull-based, secure backup automation tool for containerized applications (Podman) running on a remote node, pulling data to a local backup server or NAS.  
Optimized for **homelab stability, container state consistency (pausing), and metrics-driven observability (Prometheus Exporter + Grafana)**.

---

## 🛠️ Features

* **Pull-Based Security:** The script runs on the backup target (NAS/local server) and pulls backups from production. The production server has zero access to your backup storage.
* **Database & State Consistency:** Containers are paused using `podman pause` / `unpause` during archiving to prevent dirty reads or database file corruption without container restarts (zero downtime).
* **Pre/Post Executions:** Run custom commands (like `mariadb-dump` or `pg_dump` inside containers) before packaging and clean them up after archiving.
* **Unix-native `.tar.gz`:** Preserves file ownerships (UID/GID) and permissions, which are critical for mounting container volumes.
* **Configurable Retention:** Automatically cleans up older archives based on a configurable age threshold (in days).
* **Observability:** 
  * Exposes Prometheus metrics via a lightweight custom daemon exporter (`backup-mayster-exporter.py`).
  * Sends formatted Discord status reports (embeds).
* **Concurrency Guard:** Unix `flock` prevention ensures multiple backup instances never overlap.

---

## 🚀 Setup

### 1. Production Server Config (`prod-server`)
The backup server connects via SSH as a standard user (e.g. `backup-user`) and runs administrative tasks (`podman`, `tar`) via `sudo` without passwords.

#### Add backup SSH key
Add the backup server's public SSH key to the remote user's authorized keys:
```bash
ssh-copy-id -i ~/.ssh/id_rsa.pub backup-user@prod-server
```

#### Sudoers permission configuration
Configure `/etc/sudoers.d/backup-mayster` on the production server to allow passwordless execution of only the necessary backup commands:
```sudoers
backup-user ALL=(ALL) NOPASSWD: /usr/bin/tar -czf * -C /opt/containers *
backup-user ALL=(ALL) NOPASSWD: /usr/bin/podman pause *
backup-user ALL=(ALL) NOPASSWD: /usr/bin/podman unpause *
backup-user ALL=(ALL) NOPASSWD: /usr/bin/podman exec *
backup-user ALL=(ALL) NOPASSWD: /usr/bin/rm -f /tmp/*.tar.gz
```

---

### 2. Backup Server Setup (NAS / Local Node)

#### Installation
Clone the repository and prepare the virtual environment:
```bash
cd ~/backup-mayster
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### Log directory setup
Pre-create the logging directory with proper ownership (if running the backup under a non-root user):
```bash
sudo mkdir -p /var/log/backup-mayster
sudo chown -R backup-user:backup-user /var/log/backup-mayster
```

#### Configuration (`config.yaml`)
Create and edit `config.yaml` in the script directory:
```yaml
ssh:
  host: "prod-server.local"
  port: 22
  user: "backup-user"
  key_path: "~/.ssh/id_rsa"

backup:
  local_dir: "/path/to/backup/destination"
  remote_temp_dir: "/tmp"
  retention_days: 7
  log_file: "/var/log/backup-mayster/backup-mayster-stats.log"
  sftp_max_retries: 3
  sftp_retry_delay: 5

metrics:
  enabled: true
  stats_file: "/var/log/backup-mayster/backup-mayster-stats.json"

discord:
  webhook_url: "https://discord.com/api/webhooks/your-webhook-id"
  enabled: true

apps:
  - name: "my-app"
    path: "/opt/containers/my-app"
    pause_containers:
      - "my-app-container"
    pre_backup_commands:
      # Optional: Dump DB inside container before zipping
      - "sudo podman exec my-app-db sh -c 'exec mariadb-dump -uroot -p\"$MYSQL_ROOT_PASSWORD\" --all-databases' > /opt/containers/my-app/db_dump.sql"
    post_backup_commands:
      - "sudo rm -f /opt/containers/my-app/db_dump.sql"
```

---

## 💻 CLI Usage (Running manually)

Show help and configuration arguments:
```bash
venv/bin/python backup-mayster.py --help
```

### Examples:
* **Dry Run (Check setup & configs without making writes or pausing):**
  ```bash
  venv/bin/python backup-mayster.py --dry-run
  ```
* **Run backup for a specific application only:**
  ```bash
  venv/bin/python backup-mayster.py --app my-app
  ```

---

## 📈 Prometheus Exporter Integration

To expose metrics (`http://localhost:9115/metrics`) to Prometheus, run the custom exporter daemon.

#### 1. Automatic installation of Systemd service
Run the script as root to automatically generate, write, and reload the systemd configuration:
```bash
sudo /usr/bin/python3 backup-mayster-exporter.py --install-service --user backup-user
```

#### 2. Start and enable the service
```bash
sudo systemctl enable --now backup-mayster-exporter.service
```

#### Exposed Metrics:
* `backup_mayster_last_run_timestamp_seconds` — Timestamp of the last global backup run.
* `backup_mayster_success` — Global status of the last run (1 = OK, 0 = ERR).
* `backup_mayster_app_success{app="..."}` — Backup status per application.
* `backup_mayster_app_duration_seconds{app="..."}` — Backup execution duration.
* `backup_mayster_app_backup_size_bytes{app="..."}` — Generated archive file size in bytes (perfect for Grafana size trend forecasting).
* `backup_mayster_app_last_success_timestamp_seconds{app="..."}` — Keeps track of the last successful backup timestamp per app, persisting values on failures to facilitate Alertmanager alerts:
  `time() - backup_mayster_app_last_success_timestamp_seconds > 90000`

---

## 🔄 Restore Guide

To restore an application container state on your production node:

### 1. Copy the archive to production:
```bash
scp /path/to/backup/destination/my-app/my-app_20260623_161530.tar.gz backup-user@prod-server:/tmp/
```

### 2. Extract and restore:
Log in to production, stop the target containers, extract (preserving Unix permissions/owners via `-p`), and run compose:
```bash
ssh backup-user@prod-server

cd /opt/containers/my-app
sudo podman-compose down

# Extract preserving file permissions and ownership
sudo tar -xpf /tmp/my-app_20260623_161530.tar.gz -C /opt/containers/

sudo podman-compose up -d
rm -f /tmp/my-app_20260623_161530.tar.gz
```

---

## 🧑 Author

WhoAmI
Linux & DevOps Enthusiast — backup & automation scripts
