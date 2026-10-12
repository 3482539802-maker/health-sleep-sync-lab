"""Auto boot, exact instance selection, persistent parking, and failure ordering."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from sleep_sync_lab.automation_client import AutomationClient
from sleep_sync_lab.emulator_startup import ManagedEmulator, load_profile
from sleep_sync_lab.errors import PanelOperationError
from test_automation import CONFIG


class AutoEmulatorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = dict(CONFIG, serial='emulator-5554', adb='synthetic-adb',
                           package='com.huawei.health', process='com.huawei.health:DaemonService',
                           emulator_verified=True)
        console = Path(self.folder.name) / 'synthetic-console'
        console.touch()
        self.profile = dict(enabled=True, instance_index=0, instance_name='SyntheticLab',
                            console=str(console), state_dir=self.folder.name,
                            timeout_seconds=10, frida_server_sha256='a' * 64)
        self.manager = ManagedEmulator(self.config, self.profile)

    def proof(self):
        self.manager.parked.write_text(json.dumps(dict(parked=True, serial=self.config['serial'],
            package=self.config['package'], instance_index=0)), encoding='utf-8')

    def console(self, running=False):
        def command(*args):
            if args == ('list2',):
                return '0,SyntheticLab,0,0,1,101,102' if running else '0,SyntheticLab,0,0,0,-1,-1'
            return ''
        self.manager.command = Mock(side_effect=command)

    def ready(self):
        return patch('sleep_sync_lab.emulator_startup.run_adb',
                     return_value=subprocess.CompletedProcess([], 0, b'1\n', b''))

    def test_stopped_launches_only_configured_instance_once(self):
        self.proof()
        self.console()
        with patch('sleep_sync_lab.emulator_startup.connection_state', side_effect=[None, 'offline', 'device']), self.ready(), patch('sleep_sync_lab.emulator_startup.time.sleep'):
            self.manager.ensure_ready()
        self.manager.command.assert_any_call('launch', '--index', '0')
        self.assertEqual(self.manager.command.call_count, 2)
        self.assertTrue(self.manager.cold_started)
        self.assertTrue(self.manager.lock.exists())
        self.manager.release()

    def test_running_waits_and_never_launches_or_reboots(self):
        self.console(running=True)
        with patch('sleep_sync_lab.emulator_startup.connection_state', side_effect=['offline', 'offline', 'device']), self.ready(), patch('sleep_sync_lab.emulator_startup.time.sleep'):
            self.manager.ensure_ready()
        self.manager.command.assert_called_once_with('list2')
        self.assertFalse(self.manager.cold_started)
        self.manager.release()

    def test_no_parked_proof_refuses_online_boot(self):
        self.console()
        with patch('sleep_sync_lab.emulator_startup.connection_state', return_value=None):
            with self.assertRaisesRegex(PanelOperationError, '开机隔离'):
                self.manager.ensure_ready()
        self.manager.command.assert_called_once_with('list2')
        self.assertFalse(self.manager.lock.exists())

    def test_wrong_or_missing_instance_does_not_launch_anything(self):
        for row in ['1,SyntheticLab,0,0,0,-1,-1', '0,OtherLab,0,0,0,-1,-1']:
            self.manager.command = Mock(return_value=row)
            with self.assertRaisesRegex(PanelOperationError, '实例'):
                self.manager.ensure_ready()
            self.manager.command.assert_called_once_with('list2')
            self.assertFalse(self.manager.lock.exists())

    def test_serial_index_mismatch_and_phone_rejected_before_host_calls(self):
        for serial in ['emulator-5556', 'SYNTHETIC_PHONE']:
            with self.assertRaisesRegex(PanelOperationError, '不匹配'):
                ManagedEmulator(dict(self.config, serial=serial), self.profile)

    def test_unauthorized_never_restarts_or_selects_another_device(self):
        self.console(running=True)
        with patch('sleep_sync_lab.emulator_startup.connection_state', return_value='unauthorized'):
            with self.assertRaisesRegex(PanelOperationError, '尚未授权'):
                self.manager.ensure_ready()
        self.manager.command.assert_called_once_with('list2')
        self.assertFalse(self.manager.lock.exists())

    def test_timeout_preserves_parked_proof_and_releases_startup_lock(self):
        self.proof()
        self.console()
        with patch('sleep_sync_lab.emulator_startup.connection_state', return_value=None), patch('sleep_sync_lab.emulator_startup.time.monotonic', side_effect=[0, 1, 20]), patch('sleep_sync_lab.emulator_startup.time.sleep'):
            with self.assertRaisesRegex(PanelOperationError, '10秒'):
                self.manager.ensure_ready()
        self.assertTrue(self.manager.parked_proof())
        self.assertFalse(self.manager.lock.exists())
        self.manager.command.assert_any_call('launch', '--index', '0')

    def test_concurrent_startup_never_issues_second_launch(self):
        self.manager.acquire()
        second = ManagedEmulator(self.config, self.profile)
        second.command = Mock()
        with self.assertRaisesRegex(PanelOperationError, '另一个'):
            second.ensure_ready()
        second.command.assert_not_called()
        self.assertTrue(self.manager.lock.exists())
        self.manager.release()

    def test_activation_requires_both_verified_uid_rules(self):
        self.proof()
        client = Mock(quarantine_confirmed=False)
        with self.assertRaisesRegex(PanelOperationError, '隔离未确认'):
            self.manager.activate(client)
        client.adb.assert_not_called()
        self.assertTrue(self.manager.parked_proof())

    def test_cold_boot_rejects_package_enabled_outside_managed_flow(self):
        self.proof()
        self.manager.cold_started = True
        client = Mock(quarantine_confirmed=True)
        client.adb.return_value = ''
        with self.assertRaisesRegex(PanelOperationError, '隔离状态'):
            self.manager.activate(client)
        self.assertFalse(any('enable' in call.args for call in client.adb.call_args_list))

    def test_parked_proof_invalidated_before_app_enable(self):
        self.proof()
        self.manager.cold_started = True
        client = Mock(quarantine_confirmed=True)
        def adb(*args):
            if 'list' in args:
                return 'package:com.huawei.health' if self.manager.parked.exists() else ''
            if 'enable' in args:
                self.assertFalse(self.manager.parked.exists())
            if 'pidof' in args:
                return '123'
            return ''
        client.adb.side_effect = adb
        self.manager.activate(client)
        self.assertFalse(self.manager.parked_proof())
        self.assertTrue(self.manager.enabled_by_session)

    def test_frida_hash_mismatch_does_not_start_or_download_server(self):
        client = Mock()
        client.adb.side_effect = ['', 'b' * 64 + '  /data/local/tmp/gt2-frida-server']
        with self.assertRaisesRegex(PanelOperationError, '校验未通过'):
            self.manager.ensure_frida(client)
        self.assertEqual(client.adb.call_count, 2)

    def test_parking_survives_reboot_and_checks_disabled_state(self):
        client = Mock(quarantine_confirmed=True, uid=12345)
        client.adb.side_effect = ['', 'package:com.huawei.health']
        self.manager.park(client)
        self.assertTrue(ManagedEmulator(self.config, self.profile).parked_proof())
        client.adb.assert_any_call('shell', 'pm', 'disable-user', '--user', '0', 'com.huawei.health')

    def test_failed_parking_does_not_create_reboot_permission(self):
        client = Mock(quarantine_confirmed=True)
        client.adb.return_value = ''
        with self.assertRaisesRegex(PanelOperationError, '保存失败'):
            self.manager.park(client)
        self.assertFalse(self.manager.parked_proof())

    def test_old_plan_uses_machine_profile_without_changing_plan_config(self):
        path = Path(self.folder.name) / 'profiles.json'
        path.write_text(json.dumps({self.config['serial']: self.profile}), encoding='utf-8')
        before = dict(self.config)
        with patch('sleep_sync_lab.emulator_startup.profile_path', return_value=path):
            manager = load_profile(self.config)
        self.assertEqual(self.config, before)
        self.assertEqual(manager.index, 0)

    def test_guard_attach_failure_parks_app_before_releasing_startup_lock(self):
        client = AutomationClient(self.config)
        client.verify_adb = Mock()
        order = []
        def validated():
            order.append('validate')
            client.validated, client.uid = True, 12345
        client.validate_environment = validated
        client.adb = Mock(side_effect=lambda *args: order.append('stop'))
        def quarantine(enabled):
            order.append('quarantine' if enabled else 'online')
            client.quarantine_confirmed = enabled
        client.set_quarantine = quarantine
        client.set_offline = Mock()
        client.launch_original = Mock(side_effect=lambda: order.append('app-launch'))
        manager = Mock()
        manager.ensure_ready.side_effect = lambda: order.append('emulator-ready')
        manager.activate.side_effect = lambda c: order.append('app-enable')
        manager.park.side_effect = lambda c: order.append('park')
        manager.release.side_effect = lambda: order.append('unlock')
        with patch('sleep_sync_lab.automation_client.load_profile', return_value=manager), patch('sleep_sync_lab.client.Client.__enter__', side_effect=RuntimeError('synthetic attach failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic'):
                client.__enter__()
        self.assertLess(order.index('emulator-ready'), order.index('validate'))
        self.assertLess(order.index('quarantine'), order.index('app-enable'))
        self.assertLess(order.index('app-enable'), order.index('app-launch'))
        self.assertLess(order.index('park'), order.index('unlock'))


if __name__ == '__main__':
    unittest.main()
