---
name: new-project
description: 새 프로젝트를 시작하거나 기존 폴더에 하네스를 처음 적용할 때 사용한다. "새 프로젝트야", "하네스 프로젝트 틀 세팅해줘", "프로젝트 초기 세팅", "폴더 구조 잡아줘", "이 폴더에 하네스 적용". 표준 폴더(input·work·output·handoff·review·prompts·docs), GEMINI.md, PROGRESS.md, INDEX.md, 품질 기준, 피드백 기록, 보호 목록을 스크립트로 한 번에 만든다.
---

# 새 프로젝트 틀 세팅

프로젝트마다 구조가 달라지면 다음 세션·다른 AI가 매번 위치를 새로 찾는다. 그래서 틀은 손으로 만들지 않고 스크립트로 만든다.

## 순서
1. **대상 폴더를 본다.** 비어 있는지, 이미 파일이 있는지, `.gemini/protected.txt`·`PROGRESS.md`가 이미 있는지.
2. **미리보기를 돌린다.**
   `python "<이 스킬 폴더>/scripts/new_project.py" "<프로젝트 폴더>" --dry-run`
   (스킬 폴더: `~/.gemini/skills/new-project`. Windows에서는 `PYTHONIOENCODING=utf-8`을 준다)
3. **실제로 만든다.** 같은 명령에서 `--dry-run`을 뺀다. 이미 있는 파일은 덮어쓰지 않는다.
4. **원본을 정리한다.**
   - 사용자가 "원본 없어"라고 했으면 그대로 둔다.
   - 기존 파일이 있던 폴더면 스크립트가 보여 준 "표준 폴더 밖 항목"을 사용자에게 보여 주고, 원본이면 `input/`으로 옮길지 `protected.txt`에 경로를 적을지 묻는다. 묻기 전에 옮기지 않는다.
   - 프로젝트 특성상 보호할 것(예: `plugins/*.jar`, `원본.pdf`)이 보이면 `protected.txt`에 추가를 제안한다.
5. **목적을 채운다.** 사용자가 목적을 말했으면 `GEMINI.md`의 "프로젝트"와 `PROGRESS.md`의 "목표"에 그 말을 그대로 적는다. 안 했으면 비워 두고 첫 작업 때 채운다.
6. **보호가 켜졌는지 확인한다.** `input/README.md`를 replace 또는 write_file로 고쳐 보고 차단되는지 본다(바꾸려는 것이 아니라 시험이다).
7. **보고:** 만든 것, 보호 목록, 확인 결과, 비워 둔 칸.

## 표준 폴더
| 폴더 | 용도 | 보호 |
|---|---|---|
| `input/` | 받은 원본 | 보호 |
| `work/` | 작업본 | |
| `output/`, `output/확정/` | 결과물, 확정본 | 확정본 보호 |
| `handoff/` | 인계본 | `.zip` 보호 |
| `review/gpt/`, `review/gemini/` | 검수 의견, 분석 보고서 | |
| `prompts/gpt/` | 다른 AI용 프롬프트 | |
| `docs/` | `QUALITY_BAR.md`, `FEEDBACK_LOG.md` | |

다른 스킬(`handoff-pack`, `gpt-collab`, `feedback-round`, `resume-work`)은 이 폴더 이름을 쓴다.

## 하지 않는 것
- 표준 틀을 손으로 만들지 않는다. 스크립트가 안 되면 원인을 고치거나 알린다.
- 기존 프로젝트의 폴더 이름(예: `인계용/`)을 사용자 허락 없이 표준 이름으로 바꾸지 않는다.
- git 저장소는 사용자가 요청할 때만 만든다.
