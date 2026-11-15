# ExamPatternLab

시험 PDF를 읽어 문항을 분류하고, 문항 조각과 분류 코드가 붙은 주석 PDF를 만드는 Windows 데스크톱 앱이다.

색인은 Gemini `generateContent`로 한다. 조판은 원본 PDF에서 문항을 잘라 HTML로 만든 뒤, 헤드리스 Edge 또는 Chrome으로 PDF를 찍는다. 결과 파일은 바로 디스크에 쓰지 않고 메모리에 두었다가, 결과에서 고른 뒤 다운로드한다.

## 실행

루트의 `ExamPatternLab.exe`가 PATH의 `pythonw.exe`(없으면 `pyw.exe`)로 `app/window.py`를 연다. 작업 디렉터리는 exe가 있는 폴더다. Python 코드를 고친 뒤에는 exe를 다시 만들 필요가 없다.

PySide6나 PyMuPDF가 없으면 창을 열기 전에 `app/requirements.txt`를 설치한다. 설치 과정은 콘솔 창에 보인다. 일반 설치가 막히면 사용자 영역에 다시 설치한다.

직접 띄울 때:

```text
python app/window.py
```

필요 환경:

- Windows
- PATH에 있는 Python (`pythonw`). 이 저장소는 Python 3.10에서 사용한다.
- Microsoft Edge 또는 Google Chrome. 주석 PDF를 만들 때 쓴다.
- `app/requirements.txt`: PyMuPDF 1.27.1, PySide6-Essentials 6.12.0. HTTP는 표준 라이브러리만 쓴다.

`app/launcher.cs`가 exe의 소스다. 창을 못 찾거나 `pythonw`가 없으면 메시지 상자만 띄우고 끝난다.

## 사용 방법

### 처음 한 번

1. `app/settings/.env.example`을 `app/settings/.env`로 복사한다. 이미 파일이 있으면 그 파일을 고친다.
2. `CURRENT_KEY=GEMINI`으로 두고 `GEMINI_API_KEY`에 키를 넣는다. 색인 실행은 지금 Gemini만 된다.
3. `ExamPatternLab.exe`를 연다. 그 컴퓨터에 패키지가 없으면 설치가 끝난 뒤에 창이 열린다. 위쪽에 모델 이름이 보이면 그 모델로 실행한다. `모델을 고르세요`이면 모델을 먼저 고른다.

온도, 출력 토큰, 대기 시간은 `app/settings/model.env`에서 바꾼다. 분류 기준은 `app/settings/rules/`, 색인에 시킬 말은 `app/settings/prompt.md`다. 앱에는 편집 화면이 없고, 다음 실행부터 파일 내용이 반영된다.

### 모델 고르기

1. `모델`을 누른다. 로그에 `사용 가능한 모델을 조회합니다.`가 남고, 현재 키로 쓸 수 있는 모델이 뜬다.
2. 목록에서 고른 뒤 `선택`을 누르거나 항목을 두 번 누른다. 위쪽 모델 이름이 바뀌고 `app/cache/model.json`에 저장된다.
3. `취소`하면 기존 모델이 유지된다. 실행 중에 바꾸면 이번 작업은 그대로 두고, 다음 실행부터 새 모델이 쓰인다.

### PDF 넣고 실행

1. `PDF 추가`로 고르거나, PDF 파일을 창 위에 끌어다 놓는다. 여러 개면 위에서부터 순서대로 처리한다. 같은 파일은 다시 넣지 않는다.
2. 빼려면 입력에서 고르고 `빼기`를 누른다.
3. `실행`을 누른다. 입력이 비어 있으면 `넣을 PDF가 없습니다.`만 로그에 남는다.
4. 실행 중에는 스피너가 돌고, PDF 추가와 빼기, 실행, 드롭이 막힌다. 로그는 PDF마다 이렇게 진행된다.
   - `[n/N] 전송`: Gemini에 PDF를 보내는 중
   - `[n/N] 조판`: 문항을 잘라 주석 PDF를 만드는 중
   - `[n/N] 보관`: 결과 칸에 올라옴. 아직 디스크에 저장된 것은 아니다
   - `[n/N] 실패`: 그 PDF만 건너뛰고 다음 PDF를 계속한다
