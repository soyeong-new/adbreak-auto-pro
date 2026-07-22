# CLAUDE.md — adbreak_auto_pro

## 프로젝트 개요

유튜브 영상의 광고 삽입 후보 마커를 자동으로 찾아 Premiere Pro용 XML로 출력하는 로컬 분석 도구.
외부 API 없이 전부 로컬에서 동작. 최종 선택은 편집자가 직접 판단.

## 실행 방법

```bash
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python app.py
# 브라우저: http://localhost:8000
```

가상환경: `/Users/choisoyeong/Desktop/vscode/.venv` (Python 3.11, Apple Silicon)

## 핵심 규칙 — 절대 바꾸지 말 것

- **허용 프레임**: 30fps 기준 :00/:01/:02/:03/:28/:29 만 허용. 스냅 없음.
- **XML 자동 저장 없음**: 분석 완료 후 파일을 자동으로 디스크에 쓰지 않음. UI 다운로드 버튼으로만 저장.
- **CLIP 임계값**: 기본 `SAME_THRESHOLD = 0.80` (scene_verify.py). 유사도 < 0.80 = 진짜 장면 전환. 장르별 `clip_threshold`로 override (자취남=0.85 — 같은 집 안 약한 컷도 광고점, 측정[production 설정]: 컷앵커 마커 편당 5→11개·재현 31%→46%, `eval/measure_recall.py`로 재현).
- **텍스트 유사도 임계값**: `TEXT_SIM_THRESHOLD = 0.75` (local_breaks.py). 미만이면 주제 전환 +4.0점.

## 파일 구조 및 역할

| 파일 | 역할 |
|---|---|
| `app.py` | 로컬 웹 서버 (포트 8000), `/api/analyze` 엔드포인트 |
| `analyzer.py` | 전체 파이프라인 조율 — `run_analysis()` 진입점 |
| `pipeline.py` | Whisper 전사 / PySceneDetect 장면 탐지 / ffmpeg 음량 추출(voice·loudness) + 캐싱 |
| `local_breaks.py` | 마커 후보 생성(Path 1/2/3) + 점수 계산 + 1차 배치 선발 |
| `genres.json` | 장르 프리셋 단일 소스 (UI·공유폴더 공통) |
| `scene_verify.py` | CLIP 장면 전환 검증 (단건 + 배치) |
| `text_similarity.py` | ko-sroberta 텍스트 유사도 계산 |
| `topic_breaks.py` | Whisper 세그먼트 → 문장 병합 (한국어 종결어미 기반) |
| `patterns.py` | CTA·연속 표현 등 한국어 패턴 감지 |
| `framecode.py` | 타임코드/프레임 변환, 허용 프레임 정의 |
| `xml_output.py` | Premiere용 FCP7 xmeml v5 XML 생성 |
| `watcher.py` | 구글 드라이브 폴더 감시 → 자동 분석 |

## 마커 생성 세 가지 경로

출력 XML 두 가지 — **2차(`_adbreaks_all.xml`)는 Path 1·2·3 전체 후보, 간격 제한
없음. 1차(`_adbreaks.xml`)는 그 전체 풀에서 슬롯(첫 광고 3~10분, 이후 10~15분
간격)별 상위 최대 5개(`n_alternatives`)를 뽑은 것 — 1차는 항상 2차의 부분집합.**
(2026-07: 예전엔 2차가 컷 앵커만 담아 1차보다 적게 나올 수 있었음 — 수정됨.)

**Path 1 — 침묵 기반**
- 문장 종결 직후 적응형 침묵 ≥ 0.5s
- 침묵 안에 허용 프레임 존재
- CLIP 확인 컷 ±1.0s 이내면 `has_cut=True` 업그레이드

**Path 2 — 컷 앵커**
- CLIP 유사도 < 0.80인 실제 화면 전환
- 컷 시각이 정확히 허용 프레임 (스냅 없음)
- Whisper 문장/세그먼트 갭 ±0.5s 이내

**Path 3 — 페이드 앵커**
- ffmpeg 밝기 분석으로 탐지한 페이드 V 꼭짓점(암전)
- 침묵 처리는 장르별 (`fade_require_silence` 참조)
- CLIP 재검증 없음 (암전 프레임은 CLIP 무의미)
- 대사 기반 점수 제외, `w_fade`·:00 프레임·마무리 표현만 채점
- 컷/침묵 마커와 SCENE_RADIUS(0.3s) 이내로 겹치면 새 마커 대신 기존 마커를
  페이드 앵커로 승격 + `w_fade`를 점수에 가산(증거 병합, 2026-07)

## 점수 체계 (local_breaks.py)

