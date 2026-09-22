#!/usr/bin/env python3
import os
import sys
import time
import logging
from datetime import datetime, timezone
import argparse
import fcntl
import json
import re
import subprocess
import shutil
import yaml
import paramiko
import requests

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("backup-mayster")

def load_config(config_path):
    """Loads configuration from a YAML file."""
    try:
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
            return cfg if cfg is not None else {}
    except Exception as e:
        logger.error(f"Failed to load config file: {e}")
        sys.exit(1)

def validate_config(config):
    """Validates the configuration dictionary for required keys and types."""
    if config is None:
        logger.error("Configuration error: Configuration is empty or None.")
        sys.exit(1)
        return
    if not isinstance(config, dict):
        logger.error("Configuration error: Configuration must be a YAML dictionary.")
        sys.exit(1)
        return
        
    ssh_cfg = config.get('ssh')
    if ssh_cfg is None:
        logger.error("Configuration error: Missing 'ssh' section.")
        sys.exit(1)
        return
    if not isinstance(ssh_cfg, dict):
        logger.error("Configuration error: 'ssh' section must be a dictionary.")
        sys.exit(1)
        return
    if not ssh_cfg.get('host'):
        logger.error("Configuration error: 'ssh.host' is required.")
        sys.exit(1)
        return
    if not ssh_cfg.get('user'):
        logger.error("Configuration error: 'ssh.user' is required.")
        sys.exit(1)
        return
        
    backup_cfg = config.get('backup')
    if backup_cfg is None:
        logger.error("Configuration error: Missing 'backup' section.")
        sys.exit(1)
        return
    if not isinstance(backup_cfg, dict):
        logger.error("Configuration error: 'backup' section must be a dictionary.")
        sys.exit(1)
        return
    if not backup_cfg.get('local_dir'):
        logger.error("Configuration error: 'backup.local_dir' is required.")
        sys.exit(1)
        return
    if backup_cfg.get('engine') is not None:
        engine = backup_cfg.get('engine')
        if engine not in ['tar', 'borg']:
            logger.error(f"Configuration error: 'backup.engine' must be 'tar' or 'borg'. Got: '{engine}'")
            sys.exit(1)
            return
    if backup_cfg.get('use_sudo') is not None:
        if not isinstance(backup_cfg.get('use_sudo'), bool):
            logger.error("Configuration error: 'backup.use_sudo' must be a boolean.")
            sys.exit(1)
            return
    if backup_cfg.get('container_engine') is not None:
        engine = backup_cfg.get('container_engine')
        if engine not in ['podman', 'docker']:
            logger.error(f"Configuration error: 'backup.container_engine' must be 'podman' or 'docker'. Got: '{engine}'")
            sys.exit(1)
            return
    if backup_cfg.get('server_name') is not None:
        sname = backup_cfg.get('server_name')
        if not isinstance(sname, str) or not sname:
            logger.error("Configuration error: 'backup.server_name' must be a non-empty string.")
            sys.exit(1)
            return
        
    apps = config.get('apps')
    if apps is None:
        logger.error("Configuration error: Missing 'apps' section.")
        sys.exit(1)
        return
    if not isinstance(apps, list):
        logger.error("Configuration error: 'apps' section must be a list.")
        sys.exit(1)
        return
    if not apps:
        logger.error("Configuration error: 'apps' list cannot be empty.")
        sys.exit(1)
        return
        
    for idx, app in enumerate(apps):
        if not isinstance(app, dict):
            logger.error(f"Configuration error: app at index {idx} must be a dictionary.")
            sys.exit(1)
            return
        if not app.get('name'):
            logger.error(f"Configuration error: app at index {idx} is missing 'name'.")
            sys.exit(1)
            return
        if not app.get('path'):
            logger.error(f"Configuration error: app '{app.get('name')}' is missing 'path'.")
            sys.exit(1)
            return
        if app.get('container_engine') is not None:
            app_eng = app.get('container_engine')
            if app_eng not in ['podman', 'docker']:
                logger.error(f"Configuration error: app '{app.get('name')}' has invalid 'container_engine': '{app_eng}'. Must be 'podman' or 'docker'.")
                sys.exit(1)
                return
        if app.get('engine') is not None:
            app_engine_type = app.get('engine')
            if app_engine_type not in ['tar', 'borg']:
                logger.error(f"Configuration error: app '{app.get('name')}' has invalid 'engine': '{app_engine_type}'. Must be 'tar' or 'borg'.")
                sys.exit(1)
                return
        if app.get('use_sudo') is not None:
            if not isinstance(app.get('use_sudo'), bool):
                logger.error(f"Configuration error: app '{app.get('name')}' has invalid 'use_sudo'. Must be a boolean.")
                sys.exit(1)
                return

    global_engine_type = backup_cfg.get('engine', 'tar')
    any_borg = (global_engine_type == 'borg') or any(a.get('engine') == 'borg' for a in apps if isinstance(a, dict))
    if any_borg:
        borg_cfg = backup_cfg.get('borg')
        if borg_cfg is None:
            logger.error("Configuration error: 'backup.borg' section is required when engine is 'borg'.")
            sys.exit(1)
            return
        if not isinstance(borg_cfg, dict):
            logger.error("Configuration error: 'backup.borg' must be a dictionary.")
            sys.exit(1)
            return
        if not borg_cfg.get('repo_path'):
            logger.error("Configuration error: 'backup.borg.repo_path' is required when engine is 'borg'.")
            sys.exit(1)
            return
        if not isinstance(borg_cfg.get('repo_path'), str):
            logger.error("Configuration error: 'backup.borg.repo_path' must be a string path.")
            sys.exit(1)
            return
            
    metrics_cfg = config.get('metrics')
    if metrics_cfg is not None:
        if not isinstance(metrics_cfg, dict):
            logger.error("Configuration error: 'metrics' section must be a dictionary.")
            sys.exit(1)
            return
        if metrics_cfg.get('enabled'):
            stats_file = metrics_cfg.get('stats_file')
            if not stats_file:
                logger.error("Configuration error: 'metrics.stats_file' is required when metrics are enabled.")
                sys.exit(1)
                return
            if not isinstance(stats_file, str):
                logger.error("Configuration error: 'metrics.stats_file' must be a string path.")
                sys.exit(1)
                return
                
    discord_cfg = config.get('discord')
    if discord_cfg is not None:
        if not isinstance(discord_cfg, dict):
            logger.error("Configuration error: 'discord' section must be a dictionary.")
            sys.exit(1)
            return
        if discord_cfg.get('enabled'):
            webhook_url = discord_cfg.get('webhook_url')
            if not webhook_url:
                logger.error("Configuration error: 'discord.webhook_url' is required when discord notifications are enabled.")
                sys.exit(1)
                return
            if not isinstance(webhook_url, str):
                logger.error("Configuration error: 'discord.webhook_url' must be a string URL.")
                sys.exit(1)
                return

