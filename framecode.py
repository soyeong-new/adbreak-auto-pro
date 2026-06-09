"""타임코드 및 프레임 변환 유틸리티 (framecode.py)

fps 인자와 drop_frame 플래그를 받아 동적으로 허용 프레임을 계산합니다.
기본값 FPS=30.0, drop_frame=False (NDF).

허용 프레임 규칙:
  - :00           → FF_TOP (최우선)
  - :01~:03, 끝 2프레임 → FF_CANDIDATE (허용)
  - 나머지        → 마커 배치 불가

29.97fps NDF: frame % 30으로 FF 계산.
29.97fps DF : SMPTE 드롭프레임 공식으로 FF 계산, 타임코드에 세미콜론 사용.
스냅 없음.
"""
FPS = 30.0   # 기본값 (watcher 고정값, 하위 호환)
FF_TOP = {0}


def _base(fps):
    """초당 프레임 수를 정수로 반환. 29.97 → 30."""
    return round(fps)


def _ff_candidate(fps):
    b = _base(fps)
    return {1, 2, 3, b - 2, b - 1}


def _ff_allowed(fps):
    return FF_TOP | _ff_candidate(fps)


# 하위 호환용 모듈 레벨 상수 (30fps 기준)
FF_CANDIDATE = _ff_candidate(FPS)
FF_ALLOWED   = _ff_allowed(FPS)


# ---------------------------------------------------------------------------
# Drop-frame timecode (29.97fps DF, SMPTE 방식)
# ---------------------------------------------------------------------------
_DF_FRAMES_PER_10MIN = 17982   # 10*60*30 - 18
_DF_FRAMES_PER_MIN   = 1798    # 60*30 - 2


def _df_frame_ff(frame):
    """절대 프레임 번호 → 29.97fps DF 타임코드의 FF(프레임 자리) 숫자만 반환."""
    blocks = frame // _DF_FRAMES_PER_10MIN
    rem    = frame % _DF_FRAMES_PER_10MIN
    if rem < 1800:
        frames_in_min = rem
    else:
        rem -= 1800
        frames_in_min = rem % _DF_FRAMES_PER_MIN + 2
    return frames_in_min % 30


def _frame_to_df_timecode(frame):
    """절대 프레임 번호 → 'HH:MM:SS;FF' (DF, 세미콜론 구분)."""
    blocks        = frame // _DF_FRAMES_PER_10MIN
    rem           = frame % _DF_FRAMES_PER_10MIN
    if rem < 1800:
        minutes_in_block = 0
        frames_in_min    = rem
    else:
        rem -= 1800
        minutes_in_block = rem // _DF_FRAMES_PER_MIN + 1
        frames_in_min    = rem % _DF_FRAMES_PER_MIN + 2

    ff = frames_in_min % 30
    ss = frames_in_min // 30
    total_m = blocks * 10 + minutes_in_block
    mm = total_m % 60
    hh = total_m // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


# ---------------------------------------------------------------------------
# 공통 변환 함수
# ---------------------------------------------------------------------------

def seconds_to_frame(seconds, fps=FPS):
    return int(round(max(0.0, seconds) * fps))


def frame_to_seconds(frame, fps=FPS):
    return frame / fps


def frame_tier(frame, fps=FPS, drop_frame=False):
    """0 = top (:00), 1 = candidate, None = not allowed.

    drop_frame=True 이면 DF 타임코드 기준 FF를 사용.
    """
    if drop_frame and abs(fps - 29.97) < 0.1:
        ff = _df_frame_ff(frame)
        b  = 30
    else:
        b  = _base(fps)
        ff = frame % b

    if ff in FF_TOP:
        return 0
    if ff in {1, 2, 3, b - 2, b - 1}:
        return 1
    return None


def frame_to_timecode(frame, fps=FPS, drop_frame=False):
    """frame index → 'HH:MM:SS:FF' (NDF) 또는 'HH:MM:SS;FF' (DF)."""
    if drop_frame and abs(fps - 29.97) < 0.1:
        return _frame_to_df_timecode(frame)
    b      = _base(fps)
    ff     = frame % b
    total_s = frame // b
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def seconds_to_timecode(seconds, fps=FPS, drop_frame=False):
    return frame_to_timecode(seconds_to_frame(seconds, fps), fps, drop_frame)


def xml_timebase_ntsc(fps, drop_frame=False):
    """XML <timebase> 및 <ntsc> 값 반환.

    24fps        → (24, "FALSE")
    29.97fps NDF → (30, "FALSE")
    29.97fps DF  → (30, "TRUE")
    30fps        → (30, "FALSE")
    기타         → (round(fps), "FALSE")
    """
    b    = _base(fps)
    ntsc = "TRUE" if (drop_frame and abs(fps - 29.97) < 0.1) else "FALSE"
    return b, ntsc
