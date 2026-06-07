# Ad Break Marker — 자동 광고 타임코드 탐지 도구

영상 파일을 분석해서 Premiere Pro에 임포트할 수 있는 광고 삽입 후보 마커 XML을 생성합니다.  
외부 API 없이 전부 로컬에서 동작합니다.

---

## 개요

편집자가 광고 삽입 지점을 수동으로 찾는 시간을 줄이기 위해 만들어졌습니다.

핵심 설계 원칙은 두 가지입니다.

1. **완전 자동화를 목표로 하지 않는다.** 광고 지점은 편집자의 판단이 작용하는 주관적 결정이고, 동일한 조건을 가진 위치가 여러 개 있을 수 있습니다. 이 도구는 "딱 맞는 한 프레임"을 찾는 게 아니라 조건을 만족하는 후보를 좁혀서 제시하고 최종 선택은 편집자가 합니다.
2. **마커는 반드시 허용 프레임에만 위치한다.** 30fps 기준 :00/:01/:02/:03/:28/:29 프레임만 허용합니다. 이 외의 프레임은 어떤 이유로도 생성하지 않으며, 스냅(근접 프레임으로 보정)도 없습니다.

---

## 처리 파이프라인

```
영상 파일
  │
  ├─► [1] Whisper 음성 전사        → 문장별 시작/끝 타임스탬프
  ├─► [2] PySceneDetect 장면 탐지  → 컷 시각 목록 (ContentDetector threshold=27)
  ├─► [3] ffmpeg 음성 음량 곡선    → 침묵 판별용 dB 엔벌로프 (250~3000 Hz)
  ├─► [4] ffmpeg 전체 음량 곡선    → BGM 감지용 dB 엔벌로프 (전체 주파수)
  └─► [5] ffmpeg 프레임 밝기 분석  → 페이드 인/아웃 V 꼭짓점 목록
       (1~5 결과는 .cache/에 캐싱, 병렬 실행)
  │
  ▼
  [5] CLIP 배치 장면 검증  (ViT-B-32-quickgelu)
      PySceneDetect가 찾은 컷마다 전후 프레임을 비교
      코사인 유사도 < 0.80 → "진짜 장면 전환" 확정
      유사도 ≥ 0.80 → 동일 장면 내 앵글 변화·그래픽 등, 제외
  │
  ▼
  [6] 한국어 텍스트 유사도  (jhgan/ko-sroberta-multitask)
      데드존 밖 전체 컷 지점의 전후 발화를 임베딩해 유사도 계산
      유사도 < 0.75 → 주제가 바뀌는 컷 (+4.0점)
  │
  ▼
  [6] 마커 후보 생성  (세 가지 경로)

      Path 1 — 침묵 기반
        문장 끝 직후에 적응형 침묵(≥0.5s)이 있고
        침묵 안에 허용 프레임(:00/:01~:03/:28~:29)이 들어오는 경우
        → 마커 생성. 근처에 CLIP 확인 컷이 있으면 "검증전환"으로 업그레이드:
          · PySceneDetect 컷 ±0.3s 이내
          · CLIP 확인 컷 ±1.0s 이내 (더 넓은 반경)

      Path 2 — 컷 앵커
        CLIP 확인 컷(유사도 < 0.80)이 발화 갭(Whisper 문장 간격 또는
        원본 세그먼트 간격 기준 ±0.5s)에 떨어지고,
        해당 컷 시각이 정확히 허용 프레임인 경우에만 마커 생성.
        (침묵 없어도 되지만, 컷 자체가 허용 프레임이어야 함)

      Path 3 — 페이드 앵커
        ffmpeg 32×18 그레이스케일 프레임별 밝기 분석으로 페이드 인/아웃을 탐지.
        연속 5프레임 이상 평균 밝기 < 10(0~255)인 구간에서 가장 어두운 프레임(V 꼭짓점) 추출.
        V 꼭짓점 ±0.5s 범위 안에서 음성 침묵(≥0.2s)이 확인되고,
        침묵 안에 허용 프레임이 있으면 마커 생성.
        CLIP 검증 없음 — 암전 프레임에서는 CLIP 유사도가 항상 낮게 나와 의미 없음.
        Path 1/2 마커가 이미 ±0.3s 이내에 있으면 중복 생성 방지.
  │
  ▼
  [7] CLIP 개별 재검증
      대상: Path 1 중 일반 컷(±0.3s) 기반 마커
      (배치 CLIP으로 이미 확인된 ±1.0s 업그레이드 마커 및 Path 3 페이드 앵커는 생략)
      단건으로 재확인 후 유사도 ≥ 0.80이면 "참고"로 강등 (마커 자체는 유지)
  │
  ▼
  [8] 점수화 + 1차/2차 분리

      1차 (_adbreaks.xml):
        Path 1/2/3 마커 전체 통합 후
        간격 규칙(첫 광고 3~10분, 이후 10~15분 간격) 적용 → 슬롯당 최대 5개 후보
        같은 슬롯 안에서 has_cut=True인 마커가 우선, 그 다음 점수 순 정렬

      2차 (_adbreaks_all.xml):
        Path 2 컷 앵커 + Path 3 페이드 앵커 마커, 간격 제한 없이 전체 출력

  출력: {영상명}_adbreaks.xml (1차) + {영상명}_adbreaks_all.xml (2차)
```