def run_ssh_command(ssh_client, command):
    """Runs a command on the remote server via SSH."""
    logger.info(f"Executing remote command: {command}")
    stdin, stdout, stderr = ssh_client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8').strip()
    err = stderr.read().decode('utf-8').strip()
    return exit_status, out, err

def format_size(size_bytes):
    """Formats bytes to human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def send_discord_notification(webhook_url, stats, success, server_name="prod-pdm-node-01", is_dry_run=False):
    """Sends a summary report to Discord via Webhook."""
    color = 3066993 if success else 15158332 # Green or Red
    status_msg = "✅ All backups completed successfully!" if success else "❌ Some backups failed!"
    if is_dry_run:
        status_msg = f"[DRY RUN] {status_msg}"
    
    # Format table for embed description
    table_lines = [
        "```",
        f"{'Application':<18} | {'Status':<6} | {'Size':<20} | {'Time':<6}",
        "-" * 57
    ]
    for stat in stats:
        table_lines.append(
            f"{stat['name'][:18]:<18} | {stat['status']:<6} | {stat['size'][:20]:<20} | {stat['duration']}"
        )
    table_lines.append("```")
    description = "\n".join(table_lines)
    
    title = f"Backup Report - {server_name}"
    if is_dry_run:
        title = f"[DRY RUN] {title}"
        
    payload = {
        "username": "Backup Mayster",
        "embeds": [
            {
                "title": title,
                "description": f"**Status:** {status_msg}\n\n{description}",
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "footer": {
                    "text": "Backup Script • Homelab"
                }
            }
        ]
    }
    
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code in [200, 204]:
            logger.info("Discord notification sent successfully.")
        else:
            logger.error(f"Failed to send Discord notification: HTTP {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error sending Discord notification: {e}")

def run_retention(local_dir, app_name, retention_days):
    """Removes backups older than retention_days for a specific app."""
    app_dir = os.path.join(local_dir, app_name)
    try:
        if not os.path.exists(app_dir):
            return []
        
        deleted_files = []
        now = time.time()
        retention_seconds = retention_days * 86400
        
        for filename in os.listdir(app_dir):
            if filename.startswith(f"{app_name}_") and filename.endswith(".tar.gz"):
                file_path = os.path.join(app_dir, filename)
                try:
                    mtime = os.path.getmtime(file_path)
                    if (now - mtime) > retention_seconds:
                        os.remove(file_path)
                        deleted_files.append(filename)
                        logger.info(f"Retention: Deleted old backup file: {filename}")
                except Exception as e:
                    logger.error(f"Failed to process/delete old backup {filename}: {e}")
        return deleted_files
    except Exception as e:
        logger.error(f"Error executing retention policy for {app_name}: {e}")
        return []

def write_stats_json(stats_file_path, stats, all_success, server_name):
    """Writes backup statistics to a JSON file for the exporter, merging with other servers' stats."""
    # Acquire flock on lock file to prevent race conditions during read-modify-write
    lock_file = None
    try:
        lock_file_path = stats_file_path + ".lock"
        lock_dir = os.path.dirname(lock_file_path)
        if lock_dir:
            os.makedirs(lock_dir, exist_ok=True)
        lock_file = open(lock_file_path, 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX) # blocks until lock is acquired
    except Exception as le:
        logger.warning(f"Could not acquire lock on stats file: {le}")

    try:
        # Default structure for the new format
        data = {"servers": {}}
        
        if os.path.exists(stats_file_path):
            try:
                with open(stats_file_path, 'r') as f:
                    existing = json.load(f)
                    if isinstance(existing, dict):
                        if "servers" in existing:
                            data = existing
                        elif "apps" in existing:
                            # Migrate old format to new format
                            data = {"servers": {"default": existing}}
            except Exception as parse_err:
                logger.warning(f"Could not parse previous stats JSON file: {parse_err}")

        # Now find the previous success timestamps for the current server
        prev_success_times = {}
        server_data = data["servers"].get(server_name, {})
        if isinstance(server_data, dict):
            for app in server_data.get('apps', []):
                prev_success_times[app['name']] = app.get('last_success_timestamp_seconds', 0.0)

        # Build the updated server stats
        current_time = time.time()
        apps_data = []
        for stat in stats:
            app_name = stat['name']
            try:
                dur_val = float(stat['duration'].replace('s', ''))
            except ValueError:
                dur_val = 0.0
                
            if stat['status'] == "OK":
                last_success = current_time
            else:
                last_success = prev_success_times.get(app_name, 0.0)
                
            apps_data.append({
                "name": app_name,
                "status": stat['status'],
                "duration_seconds": dur_val,
                "size_bytes": stat.get('size_bytes', 0),
                "last_success_timestamp_seconds": last_success
            })

        data["servers"][server_name] = {
            "last_run_timestamp_seconds": current_time,
            "success": 1 if all_success else 0,
            "apps": apps_data
        }

        stats_dir = os.path.dirname(stats_file_path)
        if stats_dir:
            os.makedirs(stats_dir, exist_ok=True)
            
        temp_path = stats_file_path + ".tmp"
        with open(temp_path, 'w') as f:
            json.dump(data, f, indent=2)
            
        os.replace(temp_path, stats_file_path)
        logger.info(f"Backup stats JSON written to {stats_file_path} for server '{server_name}'")
    except Exception as e:
        logger.error(f"Failed to write stats JSON: {e}")
    finally:
        if lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                lock_file.close()
            except Exception:
                pass

