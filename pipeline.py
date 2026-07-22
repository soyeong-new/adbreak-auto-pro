"""영상 신호 추출 및 캐싱 (pipeline.py)

전사·장면 탐지·음성 음량 추출은 시간이 오래 걸리므로 결과를 .cache/에 저장합니다.
같은 영상을 다시 분석할 때는 캐시를 즉시 반환합니다.

  transcribe()              — Whisper 음성 전사 (mlx-whisper / faster-whisper 폴백)
  detect_scenes()           — PySceneDetect 장면 전환 탐지 (ContentDetector threshold=27)
  extract_voice_envelope()  — ffmpeg으로 250~3000Hz 음량 곡선 추출 (침묵 판별용)
  get_scene_proxy()         — detect_scenes/detect_fade_cuts용 저해상도 프록시 생성(fps 동일 보장)
  get_duration()            — ffprobe로 영상 길이(초) 반환
  get_fps()                 — ffprobe로 영상 프레임레이트 반환

캐시 키: {영상명}_{파일크기} — 파일이 교체되면 자동으로 재분석.
"""
import os
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

# Path 3 — 페이드 인/아웃 탐지 파라미터
FADE_DARK_THRESH = 10.0     # 0-255: 이 값 미만이면 "어두운 프레임"으로 판단
FADE_MIN_FRAMES  = 5        # 연속 어두운 프레임이 최소 이 수 이상이어야 페이드로 인정

# 씬/페이드 탐지용 저해상도 프록시 — 픽셀 수만 줄이고 fps는 원본과 동일해야 함
# (허용 프레임 판정이 fps 기준이라 타이밍이 어긋나면 안 됨). fps가 안 맞으면
# 프록시를 버리고 원본으로 폴백.
PROXY_WIDTH = 640
FPS_TOLERANCE = 0.01

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
    # timestamps in low-confidence spans -- and integer seconds, under 30
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


def _proxy_path(video_path):
    st = os.stat(video_path)
    key = f"{os.path.splitext(os.path.basename(video_path))[0]}_{st.st_size}"
    return os.path.join(CACHE_DIR, f"{key}.proxy.mp4")


def get_scene_proxy(video_path, progress=None):
    """Low-res (PROXY_WIDTH-wide) stand-in for video_path, used only to decode
    frames for detect_scenes/detect_fade_cuts. Same fps as the source so cut
    timestamps still line up with the original -- verified after encoding, and
    the original video_path is returned instead if that check fails for any
    reason (build error, fps mismatch), so callers always get a valid path.
    Cached next to the other per-video artifacts.
    """
    proxy = _proxy_path(video_path)
    src_fps = get_fps(video_path)
    if os.path.exists(proxy):
        if src_fps is not None and abs((get_fps(proxy) or 0) - src_fps) < FPS_TOLERANCE:
            return proxy
        os.remove(proxy)  # stale/bad proxy from a previous run -- rebuild below

    if progress:
        progress("분석용 저해상도 프록시 생성 중...")
    tmp = f"{proxy}.{os.getpid()}.tmp.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video_path,
           "-vf", f"scale={PROXY_WIDTH}:-2", "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", tmp]
    try:
        subprocess.run(cmd, check=True)
        proxy_fps = get_fps(tmp)
        if src_fps is None or proxy_fps is None or abs(proxy_fps - src_fps) >= FPS_TOLERANCE:
            raise ValueError(f"proxy fps mismatch: source={src_fps} proxy={proxy_fps}")
        os.replace(tmp, proxy)  # atomic -- safe even if another thread races here
        return proxy
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        return video_path


def detect_scenes(video_path, progress=None):
    """Scene-cut times (seconds) via PySceneDetect ContentDetector. Cached."""
    cached = _load_cache(video_path, "scenes")
    if cached is not None:
        return cached["scenes"]

    if progress:
        progress("장면 전환 감지 중...")
    from scenedetect import detect, ContentDetector
    proxy = get_scene_proxy(video_path, progress)
    scene_list = detect(proxy, ContentDetector(threshold=27))
    cuts = [round(start.get_seconds(), 3)
            for i, (start, _end) in enumerate(scene_list) if i > 0]
    _save_cache(video_path, "scenes", {"scenes": cuts})
    return cuts


