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


def numeric(text):
    text = text.strip()
    if not text:
        return None
    pieces = text.split('-')
    if len(pieces) == 2:
        return [int(pieces[0]), int(pieces[1])]
    return int(text)


class Panel:
    def __init__(self, root, config_path=None, runs=None):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.plan_path = None
        self.runs = private(runs or Path.home() / '.health_sleep_sync_lab' / 'runs')
        self.config_path = tk.StringVar(value=str(config_path or ''))
        self.date = tk.StringVar(value=datetime.now().strftime('%Y-%m-%d'))
        if config_path:
            self.date.set(load(private(config_path))['date'])
        self.vars = {}
        root.title('运动健康 · 数据调整面板')
        root.geometry('920x800')
        root.minsize(830, 730)
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
        ttk.Entry(setup, textvariable=self.config_path).grid(row=0, column=1, sticky='ew', padx=10)
        ttk.Button(setup, text='选择文件', command=self.choose_config).grid(row=0, column=2)
        ttk.Label(setup, text='日期 / 起床日').grid(row=1, column=0, sticky='w', pady=8)
        ttk.Entry(setup, textvariable=self.date, width=18).grid(row=1, column=1, sticky='w', padx=10)
        setup.columnconfigure(1, weight=1)
        tabs = ttk.Notebook(outer)
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
        ttk.Label(sleep, text='睡眠会删除原晚后重建。云端正确后，手机旧记录仍可能需要在原版中删除。', foreground='#795322').grid(row=8, column=0, columnspan=3, sticky='w')
        actions = ttk.Frame(outer)
        actions.pack(fill='x', pady=8)
        self.buttons = []
        for title, fn in [('生成计划', self.prepare), ('打开已保存计划', self.open_plan), ('打开备份目录', self.open_folder), ('执行当前计划', self.apply)]:
            button = ttk.Button(actions, text=title, command=fn)
            button.pack(side='left', padx=(0, 8))
            self.buttons.append(button)
        self.delete_confirm = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text='我确认当前计划中所选整晚睡眠可以删除并重建', variable=self.delete_confirm).pack(anchor='w', pady=(0, 6))
        self.status = tk.StringVar(value='填写目标后生成计划。启动面板不会自动修改任何数据。')
        ttk.Label(outer, textvariable=self.status, foreground='#264e81').pack(anchor='w', pady=5)
        self.output = tk.Text(outer, height=12, wrap='word', font=('Microsoft YaHei UI', 10), bg='white', relief='flat', padx=12, pady=10)
        self.output.pack(fill='both', expand=True)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def entry(self, frame, row, name, label, value, width=25, hint=''):
        var = tk.StringVar(value=value)
        self.vars[name] = var
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=4)
        ttk.Entry(frame, textvariable=var, width=width).grid(row=row, column=1, sticky='w', padx=12, pady=4)
        if hint:
            ttk.Label(frame, text=hint, foreground='#69717d').grid(row=row, column=2, sticky='w')

    def choose_config(self):
        value = filedialog.askopenfilename(title='选择仓库外的私有配置', filetypes=[('JSON', '*.json')])
        if value:
            self.config_path.set(value)
            self.date.set(load(private(value))['date'])

    def request(self):
        data = {name: numeric(self.vars[name].get()) for name in ['calorie', 'exercise', 'steps', 'active']}
        data.update(activity_windows=self.vars['activity_windows'].get(), preserve_active_hours=self.preserve.get(),
                    active_hours=[int(s.strip()) for s in self.vars['active_hours'].get().split(',') if s.strip()])
        if self.sleep_enabled.get():
            s = {'mode': self.sleep_mode.get(), 'restore_original_score': self.restore_score.get()}
            if s['mode'] == 'shift':
                s['advance_minutes'] = numeric(self.vars['advance_minutes'].get())
            else:
                s.update(bedtime_range=self.vars['bedtime_range'].get().split('-'), wake_range=self.vars['wake_range'].get().split('-'), duration_range=numeric(self.vars['duration_range'].get()))
            data['sleep'] = s
        if not data.get('sleep') and all(data[k] is None for k in ['calorie', 'exercise', 'steps', 'active']):
            raise ValueError('请至少填写一个修改目标。')
        return data

    def task(self, fn):
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.configure(state='disabled')
        def worker():
            try:
                self.events.put(('done', fn()))
            except Exception as error:
                self.events.put(('error', user_message(error)))
        threading.Thread(target=worker, daemon=False).start()

    def prepare(self):
        try:
            config = load(private(self.config_path.get()))
            config['date'] = self.date.get().strip()
            # Recompute overnight window when the panel date changes.
            config.pop('sleep_query_start_ms', None)
            config.pop('sleep_query_end_ms', None)
            request = self.request()
            directory = self.runs / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            self.plan_path = None
            self.delete_confirm.set(False)
            self.task(lambda: ('plan', prepare(config, request, directory, lambda s: self.events.put(('log', s)))))
        except (ValueError, OSError, KeyError):
            messagebox.showerror('无法生成计划', '请核对私有配置、日期、数值与时间区间。')

    def open_plan(self):
        value = filedialog.askopenfilename(title='打开私有计划', filetypes=[('JSON', '*.json')])
        if value:
            try:
                plan = load(private(value))
                review = describe(plan)
                self.plan_path = Path(value)
                self.delete_confirm.set(False)
                self.show_review(review)
            except Exception:
                messagebox.showerror('计划无效', '该文件未通过计划校验。')

    def apply(self):
        if not self.plan_path:
            messagebox.showinfo('尚无计划', '先生成或打开计划，查看日期和目标。')
            return
        plan = load(self.plan_path)
        if plan['sleep'] and not self.delete_confirm.get():
            messagebox.showinfo('需要确认整晚范围', '请查看当前计划中的睡眠日期与起止，再勾选删除重建确认。')
            return
        plan_path = self.plan_path
        confirmed = self.delete_confirm.get()
        self.task(lambda: ('result', execute(plan_path, allow_sleep_delete=confirmed, log=lambda s: self.events.put(('log', s)))))

    def show_review(self, value):
        self.output.delete('1.0', 'end')
        names = {'calorie': '活动热量', 'exercise': '锻炼分钟', 'steps': '步数', 'active': '活动小时'}
        target_text = '，'.join(names[k] + ' ' + str(v) for k, v in value['targets'].items() if v is not None)
        lines = ['计划日期：' + value['date'], '运动目标：' + (target_text or '不改'),
                 '运动分钟修改：' + str(value['sport_minutes_changed']) + '；新增锻炼分钟：' + str(value['exercise_minutes_added']) + '；新增活动小时：' + str(value['active_hours_added'])]
        if 'sleep' in value:
            s = value['sleep']
            lines += ['睡眠：' + s['start'] + ' → ' + s['end'] + '，共 ' + str(s['minutes']) + ' 分钟',
                      '阶段分钟：' + json.dumps(s['stages'], ensure_ascii=False), '合成阶段：' + ('是' if s['synthetic_not_measured'] else '否，沿用原阶段顺序'),
                      '电脑旧睡眠清理范围：' + s['local_delete_start'] + ' → ' + s['local_delete_end']]
        lines += value['notices']
        self.output.insert('end', '\n'.join(lines) + '\n')

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
                    self.status.set(value)
                elif value[0] == 'plan':
                    self.plan_path = value[1]
                    self.show_review(describe(load(self.plan_path)))
                    self.status.set('计划已生成并固定随机值，核对后可执行。')
                else:
                    result = value[1]
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--runs')
    parser.add_argument('--plan', help='Resume a saved private plan without executing it')
    args = parser.parse_args()
    root = tk.Tk()
    panel = Panel(root, args.config, args.runs)
    if args.plan:
        path = private(args.plan)
        plan = load(path)
        panel.plan_path = path
        panel.date.set(plan['config']['date'])
        for key in ['calorie', 'exercise', 'steps', 'active']:
            value = plan['request'].get(key)
            panel.vars[key].set('-'.join(map(str, value)) if isinstance(value, list) else '' if value is None else str(value))
        panel.show_review(describe(plan))
        panel.status.set('已恢复保存的计划；启动不会执行。请查看目标与日期。')
    root.mainloop()


if __name__ == '__main__':
    main()
