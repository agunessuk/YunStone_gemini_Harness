# 출처와 변경 기록

## 참고한 저장소
| 저장소 | 라이선스 | 가져온 것 |
|---|---|---|
| FOD-team-org/FOD-skills | MIT (Copyright (c) 2026 FOD-team-org) | 아래 스킬의 구조와 일부 규칙을 이 사용자에 맞게 줄이고 고쳐 씀 |
| kyu1204/oh-my-harness | MIT | "글로 쓴 규칙은 부탁, 훅은 강제"라는 개념, 차단 기록(events.jsonl), 하네스 자기 보호 아이디어. 코드는 가져오지 않음 |
| FOD-team-org/codex-task-playbook | — | 첫 작업 지시 프롬프트 틀("이전 대화를 모른다") 참고 |
| FOD-team-org/fod-harness | — | 가져오지 않음 (Pi 기반 전용 터미널은 불필요) |

## 스킬별 출처
| 이 저장소 스킬 | 바탕 | 바꾼 점 |
|---|---|---|
| resume-work | 새로 작성 | 기록에서 "이어서 진행해"가 반복된 것을 근거로 함 |
| feedback-round | 새로 작성 | 이펙트·레이아웃 피드백 반복을 근거로, 반복 지적을 품질 기준으로 굳히는 단계 추가 |
| handoff-pack | FOD `cross-device-handoff`, `delegation-loop` | AI 간 인계, 첫 작업 프롬프트, 변경 이력 빼기, 압축 해제 실행 확인 |
| gpt-collab | FOD `multi-ai-review`, `codex-delegation` | 그림은 GPT, 구현은 Claude라는 분담, 채택 필터 축소 |
| print-doc-qa | FOD `visual-output-qa` | 영상 항목 제거, 시험지·교재 레이아웃 항목으로 교체 |
| status-report | FOD `evidence-status-report` | 사용자 관점 표현, 내부 코드명 풀어쓰기 |
| project-index | 새로 작성 | 파일 ID 색인 요청을 근거로 함 |
| env-and-cleanup | FOD `windows-env-pitfalls`, `macos-env-pitfalls`, `agent-resource-hygiene`, `safe-cleanup` | 해당 항목만 남겨 하나로 합침 |
| blender-motion | 새로 작성 | 베어타이쿤 던전팩 작업에서 검증한 과정을 묶음: 기존 동작을 Blender 로 렌더해 Blockbench 부호 규칙 확인, 해골 3종·수호목 골렘 새 동작 제작, 팩 동작을 판정 틱에 맞춰 연결 |

