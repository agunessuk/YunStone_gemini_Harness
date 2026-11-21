# MythicMobs 스킬에 동작 연결

ModelEngine 동작은 MythicMobs `state` 로 튼다: `state{s=<동작>;li=<들어갈 틱>;lo=<나올 틱>} @self`.
판정(피해)은 `delay` 로 동작의 판정 시각에 맞춘다: **지연 틱 = round(판정 시각 × 20)**. (make_motion 의 동작표.md 에 계산돼 있음)

## 1. 새 동작 하나를 쓰는 공격 스킬
```yaml
BT_Dxx_SkelWarrior_Combo:
  Cooldown: 6
  Conditions:
  - offgcd
  TargetConditions:
  - distance{d=<3.5}
  Skills:
  - gcd{ticks=34} @self                 # 동작 길이(1.6초=32틱)보다 조금 길게 → 다른 스킬과 안 겹침
  - setai{ai=false} @self               # 동작 중엔 걷지 않게
  - state{s=attack_combo;li=1;lo=2} @self
  - delay 9                             # 1타 0.45초
  - damage{a=5} @PIR{r=3}
  - delay 10                            # 2타 0.95초
  - damage{a=6} @PIR{r=3}
  - delay 8
  - setai{ai=true} @self
```
몹 쪽:
```yaml
  Skills:
  - skill{s=BT_Dxx_SkelWarrior_Combo} @target ~onTimer:40 0.5
  - state{s=hit_heavy;li=1;lo=2} @self ~onDamaged 0.25     # 맞을 때 25% 확률로 크게 젖혀짐
```
- 투사체는 판정 시각에 `projectile{…}`·`shoot{type=ARROW;…}` 를 쏜다.
- 몸이 실제로 움직여야 하는 동작(돌진·뒤로 뛰기)은 동작에 맞춰 `lunge{v=…;vy=…} @target` 을 같은 틱에 넣는다. (뒤로는 음수 v — 인게임 확인 필요)

## 2. 이미 있는 동작을 패턴에 붙이기
팩 동작은 판정 시각을 모르므로 `inspect_model.py` 의 **정점(가장 크게 움직이는 순간)** 을 쓴다.
- **동작 시작 틱 = 판정 틱 − round(정점 × 20)**
- 예: 예고 30틱 뒤 피해, `spin_poison` 정점 0.68초 → 30 − 14 = 16틱에 `state` 시작:
```yaml
  - particlering{...} @self      # 예고 시작
  - delay 16
  - state{s=spin_poison;li=1;lo=3} @self
  - delay 14
  - damage{a=6} @PIR{r=7}        # 판정 (30틱)
```
- `hold` 동작(마지막 자세 유지)은 끝낼 때 `state{s=down;remove=true} @self`.
- 보이지 않는 표식 몹이 피해를 주는 패턴은 표식이 아니라 **부르는 쪽(보스) 스킬**에 동작을 넣는다.

## 3. 건드리면 안 되는 설정
- 서버 자체 코드가 ID·스킬 구조를 점검하는 설정(예: `LM_` 앞머리 몹·스킬)은 직접 고치지 않는다.
  같은 이름의 **보강 초안 복사본**을 만들고, 바꾼 것(동작 이름 몇 줄)만 적고, 적용 뒤 서버 로그를 확인하게 한다.
- 원본 팩 스킬 파일은 고치지 않는다. 우리 팩에서 `Template:` 으로 감싸고 우리 스킬을 더한다.

## 4. 서버에 넣기 (사용자 지시 때만)
1. 서버 `plugins/ModelEngine/blueprints/` 의 같은 이름 원본 `.bbmodel` 을 백업 (SHA256 기록).
2. 복사본(`작업/모션/모델/<모델>.bbmodel`)으로 교체 → `/meg reload` (리소스팩 다시 배포) → `/mm reload`.
3. 기존 동작 이름은 그대로라 다른 몹·코드에는 영향 없음. 새 동작은 우리 스킬이 부를 때만 나온다.
4. 되돌리기: 백업으로 다시 교체 → 같은 명령.

## 5. 팩 몹을 감쌀 때 주의 (Template)
- **팩이 멈추고 되돌리는 방식을 먼저 본다.** 어떤 팩은 `setai` 대신 `setspeed` 와 자기 되돌리기 스킬(예: 달리기 재개)을 쓴다.
  그 되돌리기 스킬의 쿨타임이 매우 길면(예: 99999초) 실패해서 속도 0 에 남을 수 있다 → 우리 쪽 되돌리기 스킬을 따로 만든다.
- Template 으로 감싸면 **원본 몹의 Skills 타이머도 그대로 돈다**(목록이 합쳐짐). 원본 공격과 우리 공격이 같은 틱에 겹치지 않게
  타이머 간격을 엇갈리게(예: 원본 20틱, 우리 15틱) 하고, 모두 `gcd`·`offgcd` 로 묶는다. 실행 순서는 인게임 확인이 필요하다.
- **덮어쓰기 동작의 피격 반응**은 공격 중에 나오면 공격 동작을 가린다 → 피격 스킬에 `offgcd` 조건과 짧은 쿨타임을 둔다:
```yaml
BT_Dxx_Hammerhead_HitHeavy:
  Cooldown: 4
  Conditions:
  - offgcd
  Skills:
  - gcd{ticks=12} @self
  - state{s=hit_heavy;li=1;lo=2} @self
```
  몹 쪽: `- skill{s=BT_Dxx_Hammerhead_HitHeavy} @self ~onDamaged 0.3`
- **lunge 와 동작 속 몸 이동을 같이 쓸 때**: 동작이 몸을 크게 앞으로 옮기면 끝날 때 0 으로 돌아오며 발이 뒤로 미끄러져 보인다.
  실제 이동은 `lunge` 가 맡고, 동작 속 몸 위치 이동은 4 칸 안쪽으로 작게 둔다.
