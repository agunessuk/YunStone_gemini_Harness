"""디테일 보강 설계 예시 — 사당의 망령 (LostAssets Wraith, 큐브 8 → 39). make_detail.py 가 읽는다.
(베어타이쿤 던전팩 프로젝트의 실제 예. 경로·모델 ID·프로필 이름은 자기 프로젝트 것으로 바꾼다)

복사 위치: <작업폴더>/설계/<모델ID>.py   실행: python -I <스킬>/scripts/make_detail.py <작업폴더>/설계/<모델ID>.py

설계 전에 할 것 (references/detail-design.md)
1. inspect: blender-motion 의 inspect_model.py 로 뼈 트리·큐브·동작 범위를 본다. 뼈가 서로 이어져 있는지(부모·자식)가 중요하다.
   이 모델은 머리 h_h, 몸 b, 팔 l_a·r_a, 다리 l_l·r_l 이 모두 루트 아래 형제 → 몸에 긴 천을 붙이면 다리가 뚫는다.
2. 동작 범위: 공격·등장에서 다리 x −61°~+27°, 머리 x −38°, 팔 x −71° → 치마는 반쪽씩 다리 뼈에, 등 망토는 허리까지.
3. 겹침: 앞 머리카락 판이 z=−3 에 있다 → 같은 자리에 면을 두면 깜박인다(z-fighting). 몸 앞쪽에는 부품을 두지 않는다.
   print(kit.cubes("h_h")) 로 그 뼈의 기존 큐브 좌표를 보고, --no-render 출력의 "[새 겹침]"(부품이 새로 만든 겹침)이 0 인지 본다.
   (원래 모델에 있던 겹침은 부품으로 없앨 수 없다 — [공통] 경고로 남는다)
4. 색: print(kit.colors()) 로 원본 주요 색 (hex, 비율, OKLab L, 명도 V) 을 보고 재질 기준색을 거기서 고른다.
   재질 center 는 OKLab L (회색이면 명도 V 0.30 ≈ L 0.42, V 0.40 ≈ L 0.51, V 0.50 ≈ L 0.60, V 0.65 ≈ L 0.73).
반복: 먼저 --no-render 로 수치·겹침만 (1초), 괜찮으면 렌더. 결과는 미리보기/<이름>/ 에 v1, v2 … 로 남는다.
좌표: Blockbench 단위(1 = 1/16 블록), 앞 = −Z, 위 = +Y. 모두 정수. 회전은 한 축 0·±22.5·±45 만.
"""

SRC = "작업/풀어본_자료/몬스터/LostAssets_MobsPack_08_DawnRiver/ModelEngine/blueprints/lostasset_mob8_Wraith.bbmodel"
# OUT = "작업/모델링/모델/lostasset_mob8_Wraith.bbmodel"     # 없으면 이 경로
RESHADE = dict(val=0.62, contrast=0.17)        # 원래 큐브 텍스처를 먼저 다시 칠함 (reshade.py 인자: mode="ramp", protect=["#ff2020"] 등)
SCALE = 1.0                                    # MythicMobs model{scale=}
PROFILE = "fantasy"                            # 2층 스타일 프로필 (없으면 기준 파일 assign → default_profile)
ROLE = "일반"                                   # 일반 / 정예 / 보스 / 소환
# THEME = "dark"                               # 프로필 안의 테마 범위 (예: 어두운 테마)
LABEL = "사당의 망령"
COMPARE = ["skeleton_warrior=해골 전사", "skeleton_archer=해골 궁수"]   # 라인업에 세울 같은 프로필 기준 몹 (ID=이름표)
PPB = 150                                      # 라인업 1블록 = px
ANIMS = ["attack", "walk"]                     # 부품이 기존 동작에서 이상 없는지 볼 동작


