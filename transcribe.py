"""Separate process: download/load the model only when transcription is requested."""
import json
import os
import sys
import time
import wave
from pathlib import Path
from meeting_core import DATA, atomic_json, duration_text


def run(folder):
    from faster_whisper import WhisperModel
    m = json.loads((folder / 'meeting.json').read_text(encoding='utf-8'))
    progress = folder / 'progress.json'
    atomic_json(progress, dict(fraction=0, stage='Carregando modelo (primeiro uso pode baixar arquivos)…'))
    model = WhisperModel(m['model'], device='cpu', compute_type='int8',
                         cpu_threads=max(1, min(4, (os.cpu_count() or 2) // 2)),
                         download_root=str(DATA / 'models'))
    start = time.process_time()
    segments_out = []
    tracks = m['tracks']
    separate = m.get('diarize', False)
    if not tracks:
        raise RuntimeError('Esta reunião não possui áudio recuperável.')
    total = len(tracks)
    for index, track in enumerate(tracks):
        path = folder / track['file']
        with wave.open(str(path)) as audio:
            if audio.getnframes() == 0:
                continue
        base = index / total
        span = 1 / total
        computer = separate and track['file'] == 'computer.wav'
        if computer:
            from diarization import detect, speaker_at
            turns = detect(path, lambda stage: atomic_json(progress, dict(fraction=base, stage=stage)))
            base += span / 2
            span /= 2
        stage = 'Transcrevendo ' + track['label'].lower()
        atomic_json(progress, dict(fraction=base, stage=stage))
        segments, info = model.transcribe(str(path), language=m['language'],
                                         beam_size=1, vad_filter=True, condition_on_previous_text=False)
        for segment in segments:
            source = speaker_at(segment.start, segment.end, turns) if computer else track['label']
            segments_out.append((segment.start, source, segment.text.strip()))
            atomic_json(progress, dict(fraction=min(.99, base + span * segment.end / max(info.duration, .001)),
                                       stage=stage))
    text = [f"# {m['title']}", '', f"- ID: {m['id']}", f"- Início: {m['start']}",
            f"- Fim: {m['end'] or 'Sessão interrompida'}", f"- Duração: {duration_text(m['duration'])}",
            f"- Modelo: {m['model']} · Idioma: {m['language']}", '', '## Transcrição', '']
    for timestamp, source, words in sorted(segments_out, key=lambda s: s[0]):
        text.append(f'[{duration_text(timestamp)}] **{source}:** {words}\n')
    if not segments_out:
        text.append('_Nenhuma fala identificada. Confira os arquivos de áudio._')
    temp = folder / 'transcription.tmp'
    temp.write_text('\n'.join(text) + '\n', encoding='utf-8')
    os.replace(temp, folder / ('transcription-speakers.md' if separate else 'transcription.md'))
    atomic_json(progress, dict(fraction=1, stage='Concluída', cpu_seconds=time.process_time() - start))


if __name__ == '__main__':
    directory = Path(sys.argv[1]).resolve()
    try:
        run(directory)
    except Exception as exc:
        atomic_json(directory / 'progress.json', dict(fraction=0, stage='Falhou', error=str(exc)))
        raise
