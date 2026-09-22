# 📦 backup-mayster — WhoAmI

A pull-based, secure backup automation tool for containerized applications (Podman) running on a remote node, pulling data to a local backup server or NAS.  
Optimized for **homelab stability, container state consistency (pausing), and metrics-driven observability (Prometheus Exporter + Grafana)**.

---

## 🛠️ Features

* **Dual Backup Engines:**
  * **BorgBackup (`engine: "borg"`, Recommended):** Block-level deduplication, Zstandard/LZ4 compression, repository encryption, and granular retention policies (`keep_daily`, `keep_weekly`, `keep_monthly`).
  * **Tarball (`engine: "tar"`, Legacy):** Standalone `.tar.gz` archives transferred via SFTP.
* **Pull-Based Security:** The script runs on the backup target (NAS/local server) and pulls backups from production. The production server has zero access to your backup storage.
* **Minimal Downtime (Rsync Staging):** In Borg mode, data is synced via `rsync` while containers are briefly paused (`podman pause`), and containers are unpaused immediately before Borg deduplication and archiving take place locally on the NAS.
* **Rootless Podman & Docker Support:** Configurable `use_sudo` (globally or per application) seamlessly accommodates Rootless Podman setups without requiring root/sudo permissions.
* **Database & State Consistency:** Containers are paused using `podman pause` / `unpause` during data capture to prevent dirty reads or database file corruption.
* **Pre/Post Executions:** Run custom commands (like `mariadb-dump` or `pg_dump` inside containers) before packaging and clean them up after archiving.
* **Observability:** 
  * Exposes Prometheus metrics via a lightweight custom daemon exporter (`backup-mayster-exporter.py`) with support for multiple servers.
  * Sends formatted Discord status reports (embeds) with original and deduplicated backup sizes.
* **Concurrency Guard:** Unix `flock` locking per configuration file (`backup-mayster-<config>.lock`) allows parallel runs for different hosts, while preventing concurrent duplicate execution. Writes to the metrics file are guarded with flock-based read-modify-write synchronization to prevent race conditions.
* **Multi-Host Orchestration:** Run backups for different environments/servers independently by specifying custom configuration files using the `--config` flag.

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

#### Borg Repository Initialization (When using Borg engine)
If using `engine: "borg"`, initialize the Borg repository on your backup server before running backups:
```bash
# For unencrypted repository (e.g. on a secure local NAS):
borg init --encryption=none /path/to/backup/destination/borg-repo

# OR with encryption (requires passphrase in config.yaml or BORG_PASSPHRASE env):
borg init --encryption=repokey /path/to/backup/destination/borg-repo
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
  # Backup engine: "borg" (recommended: deduplication, encryption) or "tar" (legacy tarball)
  engine: "borg"
  local_dir: "/path/to/backup/destination"
  use_sudo: false # Set to false for Rootless Podman or unprivileged execution
  
  # Borg configuration (used when engine: "borg")
  borg:
    repo_path: "/path/to/backup/destination/borg-repo"
    staging_dir: "/path/to/backup/destination/staging" # Optional, defaults to local_dir/staging
    passphrase: "" # Optional Borg repo passphrase
    compression: "zstd,3" # Compression algorithm (zstd,3, lz4, auto,zstd, none)
    compact: true # Automatically runs 'borg compact' to free disk space after prune
    prune:
      keep_daily: 7
      keep_weekly: 4
      keep_monthly: 6

  # Tar engine options (used when engine: "tar")
  remote_temp_dir: "/tmp"
  retention_days: 7
  sftp_max_retries: 3
  sftp_retry_delay: 5

  container_engine: "podman" # Options: "podman" or "docker" (defaults to "podman")
  server_name: "prod-server"  # Unique label to identify this server in metrics
  log_file: "/var/log/backup-mayster/backup-mayster.log"

metrics:
  enabled: true
  stats_file: "/var/log/backup-mayster/backup-mayster-stats.json"

discord:
  webhook_url: "https://discord.com/api/webhooks/your-webhook-id"
  enabled: true

apps:
  - name: "my-app"
    path: "/opt/containers/my-app"
    use_sudo: false # Optional per-app override
    pause_containers:
      - "my-app-container"
    pre_backup_commands:
      # Optional: Dump DB inside container before zipping
      - "podman exec my-app-db sh -c 'exec mariadb-dump -uroot -p\"$MYSQL_ROOT_PASSWORD\" --all-databases' > /opt/containers/my-app/db_dump.sql"
    post_backup_commands:
      - "rm -f /opt/containers/my-app/db_dump.sql"
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
* `backup_mayster_last_run_timestamp_seconds{server="..."}` — Timestamp of the last global backup run per server.
* `backup_mayster_success{server="..."}` — Global status of the last run per server (1 = OK, 0 = ERR).
* `backup_mayster_app_success{server="...",app="..."}` — Backup status per application and server.
* `backup_mayster_app_duration_seconds{server="...",app="..."}` — Backup execution duration.
* `backup_mayster_app_backup_size_bytes{server="...",app="..."}` — Generated archive file size in bytes (perfect for Grafana size trend forecasting).
* `backup_mayster_app_last_success_timestamp_seconds{server="...",app="..."}` — Keeps track of the last successful backup timestamp per app and server, persisting values on failures to facilitate Alertmanager alerts:
  `time() - backup_mayster_app_last_success_timestamp_seconds > 90000`

---

## 🔄 Restore Guide

### Option A: Restoring from BorgBackup (Recommended)

#### 1. List available archives in repository:
```bash
borg list /path/to/backup/destination/borg-repo
# or filter by app:
borg list /path/to/backup/destination/borg-repo --prefix my-app_
```

#### 2. Extract files:
You can extract files directly from the Borg repository on the backup host:
```bash
# Extract the archive into a temporary restore directory:
mkdir -p /tmp/restore && cd /tmp/restore
borg extract /path/to/backup/destination/borg-repo::my-app_20260922_120000

# Or extract a single file/directory:
borg extract /path/to/backup/destination/borg-repo::my-app_20260922_120000 my-app/docker-compose.yml
```

#### 3. Push restored files back to production:
```bash
rsync -az --delete /tmp/restore/my-app/ backup-user@prod-server:/opt/containers/my-app/
```

#### 4. Restart containers on production:
```bash
ssh backup-user@prod-server "cd /opt/containers/my-app && podman-compose up -d"
```

---

### Option B: Restoring from Tarball Archive (Legacy)

#### 1. Copy the archive to production:
```bash
scp /path/to/backup/destination/my-app/my-app_20260623_161530.tar.gz backup-user@prod-server:/tmp/
```

#### 2. Extract and restore:
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
