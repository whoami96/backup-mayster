#!/usr/bin/env python3
import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys
import importlib
import time

# Since "backup-mayster" contains a hyphen, we must import it dynamically
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
backup_mayster = importlib.import_module("backup-mayster")

class TestBackupMayster(unittest.TestCase):

    def test_format_size(self):
        """Test formatting of bytes into human-readable size string."""
        self.assertEqual(backup_mayster.format_size(500), "500.00 B")
        self.assertEqual(backup_mayster.format_size(1024), "1.00 KB")
        self.assertEqual(backup_mayster.format_size(1048576), "1.00 MB")
        self.assertEqual(backup_mayster.format_size(1073741824), "1.00 GB")
        self.assertEqual(backup_mayster.format_size(1099511627776), "1.00 TB")

    @patch("builtins.open", new_callable=mock_open, read_data="ssh:\n  host: 'prod-test'\nbackup:\n  retention_days: 5\n")
    def test_load_config_success(self, mock_file):
        """Test loading configuration successfully from YAML."""
        config = backup_mayster.load_config("dummy_path.yaml")
        mock_file.assert_called_with("dummy_path.yaml", 'r')
        self.assertEqual(config['ssh']['host'], 'prod-test')
        self.assertEqual(config['backup']['retention_days'], 5)

    @patch("builtins.open", side_file=Exception("File not found"))
    @patch("sys.exit")
    def test_load_config_failure(self, mock_exit, mock_file):
        """Test that configuration loading failure calls sys.exit(1)."""
        mock_file.side_effect = Exception("Read Error")
        backup_mayster.load_config("invalid_path.yaml")
        mock_exit.assert_called_once_with(1)

    @patch("os.path.exists")
    @patch("os.listdir")
    @patch("os.path.getmtime")
    @patch("os.remove")
    @patch("time.time")
    def test_run_retention(self, mock_time, mock_remove, mock_getmtime, mock_listdir, mock_exists):
        """Test that old backup files are removed and new ones are kept based on modification time."""
        mock_exists.return_value = True
        # Current simulated time is: 1719144000 (Sunday, Jun 23, 2024 12:00:00 PM)
        mock_time.return_value = 1719144000
        
        # Files in directory
        mock_listdir.return_value = [
            "bao_20240623_120000.tar.gz",  # Brand new file (0s old)
            "bao_20240620_120000.tar.gz",  # 3 days old (keep - retention is 7 days)
            "bao_20240615_120000.tar.gz",  # 8 days old (delete!)
            "other_file.txt",              # Not matching pattern (keep)
            "npm_20240615_120000.tar.gz"   # Not matching app prefix (keep)
        ]
        
        # Define file ages (mtime timestamps)
        mtime_map = {
            os.path.join("/backups/bao", "bao_20240623_120000.tar.gz"): 1719144000,
            os.path.join("/backups/bao", "bao_20240620_120000.tar.gz"): 1719144000 - (3 * 86400),
            os.path.join("/backups/bao", "bao_20240615_120000.tar.gz"): 1719144000 - (8 * 86400),
            os.path.join("/backups/bao", "other_file.txt"): 1719144000 - (10 * 86400),
            os.path.join("/backups/bao", "npm_20240615_120000.tar.gz"): 1719144000 - (10 * 86400)
        }
        mock_getmtime.side_effect = lambda path: mtime_map.get(path, 0)
        
        # Run retention with 7 days limit
        deleted = backup_mayster.run_retention("/backups", "bao", 7)
        
        # Assertions
        self.assertEqual(deleted, ["bao_20240615_120000.tar.gz"])
        # Only the old matching file should have os.remove called on it
        expected_deleted_path = os.path.join("/backups/bao", "bao_20240615_120000.tar.gz")
        mock_remove.assert_called_once_with(expected_deleted_path)

    @patch("requests.post")
    def test_send_discord_notification_success(self, mock_post):
        """Test successful send of Discord notification payload."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response
        
        stats = [
            {"name": "bao", "status": "OK", "size": "15.4 MB", "duration": "4.5s"},
            {"name": "npm", "status": "FAILED", "size": "--", "duration": "1.2s"}
        ]
        
        backup_mayster.send_discord_notification(
            webhook_url="https://discord.mock/webhook",
            stats=stats,
            success=False,
            is_dry_run=True
        )
        
        # Verify requests.post was called with expected arguments
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://discord.mock/webhook")
        
        # Verify JSON content structure
        payload = kwargs['json']
        self.assertEqual(payload['username'], "Backup Mayster")
        embed = payload['embeds'][0]
        self.assertIn("[DRY RUN]", embed['title'])
        self.assertIn("❌ Some backups failed!", embed['description'])
        self.assertIn("bao", embed['description'])
        self.assertIn("npm", embed['description'])

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    @patch("os.replace")
    @patch("os.makedirs")
    @patch("time.time")
    def test_write_stats_json(self, mock_time, mock_makedirs, mock_replace, mock_file, mock_exists):
        """Test writing stats JSON file, including parsing previous success timestamps."""
        # Simulated time
        mock_time.return_value = 1719144000.0
        
        # Scenario: previous file exists and has older timestamps
        mock_exists.return_value = True
        
        # Mock reading previous file and then writing the new one
        read_data = (
            '{\n'
            '  "last_run_timestamp_seconds": 1719100000.0,\n'
            '  "success": 1,\n'
            '  "apps": [\n'
            '    {"name": "bao", "status": "OK", "duration_seconds": 4.0, "size_bytes": 14000000, "last_success_timestamp_seconds": 1719100000.0},\n'
            '    {"name": "npm", "status": "OK", "duration_seconds": 1.0, "size_bytes": 500000, "last_success_timestamp_seconds": 1719110000.0}\n'
            '  ]\n'
            '}'
        )
        
        file_handle_read = mock_open(read_data=read_data).return_value
        file_handle_read.__enter__.return_value = file_handle_read
        file_handle_write = MagicMock()
        file_handle_write.__enter__.return_value = file_handle_write
        
        def open_side_effect(filename, mode='r', *args, **kwargs):
            if 'r' in mode:
                return file_handle_read
            else:
                return file_handle_write
                
        mock_file.side_effect = open_side_effect
        
        stats = [
            {"name": "bao", "status": "OK", "size": "15.4 MB", "size_bytes": 15000000, "duration": "4.5s"},
            {"name": "npm", "status": "FAILED", "size": "--", "size_bytes": 0, "duration": "1.2s"}
        ]
        
        backup_mayster.write_stats_json("/var/log/backup.json", stats, False)
        
        # Verify replace and directories are checked
        mock_replace.assert_called_once_with("/var/log/backup.json.tmp", "/var/log/backup.json")
        mock_makedirs.assert_called_once_with("/var/log", exist_ok=True)
        
        # Inspect what was written
        written_content = "".join(call.args[0] for call in file_handle_write.write.call_args_list)
        
        # Parse written JSON to assert values
        import json as test_json
        written_data = test_json.loads(written_content)
        
        self.assertEqual(written_data['success'], 0)
        self.assertEqual(written_data['last_run_timestamp_seconds'], 1719144000.0)
        
        apps_dict = {app['name']: app for app in written_data['apps']}
        
        # Check bao (Success in current run)
        self.assertEqual(apps_dict['bao']['status'], 'OK')
        self.assertEqual(apps_dict['bao']['duration_seconds'], 4.5)
        self.assertEqual(apps_dict['bao']['size_bytes'], 15000000)
        self.assertEqual(apps_dict['bao']['last_success_timestamp_seconds'], 1719144000.0)
        
        # Check npm (Failed in current run, should preserve previous timestamp 1719110000.0)
        self.assertEqual(apps_dict['npm']['status'], 'FAILED')
        self.assertEqual(apps_dict['npm']['duration_seconds'], 1.2)
        self.assertEqual(apps_dict['npm']['size_bytes'], 0)
        self.assertEqual(apps_dict['npm']['last_success_timestamp_seconds'], 1719110000.0)

    @patch("sys.exit")
    def test_validate_config(self, mock_exit):
        """Test configuration validation with both valid and invalid scenarios."""
        # 1. Valid config
        valid_cfg = {
            "ssh": {"host": "prod-test", "user": "mayster"},
            "backup": {"local_dir": "/backups"},
            "apps": [{"name": "bao", "path": "/opt/bao"}]
        }
        backup_mayster.validate_config(valid_cfg)
        mock_exit.assert_not_called()

        # 2. Invalid config - missing ssh
        invalid_cfg_1 = {
            "backup": {"local_dir": "/backups"},
            "apps": [{"name": "bao", "path": "/opt/bao"}]
        }
        backup_mayster.validate_config(invalid_cfg_1)
        mock_exit.assert_called_with(1)
        mock_exit.reset_mock()

        # 3. Invalid config - apps is not a list
        invalid_cfg_2 = {
            "ssh": {"host": "prod-test", "user": "mayster"},
            "backup": {"local_dir": "/backups"},
            "apps": "not-a-list"
        }
        backup_mayster.validate_config(invalid_cfg_2)
        mock_exit.assert_called_with(1)
        mock_exit.reset_mock()

        # 4. Invalid config - app missing path
        invalid_cfg_3 = {
            "ssh": {"host": "prod-test", "user": "mayster"},
            "backup": {"local_dir": "/backups"},
            "apps": [{"name": "bao"}]
        }
        backup_mayster.validate_config(invalid_cfg_3)
        mock_exit.assert_called_with(1)
        mock_exit.reset_mock()

        # 5. Valid config - container_engines
        valid_cfg_engines = {
            "ssh": {"host": "prod-test", "user": "mayster"},
            "backup": {"local_dir": "/backups", "container_engine": "docker"},
            "apps": [{"name": "bao", "path": "/opt/bao", "container_engine": "podman"}]
        }
        backup_mayster.validate_config(valid_cfg_engines)
        mock_exit.assert_not_called()

        # 6. Invalid config - invalid global container_engine
        invalid_cfg_engines_global = {
            "ssh": {"host": "prod-test", "user": "mayster"},
            "backup": {"local_dir": "/backups", "container_engine": "invalid-engine"},
            "apps": [{"name": "bao", "path": "/opt/bao"}]
        }
        backup_mayster.validate_config(invalid_cfg_engines_global)
        mock_exit.assert_called_with(1)
        mock_exit.reset_mock()

        # 7. Invalid config - invalid app container_engine
        invalid_cfg_engines_app = {
            "ssh": {"host": "prod-test", "user": "mayster"},
            "backup": {"local_dir": "/backups"},
            "apps": [{"name": "bao", "path": "/opt/bao", "container_engine": "invalid-engine"}]
        }
        backup_mayster.validate_config(invalid_cfg_engines_app)
        mock_exit.assert_called_with(1)
        mock_exit.reset_mock()

if __name__ == "__main__":
    unittest.main()