5. `응답이 출력 한도에서 끊겼습니다`가 보이면 그 주석 PDF는 문항이 빠졌을 수 있다. `model.env`의 `MAX_OUTPUT_TOKENS`를 올린 뒤 그 PDF만 다시 실행한다.

키 오류, 모델 없음, Gemini가 아닌 키는 실행이 시작되지 않고 로그의 `실패:`에 이유가 나온다.

### 결과 저장

1. 왼쪽 아래 결과에서 저장할 파일을 고른다. `다운로드`는 그때 누를 수 있다.
2. 저장 위치를 고른다. 제안 이름은 `원본이름_annotated.pdf`이고, 처음 폴더는 사용자 홈이다.
3. 로그에 `다운로드:`와 저장 경로가 남는다.

결과는 앱을 켜 둔 동안에만 있다. 받지 않은 PDF가 있거나 호출이 진행 중일 때 창을 닫으면 확인이 뜬다. `계속`은 창을 유지하고, `닫기`는 진행을 끊고 아직 저장하지 않은 PDF를 버린다.

## 화면

창 제목은 `ExamPatternLab`이다. 처음 크기는 최소 크기인 560×360이다.

위쪽 막대는 PDF 추가, 빼기, 모델, 현재 모델 이름, 그리고 오른쪽에 다운로드와 실행이다. 실행 중에만 그 사이에 스피너가 돈다. 실행은 누를 수 있을 때 호박색이고, 비활성 버튼은 테두리 없이 회색 글자다.

본문은 세 칸이다.

| 칸 | 위치 | 내용 |
| --- | --- | --- |
| 입력 | 왼쪽 위 | 처리할 PDF |
| 결과 | 왼쪽 아래 | 만들어진 `원본이름_annotated.pdf` |
| 로그 | 오른쪽, 입력과 결과를 세로로 덮음 | 전송, 조판, 보관, 실패 |

PDF는 파일 대화상자나 창 위로 끌어다 놓아 넣는다. `.pdf`만 받고, 같은 경로는 한 번만 넣는다. 이미 있으면 로그에 남긴다. 실행 중에는 추가, 빼기, 실행, 드롭을 받지 않는다. 모델 선택과 다운로드는 실행 중에도 누를 수 있다.

다운로드는 결과에서 고른 항목만 저장한다. 기본 위치는 사용자 홈이고, 확장자가 없으면 `.pdf`를 붙인다.

## 처리

PDF 하나마다 전송과 조판 두 단계다. 로그는 `[n/N] 전송`, `조판`, `보관`, `실패`로 남는다.

1. `app/settings`를 읽는다. 키, 모델, 지시문, 분류 지침 중 하나라도 없으면 작업 스레드를 시작하지 않고 로그에 `실패:`를 남긴다.
2. `CURRENT_KEY`가 `GEMINI`이 아니면 색인을 돌리지 않는다. Claude와 GPT 키는 모델 목록만 조회할 수 있다.
3. PDF를 Base64로 붙여 `generativelanguage.googleapis.com/v1beta/models/{모델}:generateContent`에 보낸다. 지시문은 `prompt.md`의 `{pdf_name}`, `{rules}`를 채운 문자열이다.
4. 색인 마크다운에서 문항을 읽고, PyMuPDF로 해당 문항을 잘라낸다. 위치 잡기가 남으면 같은 모델에 페이지 이미지를 보내 상자를 받는다.
5. 코드 이름이 붙은 HTML을 만들고, Edge 또는 Chrome `--headless=new --print-to-pdf`로 PDF 바이트를 만든다.
6. 결과 목록에 `{원본 stem}_annotated.pdf`로 올린다.