| 조건 | 점수 | 적용 패스 |
|---|---|---|
| CLIP 확인 장면 전환 (`w_scene`) | +8.0 | 1, 2 |
| 페이드 전환 (`w_fade`) | +8.0(영화) | 3 |
| BGM 없는 조용한 구간 (`w_quiet_cut`) | +장르별 | 1, 2 |
| 주제 전환 (텍스트 유사도 < 0.75) | +4.0 | 2 |
| 강한 전환 표현 | +3.0 | 1, 2 |
| 마무리 표현 | +2.0 | 1, 2, 3 |
| 페이드 + 침묵 동반 (`fade_silence_bonus`) | +2.0(케이팝) | 3 |
| :00 프레임 | +1.0 | 1, 2, 3 |
| 약한 전환 표현 | +1.0 | 1, 2 |
| CLIP 재검증 실패 | −8.0 | 1 |
| 연속 표현 | −2.0 | 1, 2 |
| 짧은 선행 문장 | −1.5 | 1, 2 |
| Q&A 패턴 | −1.0 | 1, 2 |

> **2026-06 변경**: CTA 패널티(`p_cta`) 전면 삭제 — 정답 측정 결과 재현에 영향이
> 없어 전체 패스·전체 장르에서 제거. Path 3(페이드)는 발화 단절로 대사 기반 점수를
> 제외하고 화면(`w_fade`)·:00 프레임·마무리 표현만 채점.
> `w_quiet_cut`: loudness_env(전체 주파수 RMS) 기반 BGM 없는 구간 보너스.
> Path 1·2에 적용. 기본값 0, 드라마=2.0(측정 기반).
> 설계 경위는 `DESIGN_markers.md` 참조.

### Path 3(페이드) 침묵 처리 — 장르별

- `fade_require_silence=true` (토크·강의·푸드·여행·자취남): 침묵이 관문, 없으면 탈락.
- `fade_require_silence=false` (영화·드라마·케이팝): 페이드 위 배경 스코어가 지속돼
  침묵이 없어도 통과. 케이팝만 침묵 동반 시 `fade_silence_bonus` 가산.
- 컷·페이드가 SCENE_RADIUS 이내 겹치면 **페이드 우선 + 증거 병합**
  (기존 마커를 페이드 앵커로 승격, 컷의 CLIP·점수 증거 보존).

## 캐시 (.cache/)

캐시 키: `{영상명}_{파일크기}`

- `*.transcript2.json` — Whisper 전사 (word_timestamps=True)
- `*.scenes.json` — PySceneDetect 장면 전환
- `*.voice.json` — 음성 음량 엔벌로프 (250~3000 Hz, 침묵 판별용)
- `*.loudness.json` — 전체 주파수 RMS 엔벌로프 (BGM 유무 판별용, `w_quiet_cut`)
- `*.clip_cuts.json` — 전체 컷 CLIP 유사도
- `*.text_sim.json` — 텍스트 유사도
- `*.fades.json` — 페이드 인/아웃 V 꼭짓점 (Path 3)

캐시 삭제 후 재분석:
```bash
rm .cache/{영상명}_{크기}.clip_cuts.json   # CLIP만 재실행
rm .cache/{영상명}_{크기}.*               # 전체 재분석
```

## 주요 상수 위치

- `SAME_THRESHOLD = 0.80` → `scene_verify.py`
- `TEXT_SIM_THRESHOLD = 0.75` → `local_breaks.py`
- `W_SCENE = 8.0`, `W_TOPIC_CHANGE = 4.0` → `local_breaks.py`
- `SILENCE_MIN = 0.5`, `CUT_BOUNDARY_WINDOW = 0.5` → `local_breaks.py`
- `ContentDetector(threshold=27)` → `pipeline.py`

## 설정 파라미터 (분 단위, app.py에서 초 변환)

| 파라미터 | 기본값 |
|---|---|
| `intro_deadzone` | 3분 |
| `outro_deadzone` | 3분 |
| `first_min` / `first_max` | 3분 / 10분 |
| `gap_min` / `gap_max` | 10분 / 15분 |
| `n_alternatives` | 5 |

## 장르 프리셋 — `genres.json` 단일 소스

장르별 가중치는 **`genres.json` 한 곳**에서 관리. UI(index.html)와 공유폴더
(setup_dropbox.sh → settings.json)가 모두 이 파일을 읽으므로, 장르 추가·수정 시
`genres.json`만 고치면 양쪽에 반영된다. 단위는 사람 단위(분·초·점수)로 통일.

키: `w_scene`, `w_topic_change`, `silence_min`(초), `w_fade`, `w_quiet_cut`(점수),
`fade_require_silence`(bool), `fade_silence_bonus`(점수),
`clip_threshold`(CLIP 장면전환 문턱, 기본 0.80),
`intro_deadzone`/`outro_deadzone`(분), 광고 간격(분).
`_검증상태` 객체로 "임시값/측정됨"을 표기 — 새 장르 데이터가 들어오면 해당 값만
수정하면 된다.

## 의존성 버전

```
mlx-whisper==0.4.3
open-clip-torch==3.3.0
scenedetect==0.7
sentence-transformers==5.5.1
opencv-python==4.13.0.92
numpy==2.4.6
Pillow==12.2.0
faster-whisper==1.2.1
torch==2.12.0
```
