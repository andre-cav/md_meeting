import json
import subprocess
import sys
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import meeting_core as core


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = core.DATA
        core.DATA = Path(self.temp.name)

    def tearDown(self):
        core.DATA = self.original
        self.temp.cleanup()

    def meeting(self):
        m = core.new_meeting('Mesmo título', False)
        folder = core.DATA / m['id']
        with wave.open(str(folder / 'computer.wav'), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 16000)
        m.update(state='pending', duration=1, end=core.now(),
                 tracks=[dict(label='Computador', file='computer.wav', rate=16000)])
        core.save(m)
        return m

    def test_unique_queue_recovery_and_audio_preserved(self):
        a, b = self.meeting(), self.meeting()
        self.assertNotEqual(a['id'], b['id'])
        self.assertTrue(core.enqueue(a))
        self.assertFalse(core.enqueue(a))
        a['state'] = 'paused'
        core.save(a)
        restored = {m['id']: m for m in core.load_meetings()}
        self.assertEqual(restored[a['id']]['state'], 'pending')
        self.assertEqual(restored[b['id']]['state'], 'pending')
        with wave.open(str(core.DATA / a['id'] / 'computer.wav')) as audio:
            self.assertEqual(audio.getnframes(), 16000)

    def test_markdown_sorted_and_estimate(self):
        import transcribe
        m = self.meeting()
        segment = types.SimpleNamespace(start=.2, end=.8, text=' Uma reunião de teste. ')
        model = types.SimpleNamespace(transcribe=lambda *a, **k: (iter([segment]), types.SimpleNamespace(duration=1)))
        fake = types.SimpleNamespace(WhisperModel=lambda *a, **k: model)
        with patch.dict(sys.modules, {'faster_whisper': fake}):
            transcribe.run(core.DATA / m['id'])
        text = (core.DATA / m['id'] / 'transcription.md').read_text(encoding='utf-8')
        self.assertIn(m['id'], text)
        self.assertIn('**Computador:** Uma reunião de teste.', text)
        self.assertEqual(json.loads((core.DATA / m['id'] / 'progress.json').read_text())['fraction'], 1)
        self.assertIn('primeira', core.estimate(m, []))
        history = [dict(m, state='done', duration=60, processing_seconds=30)]
        self.assertIn('min', core.estimate(m, history))

    def test_diarization_on_existing_meeting_keeps_original(self):
        import diarization
        import transcribe
        m = self.meeting()
        m['state'] = 'done'
        original = core.transcript_path(m)
        original.write_text('Original', encoding='utf-8')
        self.assertTrue(core.enqueue(m, True))
        self.assertFalse(core.enqueue(m, True))
        turn = types.SimpleNamespace(start=.1, end=.7, speaker=1)
        segments = [types.SimpleNamespace(start=.2, end=.6, text=' Oi. '),
                    types.SimpleNamespace(start=.8, end=.9, text=' Sem rótulo. ')]
        model = types.SimpleNamespace(transcribe=lambda *a, **k: (iter(segments), types.SimpleNamespace(duration=1)))
        fake = types.SimpleNamespace(WhisperModel=lambda *a, **k: model)
        with patch.dict(sys.modules, {'faster_whisper': fake}), patch.object(diarization, 'detect', return_value=[turn]):
            transcribe.run(core.DATA / m['id'])
        result = core.transcript_path(m, True).read_text(encoding='utf-8')
        self.assertIn('**Pessoa 2:** Oi.', result)
        self.assertIn('**Participante indefinido:** Sem rótulo.', result)
        self.assertEqual(original.read_text(encoding='utf-8'), 'Original')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows only')
    def test_process_pause_resume_and_job_cleanup(self):
        import psutil
        from app import protect_worker
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        handle = None
        try:
            handle = protect_worker(process)
            child = psutil.Process(process.pid)
            child.suspend()
            self.assertEqual(child.status(), psutil.STATUS_STOPPED)
            child.resume()
            self.assertNotEqual(child.status(), psutil.STATUS_STOPPED)
            handle[0].CloseHandle(handle[1])
            handle = None
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            if handle:
                handle[0].CloseHandle(handle[1])
            if process.poll() is None:
                process.kill()
            process.wait()

    def test_window_navigation_fits_and_saved_actions(self):
        import tkinter as tk
        from app import App
        m = self.meeting()
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        try:
            app.selected = m
            for screen in ('home', 'saved', 'library', 'detail'):
                app.screen = screen
                app.render()
                root.update()
                self.assertGreaterEqual(root.winfo_height(), root.winfo_reqheight())
                if screen == 'saved':
                    buttons = [x.cget('text') for x in app.body.winfo_children() if isinstance(x, tk.Button)]
                    self.assertEqual(buttons, ['Transcrever agora', 'Transcrever e separar participantes', 'Deixar para depois'])
        finally:
            for timer in root.tk.call('after', 'info'):
                root.after_cancel(timer)
            root.destroy()


if __name__ == '__main__':
    unittest.main()
