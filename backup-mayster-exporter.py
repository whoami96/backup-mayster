#!/usr/bin/env python3
import os
import sys
import json
import argparse
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

class MetricsHandler(BaseHTTPRequestHandler):
    stats_file = '/var/log/backup-mayster/backup-mayster-stats.json'

    def do_GET(self):
        if self.path == '/metrics':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.end_headers()
            
            metrics = self.generate_metrics()
            self.wfile.write(metrics.encode('utf-8'))
        elif self.path in ['/healthz', '/']:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence default request logging to avoid log spam, 
        # but feel free to uncomment for debugging
        pass

    def generate_metrics(self):
        if not os.path.exists(self.stats_file):
            return (
                "# HELP backup_mayster_last_run_timestamp_seconds Epoch timestamp of the last backup run.\n"
                "# TYPE backup_mayster_last_run_timestamp_seconds gauge\n"
                "backup_mayster_last_run_timestamp_seconds 0\n\n"
                "# HELP backup_mayster_success Status of the backup run (1 = success, 0 = failure).\n"
                "# TYPE backup_mayster_success gauge\n"
                "backup_mayster_success 0\n"
            )
            
        try:
            with open(self.stats_file, 'r') as f:
                data = json.load(f)
        except Exception as e:
            return f"# Error reading stats file: {e}\n"
            
        lines = []
        
        # Check if it is in the new multi-server format
        servers = {}
        if isinstance(data, dict):
            if "servers" in data:
                servers = data.get("servers", {})
            elif "apps" in data:
                # Fallback / compatibility with old single-server format
                servers = {"default": data}
                
        # Generate metrics for each server
        lines.append("# HELP backup_mayster_last_run_timestamp_seconds Epoch timestamp of the last backup run.")
        lines.append("# TYPE backup_mayster_last_run_timestamp_seconds gauge")
        for sname, sdata in servers.items():
            last_run = sdata.get('last_run_timestamp_seconds', 0.0)
            lines.append(f'backup_mayster_last_run_timestamp_seconds{{server="{sname}"}} {last_run}')
        lines.append("")
        
        lines.append("# HELP backup_mayster_success Status of the backup run (1 = success, 0 = failure).")
        lines.append("# TYPE backup_mayster_success gauge")
        for sname, sdata in servers.items():
            success = sdata.get('success', 0)
            lines.append(f'backup_mayster_success{{server="{sname}"}} {success}')
        lines.append("")
        
        lines.append("# HELP backup_mayster_app_success Status of the backup for a specific application (1 = success, 0 = failure).")
        lines.append("# TYPE backup_mayster_app_success gauge")
        for sname, sdata in servers.items():
            for app in sdata.get('apps', []):
                val = 1 if app.get('status') == "OK" else 0
                lines.append(f'backup_mayster_app_success{{server="{sname}",app="{app.get("name")}"}} {val}')
        lines.append("")
        
        lines.append("# HELP backup_mayster_app_duration_seconds Duration of the backup process for a specific application in seconds.")
        lines.append("# TYPE backup_mayster_app_duration_seconds gauge")
        for sname, sdata in servers.items():
            for app in sdata.get('apps', []):
                lines.append(f'backup_mayster_app_duration_seconds{{server="{sname}",app="{app.get("name")}"}} {app.get("duration_seconds", 0.0)}')
        lines.append("")
        
        lines.append("# HELP backup_mayster_app_backup_size_bytes Size of the backup archive in bytes.")
        lines.append("# TYPE backup_mayster_app_backup_size_bytes gauge")
        for sname, sdata in servers.items():
            for app in sdata.get('apps', []):
                lines.append(f'backup_mayster_app_backup_size_bytes{{server="{sname}",app="{app.get("name")}"}} {app.get("size_bytes", 0)}')
        lines.append("")

        lines.append("# HELP backup_mayster_app_last_success_timestamp_seconds Epoch timestamp of the last successful backup run for this application.")
        lines.append("# TYPE backup_mayster_app_last_success_timestamp_seconds gauge")
        for sname, sdata in servers.items():
            for app in sdata.get('apps', []):
                lines.append(f'backup_mayster_app_last_success_timestamp_seconds{{server="{sname}",app="{app.get("name")}"}} {app.get("last_success_timestamp_seconds", 0.0)}')
        lines.append("")
        
        return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Prometheus exporter for backup-mayster.")
    parser.add_argument('-p', '--port', type=int, default=9115, help="Port to listen on (default: 9115).")
    parser.add_argument('-f', '--file', default='/var/log/backup-mayster/backup-mayster-stats.json', help="Path to backup-mayster-stats.json")
    parser.add_argument('--install-service', action='store_true', help="Install the exporter as a systemd service (requires sudo/root).")
    parser.add_argument('--user', default='mayster', help="User to run the systemd service (default: mayster).")
    args = parser.parse_args()
    
    if args.install_service:
        # Verify root permissions
        if os.geteuid() != 0:
            print("Error: Installing systemd service requires root privileges. Please run with sudo:")
            print(f"sudo {sys.executable} {os.path.abspath(__file__)} --install-service")
            sys.exit(1)
            
        script_path = os.path.abspath(__file__)
        stats_file_path = os.path.abspath(args.file)
        service_content = f"""[Unit]
Description=Prometheus Exporter for backup-mayster
After=network.target

[Service]
Type=simple
User={args.user}
ExecStart={sys.executable} {script_path} --port {args.port} --file {stats_file_path}
Restart=always

[Install]
WantedBy=multi-user.target
"""
        service_path = "/etc/systemd/system/backup-mayster-exporter.service"
        try:
            with open(service_path, 'w') as f:
                f.write(service_content)
            print(f"Systemd service file successfully written to: {service_path}")
            
            # Reload daemon
            try:
                subprocess.run(["systemctl", "daemon-reload"], check=True)
                print("Systemd daemon reloaded.")
            except Exception as se:
                print(f"Warning: Could not run systemctl daemon-reload: {se}")
                
            print("\nInstallation successful! To start and enable the service, run:")
            print(f"  sudo systemctl enable --now backup-mayster-exporter.service")
            print("To check service status, run:")
            print("  systemctl status backup-mayster-exporter.service")
            
        except Exception as e:
            print(f"Error: Failed to write systemd service file: {e}")
            sys.exit(1)
        sys.exit(0)
        
    MetricsHandler.stats_file = args.file
    
    server_address = ('', args.port)
    try:
        httpd = HTTPServer(server_address, MetricsHandler)
    except OSError as oe:
        print(f"Error starting exporter on port {args.port}: {oe}")
        print("Please check if the port is already in use or if you have permission to bind to it.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to start web server on port {args.port}: {e}")
        sys.exit(1)
        
    print(f"Starting backup-mayster exporter on port {args.port}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping exporter...")
        httpd.server_close()
        sys.exit(0)

if __name__ == '__main__':
    main()
