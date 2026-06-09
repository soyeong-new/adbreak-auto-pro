"""타임코드 및 프레임 변환 유틸리티 (framecode.py)

fps 인자를 받아 동적으로 허용 프레임을 계산합니다.
기본값 FPS=30.0 (watcher 고정 및 하위 호환).

허용 프레임 규칙 (fps 기준):
  - :00           → FF_TOP (최우선)
  - :01~:03, 끝 2프레임 → FF_CANDIDATE (허용)
  - 나머지        → 마커 배치 불가

29.97fps는 base=30으로 처리 (초당 30 프레임 위치, NDF).
스냅(근접 프레임으로 이동) 없음.
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


def seconds_to_frame(seconds, fps=FPS):
    return int(round(max(0.0, seconds) * fps))


def frame_to_seconds(frame, fps=FPS):
    return frame / fps


def frame_tier(frame, fps=FPS):
    """0 = top (:00), 1 = candidate, None = not allowed."""
    b = _base(fps)
    ff = frame % b
    if ff in FF_TOP:
        return 0
    if ff in _ff_candidate(fps):
        return 1
    return None


def frame_to_timecode(frame, fps=FPS):
    """frame index → 'HH:MM:SS:FF' timecode."""
    b = _base(fps)
    ff = frame % b
    total_s = frame // b
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def seconds_to_timecode(seconds, fps=FPS):
    return frame_to_timecode(seconds_to_frame(seconds, fps), fps)


def xml_timebase_ntsc(fps):
    """XML <timebase> 및 <ntsc> 값 반환.

    24fps  → (24, "FALSE")
    29.97  → (30, "FALSE")  NDF 고정
    30fps  → (30, "FALSE")
    기타   → (round(fps), "FALSE")
    """
    b = _base(fps)
    return b, "FALSE"