def parse_borg_json(output):
    """Parses JSON output from Borg commands."""
    try:
        return json.loads(output)
    except Exception:
        start = output.find('{')
        end = output.rfind('}')
        if start != -1 and end != -1 and end > start:
            return json.loads(output[start:end+1])
        raise

def run_tar_backup(app, ssh_client, sftp_client, config, is_dry_run=False):
    """Executes a tar+SFTP backup for a single application."""
    app_name = app.get('name')
    app_path = app.get('path')
    backup_cfg = config.get('backup', {})
    
    global_engine = backup_cfg.get('container_engine', 'podman')
    app_engine = app.get('container_engine', global_engine)
    
    global_use_sudo = backup_cfg.get('use_sudo', True)
    app_use_sudo = app.get('use_sudo', global_use_sudo)
    sudo_prefix = "sudo " if app_use_sudo else ""
    
    pause_containers = app.get('pause_containers', [])
    pre_commands = app.get('pre_backup_commands', [])
    post_commands = app.get('post_backup_commands', [])
    
    local_dir = os.path.expanduser(backup_cfg.get('local_dir')) if backup_cfg.get('local_dir') else '/tmp'
    remote_temp_dir = backup_cfg.get('remote_temp_dir', '/tmp')
    try:
        retention_days = int(backup_cfg.get('retention_days') if backup_cfg.get('retention_days') is not None else 7)
    except (ValueError, TypeError):
        retention_days = 7
    try:
        sftp_max_retries = int(backup_cfg.get('sftp_max_retries') if backup_cfg.get('sftp_max_retries') is not None else 3)
    except (ValueError, TypeError):
        sftp_max_retries = 3
    try:
        sftp_retry_delay = float(backup_cfg.get('sftp_retry_delay') if backup_cfg.get('sftp_retry_delay') is not None else 5)
    except (ValueError, TypeError):
        sftp_retry_delay = 5.0
        
    start_time = time.time()
    app_success = True
    err_msg = ""
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    remote_archive_path = f"{remote_temp_dir}/{app_name}_{timestamp}.tar.gz"
    local_app_dir = os.path.join(local_dir, app_name)
    local_archive_path = os.path.join(local_app_dir, f"{app_name}_{timestamp}.tar.gz")
    
    paused_containers = []
    
    try:
        # 1. Run Pre-Backup Commands
        if pre_commands:
            logger.info("Running pre-backup commands...")
            for cmd in pre_commands:
                if is_dry_run:
                    logger.info(f"[DRY RUN] Would execute command: {cmd}")
                    code, out, err = 0, "", ""
                else:
                    code, out, err = run_ssh_command(ssh_client, cmd)
                if code != 0:
                    raise Exception(f"Pre-backup command failed: {cmd}. Error: {err}")
            
        # 2. Pause Containers
        if pause_containers:
            logger.info(f"Pausing containers: {', '.join(pause_containers)}...")
            for container in pause_containers:
                if is_dry_run:
                    logger.info(f"[DRY RUN] Would pause container: {container}")
                    code, out, err = 0, "", ""
                else:
                    code, out, err = run_ssh_command(ssh_client, f"{sudo_prefix}{app_engine} pause {container}")
                if code != 0:
                    logger.warning(f"Could not pause container {container}: {err}. Continuing anyway...")
                else:
                    paused_containers.append(container)
            
        # 3. Create Tarball Archive
        parent_dir = os.path.dirname(app_path)
        target_dir = os.path.basename(app_path)
        tar_cmd = f"{sudo_prefix}tar -czf {remote_archive_path} -C {parent_dir} {target_dir}"
        logger.info("Creating compressed tar archive on remote host...")
        if is_dry_run:
            logger.info(f"[DRY RUN] Would create remote archive using command: {tar_cmd}")
            code, out, err = 0, "", ""
        else:
            code, out, err = run_ssh_command(ssh_client, tar_cmd)
        if code != 0:
            raise Exception(f"Tar creation failed: {err}")
                
    except Exception as e:
        logger.error(f"Error during backup preparation/archiving for {app_name}: {e}")
        app_success = False
        err_msg = str(e)
            
    finally:
        # 4. Unpause Containers
        if paused_containers:
            logger.info(f"Unpausing containers: {', '.join(paused_containers)}...")
            for container in reversed(paused_containers):
                if is_dry_run:
                    logger.info(f"[DRY RUN] Would unpause container: {container}")
                    code, out, err = 0, "", ""
                else:
                    code, out, err = run_ssh_command(ssh_client, f"{sudo_prefix}{app_engine} unpause {container}")
                if code != 0:
                    logger.error(f"CRITICAL: Failed to unpause container {container}! Manual intervention might be required: {err}")
            
        # 5. Run Post-Backup Commands
        if post_commands:
            logger.info("Running post-backup commands...")
            for cmd in post_commands:
                if is_dry_run:
                    logger.info(f"[DRY RUN] Would execute command: {cmd}")
                    code, out, err = 0, "", ""
                else:
                    code, out, err = run_ssh_command(ssh_client, cmd)
                if code != 0:
                    logger.warning(f"Post-backup command failed: {cmd}. Error: {err}")
        
    # 6. SFTP Download and Clean Up Remote Tar
    file_size_formatted = "--"
    file_size_bytes = 0
    if app_success:
        try:
            if is_dry_run:
                logger.info(f"[DRY RUN] Would download archive via SFTP to NAS: {local_archive_path}")
                file_size_formatted = "1.2 MB (DRY)"
                file_size_bytes = 1200000
            else:
                os.makedirs(local_app_dir, exist_ok=True)
                
                # SFTP Download with retry logic
                for attempt in range(1, sftp_max_retries + 1):
                    try:
                        logger.info(f"Downloading archive via SFTP to NAS (attempt {attempt}/{sftp_max_retries}): {local_archive_path}...")
                        sftp_client.get(remote_archive_path, local_archive_path)
                        break
                    except Exception as get_err:
                        if attempt == sftp_max_retries:
                            raise get_err
                        logger.warning(f"SFTP download attempt {attempt} failed: {get_err}. Retrying in {sftp_retry_delay}s...")
                        time.sleep(sftp_retry_delay)
                            
                file_size = os.path.getsize(local_archive_path)
                file_size_formatted = format_size(file_size)
                file_size_bytes = file_size
                logger.info(f"Downloaded successfully. Size: {file_size_formatted}")
        except Exception as e:
            logger.error(f"Failed to download archive for {app_name}: {e}")
            app_success = False
            err_msg = f"SFTP Download failed: {e}"
            file_size_bytes = 0
        finally:
            # Clean up remote temp archive
            if is_dry_run:
                logger.info(f"[DRY RUN] Would delete remote archive: {remote_archive_path}")
            else:
                logger.info(f"Cleaning up remote archive: {remote_archive_path}...")
                run_ssh_command(ssh_client, f"{sudo_prefix}rm -f {remote_archive_path}")
        
    # 7. Retention policy execution
    if app_success:
        if is_dry_run:
            logger.info(f"[DRY RUN] Would run retention in {local_dir} for {app_name} keeping {retention_days} days")
        else:
            run_retention(local_dir, app_name, retention_days)
            
    duration = f"{time.time() - start_time:.1f}s"
    return {
        "name": app_name,
        "status": "OK" if app_success else "FAILED",
        "size": file_size_formatted,
        "size_bytes": file_size_bytes,
        "duration": duration,
        "error": err_msg
    }

