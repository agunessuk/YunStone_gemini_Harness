---
name: blender-motion
description: 마인크래프트 ModelEngine 몹·보스(Blockbench .bbmodel)에 새 3D 모션(공격, 시전, 소환, 돌진, 피격, 페이즈 연출)을 Blender로 만들어 넣거나, 팩에 이미 있는 동작을 MythicMobs 스킬의 판정 타이밍에 맞춰 연결할 때 사용한다. "모션 만들어", "공격 동작 추가", "애니메이션 만들어줘", "블렌더로 움직임", "몹 동작이 부족해", "보스가 가만히 서서 공격해", "판정 타이밍 맞춰", "bbmodel 애니메이션", "state 연결". 모션·동작·애니메이션이라는 말이 없어도 몹이 단조롭게 움직인다는 지적이면 쓴다. 원본 모델은 고치지 않고 복사본에 동작을 더하며, 수치 검사와 Blender 렌더 GIF로 확인한 뒤 MythicMobs 판정 틱까지 맞춘다.
---

# Blender로 몹 모션 만들기 (ModelEngine)

흐름: **키프레임 설계 파일 → 복사본 .bbmodel 에 쓰기 → 수치 검사 + Blender 렌더로 확인 → MythicMobs 판정 틱 연결**.
Blender 를 손으로 만지지 않고 스크립트로 돌리므로, 같은 설계에서 언제나 같은 결과가 나오고 고친 곳만 다시 만들 수 있다.

## 준비
- 스크립트는 이 스킬 폴더의 `scripts/` 에 있다: `inspect_model.py`(모델 읽기), `make_motion.py`(생성·검사·렌더),
  `pose_check.py`(수치 검사), `blender_preview.py`(Blender 안에서 렌더), `bbpose.py`(자세 계산).
  Python 3 + numpy + Pillow 가 필요하다. 실행은 `python -I <스킬 폴더>/scripts/…`, 현재 폴더는 프로젝트 루트.
  프로젝트 안에 같은 과정의 예전 도구 사본이 있어도 이 스킬의 스크립트를 쓴다(기능·안전장치가 더 많다).
- Blender 는 `make_motion.py` 가 찾는다: `--blender` → 환경변수 `BLENDER_EXE` → 프로젝트 안 `작업/외부도구/blender-*/` → 표준 설치 위치 → PATH.
  없으면 **사용자 허락을 받고** blender.org 공식 LTS 휴대용 zip 을 받아, 공식 `.sha256` 과 대조한 뒤 프로젝트 `작업/외부도구/` 에 푼다.

## 순서
1. **모델 읽기** — `inspect_model.py <원본.bbmodel>`: 뼈대 트리, 기존 동작 표(길이·분류·덮어쓰기·정점), 기본 자세.
   - 공격 계열이 패턴 수보다 적으면 새로 만든다. 맞는 동작이 이미 있으면 5단계(연결)만 한다.
   - **덮어쓰기 동작이 있고 기본 자세 값이 보이면**, 새 동작도 그 방식으로 만든다(`REST_FROM`·`override`, `references/conventions.md` 8장).
   - 기준 값이 필요하면 `--keys <팩 공격 동작>` 으로 키프레임 값을 본다.
2. **방향 검증** (모델 계열마다 처음 한 번) — `make_motion.py --existing <원본> <팩 공격 동작> --side --out 작업/모션`.
   장면 PNG 를 `read_file` 로 열어, 기존 동작이 의도대로(앞으로 휘두름, 옆 렌더에서 오른쪽이 앞) 보이는지 본다.
   이게 맞아야 새로 쓰는 값의 방향을 믿을 수 있다. 거꾸로면 `references/conventions.md` 6장.
3. **설계** — `templates/motion_spec_example.py` 를 `작업/모션/동작설계/<모델>.py` 로 복사해 채운다.
   - 박자 먼저: 준비 → 타격(판정 시각) → 따라가기 → 복귀. 바닥 예고가 있으면 예고 길이 = 판정 시각.
   - 수치 검사는 팩 원래 공격 동작을 기준으로 판단한다. 기준을 정하려면 `COMPARE` 에 그 이름을 적는다(없으면 자동으로 고름).
   - 요령과 기준 값(사람형·네발): `references/motion-design.md`
4. **생성·검사·렌더** — `make_motion.py <설계.py> --side` (좌우로 휘두르는 동작은 `--top` 도):
   `작업/모션/모델/<모델>.bbmodel`(원본+새 동작), `작업/모션/미리보기/<모델>/` 에 GIF·장면 PNG·`동작표.md`.
   - 먼저 출력의 **수치 검사**(바닥 뚫림·발 미끄러짐·시작·끝 끊김)를 본다. "주의"가 있으면 렌더보다 먼저 고친다.
   - 장면 PNG 를 `read_file` 로 열어 `motion-design.md` 5장 확인표대로 본다. 판정 프레임은 빨간 테두리와 정확한 시각이 찍힌다.
   - 고칠 때는 설계만 고쳐 다시 돌린다. `--no-render` 로 수치만 빠르게, `--only <동작>` 으로 그 동작만 렌더.
5. **MythicMobs 연결** — `references/mythicmobs-wiring.md` 의 틀대로.
   새 동작: `state{s=<동작>;li=1;lo=2} @self` → `delay <동작표의 판정 틱>` → 피해.
   기존 동작: 정점에 맞춰 시작 틱 = 판정 틱 − round(정점×20).
   팩 몹을 감쌌으면 같은 문서 5장(원본 타이머·되돌리기 스킬·피격 반응의 `offgcd`)을 꼭 본다.
6. **검사·보고** — 프로젝트 검사 도구가 있으면 돌린다. GIF·장면 PNG 를 사용자에게 보낸다(폰에서 볼 수 있게).
   보고에는 "수치·렌더로 확인함"과 "인게임 미확인"을 나눠 적는다.
7. **서버 적용**은 사용자 지시 때만: 원본 blueprint 백업 → 복사본으로 교체 → `/meg reload` → `/mm reload`.

## 지킬 것
- **원본 .bbmodel 은 고치지 않는다.** `make_motion` 은 복사본에만 쓰고, 원본에 있는 동작 이름과 겹치면 멈춘다
  (`--overwrite-existing` 은 사용자 허락 뒤에만).
- 기존 동작 이름·뼈 이름·몹 ID 를 바꾸지 않는다. 서버 코드나 다른 설정이 그 이름을 부를 수 있다.
  코드가 점검하는 설정은 직접 고치지 말고 보강 초안 복사본으로 만든다.
- ModelEngine 은 큐브가 뼈에 통째로 붙어 움직인다(회전·이동·크기만). 메시가 휘는 동작은 만들 수 없다.
- 수치 검사와 렌더는 인게임 확인이 아니다. 손맛(속도감·판정)은 시험 서버에서 봐야 한다.

## 참고 파일
| 파일 | 언제 |
|---|---|
| `references/conventions.md` | 좌표·부호·보간·파일 형식, 기본 자세와 덮어쓰기, 렌더가 이상할 때 |
| `references/motion-design.md` | 동작 설계 (박자, 무게감, 사람형·네발 기준 값, 확인표, 수치 기준) |
| `references/mythicmobs-wiring.md` | 스킬에 연결, 팩 몹 감쌀 때 주의, 서버에 넣을 때 |
| `templates/motion_spec_example.py` | 설계 파일 형식 (`REST_FROM`·`COMPARE`·`override` 포함) |