---

## 마커 점수 체계

조건을 통과한 마커끼리 순위를 정하기 위한 점수입니다. 점수는 1차 배치에서 슬롯 내 추천 순서를 결정합니다.

| 요소 | 가중치 | 적용 패스 | 설명 |
|---|---|---|---|
| 장면 전환 (`w_scene`) | 장르별 | 1, 2 | CLIP 확인 화면 전환 |
| 페이드 전환 (`w_fade`) | 장르별 | 3 | 페이드 인/아웃 암전 전환 |
| BGM 없는 조용한 구간 (`w_quiet_cut`) | 장르별 | 1, 2 | 전체 주파수 loudness 기준 BGM 없음 추정 |
| 주제 전환 (텍스트 유사도 < 0.75) | 장르별 | 2 | 전후 내용이 달라짐 |
| 강한 화제 전환 표현 | +3.0 | 1, 2 | 다음 문장이 "자 이제 / 다음으로 / 마지막으로…"로 시작 |
| 마무리 표현 | +2.0 | 1, 2, 3 | 앞 문장이 "겠습니다 / 이상입니다…"로 종료 |
| :00 프레임 | +1.0 | 1, 2, 3 | 최우선 허용 프레임 위치 |
| 약한 전환 표현 | +1.0 | 1, 2 | 다음 문장이 "자 / 이제 / 그러면…"으로 시작 |
| CLIP 재검증 실패 | −8.0 | 1 | 장면 전환 아님으로 판정 |
| 연속 표현 | −2.0 | 1, 2 | "근데/사실/그리고" 등 말이 이어지는 표현 |
| 짧은 선행 문장 | −1.5 | 1, 2 | 앞 문장이 너무 짧음 |
| Q&A 패턴 | −1.0 | 1, 2 | 질문-자문자답 구간 |

> 가중치 "장르별"은 `genres.json`의 장르 프리셋에서 결정됩니다. CTA 패널티는 2026-06에 전면 삭제됐습니다.

---

## 마커 XML 형식

생성된 XML은 **FCP7 xmeml v5** 형식으로 Premiere Pro에서 **파일 → 가져오기**로 직접 불러올 수 있습니다. 타임라인에 마커가 표시됩니다.

마커 이름: `광고N [검증전환|참고] [HH:MM:SS:FF]`

마커 코멘트에는 점수, 판단 근거, 앞뒤 발화 내용(각 45자)이 포함됩니다.  
텍스트 유사도 값은 Path 2(컷 앵커) 마커에만 포함됩니다. Path 1(침묵 기반) 마커 코멘트에는 없습니다.

---

## 프로젝트 구조

