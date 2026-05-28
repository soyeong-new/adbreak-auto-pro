"""영상 신호 추출 및 캐싱 (pipeline.py)

전사·장면 탐지·음성 음량 추출은 시간이 오래 걸리므로 결과를 .cache/에 저장합니다.
같은 영상을 다시 분석할 때는 캐시를 즉시 반환합니다.

  transcribe()              — Whisper 음성 전사 (mlx-whisper / faster-whisper 폴백)
  detect_scenes()           — PySceneDetect 장면 전환 탐지 (ContentDetector threshold=27)
  extract_voice_envelope()  — ffmpeg으로 250~3000Hz 음량 곡선 추출 (침묵 판별용)
  get_duration()            — ffprobe로 영상 길이(초) 반환
  get_fps()                 — ffprobe로 영상 프레임레이트 반환

캐시 키: {영상명}_{파일크기} — 파일이 교체되면 자동으로 재분석.
"""
import os
import re
import json
import subprocess

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# mlx-whisper (Apple Silicon GPU) is ~7x faster than faster-whisper on CPU;
# faster-whisper is the fallback for non-Apple-Silicon machines.
MLX_REPO = "mlx-community/whisper-small-mlx"
FW_MODEL = "small"
LANGUAGE = "ko"
SR = 1000                   # audio extraction sample rate
ENV_HZ = 20                 # voice envelope resolution

_fw_model = None


def _cache_file(video_path, kind):
    st = os.stat(video_path)
    key = f"{os.path.splitext(os.path.basename(video_path))[0]}_{st.st_size}"
    return os.path.join(CACHE_DIR, f"{key}.{kind}.json")


def _load_cache(video_path, kind):
    f = _cache_file(video_path, kind)
    if os.path.exists(f):
        with open(f, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _save_cache(video_path, kind, data):
    with open(_cache_file(video_path, kind), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def get_duration(video_path):
    """Video duration in seconds via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", video_path],
        capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def get_fps(video_path):
    """Source video frame rate (float) via ffprobe, or None if unreadable."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "json", video_path],
        capture_output=True, text=True).stdout
    try:
        rate = json.loads(out)["streams"][0]["r_frame_rate"]
        num, den = rate.split("/")
        den = float(den)
        return float(num) / den if den else None
    except Exception:
        return None


def _transcribe_mlx(video_path):
    """Apple Silicon GPU transcription. Returns segments, or None if unavailable."""
    try:
        import mlx_whisper
    except ImportError:
        return None
    # word_timestamps=True forces DTW word alignment, which gives sub-second
    # (0.02s) timestamps. Without it Whisper falls back to coarse 1-second
    # timestamps in low-confidence spans -- and integer seconds, under 29.97
    # fps, can never land on the allowed :00-03/:28-29 frames, silently
    # killing every candidate in that span.
    r = mlx_whisper.transcribe(video_path, path_or_hf_repo=MLX_REPO,
                               language=LANGUAGE, word_timestamps=True)
    return [{"start": round(s["start"], 2), "end": round(s["end"], 2),
             "text": s["text"].strip()} for s in r["segments"]]


def _transcribe_faster(video_path):
    """CPU transcription fallback (faster-whisper)."""
    global _fw_model
    if _fw_model is None:
        from faster_whisper import WhisperModel
        _fw_model = WhisperModel(FW_MODEL, device="cpu", compute_type="int8")
    segments, _info = _fw_model.transcribe(
        video_path, language=LANGUAGE, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500))
    return [{"start": round(s.start, 2), "end": round(s.end, 2),
             "text": s.text.strip()} for s in segments]


def transcribe(video_path, progress=None):
    """Whisper transcription -> list of {start, end, text}. Cached."""
    # "transcript2": the cache was bumped when word_timestamps was enabled --
    # the old "transcript" caches hold the coarse 1-second timestamps and must
    # not be reused.
    cached = _load_cache(video_path, "transcript2")
    if cached is not None:
        return cached["segments"]

    if progress:
        progress("음성을 텍스트로 변환 중...")
    out = _transcribe_mlx(video_path)
    if out is None:
        out = _transcribe_faster(video_path)
    _save_cache(video_path, "transcript2", {"segments": out})
    return out


def detect_scenes(video_path, progress=None):
    """Scene-cut times (seconds) via PySceneDetect ContentDetector. Cached."""
    cached = _load_cache(video_path, "scenes")
    if cached is not None:
        return cached["scenes"]

    if progress:
        progress("장면 전환 감지 중...")
    from scenedetect import detect, ContentDetector
    scene_list = detect(video_path, ContentDetector(threshold=27))
    cuts = [round(start.get_seconds(), 3)
            for i, (start, _end) in enumerate(scene_list) if i > 0]
    _save_cache(video_path, "scenes", {"scenes": cuts})
    return cuts


def extract_voice_envelope(video_path, progress=None):
    """Voice-band (250-3000 Hz) loudness envelope -> {rate, db}. Cached."""
    cached = _load_cache(video_path, "voice")
    if cached is not None:
        return cached

    if progress:
        progress("음성 음량 분석 중...")
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", video_path,
           "-af", "highpass=f=250,lowpass=f=3000",
           "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    win = SR // ENV_HZ
    n = (len(samples) // win) * win
    if n == 0:
        return {"rate": ENV_HZ, "db": []}
    frames = samples[:n].reshape(-1, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1.0) / 32768.0)
    data = {"rate": ENV_HZ, "db": [round(float(x), 2) for x in db]}
    _save_cache(video_path, "voice", data)
    return data
