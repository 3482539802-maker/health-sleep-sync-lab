"""Offline startup, temporary PC write guard, bounded native requests, stopped exit."""
import json
import re
import subprocess
import time
from pathlib import Path
from importlib.resources import files
from .client import Client
from .model import check
from .errors import PanelOperationError


class AutomationClient(Client):
    def process_exists(self):
        result = subprocess.run([self.config['adb'], '-s', self.config['serial'], 'shell', 'pidof',
                                 self.config['process']], capture_output=True, timeout=40)
        return bool(result.stdout.strip())

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
            self.adb('shell', 'am', 'start', '-W', '-n', components[0])
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
                if attempt < 2:
                    time.sleep(.3)
        error = PanelOperationError('电脑模拟器Root检查未通过，尚未读取备份或写入。请检查模拟器Root和ADB连接；原始输出在私有失败记录中。')
        error.attempts = failures
        raise error from last_error

    def __enter__(self):
        # Verify PC environment before any stop/start/network mutation.
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
            self.script.exports_sync.auto_guard()
            self.set_offline(False)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

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
        # stats() returns a Promise in the old adapter; request it separately.
        result = self.script.exports_sync.auto_snapshot()
        result['sleep_stats'] = self.stats()
        return result

    def health_read(self, kind, start, end):
        return self.script.exports_sync.auto_health_read(kind, start, end)

    def local_sleep(self):
        return self.script.exports_sync.auto_local_sleep()

    def upload_health(self, records):
        return self.script.exports_sync.auto_upload_health(json.dumps(records))

    def upload_sport(self, records):
        return self.script.exports_sync.auto_upload_sport(json.dumps(records))

    def sport_summary(self, value):
        return self.script.exports_sync.auto_upload_sport_summary(json.dumps(value))

    def sleep_summary(self, value):
        return self.script.exports_sync.auto_upload_sleep_summary(json.dumps(value))

    def delete_sleep(self, start, end):
        return self.script.exports_sync.auto_delete_sleep(start, end)

    def clear_local_sleep(self, start, end):
        return self.script.exports_sync.auto_clear_local_sleep(start, end)

    def __exit__(self, *exc):
        try:
            if self.validated:
                self.adb('shell', 'am', 'force-stop', self.config['package'])
        finally:
            try:
                super().__exit__(*exc)
            except Exception:
                if self.process_exists():
                    raise
            finally:
                self.set_offline(False)
                if self.lock_owned:
                    self.session_lock.unlink(missing_ok=True)
                    self.lock_owned = False