def detect_fade_cuts(video_path, progress=None):
    """페이드 인/아웃 V 꼭짓점 탐지 — ffmpeg 프레임별 밝기 분석. Cached.

    32×18 그레이스케일 축소 프레임의 평균 밝기를 프레임마다 계산한 뒤,
    FADE_MIN_FRAMES 이상 연속으로 FADE_DARK_THRESH(10/255) 미만인 구간을 "페이드"로 보고
    그 구간에서 가장 어두운 프레임(V 꼭짓점)의 시각을 반환합니다.

    반환: [float, ...] — V 꼭짓점 시각(초) 목록.
    CLIP 검증 없음 (암전 프레임에서는 CLIP 코사인 유사도가 무의미하게 낮게 나옴).
    """
    cached = _load_cache(video_path, "fades")
    if cached is not None:
        return cached["fades"]

    if progress:
        progress("페이드 인/아웃 탐지 중...")

    proxy = get_scene_proxy(video_path, progress)
    import numpy as np
    W, H = 32, 18
    cmd = ["ffmpeg", "-v", "error", "-i", proxy,
           "-vf", f"scale={W}:{H}",
           "-an", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    frame_size = W * H
    n_frames = len(raw) // frame_size
    if n_frames == 0:
        _save_cache(video_path, "fades", {"fades": []})
        return []

    fps = get_fps(video_path) or 30.0
    frames = (np.frombuffer(raw[:n_frames * frame_size], dtype=np.uint8)
              .reshape(n_frames, frame_size))
    brightness = frames.mean(axis=1)  # shape: (n_frames,), 0–255

    fades = []
    i = 0
    while i < n_frames:
        if brightness[i] < FADE_DARK_THRESH:
            j = i
            while j < n_frames and brightness[j] < FADE_DARK_THRESH:
                j += 1
            if j - i >= FADE_MIN_FRAMES:
                darkest = i + int(np.argmin(brightness[i:j]))
                fades.append(round(darkest / fps, 3))
            i = j
        else:
            i += 1

    _save_cache(video_path, "fades", {"fades": fades})
    return fades


def extract_loudness_envelope(video_path, progress=None):
    """Full-spectrum loudness envelope -> {rate, db}. Cached.

    전체 주파수 대역 RMS. voice_env(250~3000 Hz)는 음성 침묵 판별용이라
    BGM 에너지를 놓친다. 이 함수는 필터 없이 전체 에너지를 측정해
    BGM 있음/없음 신호로 사용한다.
    """
    cached = _load_cache(video_path, "loudness")
    if cached is not None:
        return cached

    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", video_path,
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
    _save_cache(video_path, "loudness", data)
    return data


def extract_voice_envelope(video_path, progress=None):
    """Voice-band (250-3000 Hz) loudness envelope -> {rate, db}. Cached.

    음성 대역(250~3000Hz)만 측정. 음성이 멈추면 낮아지므로 침묵 판별에 사용.
    BGM이 있어도 음성 대역이 조용하면 낮게 나온다.
    """
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





def extract_diarization(video_path, hf_token=None, progress=None):
    """화자 구분(diarization) → [(start, end, speaker_id)] 캐시.

    pyannote/speaker-diarization-3.1 사용.
    hf_token: HuggingFace 토큰. None이면 .env의 HF_TOKEN 사용.
    캐시: *.diarization.json
    """
    cached = _load_cache(video_path, "diarization")
    if cached is not None:
        return cached

    if hf_token is None:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith("HF_TOKEN="):
                    hf_token = line.strip().split("=", 1)[1]
                    break

    if not hf_token:
        return []

    if progress:
        progress("화자 구분 분석 중 (pyannote)...")

    import tempfile as _tmp

    with _tmp.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "audio.wav")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", video_path,
             "-ac", "1", "-ar", "16000", "-f", "wav", wav],
            capture_output=True, check=True
        )
        from pyannote.audio import Pipeline as _Pipeline
        pipe = _Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", token=hf_token
        )
        result = pipe(wav)
        diar = result.speaker_diarization
        segs = [
            {"start": round(s.start, 3), "end": round(s.end, 3), "speaker": sp}
            for s, _, sp in diar.itertracks(yield_label=True)
        ]

    _save_cache(video_path, "diarization", segs)
    return segs
