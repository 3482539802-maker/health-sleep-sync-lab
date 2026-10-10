"""Offline startup, temporary PC write guard, bounded native requests, stopped exit."""
import json
import re
import time
from pathlib import Path
from importlib.resources import files
from .client import Client
from .model import check
from .errors import PanelOperationError, AdbOperationError, AdbLaunchError
from .host_process import run_adb


class AutomationClient(Client):
    def process_exists(self):
        result = run_adb(self.config['adb'], ['shell', 'pidof', self.config['process']], self.config['serial'])
        return bool(result.stdout.strip())

    def verify_adb(self):
        result = run_adb(self.config['adb'], ['version'], timeout=10)
        if b'Android Debug Bridge version' not in result.stdout:
            error = PanelOperationError('配置的ADB程序未返回正确版本信息。请在私有配置的 adb 字段选择可用的ADB。')
            error.command, error.stdout, error.stderr = ['version'], result.stdout, result.stderr
            raise error

    def launch_original(self):
        """Launch the resolved original activity directly, never generate monkey events."""
        try:
            resolved = self.adb('shell', 'cmd', 'package', 'resolve-activity', '--brief',
                                '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER',
                                self.config['package'])
            components = [line.strip() for line in resolved.splitlines()
                          if re.fullmatch(r'com\.huawei\.health/[A-Za-z0-9_.$]+', line.strip())]
            if len(components) != 1:
                raise PanelOperationError('未找到电脑原版运动健康的启动入口，尚未写入。')
            output = self.adb('shell', 'am', 'start', '-W', '-n', components[0])
            if re.search(r'(?im)^\s*(Error:|Exception|Status:\s*(?:error|timeout))', output):
                error = PanelOperationError('电脑原版启动命令报告失败，未进入健康操作。请检查应用安装和模拟器状态；原输出见私有记录。')
                error.command, error.stdout = ['shell', 'am', 'start'], output
                raise error
        except PanelOperationError:
            raise
        except Exception as error:
            raise PanelOperationError('无法启动电脑原版运动健康，尚未写入。请检查模拟器连接和应用状态。') from error
        for _ in range(40):
            if self.process_exists():
                return
            time.sleep(.25)
        raise PanelOperationError('电脑原版已打开，但健康后台未就绪，尚未写入。请查看私有失败记录。')

    def __init__(self, config):
        super().__init__(config)
        self.firewall_rules = []
        self.uid = None
        self.validated = False
        self.lock_owned = False
        self.extra_sessions = []
        self.guard_reports = []
        self.guard_scripts = []
        self.guarded_pids = set()
        self.spawn_device = None
        self.paused_spawns = []
        self.quarantine_confirmed = False
        self.session_lock = Path.home() / '.health_sleep_sync_lab' / ('session_' + re.sub(r'[^a-zA-Z0-9_]', '_', self.config['serial']) + '.lock')

    def verify_root(self):
        # This is a read-only readiness probe, never a retry of a health mutation.
        from .audit import failure_details
        failures = []
        last_error = None
        for attempt in range(3):
            try:
                check(self.adb('shell', 'su', '-c', 'id').startswith('uid=0'), 'PC emulator root required')
                return
            except Exception as error:
                last_error = error
                failures.append({'attempt': attempt + 1, 'diagnostics': failure_details(error)})
                if isinstance(error, AdbLaunchError) or (isinstance(error, AdbOperationError) and (error.returncode & 0xffffffff) >= 0x80000000):
                    break
                if attempt < 2:
                    time.sleep(.3)
        error = PanelOperationError('电脑模拟器Root检查未通过，尚未读取备份或写入。请检查模拟器Root和ADB连接；原始输出在私有失败记录中。')
        error.attempts = failures
        raise error from last_error

    def __enter__(self):
        # Verify PC environment before any stop/start/network mutation.
        self.verify_adb()
        check(re.fullmatch(r'emulator-\d+|127\.0\.0\.1:\d+', self.config['serial']) is not None,
              'Only the configured PC emulator is supported')
        check(self.config.get('emulator_verified') is True, 'PC emulator verification required')
        check(self.adb('shell', 'getprop', 'ro.product.cpu.abi') in ['x86', 'x86_64'], 'PC CPU mismatch')
        for key, prop in [('expected_emulator_model', 'ro.product.model'), ('expected_emulator_board', 'ro.product.board')]:
            check(bool(self.config.get(key)) and self.adb('shell', 'getprop', prop) == self.config[key], 'PC profile mismatch')
        check(self.config['package'] == 'com.huawei.health' and self.config['process'] == 'com.huawei.health:DaemonService', 'Original daemon required')
        check(self.config['app_version'] == '17.0.8.310', 'Unsupported adapter version')
        package = self.adb('shell', 'dumpsys', 'package', self.config['package'])
        check('versionName=' + self.config['app_version'] in package, 'Installed version mismatch')
        uid = re.search(r'\buserId=(\d+)', package)
        check(uid is not None, 'Package UID missing')
        self.uid = int(uid.group(1))
        self.verify_root()
        self.session_lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.session_lock.open('x', encoding='utf-8') as handle:
                handle.write('PC emulator session active; inspect before removing after a crash')
        except FileExistsError:
            raise ValueError('Another PC emulator session or stale session lock exists')
        self.lock_owned = True
        self.validated = True
        try:
            self.adb('shell', 'am', 'force-stop', self.config['package'])
            self.set_offline(True)
            self.launch_original()
            super().__enter__()
            self.script.unload()
            source = files('frida_tools').joinpath('bridges/java.js').read_text(encoding='utf-8') + '\n;globalThis.Java=bridge;\n'
            source += Path(__file__).with_name('native.js').read_text(encoding='utf-8')
            source += '\n' + Path(__file__).with_name('automation_native.js').read_text(encoding='utf-8')
            self.script = self.session.create_script(source)
            self.script.load()
            self.script.exports_sync.configure(json.dumps(self.config))
            self.guard_reports.append({'process': self.config['process'], 'guard': self.script.exports_sync.auto_guard()})
            self.guard_other_processes(source)
            self.enable_process_gate()
            self.assert_guarded()
            self.set_quarantine(False)
            self.set_offline(False)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def guard_other_processes(self, source):
        """Protect the main app too before lifting the shared UID firewall."""
        import frida
        device = frida.get_device_manager().add_remote_device('127.0.0.1:' + str(self.config['frida_remote_port']))
        self.spawn_device = device
        processes = [p for p in device.enumerate_processes() if p.name == self.config['package'] or p.name.startswith(self.config['package'] + ':')]
        guarded = {self.config['process']}
        for process in processes:
            if process.name == self.config['process']:
                self.guarded_pids.add(process.pid)
                continue
            session = device.attach(process.pid)
            self.extra_sessions.append(session)
            script = session.create_script(source)
            script.load()
            script.exports_sync.configure(json.dumps(self.config))
            report = script.exports_sync.auto_guard()
            self.guard_reports.append({'process': process.name, 'guard': report})
            self.guard_scripts.append(script)
            self.guarded_pids.add(process.pid)
            guarded.add(process.name)
        check(all(p.name in guarded for p in device.enumerate_processes() if
                  p.name == self.config['package'] or p.name.startswith(self.config['package'] + ':')),
              'An unguarded app process appeared; networking remains paused')

    def enable_process_gate(self):
        # New app processes stay suspended. Their Java hooks cannot safely be installed
        # while Android startup is suspended, so this session stops instead of resuming.
        self.spawn_device.on('spawn-added', self.on_spawn)
        self.spawn_device.enable_spawn_gating()

    def on_spawn(self, spawn):
        name = spawn.identifier or ''
        if name == self.config['package'] or name.startswith(self.config['package'] + ':'):
            self.paused_spawns.append({'pid': spawn.pid, 'process': name})
        else:
            self.spawn_device.resume(spawn.pid)

    def assert_guarded(self):
        if self.spawn_device is None:
            return
        check(not self.paused_spawns, 'A new app process was paused; session stopped before further requests')
        processes = self.spawn_device.enumerate_processes()
        check(all(p.pid in self.guarded_pids for p in processes if p.name == self.config['package'] or
                  p.name.startswith(self.config['package'] + ':')), 'Unguarded app process; session stopped')

    def guard_state(self):
        self.assert_guarded()
        return {'processes': self.guard_reports, 'daemon': self.script.exports_sync.auto_guard_state(),
                'other_processes': [s.exports_sync.auto_guard_state() for s in self.guard_scripts],
                'new_process_gate': self.spawn_device is not None, 'paused_spawns': self.paused_spawns}

    def configure(self, **updates):
        self.assert_guarded()
        return super().configure(**updates)

    def stats(self):
        self.assert_guarded()
        return super().stats()

    def set_quarantine(self, enabled):
        """Keep the research PC app offline outside controlled sessions, including normal UI starts."""
        for binary in ['iptables', 'ip6tables']:
            rule = binary + ' -m owner --uid-owner ' + str(self.uid) + ' -m comment --comment health_panel_quarantine -j REJECT'
            # Probe existence without interpreting an absent rule as an ADB failure.
            output = self.adb('shell', "su -c '" + rule.replace(' -m owner', ' -C OUTPUT -m owner', 1) + " >/dev/null 2>&1; echo $?' ")
            exists = output.strip() == '0'
            if enabled and not exists:
                self.adb('shell', "su -c '" + rule.replace(' -m owner', ' -I OUTPUT -m owner', 1) + "'")
            elif not enabled and exists:
                self.adb('shell', "su -c '" + rule.replace(' -m owner', ' -D OUTPUT -m owner', 1) + "'")
            output = self.adb('shell', "su -c '" + rule.replace(' -m owner', ' -C OUTPUT -m owner', 1) + " >/dev/null 2>&1; echo $?' ")
            check((output.strip() == '0') == enabled, 'PC isolation rule verification failed')
        self.quarantine_confirmed = enabled

    def set_offline(self, enabled):
        if enabled:
            for binary in ['iptables', 'ip6tables']:
                self.adb('shell', "su -c '" + binary + ' -I OUTPUT -m owner --uid-owner ' + str(self.uid) +
                         " -m comment --comment health_panel_session -j REJECT'")
                self.firewall_rules.append(binary)
        else:
            for binary in self.firewall_rules[:]:
                self.adb('shell', "su -c '" + binary + ' -D OUTPUT -m owner --uid-owner ' + str(self.uid) +
                         " -m comment --comment health_panel_session -j REJECT'")
                self.firewall_rules.remove(binary)

    def snapshot(self):
        self.assert_guarded()
        # stats() returns a Promise in the old adapter; request it separately.
        result = self.script.exports_sync.auto_snapshot()
        result['sleep_stats'] = self.stats()
        return result

    def health_read(self, kind, start, end):
        self.assert_guarded()
        return self.script.exports_sync.auto_health_read(kind, start, end)

    def local_sleep(self):
        self.assert_guarded()
        return self.script.exports_sync.auto_local_sleep()

    def upload_health(self, records):
        self.assert_guarded()
        return self.script.exports_sync.auto_upload_health(json.dumps(records))

    def upload_sport(self, records):
        self.assert_guarded()
        return self.script.exports_sync.auto_upload_sport(json.dumps(records))

    def sport_summary(self, value):
        self.assert_guarded()
        return self.script.exports_sync.auto_upload_sport_summary(json.dumps(value))

    def sleep_summary(self, value):
        self.assert_guarded()
        return self.script.exports_sync.auto_upload_sleep_summary(json.dumps(value))

    def delete_sleep(self, start, end):
        self.assert_guarded()
        return self.script.exports_sync.auto_delete_sleep(start, end)

    def clear_local_sleep(self, start, end):
        self.assert_guarded()
        return self.script.exports_sync.auto_clear_local_sleep(start, end)

    def retire_acknowledged_sleep_deletes(self, start, end):
        self.assert_guarded()
        return self.script.exports_sync.auto_retire_acknowledged_sleep_deletes(start, end)

    def __exit__(self, *exc):
        try:
            if self.validated:
                try:
                    self.adb('shell', 'am', 'force-stop', self.config['package'])
                finally:
                    self.set_quarantine(True)
        finally:
            if self.validated and not self.quarantine_confirmed:
                # If stop/quarantine failed after a connected session, block first.
                # If even this fails, keep hooks/forward/lock attached for diagnosis.
                self.set_offline(True)
            try:
                try:
                    if self.spawn_device is not None:
                        self.spawn_device.disable_spawn_gating()
                        self.spawn_device.off('spawn-added', self.on_spawn)
                    for session in self.extra_sessions:
                        try:
                            session.detach()
                        except Exception:
                            if self.process_exists():
                                raise
                    self.extra_sessions.clear()
                finally:
                    super().__exit__(*exc)
            except Exception:
                if self.process_exists():
                    raise
            finally:
                # If stop/quarantine failed, retain session firewall and lock.
                if self.quarantine_confirmed or not self.validated:
                    self.set_offline(False)
                if self.lock_owned and self.quarantine_confirmed:
                    self.session_lock.unlink(missing_ok=True)
                    self.lock_owned = False
