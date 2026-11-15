---
name: update-rules
description: >-
  Updates app/settings/rules from docs/rules_order.md, keeping classification
  code tables loadable. Creates docs/ and docs/rules_order.md when they are
  missing. Use when the user asks to update, sync, reorder, or rewrite the
  exam rules, 분류 지침, 룰, PS/A–F/R codes, or rules_order.
---

# 분류 지침 업데이트

`docs/rules_order.md`가 기준이다. 그 문서가 시킨 변경만 `app/settings/rules`에 반영한다.

## 순서

1. `docs` 폴더나 `docs/rules_order.md`가 없으면 먼저 만든다. 룰은 아직 고치지 않는다.
   - `docs`가 없으면 그 폴더를 만든다.
   - `docs/rules_order.md`가 없으면 파일을 만들고, `app/settings/rules`의 현재 표를 기준 순서로 옮겨 적는다. PS, 세부 코드, R을 파일에 있는 행 순서대로 둔다. 각 행은 코드, 이름, 조건 또는 장면이다.
   - 만든 뒤 경로를 알리고 멈춘다. 사용자가 그 문서를 고치기 전에는 `app/settings/rules`를 바꾸지 않는다.
2. 파일이 있으면 끝까지 읽는다. 비어 있거나 바꿀 코드·순서·정의가 없으면 멈추고 그렇게 말한다. 순서를 지어내지 않는다.
3. `app/settings/rules`의 파일을 모두 읽는다.
4. 기준 문서에 적힌 차이만 고친다. 언급되지 않은 문장, 예, 경계 설명은 둔다.
5. 아래 점검이 맞을 때까지 고친다.

## 파일

로더는 `app/settings/rules` 아래 `.md`와 `.txt`를 상대 경로 순으로 이어 붙인다. 파일 사이는 빈 줄 하나다. 순서는 파일명의 `01`–`05`가 만든다.

| 파일 | 내용 |
| --- | --- |
| `01-method.md` | 네 층, 고르는 순서, 같은 구조인지 가르는 기준, 경계 |
| `02-patterns.md` | 대표 구조 PS 표 |
| `03-actions.md` | 세부 코드 A–F. 글자마다 고르는 질문과 표 |
| `04-relations.md` | 의존 관계 R 표 |
| `05-examples.md` | 적용 예. 코드 목록이 아니다 |

기준 문서가 파일을 나누거나 순서를 바꾸라고 하지 않으면 이 다섯 파일을 유지한다. 파일을 추가하면 이어 붙일 위치가 되도록 `NN-이름.md` 접두를 붙인다.

## 표

조판은 표 행에서 코드와 그 다음 칸의 이름을 읽는다. 행은 `| PS01 | 이름 | ...` 형태여야 한다. 코드는 `PS`, `R`, `A`–`F` 뒤에 숫자다. 기존처럼 두 자리로 맞춘다 (`PS01`, `A01`, `R01`).

칸을 유지한다.

- PS: `코드 | 이름 | 대표로 고르는 조건`. 제목은 `## 대표 구조`
- 세부 코드: `코드 | 이름 | 대표 장면`. 묶음 제목과 고르는 질문은 그 표 위에 둔다
- R: `코드 | 이름 | 의미`. 제목은 `## 의존 관계`

한 코드는 표에 한 번만 둔다. 이름은 두 번째 칸만 바꾸고, 설명은 세 번째 칸에 둔다. 기준 문서의 행 순서가 표의 행 순서다.

## 같이 맞출 곳

코드 집합이나 층 개수가 바뀌면 `01-method.md`의 층 표에 적힌 범위도 맞춘다. 그때만 `app/settings/prompt.md`에서 같은 범위(대표 PS, 세부 코드, 의존 관계)를 고친다. 지시문의 다른 문장은 건드리지 않는다.

키, 모델 캐시, 조판 코드는 고치지 않는다.

## 점검

- 기준 문서의 코드가 지정한 파일의 표에 빠지거나 중복되지 않는다.
- 행 순서와 이름이 기준 문서와 같다.
- 빈 지침 파일이 없다.
- 기준 문서가 빼라고 한 코드만 삭제한다. 남은 코드의 설명은 그대로 둔다.
