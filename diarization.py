"""Local speaker turns for the computer audio track."""
import shutil
import tarfile
from urllib.request import urlopen

from meeting_core import DATA

ARCHIVE = 'sherpa-onnx-pyannote-segmentation-3-0'
EMBEDDING = '3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'
RELEASES = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/'


def model_files():
    cache = DATA / 'models' / 'diarization'
    cache.mkdir(parents=True, exist_ok=True)
    segmentation = cache / 'segmentation.onnx'
    embedding = cache / EMBEDDING
    if not segmentation.exists():
        temp = segmentation.with_suffix('.part')
        with urlopen(RELEASES + f'speaker-segmentation-models/{ARCHIVE}.tar.bz2', timeout=60) as response:
            with tarfile.open(fileobj=response, mode='r|bz2') as archive:
                for member in archive:
                    if member.isfile() and member.name.removeprefix('./') == f'{ARCHIVE}/model.onnx':
                        with archive.extractfile(member) as model, temp.open('wb') as out:
                            shutil.copyfileobj(model, out)
                        break
                else:
                    raise RuntimeError('Modelo de segmentação não encontrado no download.')
        temp.replace(segmentation)
    if not embedding.exists():
        temp = embedding.with_suffix('.part')
        with urlopen(RELEASES + f'speaker-recongition-models/{EMBEDDING}', timeout=60) as response, temp.open('wb') as out:
            shutil.copyfileobj(response, out)
        temp.replace(embedding)
    return segmentation, embedding


def detect(path, stage):
    try:
        import sherpa_onnx as sherpa
    except ImportError as exc:
        raise RuntimeError('Instale requirements-diarization.txt para separar participantes.') from exc
    from faster_whisper.audio import decode_audio

    stage('Preparando modelos de diarização…')
    segmentation, embedding = model_files()
    config = sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation))),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(model=str(embedding)),
        clustering=sherpa.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError('Não foi possível carregar os modelos de diarização.')
    engine = sherpa.OfflineSpeakerDiarization(config)
    stage('Separando participantes…')
    audio = decode_audio(str(path), sampling_rate=engine.sample_rate)
    return engine.process(audio).sort_by_start_time()


def speaker_at(start, end, turns):
    # shortcut: one label per Whisper segment; use word timestamps if speaker changes need finer splits.
    seconds = 0
    label = 'Participante indefinido'
    for turn in turns:
        if turn.start >= end:
            break
        overlap = min(end, turn.end) - max(start, turn.start)
        if overlap > seconds:
            seconds = overlap
            label = f'Pessoa {turn.speaker + 1}'
    return label