```
vscode/
├── .venv/                   공용 가상환경 (여러 프로젝트 공유)
└── adbreak_auto_pro/        이 프로젝트 폴더
    ├── app.py               로컬 HTTP 서버 (포트 8000, /api/analyze 엔드포인트)
    ├── index.html           브라우저 UI
    ├── analyzer.py          전체 파이프라인 조율 (run_analysis)
    ├── pipeline.py          Whisper 전사, PySceneDetect, 음성 엔벌로프 추출 + 캐싱
    ├── local_breaks.py      마커 후보 생성 핵심 로직 + 점수 계산 + 1차 배치
    ├── scene_verify.py      CLIP 장면 전환 검증 (단건 + 배치)
    ├── text_similarity.py   한국어 텍스트 유사도 (ko-sroberta-multitask)
    ├── topic_breaks.py      Whisper 세그먼트 → 문장 병합 (한국어 종결어미 기반)
    ├── patterns.py          한국어 패턴 감지 (CTA, 연속 표현, 오프너/클로저)
    ├── framecode.py         타임코드/프레임 변환, 허용 프레임 정의 (30fps)
    ├── xml_output.py        Premiere용 FCP7 XML 생성
    ├── ground_truth.txt     평가용 정답 데이터 (에피소드별 광고 타임코드)
    └── eval/
        ├── analyze_episodes.py   에피소드 일괄 분석 스크립트
        ├── load_ground_truth.py  ground_truth.txt → JSON 변환
        ├── extract_features.py   XML 마커에서 피처 행렬 추출 (features.json/csv)
        ├── train_score.py        5-fold CV 평가 (Logistic Regression + Random Forest)
        ├── ground_truth.json     변환된 정답 데이터
        └── output/               분석 결과 (features.json, train_report.json)
```

---

## 설치 — 필요한 프로그램 정리

대상 환경: **macOS (Apple Silicon, M1/M2/M3)**, Python 3.11

### A. 시스템 프로그램