## 변경 기록
- 2026-11-13 v1: 훅 2개(guard, session_restore), 스킬 8개, 전역 CLAUDE.md, 설치기
- 2026-11-14 v1.1: 보호 경로 검사를 프로젝트 맨 위 경로로 좁힘(하위 폴더의 같은 이름은 통과), 임시 폴더 안만 지우는 rm -r·Remove-Item -Recurse는 확인 없이 통과, 시험 41개
- 2026-11-14 v1.2: 자체 평가에서 나온 문제 수정. 대소문자 다른 경로 우회 차단, `python -c`·`node -e`·.NET·`find -delete`·압축 풀기 쓰기 감지, 셸로 settings.json·훅·보호 목록 수정 시 확인, 글롭 보호(`plugins/*.jar`) 오탐 축소, 시험이 실제 기록 파일을 더럽히지 않게 함, `install.py --check` 점검 추가, `new-project` 스킬과 표준 폴더(templates/project → skills/new-project/template), 피드백 기록(FEEDBACK_LOG.md), 스킬 폴더 이름 통일. 시험 65개 + 틀 시험
- 2026-11-14 v1.3: 보호 경로 오탐 축소. cat·tee 히어독 본문(파일 내용)과 코드 속 낱말('input', <input>)은 보호 폴더로 보지 않음. 쓰는 대상이 보호 폴더이거나 폴더 이름이 셸 낱말로 쓰이면 그대로 확인. 시험 69개
- 2026-11-14 v1.4: 사용자 승인(`.claude/approvals.json`). 확인 요청에 걸린 셸 명령을 사용자가 승인하면 같은 문자열일 때만 통과(1회·항상, 만료). AI는 승인 기록을 직접 못 씀, 하네스 설정 변경은 승인 대상 아님, 기록이 깨지면 원래대로 확인. 폴더째 지우기에 shutil.rmtree·fs.rmSync(recursive)·rimraf 추가. 시험 86개
- 2026-11-14 v1.5: `blender-motion` 스킬 추가. 스크립트 5개(bbpose 자세 계산, inspect_model 모델·기본 자세·정점 점검, make_motion 생성·수치 검사·렌더, pose_check 바닥 뚫림·몸 이동·발 끌림·끊김, blender_preview 정면·옆·위 렌더), 규칙 문서 3개(좌표·부호·덮어쓰기, 동작 설계 사람형·네발, MythicMobs 연결·Template 주의), 설계 예시. 원본 .bbmodel 보호(복사본에만 쓰기, 기존 동작 이름 겹치면 거부). 스킬만 보고 새 모델(엔더 망치머리) 동작을 만드는 시험을 한 번 돌려 나온 문제를 반영. 시험 25개(`tests/test_blender_motion.py`)
- 2026-11-14 v1.5.1: `blender-motion` 문서 정정. 해골 계열은 공격 동작이 덮어쓰기지만 기본 자세가 0 이라 REST_FROM 불필요(이전 문서의 '더하기 방식'은 틀림), 비틀기는 상체로(몸 전체를 돌리면 디딘 발이 미끄러짐). 수치 검사로 해골 3종 동작 6개를 고치며 확인
- 2026-11-14 v1.6: `mob-modeling` 스킬 추가. 조사(오픈소스 도구·제작 기준·ModelEngine 제한·자동화와 AI·보유 모델 67종 수치) 결론: 기존 모델과 새 팩이 따로 노는 원인은 해상도가 아니라 명암·테두리·큐브 밀도, 소수 모델의 밀도 이탈. 스크립트 9개(lint 엔진 규칙·아트 수치 검사, metrics, lineup 같은 축척·마인크래프트 엔티티 조명 라인업, density_fix, reshade 원래 색 유지 다시 칠하기, kitbash 부품 덧붙이기·칠하기, make_detail 설계 파일 실행기, bbtex, skilldeps), 문서 4개, 아트 기준 기본값. 실제 모델로 시험: 사당의 망령 큐브 8→39 보강(2차), 시체 먹는 개미 밀도 56.5→16 px/블록, 잭 오 랜턴 2배 격자+명암. 색 계단 방식이 상징색(호박 주황)을 바꾸는 문제를 찾아 원래 색 유지(adjust)를 기본으로. 스킬만 보고 어둠 망령을 보강하는 새 에이전트 시험(큐브 11→55)에서 나온 17가지를 반영: 어두운 테마 판정(`--theme`/`THEME`), 같은 평면 겹침 검사(템플릿 자체 34쌍 → 0), 뒤집힌 큐브(Blockbench 처럼 안쪽 면으로 렌더·큐브 수에서 뺌), 부품을 원본 텍스처 빈 곳에 먼저, 원본 밀도에 맞춘 부품 밀도, 칠하기 무늬 시드 고정(실행마다 달랐음), 회차 보관(렌더한 판만 v<N>), 앞·뒤 라인업, 동작 자세 시트(다섯 순간 × 3/4·옆), 모델 ID 공용 지정·이름표, `kit.cubes()`·`kit.colors()`(원본 색, L·V), 재질 L↔V 환산표, 단색 몹의 던전 테마 포인트 색 방침. `blender-motion` 도 고침: bbpose 크기(scale) 키프레임, blender_preview 예열 렌더·기본 자세 기준 바닥·임시 폴더 정리. 시험 10개(`tests/test_mob_modeling.py`) + 크기 키 시험(`tests/test_blender_motion.py`). 이어서 다른 작업자도 쓰도록 **기준을 2층 구조로**: 1층 공통 규칙(엔진·텍셀 밀도 하나·믹셀·겹침·소수 크기·색 위생·명도 하한·렌더 뼈 예산, 스킬 기본값 + 프로젝트 `common` 조정·`exceptions` 승인) + 2층 스타일 프로필(`make_profile.py` 로 기준 몹에서 범위를 재서 여러 개, `assign` 으로 몹마다 프로필·역할·테마). 해골 수치는 기본값에서 빼고 예시로. 경로·글꼴·템플릿 범용화(`paths.work`·`paths.models`, `MOB_MODELING_STD`, 리눅스 CJK 글꼴), 발광 텍스처 판별 버그(`rstrip('.png')`) 수정. 실제 프로젝트(베어타이쿤)에 적용하며 보정: 몹 47종 전체 검사로 기본값 조정(렌더 뼈 예산 24/32/96·순흑 5%·원래 모델 겹침 16px² 이상만), 발광 짝 텍스처(X_e, OptiFine 식)를 밀도 맞추기·부품·라인업이 함께 처리, `--pow2` 캔버스 늘리기, 이름표 잘림. 새 에이전트 2명이 스킬만 보고 보강(모래 해골 8→44, 엔더 찢개 12→42)해 낸 지적 35가지 반영: 부품 텍스처를 저장 때 한꺼번에 원본의 빈 영역들에 1px 간격으로, 뼈 격자(.5) 좌표 허용, 프로필 칠하기 묶음(`paint`)·`kit.defaults`·`hue_shift`·`palette`, glow 부품의 발광 층 칠하기, `--no-render` 는 초안 파일에만, 어두운 프로필 조명 50% 줄, 확대 그림, 크기 키 순간 자세, 대기 동작 첫 자세로 라인업, 실효 목표 출력, 정확한 다각형 겹침, reshade `--sat`·adjust 에도 `--tint`, 색 예산, cloth `bands`, 금속 작은 면, 빈 뼈 알림, 텍스처별 UV 크기 지키기. 시험 13개
- 2026-11-14 v1.7: 확인 요청 헛경보 줄이기(권한 검토). 실제 두 세션(던전팩·워크벤치)에서 훅이 띄운 확인 57건 중 실제로 물어볼 만한 것은 2건뿐이었음. 원인: 글 속 낱말('원본 채도', 커밋 메시지의 input/)을 경로로 봄, 쓰기와 언급을 구별하지 않음, 다른 폴더 작업에도 이 프로젝트 보호 목록 적용, 변수로 적은 임시 폴더 삭제·문자열 속 shutil.rmtree 도 확인. 셸 검사를 '쓰는 대상' 기준으로 새로 씀: 명령을 낱말로 나눠 대상(리다이렉트, cp·mv 목적지, rm, sed -i, Set-Content, unzip·tar·7z·Expand-Archive 위치, curl·wget, find -delete, xargs, git rm·mv·clean)을 찾고 cd·변수(S="...")·bash -c·powershell -Command 를 반영. 코드(python -c·node -e·히어독)는 파일을 바꾸는 호출이 있는 줄의 경로만, 문자열·주석은 뺌. 하네스 자기 보호도 쓰는 대상일 때만(임시 폴더의 시험 프로젝트는 제외). 폴더째 지우기는 절대 경로로 적은 임시 폴더 안(프로젝트 밖)만 통과, 대상을 모르면 확인. git 위험 명령은 따옴표·히어독 글을 빼고 검사. 분석 중 오류가 나면 예전 방식으로 판단. 훅 기록에 명령 앞 300자 저장. 검증: 두 세션의 셸 명령 전체 1,155개를 다시 넣어 확인 5건(전부 실제로 물어볼 만한 것: 보호 폴더에 쓰기, 프로젝트 보호 목록 쓰기, 프로젝트 폴더 통째 삭제, 대상 모를 삭제), 판정 최대 0.03초. 시험 87 → 112개
- 2026-11-21 gemini-v1: YunStone Claude Harness(v1.7)를 Gemini CLI 공식 훅(`BeforeTool`, `SessionStart`), `write_file`/`replace`/`run_shell_command` 도구 스펙, `GEMINI.md`, `~/.gemini/` 구조에 맞게 포팅. Mac/POSIX 절대 경로(`resolve`, `is_temp_inside`, `is_fragment`, 대소문자 `approvals.json`) 수정 및 112개 훅 시험·8개 프로젝트 틀 시험 전부 통과
