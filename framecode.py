"""타임코드 및 프레임 변환 유틸리티 (framecode.py)

29.97fps Non-Drop-Frame 기준으로 프레임 번호 ↔ 초(seconds) ↔ HH:MM:SS:FF 변환을 담당합니다.

허용 프레임 규칙:
  - :00        → FF_TOP (최우선)
  - :01~:03, :28~:29 → FF_CANDIDATE (허용)
  - :04~:27    → 마커 배치 불가

스냅(근접 프레임으로 이동) 없음 — 이벤트가 정확히 허용 프레임에 해당할 때만 마커 생성.
"""
FPS = 30000.0 / 1001.0   # 29.97002997...
FF_TOP = {0}
FF_CANDIDATE = {1, 2, 3, 28, 29}
FF_ALLOWED = FF_TOP | FF_CANDIDATE


def seconds_to_frame(seconds):
    return int(round(max(0.0, seconds) * FPS))


def frame_to_seconds(frame):
    return frame / FPS


def frame_tier(frame):
    """0 = top (:00), 1 = candidate (:01-03/:28-29), None = not allowed."""
    ff = frame % 30
    if ff in FF_TOP:
        return 0
    if ff in FF_CANDIDATE:
        return 1
    return None


def frame_to_timecode(frame):
    """29.97 NDF frame index -> nominal 30 fps NDF timecode 'HH:MM:SS:FF'."""
    ff = frame % 30
    total_s = frame // 30
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def seconds_to_timecode(seconds):
    return frame_to_timecode(seconds_to_frame(seconds))