def build(kit):
    # 재질: 원래 색 계단과 맞춘다 (다시 칠한 몸 중심 L 0.68, 폭 ±0.25)
    kit.material("robe", "#9aa6a9", half=0.25, center=0.68)
    kit.material("shade", "#5f6e74", half=0.20, center=0.50)     # 두건·베일 (몸보다 어둡게 → 얼굴이 돋보임)
    kit.material("iron", "#5b5654", half=0.20, center=0.46)
    kit.material("frame", "#3e4148", half=0.16, center=0.38)
    kit.material("flame", "#4fd8f0", half=0.20, center=0.80)     # 포인트 색은 하나만 (혼불)

    # 머리: 두건 껍데기 (얼굴 쪽 north 와 아래는 열림) + 차양 + 옆 테 + 안쪽 테 + 뒤로 솟은 끝 + 베일
    kit.cube("h_h", [-5, 20, -5], [5, 31, 6], "shade", "cloth", "hood_shell", skip=["north", "down"],
             opts=dict(hem_depth=1, folds=0.6))
    kit.cube("h_h", [-5, 29, -6], [5, 31, -5], "shade", "cloth", "hood_brim", opts=dict(hem=False, folds=0))
    # 안쪽 테: 껍데기 앞이 열려 머리와 벽 사이 틈이 비쳐 보이는 것을 막는다. 바깥 벽과 같은 평면인 옆면은 skip
    # (아래쪽 y<22 는 소매 윗단 앞면(z=-5)이 가리므로 거기까지만 — 내려오면 소매 앞면과 같은 평면)
    kit.cube("h_h", [-5, 22, -5], [-4, 29, -4], "shade", "dark", "hood_inner_r", skip=["west"])
    kit.mirror_x("h_h", [-5, 22, -5], [-4, 29, -4], "shade", "dark", "hood_inner_l", skip=["west"])
    # 옆 테는 어깨(팔 윗면 y=21) 위에서 시작 → 팔 바깥면(x=±6)과 겹치지 않게
    kit.cube("h_h", [-6, 21, -6], [-5, 31, 1], "shade", "cloth", "hood_edge_r", opts=dict(hem_depth=1, folds=0))
    kit.mirror_x("h_h", [-6, 21, -6], [-5, 31, 1], "shade", "cloth", "hood_edge_l", opts=dict(hem_depth=1, folds=0))
    # x −22.5: 뒤(+z) 끝이 올라간다 → 뒤로 솟은 두건 끝 (처지게 하려면 +22.5)
    kit.cube("h_h", [-3, 27, 5], [3, 31, 9], "shade", "cloth", "hood_tail", rot=("x", -22.5), opts=dict(hem=False))
    kit.cube("h_h", [-4, 15, 6], [4, 22, 7], "shade", "cloth", "veil_back", opts=dict(hem_depth=3))

    # 몸: 등 망토 (허리 위까지, 몸 뒷면 z=2 에 붙여 뜨지 않게. 아랫단 y=12 → 치마 옆면과 겹치지 않게)
    kit.cube("b", [-4, 12, 2], [4, 21, 3], "robe", "cloth", "cape", opts=dict(hem_depth=2))

    # 팔: 위는 소매, 아래는 넓게 퍼진 소매 + 소매 끝 띠.
    # 소매 윗단 앞뒤 z=±5 → 머리카락 판(z=-3)·머리 앞뒷면(z=±4)과 같은 평면을 피한다. 윗면 y=22 → 팔·몸 윗면(y=21)과 겹치지 않게
    for bone, side in (("l_a", 1), ("r_a", -1)):
        c = kit.cube if side > 0 else kit.mirror_x
        c(bone, [2, 15, -5], [7, 22, 5], "robe", "cloth", f"sleeve_up_{bone}", skip=["down"], opts=dict(hem=False))
        c(bone, [1, 8, -5], [8, 15, 5], "robe", "cloth", f"sleeve_flare_{bone}", skip=["down", "up"], opts=dict(hem_depth=2))
        # 끝 띠는 퍼진 소매보다 1칸 크게 (같은 평면이면 깜박임). 퍼진 소매 윗면은 띠가 덮으므로 skip
        c(bone, [0, 14, -6], [9, 15, 6], "shade", "cloth", f"sleeve_trim_{bone}", skip=["down"], opts=dict(hem=False, folds=0))
    kit.mirror_x("r_a", [4, 5, 0], [5, 8, 1], "iron", "metal", "chain1_r_a")
    kit.mirror_x("r_a", [4, 2, 0], [5, 5, 1], "iron", "metal", "chain2_r_a", rot=("y", 45))

    # 등불 (+X 손 바깥쪽, 치마 x≤4 밖): 틀은 metal, 불은 glow (테두리 없이 가운데 밝게)
    kit.cube("l_a", [6, 6, -1], [7, 8, 0], "iron", "metal", "lantern_chain")
    kit.cube("l_a", [5, 5, -2], [8, 6, 1], "frame", "metal", "lantern_top")
    kit.cube("l_a", [5, 1, -2], [8, 5, 1], "flame", "glow", "lantern_flame")
    kit.cube("l_a", [5, 0, -2], [8, 1, 1], "frame", "metal", "lantern_bottom")
    for x, z in ((4, -3), (8, -3), (4, 1), (8, 1)):
        kit.cube("l_a", [x, 1, z], [x + 1, 5, z + 1], "frame", "metal", f"lantern_bar_{x}_{z}")

    # 다리: 찢어진 치마 반쪽씩. 안쪽 면(x=0)은 두 반쪽이 맞닿아 skip, 윗면 y=12 → 다리 윗면(y=11)과 겹치지 않게
    kit.cube("l_l", [0, 0, -4], [4, 12, 4], "robe", "cloth", "skirt_l", skip=["west"], opts=dict(hem_depth=3))
    kit.mirror_x("r_l", [0, 0, -4], [4, 12, 4], "robe", "cloth", "skirt_r", skip=["west"], opts=dict(hem_depth=3))
    # 늘어진 천 (두께 0 판): 치마 앞뒤로 1칸 띄우고, 퍼진 소매(y≥8)와 같은 평면에 닿지 않게 y<8
    kit.cube("l_l", [1, -2, -5], [4, 8, -5], "shade", "cloth", "strip_front_l", opts=dict(hem_depth=3))
    kit.mirror_x("r_l", [1, -1, -5], [3, 8, -5], "shade", "cloth", "strip_front_r", opts=dict(hem_depth=3))
    kit.cube("l_l", [0, -1, 5], [3, 8, 5], "shade", "cloth", "strip_back_l", opts=dict(hem_depth=2))
    kit.mirror_x("r_l", [1, -2, 5], [4, 8, 5], "shade", "cloth", "strip_back_r", opts=dict(hem_depth=2))
