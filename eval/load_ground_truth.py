"""자유 형식의 정답 텍스트를 {에피소드: [초, ...]} 형태로 파싱 (load_ground_truth.py)

영상을 보면서 손으로 적은 광고 삽입 시간을 읽어 들입니다.
입력 형식이 느슨해도 모두 동일하게 파싱됩니다:

    EP31: 07:37, 04:42, 03:12, 19:18
    SKA EP32  12:34  18:22

    YBJ_S30_EP45
    00:12:34
    18:22

    EP38:
      07:37
      04:42

파싱 규칙:
  - 각 블록에 *에피소드 헤더* 한 줄 (타임코드 없이 EP 토큰만 있는 줄).
    EP31로 인식되는 예시: "EP31", "SKA_S01_EP31", "EP31:", "SKA EP31  ",
    "ska s01 ep31 - hd_kr". 대소문자 구분 없음. 채널·시즌 접두어는 선택.
  - 시즌 토큰 S<N>이 EP<M> 바로 앞에 있으면 키는 "s{N}_ep{M}" (예: "s23_ep1").
    같은 에피소드 번호를 여러 시리즈가 공유할 때 충돌 방지용
    (예: YBJ_S23_EP01 vs YBJ_S24_EP01). 시즌 토큰 없으면 "ep{M}".
  - 타임코드는 헤더와 같은 줄 또는 다음 줄에 쉼표·공백·개행으로 구분하여 입력.
    다음 헤더 전까지가 해당 에피소드의 시간 목록.
  - 시간 형식: HH:MM:SS, MM:SS, H:MM:SS. 초 단위 숫자("457")도 허용하나 비권장.
  - '#'로 시작하는 줄과 빈 줄은 무시.

반환하는 에피소드 키는 소문자 EP 토큰, 시즌이 있으면 앞에 붙임: "ep31" / "s23_ep1".
전체 파일명(예: "SKA_S01_EP31_HD_KR")이 필요하면 `resolve_episode`로 XML 파일명에서 해석.

엄격한 파서가 아닙니다 — 사용자가 붙여 넣은 내용을 최대한 읽어 들이는 게 목표.
파싱 불가 항목은 수집·보고하며, 조용히 버리지 않습니다.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

# Matches "S23_EP01", "S 23 EP 1", "S01-EP31" — season immediately before EP.
# Returns groups: (season_number, ep_number)
SEASON_EP_RE = re.compile(
    r"(?<![A-Za-z0-9])S[\s_-]?0*(\d+)[\s_-]?EP[\s_-]?0*(\d+)(?![0-9])",
    re.IGNORECASE,
)

# Matches a bare "EP31" (no season prefix on the same match).
# Used as fallback when SEASON_EP_RE doesn't match.
EP_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])EP[\s_-]?0*(\d+)(?![0-9])", re.IGNORECASE)

# Matches one timecode anywhere in a string. Groups: hours, minutes, seconds.
# Accepts HH:MM:SS, H:MM:SS, MM:SS, M:SS. Frames (the trailing :FF on
# HH:MM:SS:FF) are deliberately ignored — the user said the ground truth is
# second-level.
TIME_RE = re.compile(
    r"(?<![\d:])"
    r"(?:(\d{1,2}):)?"      # optional hours
    r"(\d{1,2}):(\d{2})"     # MM:SS (mandatory)
    r"(?::\d{2})?"           # ignore frame field if present
    r"(?![\d:])"
)


def _parse_time(hours: str | None, minutes: str, seconds: str) -> float:
    h = int(hours) if hours else 0
    m = int(minutes)
    s = int(seconds)
    return h * 3600 + m * 60 + s


def _find_times(line: str) -> List[float]:
    out: List[float] = []
    for m in TIME_RE.finditer(line):
        out.append(_parse_time(m.group(1), m.group(2), m.group(3)))
    return out


def _find_episode_token(line: str) -> str | None:
    """Return the episode key if `line` contains an episode header, else None.

    Key format:
      - "s{N}_ep{M}"  when the line has S<N> immediately before EP<M>
      - "ep{M}"       when there is only a bare EP token (no season prefix)

    A line counts as a header only when it has the EP token AND no full
    timecode (HH:MM:SS / MM:SS) on it. A line with both — "EP31: 07:37" — is
    treated as header *and* time line; the caller handles both signals.
    """
    # Try season+ep first (more specific).
    m = SEASON_EP_RE.search(line)
    if m:
        return f"s{int(m.group(1))}_ep{int(m.group(2))}"
    # Fall back to bare EP token.
    m = EP_TOKEN_RE.search(line)
    if m:
        return f"ep{int(m.group(1))}"
    return None


def parse(text: str) -> Tuple[Dict[str, List[float]], List[str]]:
    """Parse the free-form text. Returns (gt, unparseable_lines).

    gt: {"s23_ep1": [580.0, 1239.0, ...], "ep31": [457.0, ...], ...}.
    unparseable_lines: non-blank, non-comment lines that have no EP token and
    no recognizable time. Reported so the user can see what was skipped.
    """
    gt: Dict[str, List[float]] = {}
    unparseable: List[str] = []
    current: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        ep = _find_episode_token(line)
        times = _find_times(line)

        if ep is not None:
            current = ep
            gt.setdefault(current, [])
            # A header line may also carry times after the colon — keep them.
            for t in times:
                if t not in gt[current]:
                    gt[current].append(t)
            continue

        if times:
            if current is None:
                unparseable.append(line)
                continue
            for t in times:
                if t not in gt[current]:
                    gt[current].append(t)
            continue

        unparseable.append(line)

    for ep in gt:
        gt[ep].sort()
    return gt, unparseable


def resolve_episode(ep_key: str, xml_dir: str) -> str | None:
    """Map an ep_key → the matching XML filename stem in `xml_dir`.

    Handles two key formats:
      - "ep31"       → find XML containing EP31 (no season constraint)
      - "s23_ep1"    → find XML containing S23 *and* EP1/EP01

    Returns None if no XML file in xml_dir matches.
    Picks the shortest match if several files match (the cleanest filename).
    """
    if not os.path.isdir(xml_dir):
        return None

    target = ep_key.lower()

    # Parse compound key "s{N}_ep{M}".
    compound = re.match(r"s(\d+)_ep(\d+)$", target)
    if compound:
        season_num = int(compound.group(1))
        ep_num = int(compound.group(2))
        season_pat = re.compile(
            rf"(?<![A-Za-z0-9])S[\s_-]?0*{season_num}(?![0-9])", re.IGNORECASE)
        ep_pat = re.compile(
            rf"(?<![A-Za-z0-9])EP[\s_-]?0*{ep_num}(?![0-9])", re.IGNORECASE)
    else:
        # Plain "ep{N}".
        m = re.match(r"ep(\d+)", target)
        if not m:
            return None
        ep_num = int(m.group(1))
        season_pat = None
        ep_pat = re.compile(
            rf"(?<![A-Za-z0-9])EP[\s_-]?0*{ep_num}(?![0-9])", re.IGNORECASE)

    matches = []
    for name in os.listdir(xml_dir):
        if not name.endswith(".xml"):
            continue
        if not ep_pat.search(name):
            continue
        if season_pat is not None and not season_pat.search(name):
            continue
        stem = name[:-len(".xml")]
        # Drop the "_adbreaks" / "_adbreaks_all" / "_candidates" suffix.
        for suf in ("_adbreaks_all", "_adbreaks", "_candidates_all",
                    "_candidates"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        matches.append(stem)
    if not matches:
        return None
    # Prefer the shortest unique stem.
    return sorted(set(matches), key=len)[0]


def load_from_file(path: str) -> Tuple[Dict[str, List[float]], List[str]]:
    with open(path, "r", encoding="utf-8") as f:
        return parse(f.read())


# ---------- CLI ----------
def _cli():
    import argparse, json, sys
    p = argparse.ArgumentParser(
        description="Parse a free-form ground-truth file into JSON.")
    p.add_argument("input", help="Path to the GT text file, or '-' for stdin.")
    p.add_argument("--xml-dir",
                   help="If given, also resolve each EP to its XML filename.")
    p.add_argument("--out", help="Write JSON here; otherwise print to stdout.")
    args = p.parse_args()

    if args.input == "-":
        text = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()

    gt, unparseable = parse(text)
    payload: dict = {"ground_truth": gt}
    if args.xml_dir:
        payload["resolved"] = {
            ep: resolve_episode(ep, args.xml_dir) for ep in gt
        }
    if unparseable:
        payload["unparseable_lines"] = unparseable
        print(f"⚠ {len(unparseable)} line(s) could not be parsed — see "
              f"'unparseable_lines' in the output.", file=sys.stderr)

    out = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"✓ wrote {args.out} — {len(gt)} episode(s), "
              f"{sum(len(v) for v in gt.values())} marker(s)")
    else:
        print(out)


if __name__ == "__main__":
    _cli()
