"""Local desktop panel. All real settings, plans and results stay outside this repository."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from .automation import execute, load, prepare, private
from .automation_model import describe
from .errors import user_message
from .audit import source_fingerprints
from .presets import preset_request
from .batch import prepare_batch, describe_batch, execute_batch, selected_dates
from .input_validation import (InputValidationError, TARGETS, number, hours, time_range,
                               activity_windows, date_input, validate_config, validate_request)
from .diagnostics import record_incident
from .host_process import silence_windows_crash_dialogs


def review_saved(path):
    return describe_batch(path) if load(path).get('batch_format_version') else describe(load(path))


LOADED_SOURCE_HASHES = source_fingerprints()


def source_is_current():
    return source_fingerprints() == LOADED_SOURCE_HASHES


def numeric(text):
    return number(text.strip(), '数值', -200000, 200000)


def read_config(value):
    if not str(value).strip():
        raise InputValidationError('私有配置', '', '请先选择仓库外的JSON配置文件')
    path = private(value)
    try:
        config = load(path)
    except json.JSONDecodeError as error:
        raise InputValidationError('私有配置', path.name, f'JSON格式有误，第{error.lineno}行第{error.colno}列') from error
    except OSError as error:
        raise InputValidationError('私有配置', path.name, '文件无法读取，请检查是否存在及访问权限') from error
    if not isinstance(config, dict):
        raise InputValidationError('私有配置', path.name, 'JSON顶层需要对象格式')
    return config


class Panel:
    def __init__(self, root, config_path=None, runs=None):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.plan_path = None
        self.plan_inputs = None
        self.pending_inputs = None
        self.field_entries = {}
        self.runs = private(runs or Path.home() / '.health_sleep_sync_lab' / 'runs')
        self.config_path = tk.StringVar(value=str(config_path or ''))
        self.date = tk.StringVar(value=datetime.now().strftime('%Y-%m-%d'))
        initial_error = None
        if config_path:
            try:
                initial = read_config(config_path)
                date_input(initial.get('date', ''))
                self.date.set(initial['date'])
            except Exception as error:
                initial_error = error
        self.end_date = tk.StringVar(value='')
        self.scheme = tk.StringVar(value='自定义')
        self.vars = {}
        root.title('运动健康 · 数据调整面板')
        width, height = min(980, root.winfo_screenwidth() - 80), min(860, root.winfo_screenheight() - 100)
        root.geometry(f'{width}x{height}')
        root.minsize(min(900, width), min(690, height))
        root.configure(bg='#f3f5f9')
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background='#f3f5f9')
        style.configure('TLabel', background='#f3f5f9', font=('Microsoft YaHei UI', 10))
        style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=8)
        outer = ttk.Frame(root, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='运动健康数据调整', font=('Microsoft YaHei UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='选择日期与目标 → 生成并查看计划 → 执行 → 手机同步验收').pack(anchor='w', pady=(4, 14))
        setup = ttk.Frame(outer)
        setup.pack(fill='x')
        ttk.Label(setup, text='私有配置').grid(row=0, column=0, sticky='w')
        self.field_entries['私有配置'] = ttk.Entry(setup, textvariable=self.config_path)
        self.field_entries['私有配置'].grid(row=0, column=1, columnspan=2, sticky='ew', padx=10)
        ttk.Button(setup, text='选择文件', command=self.choose_config).grid(row=0, column=3)
        ttk.Label(setup, text='日期 / 起床日').grid(row=1, column=0, sticky='w', pady=8)
        self.field_entries['日期 / 起床日'] = ttk.Entry(setup, textvariable=self.date, width=18)
        self.field_entries['日期 / 起床日'].grid(row=1, column=1, sticky='w', padx=10)
        ttk.Label(setup, text='批量截止日期（含当天）').grid(row=1, column=2, sticky='w')
        self.field_entries['批量截止日期'] = ttk.Entry(setup, textvariable=self.end_date, width=18)
        self.field_entries['批量截止日期'].grid(row=1, column=3, padx=8)
        ttk.Label(setup, text='生成方案').grid(row=2, column=0, sticky='w')
        self.scheme_box = ttk.Combobox(setup, textvariable=self.scheme, values=['自定义', '默认方案'], state='readonly', width=16)
        self.scheme_box.grid(row=2, column=1, sticky='w', padx=10)
        self.scheme_box.bind('<<ComboboxSelected>>', self.scheme_changed)
        ttk.Label(setup, text='默认：三环只增、睡眠随机、步数不改；截止留空为单日', foreground='#264e81').grid(row=3, column=0, columnspan=4, sticky='w', pady=(6, 0))
        setup.columnconfigure(1, weight=1)
        tabs = ttk.Notebook(outer)
        self.tabs = tabs
        self.parameters_visible = True
        tabs.pack(fill='x', pady=10)
        sport = ttk.Frame(tabs, padding=15)
        sleep = ttk.Frame(tabs, padding=15)
        tabs.add(sport, text='三环与每日步数')
        tabs.add(sleep, text='睡眠')
        ttk.Label(sport, text='留空表示不改；填写单值，或用“最小值-最大值”随机选择。').grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 10))
        for row, (name, label) in enumerate([('calorie', '活动热量 kcal'), ('exercise', '锻炼分钟'), ('steps', '每日步数'), ('active', '活动小时 0–24')], 1):
            self.entry(sport, row, name, label, '')
        self.entry(sport, 5, 'activity_windows', '允许分配的时段', '12:00-14:30,18:00-24:00', width=42)
        self.entry(sport, 6, 'active_hours', '优先新增活动小时', '', hint='逗号分隔，如 9,17；留空由计划选择')
        self.preserve = tk.BooleanVar(value=True)
        ttk.Checkbutton(sport, text='步数与锻炼优先安排在已有活动小时', variable=self.preserve).grid(row=7, column=0, columnspan=3, sticky='w', pady=8)
        ttk.Label(sport, text='部分日总数只接受增加。执行结果会分别显示请求值与实际云端值。', foreground='#795322').grid(row=8, column=0, columnspan=3, sticky='w')
        self.sleep_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(sleep, text='调整所选日期的整晚睡眠', variable=self.sleep_enabled).grid(row=0, column=0, columnspan=3, sticky='w')
        self.sleep_mode = tk.StringVar(value='shift')
        ttk.Radiobutton(sleep, text='整体提前，保持原阶段顺序', variable=self.sleep_mode, value='shift').grid(row=1, column=0, columnspan=3, sticky='w', pady=8)
        self.entry(sleep, 2, 'advance_minutes', '提前分钟 / 随机范围', '120')
        ttk.Radiobutton(sleep, text='随机选择入睡、醒来，并生成合成阶段', variable=self.sleep_mode, value='random').grid(row=3, column=0, columnspan=3, sticky='w', pady=8)
        self.entry(sleep, 4, 'bedtime_range', '入睡区间 HH:MM-HH:MM', '23:00-01:00')
        self.entry(sleep, 5, 'wake_range', '醒来区间 HH:MM-HH:MM', '07:00-09:00')
        self.entry(sleep, 6, 'duration_range', '总睡眠分钟范围', '420-540')
        self.restore_score = tk.BooleanVar(value=False)
        ttk.Checkbutton(sleep, text='保留备份中的原评分（不是重新计算）', variable=self.restore_score).grid(row=7, column=0, columnspan=3, sticky='w', pady=8)
        ttk.Label(sleep, text='先生成并保存计划，再在手机原版仅删所选晚并同步；程序核验云端为空后重建。', foreground='#795322').grid(row=8, column=0, columnspan=3, sticky='w')
        actions = ttk.Frame(outer)
        self.actions = actions
        actions.pack(fill='x', pady=8)
        self.buttons = []
        for title, fn in [('生成计划', self.prepare), ('打开已保存计划', self.open_plan), ('打开备份目录', self.open_folder), ('执行当前计划', self.apply), ('展开/收起参数', self.toggle_parameters)]:
            button = ttk.Button(actions, text=title, command=fn)
            button.pack(side='left', padx=(0, 8))
            self.buttons.append(button)
        self.delete_confirm = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text='已备份计划，并在手机原版删除所选各晚睡眠、完成正常同步', variable=self.delete_confirm).pack(anchor='w', pady=(0, 6))
        self.status = tk.StringVar(value='填写目标后生成计划。启动面板不会自动修改任何数据。')
        ttk.Label(outer, textvariable=self.status, foreground='#264e81').pack(anchor='w', pady=5)
        output_frame = ttk.Frame(outer)
        output_frame.pack(fill='both', expand=True)
        self.output = tk.Text(output_frame, height=12, wrap='word', font=('Microsoft YaHei UI', 10), bg='white', relief='flat', padx=12, pady=10)
        scroll = ttk.Scrollbar(output_frame, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.output.pack(side='left', fill='both', expand=True)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)
        for variable in [self.config_path, self.date, self.end_date, self.scheme, self.preserve,
                         self.sleep_enabled, self.sleep_mode, self.restore_score, *self.vars.values()]:
            variable.trace_add('write', self.form_changed)
        if initial_error:
            root.after(0, lambda error=initial_error: self.display_error(self.incident(error, '加载配置')))

    def entry(self, frame, row, name, label, value, width=25, hint=''):
        var = tk.StringVar(value=value)
        self.vars[name] = var
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=4)
        widget = ttk.Entry(frame, textvariable=var, width=width)
        widget.grid(row=row, column=1, sticky='w', padx=12, pady=4)
        self.field_entries[label] = widget
        if hint:
            ttk.Label(frame, text=hint, foreground='#69717d').grid(row=row, column=2, sticky='w')

    def choose_config(self):
        value = filedialog.askopenfilename(title='选择仓库外的私有配置', filetypes=[('JSON', '*.json')])
        if value:
            self.config_path.set(value)
            try:
                config = read_config(value)
                date_input(config.get('date', ''))
                self.date.set(config['date'])
                if self.scheme.get() == '默认方案':
                    self.scheme_changed()
            except Exception as error:
                self.display_error(self.incident(error, '选择配置'))

    def form_values(self):
        return {'config_path': self.config_path.get(), 'date': self.date.get(), 'end_date': self.end_date.get(),
                'scheme': self.scheme.get(), 'fields': {key: var.get() for key, var in self.vars.items()},
                'preserve_active_hours': self.preserve.get(), 'sleep_enabled': self.sleep_enabled.get(),
                'sleep_mode': self.sleep_mode.get(), 'restore_score': self.restore_score.get()}

    def form_changed(self, *args):
        if self.plan_path:
            self.delete_confirm.set(False)
            if not self.busy:
                self.status.set('输入已改变。请重新生成计划，或重新打开保存的计划后执行。')

    def incident(self, error, phase, directory=None):
        return record_incident(error, self.runs, phase, self.form_values(), directory)

    def display_error(self, packet):
        message = packet['phase'] + '失败：\n' + packet['message']
        if packet.get('evidence'):
            message += '\n\n失败记录目录：\n' + packet['evidence']
        self.status.set('操作已停止，具体原因见下方记录及弹窗。')
        self.output.insert('end', '\n' + message + '\n')
        self.output.see('end')
        field = packet.get('field') or ''
        if field:
            if not self.parameters_visible:
                self.toggle_parameters()
            label = '私有配置' if field.startswith(('私有配置.', 'default_preset.', '默认')) else field
            entry = next((widget for name, widget in self.field_entries.items() if name.startswith(label)), None)
            if entry:
                if entry.master in self.tabs.winfo_children():
                    self.tabs.select(entry.master)
                entry.focus_set()
                entry.selection_range(0, 'end')
        messagebox.showerror(packet['phase'] + '失败', message, parent=self.root)

    def preset_controls(self):
        state = 'disabled' if self.scheme.get() == '默认方案' else 'normal'
        for label, widget in self.field_entries.items():
            if label not in ['私有配置', '日期 / 起床日', '批量截止日期']:
                widget.configure(state=state)
        for pane in self.tabs.winfo_children():
            for widget in pane.winfo_children():
                if widget.winfo_class() in ['TCheckbutton', 'TRadiobutton'] and str(widget.cget('variable')) != str(self.restore_score):
                    widget.configure(state=state)

    def scheme_changed(self, event=None):
        self.plan_path = None
        self.plan_inputs = None
        self.delete_confirm.set(False)
        if not self.parameters_visible:
            self.toggle_parameters()
        if self.scheme.get() != '默认方案':
            self.preset_controls()
            self.status.set('自定义方案使用输入框；更改参数后重新生成。')
            return
        try:
            spec = read_config(self.config_path.get()).get('default_preset')
            request = preset_request(spec)
            for key in ['calorie', 'exercise', 'active']:
                self.vars[key].set('-'.join(map(str, spec[key]['range'])))
            self.vars['steps'].set('')
            self.vars['active_hours'].set('')
            self.vars['activity_windows'].set('00:00-24:00')
            self.preserve.set(True)
            self.sleep_enabled.set(True)
            self.sleep_mode.set('random')
            self.restore_score.set(spec.get('restore_original_score', False))
            for key in ['bedtime_range', 'wake_range', 'duration_range']:
                self.vars[key].set('-'.join(map(str, request['sleep'][key])))
            self.status.set('默认方案按私有预设加权抽样；输入框显示范围。要编辑范围请切到自定义。')
            self.preset_controls()
        except Exception as error:
            self.scheme.set('自定义')
            self.preset_controls()
            self.display_error(self.incident(error, '默认方案配置'))

    def request(self):
        if self.scheme.get() == '默认方案':
            spec = read_config(self.config_path.get()).get('default_preset')
            preset_request(spec)
            spec['restore_original_score'] = self.restore_score.get()
            return preset_request(spec)
        data = {name: number(self.vars[name].get().strip(), *TARGETS[name]) for name in TARGETS}
        data.update(activity_windows=activity_windows(self.vars['activity_windows'].get()), preserve_active_hours=self.preserve.get(),
                    active_hours=hours(self.vars['active_hours'].get()))
        if self.sleep_enabled.get():
            s = {'mode': self.sleep_mode.get(), 'restore_original_score': self.restore_score.get()}
            if s['mode'] == 'shift':
                s['advance_minutes'] = number(self.vars['advance_minutes'].get().strip(), '提前分钟', -720, 720, True)
            else:
                def clocks(key, label):
                    values = time_range(self.vars[key].get(), label)
                    return [f'{v//60:02}:{v%60:02}' for v in values]
                s.update(bedtime_range=clocks('bedtime_range', '入睡区间'), wake_range=clocks('wake_range', '醒来区间'),
                         duration_range=number(self.vars['duration_range'].get().strip(), '总睡眠分钟范围', 240, 720, True), stage_model='natural_v2')
            data['sleep'] = s
        return validate_request(data)

    def task(self, fn, phase='操作', directory=None):
        if self.busy:
            return
        if not source_is_current():
            self.status.set('程序文件已更新。请关闭面板再重新打开，加载新版后继续。尚未启动本次操作。')
            messagebox.showinfo('需要重新打开面板', self.status.get())
            return
        self.busy = True
        inputs = self.form_values()
        for button in self.buttons:
            button.configure(state='disabled')
        def worker():
            try:
                self.events.put(('done', fn()))
            except Exception as error:
                self.events.put(('error', record_incident(error, self.runs, phase, inputs, directory)))
        threading.Thread(target=worker, daemon=False).start()

    def prepare(self):
        try:
            if self.busy:
                return
            config = read_config(self.config_path.get())
            config['date'] = self.date.get().strip()
            date_input(config['date'])
            validate_config(config)
            # Recompute overnight window when the panel date changes.
            config.pop('sleep_query_start_ms', None)
            config.pop('sleep_query_end_ms', None)
            request = self.request()
            end = self.end_date.get().strip() or config['date']
            end_day = date_input(end, '批量截止日期')
            first_day = date_input(config['date'])
            if end_day < first_day or (end_day - first_day).days >= 31:
                raise InputValidationError('批量截止日期', end, f'不得早于开始日期{config["date"]}，一次最多31天')
            dates = selected_dates(config['date'], end)
            if len(dates) > 1 and not request.get('preset'):
                messagebox.showerror('批量方案', '日期区间批量目前仅支持默认方案。')
                return
            directory = self.runs / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            self.plan_path = None
            self.delete_confirm.set(False)
            self.pending_inputs = self.form_values()
            log = lambda s: self.events.put(('log', s))
            self.task(lambda: ('plan', prepare_batch(config, request, config['date'], end, directory, log)
                                   if len(dates) > 1 else prepare(config, request, directory, log)), '生成计划', directory)
        except Exception as error:
            self.display_error(self.incident(error, '生成计划输入检查'))

    def open_plan(self):
        value = filedialog.askopenfilename(title='打开私有计划', filetypes=[('JSON', '*.json')])
        if value:
            try:
                self.restore_plan(value)
            except Exception as error:
                self.display_error(self.incident(error, '打开保存的计划'))

    def restore_plan(self, value):
        path = private(value)
        plan = load(path)
        review = review_saved(path)
        batch = bool(plan.get('batch_format_version'))
        if batch:
            manifest = plan
            plan = load(path.parent / manifest['days'][0]['path'])
        request = plan['request']
        def formatted(value):
            return '-'.join(map(str, value)) if isinstance(value, list) else '' if value is None else str(value)
        self.plan_path = path
        self.date.set(plan['config']['date'])
        if hasattr(self, 'end_date'):
            self.end_date.set(manifest['end'] if batch else '')
            self.scheme.set('默认方案' if request.get('preset') else '自定义')
        config_path = path.parent / 'config_private.json'
        if config_path.exists():
            self.config_path.set(str(config_path))
        for key in ['calorie', 'exercise', 'steps', 'active']:
            display = request['preset'][key]['range'] if request.get('preset') and key != 'steps' else request.get(key)
            self.vars[key].set(formatted(display))
        self.vars['activity_windows'].set(request.get('activity_windows', '12:00-14:30,18:00-24:00'))
        self.vars['active_hours'].set(','.join(map(str, request.get('active_hours', []))))
        self.preserve.set(request.get('preserve_active_hours', True))
        sleep = request.get('sleep') or {}
        self.sleep_enabled.set(bool(sleep))
        self.sleep_mode.set(sleep.get('mode', 'shift'))
        self.restore_score.set(sleep.get('restore_original_score', False))
        for key, default in [('advance_minutes', 120), ('bedtime_range', ['23:00', '01:00']),
                             ('wake_range', ['07:00', '09:00']), ('duration_range', [420, 540])]:
            self.vars[key].set(formatted(sleep.get(key, default)))
        self.delete_confirm.set(False)
        self.show_review(review)
        self.status.set('已恢复保存的计划；修改输入后须重新生成计划。启动不会执行。')
        if hasattr(self, 'end_date'):
            self.preset_controls()
            self.plan_inputs = self.form_values()

    def apply(self):
        if not self.plan_path:
            messagebox.showinfo('尚无计划', '先生成或打开计划，查看日期和目标。')
            return
        if self.busy:
            return
        if self.plan_inputs is None or self.form_values() != self.plan_inputs:
            messagebox.showinfo('输入已变化', '当前输入与保存的计划不同。请重新生成，或重新打开保存的计划恢复输入；本次未执行。', parent=self.root)
            return
        try:
            plan = load(self.plan_path)
            review_saved(self.plan_path)
        except Exception as error:
            self.display_error(self.incident(error, '执行前计划检查', self.plan_path.parent))
            return
        batch = bool(plan.get('batch_format_version'))
        if (batch or plan['sleep']) and not self.delete_confirm.get():
            messagebox.showinfo('需要手机先清理旧睡眠', '先保存并核对计划，在手机原版“睡眠→所有数据”仅删除计划所选各晚，完成正常同步后勾选。程序还会验证云端分段及汇总为空；不会自动删除其他日期。')
            return
        plan_path = self.plan_path
        confirmed = self.delete_confirm.get()
        log = lambda s: self.events.put(('log', s))
        self.task(lambda: ('result', execute_batch(plan_path, confirmed, log, phone_deleted=confirmed) if batch else
                          execute(plan_path, allow_sleep_delete=confirmed, log=log, phone_deleted=confirmed)), '执行计划', plan_path.parent)

    def show_review(self, value):
        if hasattr(self, 'tabs') and self.parameters_visible:
            self.toggle_parameters()
        self.output.delete('1.0', 'end')
        if value.get('batch'):
            self.output.insert('end', '批量日期：' + value['start'] + ' 至 ' + value['end'] + '（含首尾）\n')
            self.output.insert('end', '执行前核验全部日期；按日执行，失败停止后续日期。确认框适用于下方全部睡眠日期。\n\n')
            for day in value['days']:
                self.output.insert('end', '\n'.join(self.review_lines(day)) + '\n\n')
            return
        self.output.insert('end', '\n'.join(self.review_lines(value)) + '\n')

    def review_lines(self, value):
        names = {'calorie': '活动热量', 'exercise': '锻炼分钟', 'steps': '步数', 'active': '活动小时'}
        target_text = '，'.join(names[k] + ' ' + str(v) for k, v in value['targets'].items() if v is not None)
        lines = ['计划日期：' + value['date'], '运动目标：' + (target_text or '不改'),
                 '运动分钟修改：' + str(value['sport_minutes_changed']) + '；新增锻炼分钟：' + str(value['exercise_minutes_added']) + '；新增活动小时：' + str(value['active_hours_added'])]
        if 'sampled_targets' in value:
            lines.append('独立抽样：' + '，'.join(names[k] + ' ' + str(v) for k, v in value['sampled_targets'].items() if v is not None))
            reasons = {'not_above_original': '不高于原值，保持原值', 'insufficient_awake_hours': '可新增清醒小时不足，保持原值'}
            for k, detail in value['skipped'].items():
                lines.append(names[k] + '：抽样 ' + str(detail['sampled']) + '，原值 ' + str(detail['original']) + '，' + reasons[detail['reason']])
        if 'sleep' in value:
            s = value['sleep']
            lines += ['睡眠：' + s['start'] + ' → ' + s['end'] + '，共 ' + str(s['minutes']) + ' 分钟',
                      '阶段分钟：' + json.dumps(s['stages'], ensure_ascii=False), '合成阶段：' + ('是' if s['synthetic_not_measured'] else '否，沿用原阶段顺序'),
                      '电脑旧睡眠清理范围：' + s['local_delete_start'] + ' → ' + s['local_delete_end'],
                      '执行条件：保存计划后，先在手机原版删除此晚并正常同步；云端分段及汇总为空才重建。']
        lines += [n for n in value['notices'] if ': skipped (' not in n]
        return lines

    def toggle_parameters(self):
        if self.parameters_visible:
            self.tabs.pack_forget()
        else:
            self.tabs.pack(fill='x', pady=10, before=self.actions)
        self.parameters_visible = not self.parameters_visible

    def open_folder(self):
        path = self.plan_path.parent if self.plan_path else self.runs
        path.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            os.startfile(path)

    def poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self.status.set(value)
            else:
                self.busy = False
                for button in self.buttons:
                    button.configure(state='normal')
                if kind == 'error':
                    self.display_error(value)
                elif value[0] == 'plan':
                    self.plan_path = value[1]
                    self.plan_inputs = self.pending_inputs
                    self.show_review(review_saved(self.plan_path))
                    self.status.set('计划已生成并固定随机值，核对后可执行。' if self.form_values() == self.plan_inputs else '生成期间输入发生变化，请重新生成或打开保存计划后执行。')
                else:
                    result = value[1]
                    if result.get('batch'):
                        self.output.insert('end', '\n批量云端核验完成：' + '、'.join(result['completed_dates']) + '\n手机逐日显示待正常同步验收。\n')
                        self.status.set('批量执行结束；逐日结果保存在备份目录。')
                        continue
                    names = {'calorie': '活动热量 kcal', 'exercise': '锻炼分钟', 'steps': '每日步数', 'active': '活动小时'}
                    lines = ['\n执行结果：']
                    for k, v in result['cloud_totals'].items():
                        requested = result['targets'][k]
                        lines.append(names[k] + '：云端 ' + str(v) + ('；请求 ' + str(requested) + ('，已采用' if result['target_adopted'][k] else '，未采用') if requested is not None else ''))
                    if 'sleep_cloud_segments_match' in result:
                        lines.append('睡眠分段云核验：' + ('通过' if result['sleep_cloud_segments_match'] else '未通过'))
                        lines.append('睡眠汇总云核验：' + ('通过' if result['sleep_cloud_summary_match'] else '未通过'))
                    lines.append('手机显示待正常同步验收。完整记录保存在备份目录。')
                    self.output.insert('end', '\n'.join(lines) + '\n')
                    self.status.set('执行结束。云端值见下方；手机显示请正常同步核验。')
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('任务正在进行', '请等待任务结束并完成连接清理后关闭。')
        else:
            self.root.destroy()


def main():
    silence_windows_crash_dialogs()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--runs')
    parser.add_argument('--plan', help='Resume a saved private plan without executing it')
    args = parser.parse_args()
    root = None
    try:
        root = tk.Tk()
        panel = Panel(root, args.config, args.runs)
        root.report_callback_exception = lambda kind, error, trace: panel.display_error(panel.incident(error, '界面操作'))
        if args.plan:
            try:
                panel.restore_plan(args.plan)
            except Exception as error:
                panel.display_error(panel.incident(error, '启动时打开计划'))
        root.mainloop()
    except Exception as error:
        packet = record_incident(error, Path.home() / '.health_sleep_sync_lab' / 'runs', '启动面板',
                                 {'config_path': args.config, 'runs': args.runs, 'plan': args.plan})
        text = packet['message'] + ('\n\n失败记录目录：\n' + packet['evidence'] if packet.get('evidence') else '')
        if root is not None:
            messagebox.showerror('面板启动失败', text, parent=root)
            root.destroy()
        elif os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, '面板启动失败', 0x10)
        else:
            __import__('sys').stderr.write(text + '\n')


if __name__ == '__main__':
    main()