| 프로그램 | 분류 | 설명 |
|---|---|---|
| **Homebrew** | 필수 | macOS용 패키지 매니저. 아래 시스템 프로그램들을 설치하기 위한 기반 도구 (https://brew.sh) |
| **Python 3.11** | 필수 | 분석 도구의 실행 언어. 3.11.15 기준으로 동작 검증됨 (3.12/3.13은 의존성 충돌 가능) |
| **FFmpeg** | 필수 | 영상에서 음성·프레임 밝기를 추출하는 영상 처리 엔진. `ffmpeg`, `ffprobe` 두 명령이 사용됨 |
| **Git** | 필수 | 프로젝트 코드 버전 관리·복사용 (macOS에 보통 기본 설치돼 있음) |
| **Visual Studio Code** | 권장 | 코드 편집·실행용 IDE |
| **Google Drive 데스크톱 앱** | 권장 | 팀 공유 폴더 자동 감시(`watcher.py`)를 사용할 경우 필요. 구글 드라이브를 로컬 폴더처럼 마운트 |
| **Adobe Premiere Pro** | 선택 | 분석 결과 XML을 실제 편집에 사용하는 영상 편집 도구. 분석 PC에는 꼭 필요하지는 않음 |

### B. Python 패키지 (가상환경에 설치)

| 패키지 | 버전 | 설명 |
|---|---|---|
| **mlx-whisper** | 0.4.3 | Apple Silicon 전용 음성 인식 엔진. 영상 음성을 텍스트 + 단어별 타임스탬프로 변환 |
| **faster-whisper** | 1.2.1 | mlx 사용 불가 환경(Intel Mac 등)에서의 폴백용 음성 인식 엔진 |
| **open-clip-torch** | 3.3.0 | CLIP 시각 모델(ViT-B-32-quickgelu) 로더. 컷 전후 프레임 비교로 진짜 장면 전환 여부 판별 |
| **scenedetect** (PySceneDetect) | 0.7 | 영상에서 장면 전환 컷을 자동으로 찾아주는 라이브러리 |
| **sentence-transformers** | 5.5.1 | 한국어 문장 임베딩 모델(`jhgan/ko-sroberta-multitask`) 로더. 컷 전후 발화의 주제 변화 측정 |
| **opencv-python** | 4.13.0.92 | 영상 프레임 디코딩 및 이미지 처리 |
| **Pillow** | 12.2.0 | 이미지 처리 라이브러리 (CLIP 입력 변환용) |
| **numpy** | 2.4.6 | 수치 연산 기본 라이브러리. 위 모든 패키지의 공통 의존성 |
| **torch** (PyTorch) | 2.12.0 | CLIP, sentence-transformers의 딥러닝 백엔드 |
| **torchvision** | 0.27.0 | torch와 함께 쓰는 비전 모델/변환 유틸 |

### C. 외부 리소스 (자동 다운로드)

| 항목 | 크기 | 설명 |
|---|---|---|
| CLIP 모델 (ViT-B-32-quickgelu) | ~350MB | 첫 실행 시 자동 다운로드 |
| ko-sroberta 모델 | ~400MB | 첫 실행 시 자동 다운로드 |

> 최초 1회는 인터넷 연결이 필요합니다.

---

### 설치 순서 요약

```bash
# 1. Homebrew 설치 (https://brew.sh 의 한 줄 명령 실행)

# 2. 시스템 프로그램 설치
brew install python@3.11 ffmpeg git

# 3. 프로젝트 코드 복사
#    /Users/{사용자명}/Desktop/vscode/adbreak_auto_pro 위치에 배치

# 4. 가상환경 생성 + Python 패키지 설치
cd /Users/{사용자명}/Desktop/vscode
python3.11 -m venv .venv
.venv/bin/pip install mlx-whisper==0.4.3 open-clip-torch==3.3.0 \
  scenedetect==0.7 sentence-transformers==5.5.1 opencv-python==4.13.0.92 \
  numpy==2.4.6 Pillow==12.2.0 faster-whisper==1.2.1 \
  torch==2.12.0 torchvision==0.27.0

# 5. 실행 (첫 실행 시 모델 자동 다운로드 ~750MB)
cd /Users/{사용자명}/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python app.py
```

> ⚠️ `start_watcher.command` 등 일부 파일에 `/Users/choisoyeong/Desktop/vscode/...` 경로가 하드코딩되어 있습니다. 새 컴퓨터의 사용자명이 다르면 해당 경로를 수정해야 합니다.

---

## 사용법

### 로컬 서버 실행

```bash
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python app.py
```

브라우저에서 열기 → **http://localhost:8000**  
(8000이 사용 중이면 8001~8009 순으로 자동 시도)

---

### 구글 드라이브 자동 감시 (팀 공유)

팀원이 구글 드라이브 폴더에 영상을 올리면 자동으로 분석하고 XML을 같은 폴더에 저장합니다.

**공유 폴더**: https://drive.google.com/drive/u/0/folders/1CjkqNk8ZJUsCZ7zDlfBHZMsth3BvcDEB  
**로컬 동기화 경로**: `/Users/choisoyeong/Library/CloudStorage/GoogleDrive-so-yeong@its-newid.com/내 드라이브/AD Break`

**실행 방법 1 — 더블클릭**  
`start_watcher.command` 파일을 더블클릭하면 터미널이 열리면서 자동 실행됩니다.

**실행 방법 2 — 터미널 직접 입력**

```bash
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
../.venv/bin/python watcher.py "/Users/choisoyeong/Library/CloudStorage/GoogleDrive-so-yeong@its-newid.com/내 드라이브/AD Break"
```

> 컴퓨터가 켜져 있고 watcher가 실행 중인 동안만 자동 처리됩니다.  
> 팀원은 구글 드라이브 링크에서 영상 업로드만 하면 되고, 별도 설치 불필요합니다.

---

### API 직접 호출

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"video_path": "/Volumes/AIPP22/유병재/S30/YBJ_S30_EP78.mp4"}'
```

### 에피소드 일괄 분석

```bash
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro

# eval/analyze_episodes.py 내 VIDEO_LIST 전체 분석
../.venv/bin/python eval/analyze_episodes.py

# 특정 영상
../.venv/bin/python eval/analyze_episodes.py /Volumes/AIPP22/유병재/S23/YBJ_S23_EP01.mp4

# 시즌 폴더 전체
../.venv/bin/python eval/analyze_episodes.py --season /Volumes/AIPP22/유병재/S30

