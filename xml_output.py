"""Build a Final Cut Pro 7 XML (xmeml v5) that Premiere Pro imports as a
sequence with timeline markers -- one marker per ad break candidate.

The user reviews all candidate markers in Premiere and keeps the ones they want.
"""
import os
from framecode import seconds_to_frame


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _marker_xml(name, comment, frame):
    return (
        "    <marker>\n"
        f"      <name>{_esc(name)}</name>\n"
        f"      <comment>{_esc(comment)}</comment>\n"
        f"      <in>{frame}</in>\n"
        f"      <out>{frame}</out>\n"
        "    </marker>"
    )


def build_candidate_xml(markers, video_path, duration):
    """markers: flat list from select_ad_breaks_local. Returns xmeml XML."""
    base = os.path.splitext(os.path.basename(video_path))[0] if video_path else "ad_breaks"

    marker_xmls = []
    last_frame = 0
    for i, m in enumerate(markers, start=1):
        frame = m["frame"]
        last_frame = max(last_frame, frame)
        kind = "검증전환" if m.get("has_cut") else "참고"
        name = f"광고{i} [{kind}] [{m['timecode']}]"
        ended = m["ended_sentence"][-45:]
        nxt = m["next_sentence"][:45]
        text_sim_tag = ""
        if m.get("text_sim") is not None:
            text_sim_tag = f" | 텍스트유사도 {m['text_sim']:.3f}"
        comment = (f"점수 {m['score']} | {m['reason']}{text_sim_tag} | "
                   f"…{ended}  ▶  {nxt}…")
        marker_xmls.append(_marker_xml(name, comment, frame))

    duration_frames = max(seconds_to_frame(duration), last_frame + 300)

    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE xmeml SYSTEM "http://www.apple.com/DTDs/xmeml-1.0.dtd">',
        '<xmeml version="5">',
        '  <sequence id="sequence-1">',
        f'    <name>{_esc(base)}_AdBreakCandidates</name>',
        f'    <duration>{duration_frames}</duration>',
        '    <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>',
        '    <timecode>',
        '      <string>00:00:00:00</string>',
        '      <frame>0</frame>',
        '      <displayformat>NDF</displayformat>',
        '      <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>',
        '    </timecode>',
        '    <media>',
        '      <video>',
        '        <format>',
        '          <samplecharacteristics>',
        '            <width>1920</width>',
        '            <height>1080</height>',
        '            <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>',
        '          </samplecharacteristics>',
        '        </format>',
        '        <track></track>',
        '      </video>',
        '    </media>',
    ]
    tail = ['  </sequence>', '</xmeml>']
    return "\n".join(head + marker_xmls + tail) + "\n"
