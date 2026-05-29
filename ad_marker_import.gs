/**
 * Premiere Pro 마커 CSV → 스프레드시트 AD열 자동 입력
 *
 * 워크플로:
 *   1. Premiere에서 최종 마커 확정
 *   2. 마커 패널 → CSV 내보내기
 *   3. Google Drive에 CSV 업로드
 *   4. 스프레드시트 상단 "🎬 AD 마커" → "📥 CSV에서 가져오기" 클릭
 *   5. CSV 파일의 Drive URL 또는 파일 ID 붙여넣기 → 확인
 *      (여러 파일은 줄바꿈으로 구분)
 *
 * 타임코드 변환 규칙 (29.97fps NDF):
 *   :00 ~ :03 프레임 → 초 버림  (예: 00:09:09:03 → 00:09:09)
 *   :28 ~ :29 프레임 → 초 올림  (예: 00:09:09:28 → 00:09:10)
 *
 * 파일명 매칭:
 *   YBJ_S25_EP01_AdBreakCandidates.csv → YBJ_S25_EP01.mp4
 *   C열에서 해당 파일명 행을 찾아 F열(AD)에 입력
 */

// ── 설정 ──────────────────────────────────────────────────────────────
const SHEET_NAME   = '작업시트';
const COL_FILENAME = 3;    // C열: 파일명
const COL_AD       = 6;    // F열: AD 타임코드
const SEPARATOR    = ',';  // 여러 타임코드 구분자
// ─────────────────────────────────────────────────────────────────────


/** 스프레드시트 열릴 때 메뉴 추가 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('🎬 AD 마커')
    .addItem('📥 CSV에서 가져오기', 'importFromCsv')
    .addToUi();
}


/** 메인 함수: CSV 파일 URL/ID를 입력받아 처리 */
function importFromCsv() {
  const ui = SpreadsheetApp.getUi();

  const response = ui.prompt(
    '📥 AD 마커 CSV 가져오기',
    'Premiere 마커 CSV 파일의 Google Drive URL 또는 파일 ID를 붙여넣으세요.\n여러 파일은 줄바꿈으로 구분하세요.',
    ui.ButtonSet.OK_CANCEL
  );

  if (response.getSelectedButton() !== ui.Button.OK) return;

  const input = response.getResponseText().trim();
  if (!input) return;

  const lines       = input.split('\n').map(l => l.trim()).filter(Boolean);
  const successList = [];
  const errorList   = [];

  for (const line of lines) {
    try {
      const fileId  = extractFileId(line);
      const file    = DriveApp.getFileById(fileId);
      const content = file.getBlob().getDataAsString('UTF-8');
      const result  = processCsv(content, file.getName());
      successList.push(`✅ ${result.videoName} → ${result.timecodes.join(SEPARATOR)}`);
    } catch (e) {
      errorList.push(`❌ ${line}\n   ${e.message}`);
    }
  }

  const msg = [
    `처리 결과 (${successList.length}성공 / ${errorList.length}실패)`,
    '',
    ...successList,
    ...(errorList.length ? ['', '─ 오류 ─', ...errorList] : []),
  ].join('\n');

  ui.alert(msg);
}


/** Drive URL 또는 파일 ID에서 파일 ID 추출 */
function extractFileId(input) {
  // https://drive.google.com/file/d/FILE_ID/view 형태
  const match = input.match(/\/d\/([a-zA-Z0-9_-]+)/);
  if (match) return match[1];
  // 파일 ID 직접 입력
  if (/^[a-zA-Z0-9_-]{10,}$/.test(input)) return input;
  throw new Error('유효하지 않은 파일 URL 또는 ID입니다.');
}


/** CSV 파싱 → 타임코드 추출 → 시트 기록 */
function processCsv(csvContent, csvFilename) {
  // 파일명에서 영상명 추출
  // YBJ_S25_EP01_AdBreakCandidates.csv → YBJ_S25_EP01.mp4
  const videoName = csvFilename
    .replace(/_AdBreakCandidates\.csv$/i, '.mp4')
    .replace(/\.csv$/i, '.mp4');

  // CSV 파싱
  const rows = parseCsv(csvContent);
  if (rows.length < 2) throw new Error('마커 데이터가 없습니다.');

  // 헤더에서 "시작" 열 인덱스 찾기
  const header   = rows[0].map(h => h.trim());
  const startIdx = header.indexOf('시작');
  if (startIdx === -1) throw new Error('"시작" 열을 찾을 수 없습니다.');

  // 타임코드 추출 및 변환
  const timecodes = [];
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    if (!row[startIdx]) continue;
    const tc = row[startIdx].trim();
    // HH:MM:SS:FF 형식 확인
    if (!/^\d{2}:\d{2}:\d{2}:\d{2}$/.test(tc)) continue;
    timecodes.push(convertTimecode(tc));
  }

  if (timecodes.length === 0) throw new Error('유효한 타임코드를 찾지 못했습니다.');

  // 시트에서 파일명 매칭 후 AD열 기록
  const ss    = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) throw new Error(`시트 "${SHEET_NAME}"를 찾을 수 없습니다.`);

  const lastRow = sheet.getLastRow();
  const colData = sheet.getRange(1, COL_FILENAME, lastRow, 1).getValues();
  let rowIndex  = -1;

  for (let i = 0; i < colData.length; i++) {
    if (String(colData[i][0]).trim() === videoName) {
      rowIndex = i + 1;
      break;
    }
  }

  if (rowIndex === -1) {
    throw new Error(`"${videoName}"을 ${SHEET_NAME} C열에서 찾지 못했습니다.`);
  }

  sheet.getRange(rowIndex, COL_AD).setValue(timecodes.join(SEPARATOR));

  return { videoName, timecodes };
}


/**
 * 타임코드 변환: HH:MM:SS:FF → HH:MM:SS
 *   FF 00~03 : 버림 (초 그대로)
 *   FF 28~29 : 올림 (초 +1, 분·시 자리올림 처리)
 */
function convertTimecode(tc) {
  const [hhS, mmS, ssS, ffS] = tc.split(':');
  let hh = parseInt(hhS, 10);
  let mm = parseInt(mmS, 10);
  let ss = parseInt(ssS, 10);
  const ff = parseInt(ffS, 10);

  if (ff >= 28) {
    ss += 1;
    if (ss >= 60) { ss = 0; mm += 1; }
    if (mm >= 60) { mm = 0; hh += 1; }
  }
  // ff 00~03: 버림, 그대로 사용

  return `${pad2(hh)}:${pad2(mm)}:${pad2(ss)}`;
}


/** 숫자를 2자리 문자열로 변환 */
function pad2(n) {
  return String(n).padStart(2, '0');
}


/**
 * CSV 문자열 파싱 → 2차원 배열 반환
 * 쉼표와 큰따옴표 이스케이프 처리 포함
 */
function parseCsv(text) {
  const rows = [];
  const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  for (const line of lines) {
    if (!line.trim()) continue;
    const cols = [];
    let cur = '', inQ = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
        else inQ = !inQ;
      } else if (ch === ',' && !inQ) {
        cols.push(cur); cur = '';
      } else {
        cur += ch;
      }
    }
    cols.push(cur);
    rows.push(cols);
  }
  return rows;
}
