"""29.97 fps Non-Drop-Frame timecode helpers.

A marker may only sit on an allowed frame -- last digit (FF) in
{00,01,02,03,28,29}. :00 is top priority; :01-03/:28-29 are candidate-tier.
Any other frame (:04-:27) cannot hold a marker. Markers are never moved/snapped
onto an allowed frame -- a candidate simply qualifies only if the event already
lands on one.
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
