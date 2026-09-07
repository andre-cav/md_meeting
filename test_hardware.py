"""Optional integration check. Plays synthetic speech and captures ~12 seconds.

Generate data/diagnostics/speech.wav first (see README). This is not a unit test.
"""
import json
import sys
import time
import wave
import winsound

import numpy as np
import meeting_core as core


def main():
    fixture = core.DATA / 'diagnostics' / 'speech.wav'
    if not fixture.exists():
        raise SystemExit('Gere data/diagnostics/speech.wav seguindo README.md.')
    with wave.open(str(fixture)) as audio:
        length = audio.getnframes() / audio.getframerate()
    original = core.DATA
    core.DATA = original / 'diagnostics' / 'captures'
    m = core.new_meeting('Teste automático de áudio', True)
    recorder = core.Recorder(m)
    try:
        recorder.start()
        time.sleep(.3)
        winsound.PlaySound(str(fixture), winsound.SND_FILENAME | winsound.SND_ASYNC)
        time.sleep(length + .6)
    finally:
        winsound.PlaySound(None, 0)
        recorder.stop()
    assert not recorder.errors, recorder.errors
    for track in m['tracks']:
        with wave.open(str(core.DATA / m['id'] / track['file'])) as audio:
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
            assert len(samples) > 0, track
            if track['label'] == 'Computador':
                assert np.max(np.abs(samples.astype(float))) > 100, 'Loopback sem sinal.'
            print(track['label'], 'frames:', len(samples), 'pico:', np.max(np.abs(samples.astype(float))))
    print('Captura validada:', core.DATA / m['id'])
    (original / 'diagnostics' / 'last-capture.txt').write_text(str(core.DATA / m['id']), encoding='utf-8')


if __name__ == '__main__':
    main()