def run_borg_backup(app, ssh_client, config, is_dry_run=False):
    """Executes an rsync+Borg backup for a single application."""
    app_name = app.get('name')
    app_path = app.get('path')
    ssh_cfg = config.get('ssh', {})
    backup_cfg = config.get('backup', {})
    borg_cfg = backup_cfg.get('borg', {})
    
    global_use_sudo = backup_cfg.get('use_sudo', True)
    app_use_sudo = app.get('use_sudo', global_use_sudo)
    sudo_prefix = "sudo " if app_use_sudo else ""
    
    global_engine = backup_cfg.get('container_engine', 'podman')
    app_engine = app.get('container_engine', global_engine)
    
    pause_containers = app.get('pause_containers', [])
    pre_commands = app.get('pre_backup_commands', [])
    post_commands = app.get('post_backup_commands', [])
    
    local_dir = os.path.expanduser(backup_cfg.get('local_dir', '/tmp'))
    staging_dir = os.path.expanduser(borg_cfg.get('staging_dir')) if borg_cfg.get('staging_dir') else os.path.join(local_dir, 'staging')
    local_app_staging = os.path.join(staging_dir, app_name)
    repo_path = os.path.expanduser(borg_cfg.get('repo_path')) if borg_cfg.get('repo_path') else None
    
    start_time = time.time()
    app_success = True
    err_msg = ""
    file_size_formatted = "--"
    file_size_bytes = 0
    dedup_size_bytes = 0
    
    paused_containers = []
    
    try:
        # 1. Run Pre-Backup Commands
        if pre_commands:
            logger.info("Running pre-backup commands...")
            for cmd in pre_commands:
                if is_dry_run:
                    logger.info(f"[DRY RUN] Would execute command: {cmd}")
                    code, out, err = 0, "", ""
                else:
                    code, out, err = run_ssh_command(ssh_client, cmd)
                if code != 0:
                    raise Exception(f"Pre-backup command failed: {cmd}. Error: {err}")
            
        # 2. Pause Containers
        if pause_containers:
            logger.info(f"Pausing containers: {', '.join(pause_containers)}...")
            for container in pause_containers:
                if is_dry_run:
                    logger.info(f"[DRY RUN] Would pause container: {container}")
                    code, out, err = 0, "", ""
                else:
                    code, out, err = run_ssh_command(ssh_client, f"{sudo_prefix}{app_engine} pause {container}")
                if code != 0:
                    logger.warning(f"Could not pause container {container}: {err}. Continuing anyway...")
                else:
                    paused_containers.append(container)
            
        # 3. Rsync remote data to staging directory
        if not is_dry_run:
            os.makedirs(local_app_staging, exist_ok=True)
            
        ssh_port = ssh_cfg.get('port', 22)
        ssh_user = ssh_cfg.get('user')
        ssh_host = ssh_cfg.get('host')
        ssh_key = ssh_cfg.get('key_path')
        if ssh_key:
            ssh_key = os.path.expanduser(ssh_key)
        
        ssh_e_cmd = f"ssh -p {ssh_port} -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3"
        if ssh_key:
            ssh_e_cmd += f" -i {ssh_key}"
            
        rsync_cmd = ["rsync", "-az", "--delete", "-e", ssh_e_cmd]
        
        # Add excludes if configured
        excludes = app.get('exclude', [])
        if isinstance(excludes, list):
            for exc in excludes:
                rsync_cmd.extend(["--exclude", str(exc)])
                
        remote_rsync = app.get('rsync_path') or borg_cfg.get('rsync_path')
        if not remote_rsync and app_use_sudo:
            remote_rsync = "sudo rsync"
        if remote_rsync:
            rsync_cmd.append(f"--rsync-path={remote_rsync}")
            
        remote_src = f"{ssh_user}@{ssh_host}:{app_path.rstrip('/')}/"
        local_dst = f"{local_app_staging}/"
        rsync_cmd.extend([remote_src, local_dst])
        
        logger.info(f"Syncing data via rsync to staging: {local_app_staging}...")
        if is_dry_run:
            logger.info(f"[DRY RUN] Would execute rsync: {' '.join(rsync_cmd)}")
        else:
            try:
                proc = subprocess.run(rsync_cmd, capture_output=True, text=True)
                if proc.returncode != 0:
                    raise Exception(f"Rsync failed with code {proc.returncode}: {proc.stderr.strip()}")
                logger.info("Rsync completed successfully.")
            except FileNotFoundError:
                raise Exception("rsync command not found on local system. Please install rsync.")
                
    except Exception as e:
        logger.error(f"Error during backup staging for {app_name}: {e}")
        app_success = False
        err_msg = str(e)
            
    finally:
        # 4. Unpause Containers IMMEDIATELY after rsync completes
        if paused_containers:
            logger.info(f"Unpausing containers: {', '.join(paused_containers)}...")
            for container in reversed(paused_containers):
                if is_dry_run:
                    logger.info(f"[DRY RUN] Would unpause container: {container}")
                    code, out, err = 0, "", ""
                else:
                    code, out, err = run_ssh_command(ssh_client, f"{sudo_prefix}{app_engine} unpause {container}")
                if code != 0:
                    logger.error(f"CRITICAL: Failed to unpause container {container}! Manual intervention might be required: {err}")
            
        # 5. Run Post-Backup Commands
        if post_commands:
            logger.info("Running post-backup commands...")
            for cmd in post_commands:
                if is_dry_run:
                    logger.info(f"[DRY RUN] Would execute command: {cmd}")
                    code, out, err = 0, "", ""
                else:
                    code, out, err = run_ssh_command(ssh_client, cmd)
                if code != 0:
                    logger.warning(f"Post-backup command failed: {cmd}. Error: {err}")
                    
    # 6. Create Borg Archive
    if app_success:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        archive_name = f"{app_name}_{timestamp}"
        archive_spec = f"{repo_path}::{archive_name}"
        compression = borg_cfg.get('compression', 'zstd,3')
        
        borg_env = os.environ.copy()
        passphrase = borg_cfg.get('passphrase')
        if passphrase is not None:
            borg_env['BORG_PASSPHRASE'] = str(passphrase)
        borg_env['BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK'] = 'yes'
        borg_env['BORG_RELOCATED_REPO_ACCESS_IS_OK'] = 'yes'
        
        borg_create_cmd = [
            "borg", "create",
            "--compression", compression,
            "--json",
            "--stats"
        ]
        if isinstance(excludes, list):
            for exc in excludes:
                borg_create_cmd.extend(["--exclude", str(exc)])
        borg_create_cmd.extend([archive_spec, app_name])
        
        logger.info(f"Creating Borg archive '{archive_name}' in repo '{repo_path}'...")
        if is_dry_run:
            logger.info(f"[DRY RUN] Would execute Borg create: {' '.join(borg_create_cmd)} in cwd {staging_dir}")
            file_size_formatted = "15.00 MB (+1.20 MB) (DRY)"
            file_size_bytes = 15000000
            dedup_size_bytes = 1200000
        else:
            try:
                proc = subprocess.run(
                    borg_create_cmd,
                    cwd=staging_dir,
                    env=borg_env,
                    capture_output=True,
                    text=True
                )
                if proc.returncode != 0:
                    stderr_msg = proc.stderr.strip()
                    if "lock" in stderr_msg.lower():
                        logger.error(f"Borg repository '{repo_path}' is locked. If no other backup process is running, you can unlock it with: borg break-lock {repo_path}")
                    raise Exception(f"Borg create failed with code {proc.returncode}: {stderr_msg}")
                    
                json_output = proc.stdout.strip()
                if not json_output and proc.stderr:
                    json_output = proc.stderr.strip()
                    
                try:
                    stats_json = parse_borg_json(json_output)
                    archive_stats = stats_json.get('archive', {}).get('stats', {})
                    orig_size = archive_stats.get('original_size', 0)
                    dedup_size = archive_stats.get('deduplicated_size', 0)
                    file_size_bytes = orig_size
                    dedup_size_bytes = dedup_size
                    file_size_formatted = f"{format_size(orig_size)} (+{format_size(dedup_size)})"
                    logger.info(f"Borg archive created. Original: {format_size(orig_size)}, Deduplicated (new): {format_size(dedup_size)}")
                except Exception as je:
                    logger.warning(f"Could not parse Borg JSON output: {je}. Raw: {json_output[:200]}")
                    file_size_formatted = "OK (borg)"
                    file_size_bytes = 0
            except FileNotFoundError:
                logger.error("borg command not found on local system. Please install borgbackup.")
                app_success = False
                err_msg = "borg command not found on local system"
            except Exception as be:
                logger.error(f"Borg create failed for {app_name}: {be}")
                app_success = False
                err_msg = f"Borg create failed: {be}"
                
    # 7. Borg Prune
    if app_success:
        prune_cfg = borg_cfg.get('prune', {})
        keep_daily = prune_cfg.get('keep_daily')
        if keep_daily is None and backup_cfg.get('retention_days') is not None:
            keep_daily = backup_cfg.get('retention_days')
        keep_weekly = prune_cfg.get('keep_weekly')
        keep_monthly = prune_cfg.get('keep_monthly')
        keep_within = prune_cfg.get('keep_within')
        
        # Ensure at least one keep option is passed to avoid Borg prune error
        if not any([keep_daily, keep_weekly, keep_monthly, keep_within]):
            keep_daily = 7
        
        borg_prune_cmd = [
            "borg", "prune",
            "--list",
            "--prefix", f"{app_name}_"
        ]
        if keep_daily:
            borg_prune_cmd.extend(["--keep-daily", str(keep_daily)])
        if keep_weekly:
            borg_prune_cmd.extend(["--keep-weekly", str(keep_weekly)])
        if keep_monthly:
            borg_prune_cmd.extend(["--keep-monthly", str(keep_monthly)])
        if keep_within:
            borg_prune_cmd.extend(["--keep-within", str(keep_within)])
        borg_prune_cmd.append(repo_path)
        
        logger.info(f"Running Borg prune for app prefix '{app_name}_'...")
        if is_dry_run:
            logger.info(f"[DRY RUN] Would execute Borg prune: {' '.join(borg_prune_cmd)}")
        else:
            try:
                proc = subprocess.run(
                    borg_prune_cmd,
                    env=borg_env,
                    capture_output=True,
                    text=True
                )
                if proc.returncode != 0:
                    stderr_msg = proc.stderr.strip()
                    if "lock" in stderr_msg.lower():
                        logger.error(f"Borg repository '{repo_path}' is locked. You can unlock it with: borg break-lock {repo_path}")
                    logger.warning(f"Borg prune returned code {proc.returncode}: {stderr_msg}")
                else:
                    logger.info(f"Borg prune completed for {app_name}.")
            except Exception as pe:
                logger.warning(f"Borg prune error: {pe}")
                
    duration = f"{time.time() - start_time:.1f}s"
    return {
        "name": app_name,
        "status": "OK" if app_success else "FAILED",
        "size": file_size_formatted,
        "size_bytes": file_size_bytes,
        "dedup_size_bytes": dedup_size_bytes,
        "duration": duration,
        "error": err_msg
    }

