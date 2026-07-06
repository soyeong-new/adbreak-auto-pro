"""로컬 웹 서버 (app.py)

브라우저 UI를 서빙하고 분석 API를 제공합니다.

  - GET  /          → index.html 반환
  - POST /api/analyze → 영상 분석 실행, 결과(마커 목록 + XML 문자열)를 JSON으로 반환

분석이 완료돼도 XML 파일을 자동으로 저장하지 않습니다.
UI에서 다운로드 버튼을 눌러야 로컬에 저장됩니다.

실행: ../.venv/bin/python app.py  (포트 8000, 사용 중이면 8001~8009 순으로 시도)
"""
import http.server
import socketserver
import json
import os
import sys
import shutil

from analyzer import run_analysis
from pipeline import get_duration

DEFAULT_PORT = 8000


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            index = os.path.join(os.getcwd(), "index.html")
            if not os.path.exists(index):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(index, "rb") as f:
                self.wfile.write(f.read())
            return

        if self.path == "/genres.json":
            # 장르 프리셋 단일 소스 — UI가 fetch로 읽어 PRESETS 구성.
            path = os.path.join(os.getcwd(), "genres.json")
            if not os.path.exists(path):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as f:
                self.wfile.write(f.read())
            return

        if self.path == "/api/pick-files":
            # macOS Finder 파일 선택 창 — osascript로 전체 경로 반환
            import subprocess
            script = (
                'set theFiles to choose file of type {"mp4","mov","mkv","m4v"} '
                'with multiple selections allowed '
                'with prompt "영상 파일을 선택하세요"\n'
                'set out to ""\n'
                'repeat with f in theFiles\n'
                '  set out to out & POSIX path of f & "\\n"\n'
                'end repeat\n'
                'return out'
            )
            try:
                r = subprocess.run(["osascript", "-e", script],
                                   capture_output=True, text=True, timeout=120)
                paths = [p.strip() for p in r.stdout.splitlines() if p.strip()]
                self._send_json(200, {"paths": paths})
            except subprocess.TimeoutExpired:
                self._send_json(200, {"paths": []})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/analyze":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "잘못된 요청입니다."})
            return

        # Accept a list of paths (each a video file or a folder) or a single path.
        raw = data.get("video_paths")
        if not raw and data.get("video_path"):
            raw = [data["video_path"]]
        raw = [p.strip() for p in (raw or []) if p and p.strip()]
        if not raw:
            self._send_json(400, {"error": "영상 파일 또는 폴더 경로를 입력해 주세요."})
            return

        # UI sends spacing rules / dead zones in minutes; convert to seconds.
        settings = {}
        for key in ("first_min", "first_max", "gap_min", "gap_max",
                    "intro_deadzone", "outro_deadzone"):
            val = data.get("settings", {}).get(key)
            if val is not None:
                settings[key] = float(val) * 60.0

        # 장르 가중치 — 분 변환 없이 그대로 사용
        for key in ("w_scene", "w_topic_change", "w_fade", "fade_silence_bonus",
                    "clip_threshold", "w_quiet_cut"):
            val = data.get("settings", {}).get(key)
            if val is not None:
                settings[key] = float(val)
        val = data.get("settings", {}).get("silence_min")
        if val is not None:
            settings["silence_min"] = float(val)
        # 페이드 침묵 관문 여부 (bool)
        val = data.get("settings", {}).get("fade_require_silence")
        if val is not None:
            settings["fade_require_silence"] = bool(val)

        # fps_mode: "auto" | "30" | "29.97_ndf" | "29.97_df"
        fps_mode = data.get("fps_mode", "30")
        settings["fps_mode"] = str(fps_mode)

        # A path may be an absolute file/folder, or a bare filename dragged in;
        # bare filenames are resolved by searching the base folder ('root').
        root = (data.get("root") or "").strip()
        exts = (".mp4", ".mov", ".mkv", ".m4v")
        videos, errors, seen = [], [], set()

        def search_root(name):
            if not root or not os.path.isdir(root):
                return None, "기본 폴더가 지정되지 않았습니다 — 절대경로를 입력하거나 기본 폴더를 지정하세요."
            hits = []
            for dirpath, _dirs, files in os.walk(root):
                if name in files:
                    hits.append(os.path.join(dirpath, name))
            if not hits:
                return None, f"기본 폴더에서 '{name}'을(를) 찾지 못했습니다."
            if len(hits) > 1:
                return None, f"'{name}'이(가) 여러 곳에 있습니다 — 절대경로로 지정하세요."
            return hits[0], None

        for p in raw:
            if os.path.isdir(p):
                found = sorted(f for f in os.listdir(p)
                               if f.lower().endswith(exts) and not f.startswith("._"))
                if not found:
                    errors.append({"path": p, "error": "폴더에 영상 파일이 없습니다."})
                for f in found:
                    videos.append(os.path.join(p, f))
            elif os.path.isfile(p):
                videos.append(p)
            else:
                resolved, err = search_root(os.path.basename(p))
                if resolved:
                    videos.append(resolved)
                else:
                    errors.append({"path": p, "error": err})
        videos = [v for v in videos if not (v in seen or seen.add(v))]

        results = []
        for idx, video in enumerate(videos, 1):
            try:
                print(f"\n[{idx}/{len(videos)} 분석 시작] {video}", flush=True)
                duration = get_duration(video)
                if duration < 300:
                    mins = duration / 60
                    print(f"[{idx}/{len(videos)} 건너뜀] 영상 길이 {mins:.1f}분 — 5분 미만은 분석하지 않습니다.", flush=True)
                    results.append({
                        "video_path": video,
                        "video_name": os.path.basename(video),
                        "duration": duration,
                        "skipped_short": True,
                        "primary_count": 0,
                        "marker_count": 0,
                        "primary_slots": [],
                        "markers": [],
                        "xml_primary": "",
                        "xml_all": "",
                    })
                    continue
                report = run_analysis(video, settings or None,
                                      progress=lambda m: print(f"  · {m}", flush=True))
                print(f"[{idx}/{len(videos)} 완료] 1차 {report['primary_count']}구간 / "
                      f"전체 {report['marker_count']}개",
                      flush=True)
                results.append(report)
            except Exception as e:
                import traceback
                traceback.print_exc()
                errors.append({"path": video, "error": str(e)})

        self._send_json(200, {"results": results, "errors": errors})


def main():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("경고: ffmpeg/ffprobe를 찾을 수 없습니다. 'brew install ffmpeg' 필요.")

    port = DEFAULT_PORT
    server = None
    while port < DEFAULT_PORT + 10:
        try:
            server = socketserver.TCPServer(("", port), Handler)
            break
        except OSError:
            port += 1
    if server is None:
        print("열린 포트를 찾을 수 없습니다.")
        sys.exit(1)

    url = f"http://localhost:{port}"
    print("=" * 60, flush=True)
    print("🎬 프리미어 Ad Break 마커 자동화 서버", flush=True)
    print(f"🔗 브라우저에서 열기:  {url}", flush=True)
    print("=" * 60, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료.")
        server.server_close()


if __name__ == "__main__":
    main()
