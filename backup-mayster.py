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
    if backup_cfg.get('container_engine') is not None:
        engine = backup_cfg.get('container_engine')
        if engine not in ['podman', 'docker']:
            logger.error(f"Configuration error: 'backup.container_engine' must be 'podman' or 'docker'. Got: '{engine}'")
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

def send_discord_notification(webhook_url, stats, success, is_dry_run=False):
    """Sends a summary report to Discord via Webhook."""
    color = 3066993 if success else 15158332 # Green or Red
    status_msg = "✅ All backups completed successfully!" if success else "❌ Some backups failed!"
    if is_dry_run:
        status_msg = f"[DRY RUN] {status_msg}"
    
    # Format table for embed description
    table_lines = [
        "```",
        f"{'Application':<20} | {'Status':<8} | {'Size':<12} | {'Time':<8}",
        "-" * 55
    ]
    for stat in stats:
        table_lines.append(
            f"{stat['name'][:20]:<20} | {stat['status']:<8} | {stat['size']:<12} | {stat['duration']}"
        )
    table_lines.append("```")
    description = "\n".join(table_lines)
    
    title = f"Backup Report - prod-pdm-node-01"
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

def write_stats_json(stats_file_path, stats, all_success):
    """Writes backup statistics to a JSON file for the exporter."""
    prev_success_times = {}
    if os.path.exists(stats_file_path):
        try:
            with open(stats_file_path, 'r') as f:
                old_data = json.load(f)
                for app in old_data.get('apps', []):
                    prev_success_times[app['name']] = app.get('last_success_timestamp_seconds', 0.0)
        except Exception as parse_err:
            logger.warning(f"Could not parse previous stats JSON file: {parse_err}")

    try:
        stats_dir = os.path.dirname(stats_file_path)
        if stats_dir:
            os.makedirs(stats_dir, exist_ok=True)
            
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
            
        payload = {
            "last_run_timestamp_seconds": current_time,
            "success": 1 if all_success else 0,
            "apps": apps_data
        }
        
        temp_path = stats_file_path + ".tmp"
        with open(temp_path, 'w') as f:
            json.dump(payload, f, indent=2)
            
        os.replace(temp_path, stats_file_path)
        logger.info(f"Backup stats JSON written to {stats_file_path}")
    except Exception as e:
        logger.error(f"Failed to write stats JSON: {e}")

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
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Acquire flock to prevent concurrent executions
    lock_file_path = os.path.join(script_dir, 'backup-mayster.lock')
    try:
        lock_file_obj = open(lock_file_path, 'w')
    except Exception as e:
        logger.error(f"Failed to open/create lock file '{lock_file_path}': {e}")
        logger.error("Please ensure the script directory is writable or that you have sufficient permissions.")
        sys.exit(1)

    try:
        fcntl.flock(lock_file_obj, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.error("Another instance of backup-mayster is already running (locked). Exiting.")
        sys.exit(1)

    config_path = args.config
    if not config_path:
        config_path = os.path.join(script_dir, 'config.yaml')
    
    config = load_config(config_path)
    validate_config(config)
    
    ssh_cfg = config.get('ssh', {})
    backup_cfg = config.get('backup', {})
    discord_cfg = config.get('discord', {})
    apps = config.get('apps', [])
    global_engine = backup_cfg.get('container_engine', 'podman')
    
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
    
    local_dir = backup_cfg.get('local_dir')
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
    
    # Connect to SSH
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        logger.info(f"Connecting to {ssh_cfg.get('host')} via SSH as {ssh_cfg.get('user')}...")
        try:
            port_val = int(ssh_cfg.get('port') if ssh_cfg.get('port') is not None else 22)
        except (ValueError, TypeError):
            port_val = 22
            
        ssh_client.connect(
            hostname=ssh_cfg.get('host'),
            port=port_val,
            username=ssh_cfg.get('user'),
            key_filename=ssh_cfg.get('key_path'),
            timeout=15
        )
        transport = ssh_client.get_transport()
        if transport:
            transport.set_keepalive(30)
            
        sftp_client = ssh_client.open_sftp()
    except Exception as e:
        logger.error(f"Failed to connect to production server: {e}")
        # Send Discord notification about connection failure if webhook is enabled
        if discord_cfg.get('enabled') and discord_cfg.get('webhook_url'):
            send_discord_notification(
                discord_cfg.get('webhook_url'),
                [{"name": "SSH Connection", "status": "FAILED", "size": "--", "duration": "0s"}],
                success=False,
                is_dry_run=is_dry_run
            )
        sys.exit(1)
        
    stats = []
    all_success = True
    
    for app in apps:
        app_name = app.get('name')
        app_path = app.get('path')
        app_engine = app.get('container_engine', global_engine)
        pause_containers = app.get('pause_containers', [])
        pre_commands = app.get('pre_backup_commands', [])
        post_commands = app.get('post_backup_commands', [])
        
        logger.info(f"=== Starting backup for app: {app_name} ===")
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
                        code, out, err = run_ssh_command(ssh_client, f"sudo {app_engine} pause {container}")
                    if code != 0:
                        logger.warning(f"Could not pause container {container}: {err}. Continuing anyway...")
                    else:
                        paused_containers.append(container)
            
            # 3. Create Tarball Archive
            parent_dir = os.path.dirname(app_path)
            target_dir = os.path.basename(app_path)
            tar_cmd = f"sudo tar -czf {remote_archive_path} -C {parent_dir} {target_dir}"
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
                        code, out, err = run_ssh_command(ssh_client, f"sudo {app_engine} unpause {container}")
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
                    run_ssh_command(ssh_client, f"sudo rm -f {remote_archive_path}")
        
        # 7. Retention policy execution
        if app_success:
            if is_dry_run:
                logger.info(f"[DRY RUN] Would run retention in {local_dir} for {app_name} keeping {retention_days} days")
            else:
                run_retention(local_dir, app_name, retention_days)
            
        duration = f"{time.time() - start_time:.1f}s"
        stats.append({
            "name": app_name,
            "status": "OK" if app_success else "FAILED",
            "size": file_size_formatted,
            "size_bytes": file_size_bytes,
            "duration": duration,
            "error": err_msg
        })
        
        if not app_success:
            all_success = False
            
        logger.info(f"=== Finished backup for app: {app_name} (Status: {'OK' if app_success else 'FAILED'}) ===\n")
        
    # Close SSH connection
    try:
        sftp_client.close()
        ssh_client.close()
        logger.info("SSH connection closed.")
    except Exception as e:
        logger.warning(f"Error while closing SSH client: {e}")
        
    # Send Discord notification
    if discord_cfg.get('enabled') and discord_cfg.get('webhook_url'):
        logger.info("Sending Discord notification...")
        send_discord_notification(discord_cfg.get('webhook_url'), stats, all_success, is_dry_run=is_dry_run)
        
    # Write JSON metrics if configured
    metrics_cfg = config.get('metrics', {})
    if metrics_cfg.get('enabled') and metrics_cfg.get('stats_file'):
        write_stats_json(metrics_cfg.get('stats_file'), stats, all_success)
        
    logger.info("Backup script run finished.")
    if not all_success:
        logger.error("Some backups encountered errors. Please check the logs above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