키 문자열이 로그나 예외 문구에 들어가지 않게 `AIza`, `AQ.`, `sk-ant-`, `sk-` 형태는 가린다. HTTP 401/403이나 키 거부 응답은 `app/settings/.env`의 키를 확인하라는 한 줄로 바꾼다. 출력 한도에서 끊기면 `MAX_TOKENS`를 로그에 남기고, 그 PDF는 불완전할 수 있다.

임시 HTML과 크롭 이미지는 조판이 끝나면 지운다.

## 설정

앱 안의 편집기는 없다. 파일만 고친다.

| 파일 | 역할 |
| --- | --- |
| `app/settings/.env` | `CURRENT_KEY`와 `GEMINI_API_KEY`, `CLAUDE_API_KEY`, `GPT_API_KEY`. 실제 키는 이 파일에만 둔다. |
| `app/settings/.env.example` | 키 파일의 빈 틀 |
| `app/settings/model.env` | `TEMPERATURE`, `MAX_OUTPUT_TOKENS`, `TIMEOUT_SECONDS`, `VISION_TIMEOUT_SECONDS`. 모델 이름은 없다. |
| `app/settings/prompt.md` | 색인 지시문. `{pdf_name}`과 `{rules}`가 둘 다 있어야 한다. |
| `app/settings/rules/*.md` | 분류 지침. 상대 경로 순으로 이어 붙이고, 파일 사이에 빈 줄을 넣는다. |
| `app/cache/model.json` | 공급자별 선택 모델. 예: `{"GEMINI": "gemini-3.5-flash-lite"}`. 키는 넣지 않는다. |

`CURRENT_KEY`는 `GEMINI`, `CLAUDE`, `GPT` 중 하나다. 모델 버튼을 누르면 그 키로 호출 가능한 모델을 조회하고, 고른 이름을 `model.json`에 공급자 키로 저장한다.

- Gemini: `GET /v1beta/models`. `generateContent`를 지원하고 이름이 `models/{id}`인 것만.
- Claude: `GET https://api.anthropic.com/v1/models`
- OpenAI: `GET https://api.openai.com/v1/models`. embedding, whisper, tts 같은 비대화 모델은 뺀다.

선택한 모델이 없으면 실행은 `실패:`로 끝난다.

## 분류 코드

지침은 풀이 구조만 다룬다. 단원 이름이나 공식 이름이 아니다. 한 문항의 층은 네 개다.

| 층 | 코드 | 개수 |
| --- | --- | --- |
| 대표 구조 | PS01–PS16 | 1 |
| 보조 구조 | PS01–PS16 | 대표와 다른 것만, 없을 수 있음 |
| 세부 코드 | A01–F12 | 풀이에서 실제로 한 동작만 |
| 의존 관계 | R01–R05 | 1 |

지침 파일은 `01-method.md`, `02-patterns.md`, `03-actions.md`, `04-relations.md`, `05-examples.md`다. 표의 코드와 이름은 조판 태그로 쓴다.

## 모듈

| 파일 | 역할 |
| --- | --- |
| `app/window.py` | 창, 드롭, 모델 선택, 작업 스레드, 다운로드 |
| `app/job.py` | PDF 목록을 순서대로 색인하고 주석 PDF 바이트를 넘긴다 |
| `app/gemini.py` | Gemini 호출과 비밀값 가리기 |
| `app/models.py` | 현재 키로 모델 목록 조회 |
| `app/sheet.py` | 색인 파싱, 크롭, HTML, 헤드리스 PDF |
| `app/settings/` | 키, 호출 한도, 지시문, 지침 로딩 |
| `app/errors.py` | `AppError` |
| `app/launcher.cs` | exe 런처 |

작업은 GUI 스레드 밖에서 돈다. 설정 오류는 스레드를 만들기 전에 GUI에서 로그로 남긴다. 스레드 안의 예외도 `실패:`로 올려 로그가 비지 않게 한다.
