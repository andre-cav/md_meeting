"""Persistence and incremental WASAPI recording. No model loaded in the UI."""
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid
import wave
from datetime import datetime

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def save(meeting):
    atomic_json(DATA / meeting['id'] / 'meeting.json', meeting)


def new_meeting(title, microphone):
    ident = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:10]
    (DATA / ident).mkdir(parents=True)
    meeting = dict(id=ident, title=title.strip()[:120] or 'Reunião ' + datetime.now().strftime('%d/%m %H:%M'),
                   start=now(), end=None, duration=0, microphone=microphone,
                   state='recording', tracks=[], error='', model='base', language='pt')
    save(meeting)
    return meeting


def load_meetings():
    DATA.mkdir(parents=True, exist_ok=True)
    result = []
    for path in DATA.glob('*/meeting.json'):
        try:
            m = json.loads(path.read_text(encoding='utf-8'))
            if m['id'] != path.parent.name:
                continue
            if m['state'] in ('recording', 'transcribing', 'paused'):
                m['state'] = 'pending'
                m['error'] = 'Sessão interrompida. Confira o áudio antes de transcrever.'
                lengths = []
                for track in m['tracks']:
                    audio = path.parent / track['file']
                    try:
                        # Repair the standard PCM WAV header after an abrupt exit.
                        size = audio.stat().st_size - 44
                        if size >= 0:
                            import struct
                            with audio.open('r+b') as f:
                                f.seek(4)
                                f.write(struct.pack('<I', size + 36))
                                f.seek(40)
                                f.write(struct.pack('<I', size))
                            lengths.append(size / (track['rate'] * 2))
                    except OSError:
                        pass
                m['duration'] = max(lengths, default=m['duration'])
                save(m)
            result.append(m)
        except (OSError, ValueError, KeyError):
            # Keep unreadable files intact for manual recovery.
            continue
    return sorted(result, key=lambda m: m['start'], reverse=True)


def enqueue(m):
    if m['state'] not in ('pending', 'failed'):
        return False
    m.update(state='queued', error='')
    save(m)
    return True


def estimate(meeting, history, fraction=0):
    ratios = [m['processing_seconds'] / m['duration'] for m in history
              if m.get('processing_seconds') and m['duration'] >= 20
              and m.get('model') == meeting['model'] and m.get('state') == 'done'
              and m.get('microphone') == meeting.get('microphone')]
    if not ratios:
        return 'Estimativa disponível após a primeira transcrição'
    import statistics
    seconds = statistics.median(ratios[:5]) * meeting['duration'] * (1 - fraction)
    low, high = max(1, round(seconds * .7 / 60)), max(1, round(seconds * 1.4 / 60))
    return f'Estimativa: {low}–{high} min (aproximada)'


def duration_text(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


class Recorder:
    def __init__(self, meeting):
        self.meeting = meeting
        self.started = time.monotonic()
        self.streams = []
        self.writers = []
        self.queues = []
        self.errors = []
        self.levels = {}
        self.pa = None

    def start(self):
        import pyaudiowpatch as pa
        self.pa = pa.PyAudio()
        try:
            devices = [('Computador', self.pa.get_default_wasapi_loopback())]
            if self.meeting['microphone']:
                api = self.pa.get_host_api_info_by_type(pa.paWASAPI)
                devices.append(('Microfone', self.pa.get_device_info_by_index(int(api['defaultInputDevice']))))
            self.started = time.monotonic()
            for label, device in devices:
                self._open(label, device, pa)
            save(self.meeting)
        except Exception:
            self.stop()
            raise

    def _open(self, label, device, pa):
        import numpy as np
        rate, channels = int(device['defaultSampleRate']), min(2, int(device['maxInputChannels']))
        if channels < 1:
            raise RuntimeError(f'{label}: dispositivo sem canal de entrada.')
        filename = 'computer.wav' if label == 'Computador' else 'microphone.wav'
        track = dict(label=label, file=filename, rate=rate, device=device['name'])
        self.meeting['tracks'].append(track)
        save(self.meeting)
        packets = queue.Queue(maxsize=300)
        self.queues.append(packets)
        path = DATA / self.meeting['id'] / filename

        def write():
            try:
                with wave.open(str(path), 'wb') as wav:
                    wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                    wav.writeframes(b'')
                    position = 0
                    while True:
                        item = packets.get()
                        if item is None:
                            break
                        elapsed, raw = item
                        samples = np.frombuffer(raw, dtype='<i2').reshape(-1, channels).astype(np.float32).mean(axis=1)
                        self.levels[label] = min(1., float(np.max(np.abs(samples), initial=0)) / 32768)
                        # Keep silence gaps on the meeting timeline (WASAPI may stop callbacks while idle).
                        expected = max(0, int(elapsed * rate) - len(samples))
                        gap = expected - position
                        if gap > rate // 5:
                            while gap > 0:
                                count = min(gap, rate)
                                wav.writeframes(b'\0' * (count * 2))
                                gap -= count
                                position += count
                        wav.writeframes(samples.astype('<i2').tobytes())
                        position += len(samples)
                        if position >= rate * 60 * 60 * 10:
                            raise RuntimeError('Limite de 10 horas por gravação atingido. Inicie outra reunião.')
            except Exception as exc:
                self.errors.append(f'{label}: {exc}')

        def callback(data, frame_count, time_info, status):
            if status:
                self.errors.append(f'{label}: falha de captura (código {status}); o áudio pode conter uma lacuna.')
            try:
                packets.put_nowait((time.monotonic() - self.started, data))
            except queue.Full:
                self.errors.append(f'{label}: disco não acompanhou a gravação.')
                return None, pa.paAbort
            return None, pa.paContinue

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        self.writers.append(writer)
        stream = self.pa.open(format=pa.paInt16, channels=channels, rate=rate, input=True,
                              input_device_index=int(device['index']), frames_per_buffer=max(1024, rate // 10),
                              stream_callback=callback, start=False)
        self.streams.append(stream)
        stream.start_stream()

    def stop(self):
        for stream in self.streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception as exc:
                self.errors.append(str(exc))
        self.streams.clear()
        for packets, writer in zip(self.queues, self.writers):
            while writer.is_alive():
                try:
                    packets.put(None, timeout=.2)
                    break
                except queue.Full:
                    continue
            writer.join()
        if self.pa:
            self.pa.terminate()
            self.pa = None
        self.meeting.update(end=now(), duration=time.monotonic() - self.started, state='pending',
                            error='\n'.join(dict.fromkeys(self.errors)))
        save(self.meeting)