# 이미 XML이 있어도 재분석
../.venv/bin/python eval/analyze_episodes.py --no-skip
```

---

## 설정 파라미터

UI 또는 API 요청의 `settings` 오브젝트로 전달합니다. **시간 값은 분(minute) 단위**로 입력합니다. app.py가 내부적으로 ×60하여 초로 변환합니다.

모든 시간/간격 파라미터는 `genres.json`에서 장르별로 관리됩니다. UI에서 장르를 선택하면 자동으로 채워지며, 슬라이더로 수동 조정도 가능합니다.

| 파라미터 | 단위 | 설명 |
|---|---|---|
| `intro_deadzone` | 분 | 영상 시작 후 마커 금지 구간 |
| `outro_deadzone` | 분 | 영상 끝 전 마커 금지 구간 |
| `first_min` / `first_max` | 분 | 첫 광고 삽입 시간 창 |
| `gap_min` / `gap_max` | 분 | 광고 간 간격 범위 |
| `w_scene` | 점수 | 장면 전환 가중치 |
| `w_topic_change` | 점수 | 주제 전환 가중치 |
| `w_fade` | 점수 | 페이드 전환 가중치 |
| `w_quiet_cut` | 점수 | BGM 없는 조용한 구간 보너스 |
| `silence_min` | 초 | Path 1 침묵 최소 길이 |
| `clip_threshold` | 0~1 | CLIP 장면 전환 판정 문턱 (미만이면 진짜 전환) |

기본값 및 장르별 설정은 `genres.json` 참조. `setup_dropbox.sh`를 실행하면 공유 폴더 `settings.json`에 자동 반영됩니다.

---

## 캐시

분석 결과는 프로젝트 폴더의 `.cache/` 디렉토리에 저장됩니다. 같은 영상을 다시 실행하면 캐시를 바로 사용합니다.

캐시 키는 `{영상명}_{파일크기}` 조합으로 생성됩니다. 파일 내용이 바뀌면 크기도 바뀌므로 자동으로 새 분석을 수행합니다.

| 파일 | 내용 |
|---|---|
| `*.transcript2.json` | Whisper 전사 결과 (word_timestamps=True) |
| `*.scenes.json` | PySceneDetect 장면 전환 목록 |
| `*.voice.json` | 음성 음량 엔벌로프 (250~3000Hz, 침묵 판별용) |
| `*.loudness.json` | 전체 주파수 RMS 엔벌로프 (BGM 감지용, `w_quiet_cut`) |
| `*.clip_cuts.json` | 전체 컷 CLIP 유사도 결과 |
| `*.text_sim.json` | 컷 전후 텍스트 유사도 결과 |
| `*.fades.json` | 페이드 인/아웃 V 꼭짓점 시각 목록 |

캐시 삭제 후 재분석:

```bash
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro
rm .cache/{영상명}_{크기}.scenes.json   # 장면 탐지만 재실행
rm .cache/{영상명}_{크기}.clip_cuts.json  # CLIP 검증만 재실행
rm .cache/{영상명}_{크기}.*             # 해당 영상 전체 재분석
```

---

## 평가 워크플로

ML 피처 분석 및 모델 성능 측정이 필요할 때 사용합니다.

```bash
cd /Users/choisoyeong/Desktop/vscode/adbreak_auto_pro

# 1. ground_truth.txt → JSON 변환
../.venv/bin/python eval/load_ground_truth.py ground_truth.txt \
  --xml-dir . --out eval/ground_truth.json

# 2. XML 마커에서 피처 행렬 추출
../.venv/bin/python eval/extract_features.py

# 3. 5-fold CV 평가 (결과: eval/output/train_report.json)
../.venv/bin/python eval/train_score.py
```

GT 매칭 기준: 마커 시각이 정답 타임코드와 **5초 이내**이면 hit(label=1)으로 처리합니다 (`LABEL_TOL = 5.0`).

---

## 주요 기술 스택

| 역할 | 기술 | 버전 |
|---|---|---|
| 음성 전사 | mlx-whisper (Apple Silicon) / faster-whisper (폴백) | 0.4.3 / 1.2.1 |
| 장면 전환 탐지 | PySceneDetect ContentDetector (threshold=27) | 0.7 |
| 장면 전환 검증 | open-clip ViT-B-32-quickgelu (SAME_THRESHOLD=0.80) | 3.3.0 |
| 한국어 텍스트 유사도 | jhgan/ko-sroberta-multitask | sentence-transformers 5.5.1 |
| 딥러닝 프레임워크 | PyTorch | 2.12.0 |
| 문장 분리 | 자체 한국어 종결어미 판별기 (topic_breaks.py) | — |
| 영상 처리 | FFmpeg / ffprobe | brew 최신 |
| 프레임 기준 | 30 fps | — |
| 웹 서버 | Python http.server | Python 3.11 |
