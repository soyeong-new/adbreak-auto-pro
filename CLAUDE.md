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

- **허용 프레임**: 29.97fps NDF 기준 :00/:01/:02/:03/:28/:29 만 허용. 스냅 없음.
- **XML 자동 저장 없음**: 분석 완료 후 파일을 자동으로 디스크에 쓰지 않음. UI 다운로드 버튼으로만 저장.
- **CLIP 임계값**: `SAME_THRESHOLD = 0.80` (scene_verify.py). 유사도 < 0.80 = 진짜 장면 전환.
- **텍스트 유사도 임계값**: `TEXT_SIM_THRESHOLD = 0.75` (local_breaks.py). 미만이면 주제 전환 +4.0점.

## 파일 구조 및 역할

| 파일 | 역할 |
|---|---|
| `app.py` | 로컬 웹 서버 (포트 8000), `/api/analyze` 엔드포인트 |
| `analyzer.py` | 전체 파이프라인 조율 — `run_analysis()` 진입점 |
| `pipeline.py` | Whisper 전사 / PySceneDetect 장면 탐지 / ffmpeg 음량 추출 + 캐싱 |
| `local_breaks.py` | 마커 후보 생성(Path 1/2) + 점수 계산 + 1차 배치 선발 |
| `scene_verify.py` | CLIP 장면 전환 검증 (단건 + 배치) |
| `text_similarity.py` | ko-sroberta 텍스트 유사도 계산 |
| `topic_breaks.py` | Whisper 세그먼트 → 문장 병합 (한국어 종결어미 기반) |
| `patterns.py` | CTA·연속 표현 등 한국어 패턴 감지 |
| `framecode.py` | 타임코드/프레임 변환, 허용 프레임 정의 |
| `xml_output.py` | Premiere용 FCP7 xmeml v5 XML 생성 |
| `watcher.py` | 구글 드라이브 폴더 감시 → 자동 분석 |

## 마커 생성 두 가지 경로

**Path 1 — 침묵 기반**
- 문장 종결 직후 적응형 침묵 ≥ 0.5s
- 침묵 안에 허용 프레임 존재
- CLIP 확인 컷 ±1.0s 이내면 `has_cut=True` 업그레이드
- 1차 XML에 포함 (전체 마커 대상, 간격 규칙 적용)

**Path 2 — 컷 앵커**
- CLIP 유사도 < 0.80인 실제 화면 전환
- 컷 시각이 정확히 허용 프레임 (스냅 없음)
- Whisper 문장/세그먼트 갭 ±0.5s 이내
- 2차 XML (_adbreaks_all.xml) 에만 포함

## 점수 체계 (local_breaks.py)

| 조건 | 점수 |
|---|---|
| CLIP 확인 장면 전환 | +8.0 |
| 주제 전환 (텍스트 유사도 < 0.75) | +4.0 |
| 강한 전환 표현 | +3.0 |
| 마무리 표현 | +2.0 |
| :00 프레임 | +1.0 |
| 약한 전환 표현 | +1.0 |
| CLIP 재검증 실패 | −8.0 |
| CTA 키워드 | −3.0 |
| 연속 표현 | −2.0 |
| 짧은 선행 문장 | −1.5 |
| Q&A 패턴 | −1.0 |

## 캐시 (.cache/)

캐시 키: `{영상명}_{파일크기}`

- `*.transcript2.json` — Whisper 전사 (word_timestamps=True)
- `*.scenes.json` — PySceneDetect 장면 전환
- `*.voice.json` — 음성 음량 엔벌로프
- `*.clip_cuts.json` — 전체 컷 CLIP 유사도
- `*.text_sim.json` — 텍스트 유사도

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