ASCII_ART = r"""
 _                _               ___  ___                  _            
| |              | |              |  \/  |                 | |           
| |__   __ _  ___| | ___   _ _ __ | .  . | __ _ _   _  ___ | |_ ___ _ __ 
| '_ \ / _` |/ __| |/ / | | | '_ \| |\/| |/ _` | | | |/ __|| __/ _ \ '__|
| |_) | (_| | (__|   <| |_| | |_) | |  | | (_| | |_| |\__ \| ||  __/ |   
|_.__/ \__,_|\___|_|\_\\__,_| .__/\_|  |_/\__,_|\__, ||___/ \__\___|_|   
                            | |                  __/ |                   
                            |_|                 |___/                    
"""

def main():
    print(ASCII_ART)
    parser = argparse.ArgumentParser(
        description="backup-mayster - A lightweight, pull-based backup tool for Podman container configurations."
    )
    parser.add_argument(
        '-c', '--config',
        help="Path to the config.yaml configuration file (defaults to config.yaml in the script directory)."
    )
    parser.add_argument(
        '-a', '--app',
        help="Backup only a specific application (e.g. --app bao). By default, all applications defined in config are backed up."
    )
    parser.add_argument(
        '-d', '--dry-run',
        action='store_true',
        help="Perform a dry run. Connects and runs checks, logs actions, but does not perform pause, tar, SFTP, or retention."
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help="Run 'borg check' on the repository to verify data integrity."
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    config_path = args.config
    if not config_path:
        config_path = os.path.join(script_dir, 'config.yaml')
        
    config_name = os.path.basename(config_path).replace('.yaml', '').replace('.yml', '')
    
    # Acquire flock to prevent concurrent executions of the same configuration
    lock_file_path = os.path.join(script_dir, f'backup-mayster-{config_name}.lock')
    try:
        lock_file_obj = open(lock_file_path, 'w')
    except Exception as e:
        logger.error(f"Failed to open/create lock file '{lock_file_path}': {e}")
        logger.error("Please ensure the script directory is writable or that you have sufficient permissions.")
        sys.exit(1)

    try:
        fcntl.flock(lock_file_obj, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.error(f"Another instance of backup-mayster for config '{config_name}' is already running (locked). Exiting.")
        sys.exit(1)

    config = load_config(config_path)
    validate_config(config)
    
    ssh_cfg = config.get('ssh', {})
    backup_cfg = config.get('backup', {})
    discord_cfg = config.get('discord', {})
    apps = config.get('apps', [])
    global_engine = backup_cfg.get('container_engine', 'podman')
    
    server_name = backup_cfg.get('server_name')
    if not server_name:
        server_name = ssh_cfg.get('host', 'unknown-server')
        
    # Handle Borg integrity check CLI action
    if args.check:
        borg_cfg = backup_cfg.get('borg', {})
        repo_path = os.path.expanduser(borg_cfg.get('repo_path')) if borg_cfg.get('repo_path') else None
        if not repo_path:
            logger.error("Borg repository path is not configured. Cannot perform integrity check.")
            sys.exit(1)
        logger.info(f"Running Borg integrity check on repo: {repo_path}...")
        borg_env = os.environ.copy()
        passphrase = borg_cfg.get('passphrase')
        if passphrase is not None:
            borg_env['BORG_PASSPHRASE'] = str(passphrase)
        borg_env['BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK'] = 'yes'
        borg_env['BORG_RELOCATED_REPO_ACCESS_IS_OK'] = 'yes'
        try:
            proc = subprocess.run(["borg", "check", repo_path], env=borg_env, capture_output=True, text=True)
            if proc.returncode == 0:
                logger.info("Borg check completed successfully. Repository is consistent and healthy.")
                sys.exit(0)
            else:
                logger.error(f"Borg check detected issues (code {proc.returncode}): {proc.stderr.strip()}")
                sys.exit(1)
        except FileNotFoundError:
            logger.error("borg command not found on local system. Please install borgbackup.")
            sys.exit(1)
    
    # Filter apps if a specific one was requested
    if args.app:
        apps = [app for app in apps if app.get('name') == args.app]
        if not apps:
            logger.error(f"Application '{args.app}' not found in configuration file.")
            sys.exit(1)
            
    is_dry_run = args.dry_run
    
    # Configure file logging if specified or default to /var/log/backup-mayster/backup-mayster.log
    log_file = backup_cfg.get('log_file')
    if log_file and not isinstance(log_file, str):
        logger.error("Configuration error: 'backup.log_file' must be a string path.")
        sys.exit(1)
        
    if not log_file:
        log_file = '/var/log/backup-mayster/backup-mayster.log'
    else:
        # Resolve relative paths relative to script dir
        if not os.path.isabs(log_file):
            log_file = os.path.abspath(os.path.join(script_dir, log_file))
            
    try:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            
        from logging.handlers import RotatingFileHandler
        # Rotate logs after 10MB, keep last 5 logs
        file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logging.getLogger().addHandler(file_handler)
        logger.info(f"Logging file handler added. Writing logs to: {log_file}")
    except Exception as e:
        logger.error(f"Failed to configure file logging to {log_file}: {e}")
        
    if is_dry_run:
        logger.info("=== DRY RUN MODE ENABLED - NO WRITE/PAUSE ACTIONS WILL BE EXECUTED ===")
        
    logger.info("Starting backup process...")
    
    local_dir = os.path.expanduser(backup_cfg.get('local_dir')) if backup_cfg.get('local_dir') else '/tmp'
    remote_temp_dir = backup_cfg.get('remote_temp_dir', '/tmp')
    try:
        retention_days = int(backup_cfg.get('retention_days') if backup_cfg.get('retention_days') is not None else 7)
    except (ValueError, TypeError):
        retention_days = 7
    try:
        sftp_max_retries = int(backup_cfg.get('sftp_max_retries') if backup_cfg.get('sftp_max_retries') is not None else 3)
    except (ValueError, TypeError):
        sftp_max_retries = 3
        
    try:
        sftp_retry_delay = float(backup_cfg.get('sftp_retry_delay') if backup_cfg.get('sftp_retry_delay') is not None else 5)
    except (ValueError, TypeError):
        sftp_retry_delay = 5.0
    
    # Ensure local backup directory exists
    if not is_dry_run:
        try:
            os.makedirs(local_dir, exist_ok=True)
        except PermissionError as pe:
            logger.error(f"Permission denied when creating local backup directory '{local_dir}': {pe}")
            logger.error("Please run the script with appropriate permissions or adjust the 'local_dir' path in config.yaml.")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to create local backup directory '{local_dir}': {e}")
            sys.exit(1)
    
    global_engine_type = backup_cfg.get('engine', 'tar')
    apps_engine_types = [app.get('engine', global_engine_type) for app in apps]
    needs_tar = any(e == 'tar' for e in apps_engine_types)
    needs_borg = any(e == 'borg' for e in apps_engine_types)
    
    # Connect to SSH
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    sftp_client = None
    try:
        logger.info(f"Connecting to {ssh_cfg.get('host')} via SSH as {ssh_cfg.get('user')}...")
        try:
            port_val = int(ssh_cfg.get('port') if ssh_cfg.get('port') is not None else 22)
        except (ValueError, TypeError):
            port_val = 22
            
        ssh_key_path = os.path.expanduser(ssh_cfg.get('key_path')) if ssh_cfg.get('key_path') else None
        ssh_client.connect(
            hostname=ssh_cfg.get('host'),
            port=port_val,
            username=ssh_cfg.get('user'),
            key_filename=ssh_key_path,
            timeout=15
        )
        transport = ssh_client.get_transport()
        if transport:
            transport.set_keepalive(30)
            
        if needs_tar:
            sftp_client = ssh_client.open_sftp()
    except Exception as e:
        logger.error(f"Failed to connect to production server: {e}")
        # Send Discord notification about connection failure if webhook is enabled
        if discord_cfg.get('enabled') and discord_cfg.get('webhook_url'):
            send_discord_notification(
                discord_cfg.get('webhook_url'),
                [{"name": "SSH Connection", "status": "FAILED", "size": "--", "duration": "0s"}],
                success=False,
                server_name=server_name,
                is_dry_run=is_dry_run
            )
        sys.exit(1)
        
    stats = []
    all_success = True
    
    for app in apps:
        app_name = app.get('name')
        app_engine_type = app.get('engine', global_engine_type)
        logger.info(f"=== Starting backup for app: {app_name} (engine: {app_engine_type}) ===")
        
        if app_engine_type == 'borg':
            stat = run_borg_backup(app, ssh_client, config, is_dry_run)
        else:
            stat = run_tar_backup(app, ssh_client, sftp_client, config, is_dry_run)
            
        stats.append(stat)
        if stat['status'] != "OK":
            all_success = False
            
        logger.info(f"=== Finished backup for app: {app_name} (Status: {stat['status']}) ===\n")
        
    # Close SSH connection
    try:
        if sftp_client:
            sftp_client.close()
        ssh_client.close()
        logger.info("SSH connection closed.")
    except Exception as e:
        logger.warning(f"Error while closing SSH client: {e}")
        
    # Optional Borg compact at the end of the run
    if needs_borg:
        borg_cfg = backup_cfg.get('borg', {})
        repo_path = borg_cfg.get('repo_path')
        if repo_path and borg_cfg.get('compact', True) and not is_dry_run:
            logger.info(f"Running Borg compact on repo '{repo_path}'...")
            try:
                borg_env = os.environ.copy()
                passphrase = borg_cfg.get('passphrase')
                if passphrase is not None:
                    borg_env['BORG_PASSPHRASE'] = str(passphrase)
                borg_env['BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK'] = 'yes'
                borg_env['BORG_RELOCATED_REPO_ACCESS_IS_OK'] = 'yes'
                proc = subprocess.run(["borg", "compact", repo_path], env=borg_env, capture_output=True, text=True)
                if proc.returncode == 0:
                    logger.info("Borg compact completed.")
                else:
                    logger.warning(f"Borg compact returned code {proc.returncode}: {proc.stderr.strip()}")
            except Exception as ce:
                logger.warning(f"Borg compact encountered an issue: {ce}")
        
    # Send Discord notification
    if discord_cfg.get('enabled') and discord_cfg.get('webhook_url'):
        logger.info("Sending Discord notification...")
        send_discord_notification(discord_cfg.get('webhook_url'), stats, all_success, server_name=server_name, is_dry_run=is_dry_run)
        
    # Write JSON metrics if configured
    metrics_cfg = config.get('metrics', {})
    if metrics_cfg.get('enabled') and metrics_cfg.get('stats_file'):
        write_stats_json(metrics_cfg.get('stats_file'), stats, all_success, server_name)
        
    logger.info("Backup script run finished.")
    if not all_success:
        logger.error("Some backups encountered errors. Please check the logs above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
