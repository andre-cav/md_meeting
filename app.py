"""meeting.md — compact Windows recorder and local transcription queue."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

import psutil
import meeting_core as core

BG, SOFT, INK, MUTED, GREEN = '#ffffff', '#f0f4f2', '#202927', '#62716a', '#26735e'
STATES = {'pending':'Pendente', 'queued':'Na fila', 'transcribing':'Transcrevendo',
          'paused':'Pausada', 'done':'Concluída', 'failed':'Falhou', 'recording':'Gravando'}


def protect_worker(process):
    """Windows closes the worker even if the UI crashes while it is suspended."""
    import ctypes as c
    from ctypes import wintypes as w
    class Basic(c.Structure):
        _fields_ = [('per_process', c.c_int64), ('per_job', c.c_int64), ('flags', w.DWORD),
                    ('min_ws', c.c_size_t), ('max_ws', c.c_size_t), ('active', w.DWORD),
                    ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class IO(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read', 'write', 'other')]
    class Limits(c.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_mem', c.c_size_t), ('job_mem', c.c_size_t),
                    ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)):
        if job:
            kernel.CloseHandle(job)
        raise c.WinError(c.get_last_error())
    if not kernel.AssignProcessToJobObject(job, w.HANDLE(int(process._handle))):
        kernel.CloseHandle(job)
        raise c.WinError(c.get_last_error())
    return kernel, job


class App:
    def __init__(self, root):
        self.root = root
        self.meetings = core.load_meetings()
        self.recorder = None
        self.busy = False
        self.events = queue.Queue()
        self.process = None
        self.job = None
        self.job_handle = None
        self.paused = False
        self.active_seconds = 0
        self.last_tick = time.monotonic()
        self.screen = 'home'
        self.selected = None
        self.title = tk.StringVar()
        self.mic = tk.BooleanVar(value=True)
        self.root.title('meeting.md')
        self.root.geometry('380x590')
        self.root.resizable(False, False)
        self.root.configure(bg=BG, highlightthickness=1,
                            highlightbackground='#b8c5be', highlightcolor='#b8c5be')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.bind('<Map>', self.restore_chrome)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Escape>', lambda e: self.home())
        header = tk.Frame(root, bg=BG, padx=14, pady=8)
        header.pack(fill='x')
        brand = tk.Label(header, text='◉  meeting.md', bg=BG, fg=GREEN, font=('Segoe UI', 12, 'bold'))
        brand.pack(side='left', fill='x', expand=True)
        brand.bind('<ButtonPress-1>', self.drag_start)
        brand.bind('<B1-Motion>', self.drag)
        self.button(header, '×', self.close, compact=True).pack(side='right')
        self.button(header, '−', self.minimize, compact=True).pack(side='right')
        self.body = tk.Frame(root, bg=BG, padx=22, pady=12)
        self.body.pack(fill='both', expand=True)
        self.footer = tk.Label(root, text='', bg=SOFT, fg=MUTED, wraplength=345, font=('Segoe UI', 9), pady=10)
        self.footer.pack(fill='x')
        self.render()
        root.after(250, self.tick)

    def button(self, parent, text, command, primary=False, compact=False):
        return tk.Button(parent, text=text, command=command, bg=GREEN if primary else SOFT,
                         fg='white' if primary else INK, activebackground=GREEN if primary else '#dfe8e3',
                         activeforeground='white' if primary else INK, relief='flat', bd=0,
                         font=('Segoe UI', 10), padx=10, pady=4 if compact else 11, cursor='hand2')

    def label(self, text, size=10, color=INK, parent=None):
        label = tk.Label(parent or self.body, text=text, font=('Segoe UI', size), bg=BG,
                         fg=color, wraplength=330, justify='left', anchor='w')
        label.pack(fill='x', pady=5)
        return label

    def action(self, text, command, primary=False):
        b = self.button(self.body, text, command, primary)
        b.pack(fill='x', pady=4)
        if self.busy:
            b.configure(state='disabled')
        return b

    def drag_start(self, event):
        self.offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def drag(self, event):
        self.root.geometry(f'+{event.x_root-self.offset[0]}+{event.y_root-self.offset[1]}')

    def minimize(self):
        self.root.overrideredirect(False)
        self.root.iconify()

    def restore_chrome(self, event):
        if event.widget is self.root and self.root.state() == 'normal':
            self.root.after(20, lambda: self.root.overrideredirect(True))

    def home(self):
        self.screen = 'home'
        self.render()

    def library(self):
        self.screen = 'library'
        self.render()

    def render(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.clock = None
        self.meters = None
        self.detail_status = None
        if self.screen == 'home':
            self.label('NOME DA REUNIÃO', 9, MUTED)
            entry = tk.Entry(self.body, textvariable=self.title, font=('Segoe UI', 12), bg=SOFT,
                             fg=INK, relief='flat', disabledbackground=SOFT)
            entry.pack(fill='x', ipady=8)
            self.label('Áudio do computador · saída padrão do Windows', 9, MUTED)
            check = tk.Checkbutton(self.body, text='Incluir meu microfone', variable=self.mic,
                                   bg=BG, activebackground=BG, fg=INK, font=('Segoe UI', 10))
            check.pack(anchor='w')
            if self.recorder or self.busy:
                entry.configure(state='disabled')
                check.configure(state='disabled')
            self.label('● Gravando áudio' if self.recorder else 'Pronto para gravar', color=GREEN)
            self.clock = self.label(core.duration_text(0), 32)
            self.meters = self.label('A transcrição pode ficar para depois.', 9, MUTED)
            self.action('Aguarde…' if self.busy else ('■ Parar e salvar áudio' if self.recorder else '● Iniciar gravação'),
                        self.stop_recording if self.recorder else self.start_recording, True)
            self.action(f"Reuniões · {sum(m['state'] != 'done' for m in self.meetings)} pendente(s)", self.library)
        elif self.screen == 'saved':
            m = self.selected
            self.label('✓ Áudio salvo', color=GREEN)
            self.label(m['title'], 17)
            self.label(f"{m['start'][:16].replace('T', ' ')} · {core.duration_text(m['duration'])}", 9, MUTED)
            self.label(core.estimate(m, self.meetings), 10, MUTED)
            if m['error']:
                self.label(m['error'], 9, '#a13b30')
            self.action('Transcrever agora', lambda: self.queue_meeting(m), True)
            self.action('Deixar para depois', self.home)
        elif self.screen == 'library':
            self.label('Reuniões', 18)
            self.action('Voltar à gravação' if self.recorder else 'Nova gravação', self.home, True)
            frame = tk.Frame(self.body, bg=BG)
            frame.pack(fill='both', expand=True, pady=8)
            self.listbox = tk.Listbox(frame, height=9, relief='flat', bg=SOFT, fg=INK, font=('Segoe UI', 10),
                                      selectbackground=GREEN, selectforeground='white', activestyle='none')
            scroll = tk.Scrollbar(frame, command=self.listbox.yview)
            self.listbox.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right', fill='y')
            self.listbox.pack(side='left', fill='both', expand=True)
            self.update_list()
            self.listbox.bind('<Double-Button-1>', lambda e: self.open_selected())
            self.action('Ver reunião selecionada', self.open_selected)
            self.action('Abrir pasta dos áudios', lambda: os.startfile(core.DATA))
        else:
            m = self.selected
            name = tk.StringVar(value=m['title'])
            entry = tk.Entry(self.body, textvariable=name, font=('Segoe UI', 12), bg=SOFT, fg=INK, relief='flat')
            entry.pack(fill='x', ipady=8)
            def rename(event=None):
                title = name.get().strip()[:120]
                if title and title != m['title']:
                    m['title'] = title
                    core.save(m)
            entry.bind('<FocusOut>', rename)
            entry.bind('<Return>', rename)
            self.label(f"Início: {m['start']}\nFim: {m['end'] or 'Interrompida'}\nDuração: {core.duration_text(m['duration'])}", 9, MUTED)
            self.label('Fontes: ' + ', '.join(t['label'] for t in m['tracks']), 9, MUTED)
            self.detail_status = self.label('', 10, GREEN)
            self.label(core.estimate(m, self.meetings), 9, MUTED)
            if m['error']:
                self.label(m['error'][:350], 9, '#a13b30')
            if m['state'] in ('pending', 'failed'):
                self.action('Transcrever' if m['state'] == 'pending' else 'Tentar novamente', lambda: self.queue_meeting(m), True)
            if m['state'] == 'done':
                self.action('Abrir Markdown', lambda: os.startfile(core.DATA / m['id'] / 'transcription.md'), True)
            self.action('Abrir áudio e informações', lambda: os.startfile(core.DATA / m['id']))
            self.action('Voltar às reuniões', self.library)
        self.update_status()
        self.root.update_idletasks()
        self.root.geometry(f'380x{max(510, self.root.winfo_reqheight())}')

    def update_list(self):
        old = self.listbox.curselection()
        self.listbox.delete(0, 'end')
        for m in self.meetings:
            self.listbox.insert('end', f"{m['start'][5:16].replace('T', ' ')} · {STATES[m['state']]} · {m['title']}")
        if old and old[0] < len(self.meetings):
            self.listbox.selection_set(old[0])

    def open_selected(self):
        selection = self.listbox.curselection()
        if selection:
            self.selected = self.meetings[selection[0]]
            self.screen = 'detail'
            self.render()

    def queue_meeting(self, meeting):
        core.enqueue(meeting)
        self.home()

    def pause_worker(self):
        if self.process and self.process.poll() is None and not self.paused:
            psutil.Process(self.process.pid).suspend()
            self.paused = True
            self.job['state'] = 'paused'
            core.save(self.job)

    def start_recording(self):
        if self.busy or self.recorder:
            return
        try:
            self.pause_worker()
            m = core.new_meeting(self.title.get(), self.mic.get())
        except Exception as exc:
            messagebox.showerror('Não foi possível iniciar', str(exc), parent=self.root)
            return
        self.busy = True
        self.render()
        def start():
            recorder = core.Recorder(m)
            try:
                recorder.start()
                self.events.put(('started', recorder))
            except Exception as exc:
                self.events.put(('start_failed', (m, str(exc))))
        threading.Thread(target=start, daemon=True).start()

    def stop_recording(self):
        if self.busy or not self.recorder:
            return
        self.busy = True
        self.render()
        def stop():
            try:
                self.recorder.stop()
                self.events.put(('stopped', None))
            except Exception as exc:
                self.events.put(('stop_failed', str(exc)))
        threading.Thread(target=stop, daemon=True).start()

    def start_worker(self, meeting):
        folder = core.DATA / meeting['id']
        self.active_seconds = 0
        self.paused = False
        core.atomic_json(folder / 'progress.json', dict(fraction=0, stage='Iniciando transcrição…'))
        with (folder / 'transcription.log').open('w', encoding='utf-8') as log:
            self.process = subprocess.Popen([sys.executable, str(core.ROOT / 'transcribe.py'), str(folder)],
                                            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.job_handle = protect_worker(self.process)
        except Exception:
            self.process.kill()
            self.process.wait()
            self.process = None
            raise
        self.job = meeting
        psutil.Process(self.process.pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        meeting['state'] = 'transcribing'
        core.save(meeting)

    def progress(self):
        if not self.job:
            return {}
        try:
            return json.loads((core.DATA / self.job['id'] / 'progress.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def update_status(self):
        if self.recorder and self.clock:
            self.clock.configure(text=core.duration_text(time.monotonic() - self.recorder.started))
            self.meters.configure(text=' · '.join(f'{name}: {"▰" * max(1, int(value * 10))} {int(value * 100)}%'
                                                 for name, value in self.recorder.levels.items()) or 'Aguardando som…')
        if self.job:
            progress = self.progress()
            if self.paused:
                text = 'Transcrição pausada. A gravação tem prioridade.'
            else:
                fraction = progress.get('fraction', 0)
                text = f"{progress.get('stage', 'Iniciando…')} · {fraction:.0%}\n{core.estimate(self.job, self.meetings, fraction)}"
        else:
            text = 'Áudio salvo localmente · Transcrição em português'
        self.footer.configure(text=text)
        if self.detail_status:
            self.detail_status.configure(text=STATES[self.selected['state']] if self.selected is not self.job else text)

    def tick(self):
        try:
            current = time.monotonic()
            if self.process and not self.paused and self.progress().get('stage', '').startswith('Transcrevendo'):
                self.active_seconds += current - self.last_tick
            self.last_tick = current
            while not self.events.empty():
                event, value = self.events.get_nowait()
                self.busy = False
                if event == 'started':
                    self.recorder = value
                    self.meetings.insert(0, value.meeting)
                    self.home()
                elif event == 'stopped':
                    self.selected = self.recorder.meeting
                    self.recorder = None
                    self.title.set('')
                    self.screen = 'saved'
                    self.render()
                elif event == 'start_failed':
                    m, error = value
                    m.update(state='failed', error=error)
                    core.save(m)
                    self.meetings.insert(0, m)
                    self.home()
                    messagebox.showerror('Falha na captura', error + '\nConfira a saída padrão e o microfone do Windows.', parent=self.root)
                elif event == 'stop_failed':
                    messagebox.showerror('Falha ao salvar', value + '\nOs arquivos parciais foram preservados.', parent=self.root)
                    self.render()
            if self.recorder and self.recorder.errors and not self.busy:
                self.stop_recording()
            if self.process and self.process.poll() is not None:
                progress = self.progress()
                success = self.process.returncode == 0 and (core.DATA / self.job['id'] / 'transcription.md').exists()
                self.job.update(state='done' if success else 'failed', error='' if success else progress.get('error', 'Transcrição interrompida. Consulte transcription.log.'),
                                processing_seconds=self.active_seconds)
                core.save(self.job)
                if self.job_handle:
                    self.job_handle[0].CloseHandle(self.job_handle[1])
                self.process = self.job = self.job_handle = None
                self.paused = False
                if self.screen in ('library', 'detail'):
                    self.render()
            if not self.recorder and not self.busy:
                if self.process and self.paused:
                    psutil.Process(self.process.pid).resume()
                    self.paused = False
                    self.job['state'] = 'transcribing'
                    core.save(self.job)
                if not self.process:
                    waiting = next((m for m in reversed(self.meetings) if m['state'] == 'queued'), None)
                    if waiting:
                        try:
                            self.start_worker(waiting)
                        except Exception as exc:
                            waiting.update(state='failed', error=str(exc))
                            core.save(waiting)
            self.update_status()
            if self.screen == 'library':
                self.update_list()
        except Exception as exc:
            self.footer.configure(text='Erro: ' + str(exc))
        self.root.after(250, self.tick)

    def close(self):
        if self.recorder or self.busy:
            messagebox.showinfo('Gravação em andamento', 'Pare e salve o áudio antes de fechar. Você pode minimizar.', parent=self.root)
            return
        if self.process:
            if not messagebox.askyesno('Fechar meeting.md?', 'A transcrição será interrompida e ficará pendente para tentar novamente. O áudio será preservado.', parent=self.root):
                return
            self.process.kill()
            self.process.wait(timeout=10)
            self.job['state'] = 'pending'
            core.save(self.job)
            if self.job_handle:
                self.job_handle[0].CloseHandle(self.job_handle[1])
        self.root.destroy()


def main():
    if sys.platform != 'win32':
        raise SystemExit('Esta versão requer Windows 10/11.')
    # One UI owns the queue; a second launch must not recover an active recording.
    import msvcrt
    core.DATA.mkdir(parents=True, exist_ok=True)
    lock = (core.DATA / 'app.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    root = tk.Tk()
    root.withdraw()
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        messagebox.showinfo('meeting.md', 'O aplicativo já está aberto. Confira a barra de tarefas.')
        root.destroy()
        lock.close()
        return
    App(root)
    root.deiconify()
    root.mainloop()
    lock.close()


if __name__ == '__main__':
    main()
