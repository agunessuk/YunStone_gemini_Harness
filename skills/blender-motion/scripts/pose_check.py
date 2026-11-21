"""동작 수치 검사 (Blender 없이): 바닥 뚫림, 몸 이동, 발 끌림, 시작·끝 자세 끊김.

사용: python -I pose_check.py <모델.bbmodel> <동작> [<동작> …] [--ref idle] [--compare <팩 동작>]
  --ref      기준 자세로 쓸 동작 (0초 자세). 기본: idle 이 있으면 idle, 없으면 뼈 기본 자세
  --compare  팩에 원래 있던 공격 동작을 같이 잰다 → "이 팩은 원래 이 정도다"를 기준으로 판단 (없으면 첫 공격 계열 동작)

기준 자세에서 가장 낮은 점을 바닥(0)으로, 그때 바닥에 닿은 큐브를 '발'로 본다. 단위는 Blockbench 칸(1/16 블록).
- 바닥 아래: 동작 중 가장 낮은 점 (음수면 바닥을 뚫음, 무기 끝 포함)
- 몸 이동: 맨 위 뼈(보통 body)의 기준점이 수평으로 움직인 최대 거리 (돌진·밀려남). 크면 lunge 와 같이 쓸 때 끝에 뒤로 미끄러져 보인다
- 발 끌림: 발이 바닥에 닿아 있는 동안, 몸 이동을 빼고 발이 수평으로 움직인 최대 거리 (몸통 비틀기로 발이 도는 것도 포함)
- 시작·끝 끊김: 덮어쓰는(override) 동작은 기준 자세와, 더하는 동작은 0 과의 차이(회전은 360° 단위 무시). 크면 켜지고 꺼질 때 튄다
몸을 비틀거나 발을 내딛는 동작은 팩 원래 동작도 값이 크다 → 판단은 팩 원래 공격 동작과 비교해서 한다.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True      # 스킬 폴더에 __pycache__ 를 남기지 않음
import numpy as np
import bbpose

ATTACK = re.compile(r"attack|slash|swing|bite|claw|punch|slam|stab|combo|strike|smash|spell|cast|charge|bump|ram|stomp|melee|pierce|spit")


def _corners(e):
    a, b = np.array(e["from"], float), np.array(e["to"], float)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    return np.array([[x, y, z, 1] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def _hidden_groups(M):
    return {u for u, g in M.groups.items() if g["name"].lower() == "hitbox"}


def _visible(M):
    hide = _hidden_groups(M)
    return [eu for eu, e in M.elements.items()
            if M.elem_group.get(eu) not in hide and e.get("visibility") is not False and (e.get("name") or "").lower() != "hitbox"]


def _world(M, elems, anim, t):
    gm = M.group_mats(anim, t)
    return gm, {eu: (M.element_mat(eu, gm) @ _corners(M.elements[eu]).T).T[:, :3] for eu in elems}


def _low_face(pts):
    return pts[np.argsort(pts[:, 1])[:4]].mean(0)


def _roots(M):
    hide = _hidden_groups(M)
    return [u for u in M.order if M.groups[u]["parent"] is None and u not in hide]


def _root_pos(M, gm, roots):
    return np.mean([(gm[u] @ np.append(M.groups[u]["origin"], 1.0))[:3] for u in roots], axis=0) if roots else np.zeros(3)


def measure(M, anim, ref=None, dt=0.05):
    """한 동작의 수치. ref = 기준 동작(dict) 또는 None(뼈 기본 자세)."""
    elems = _visible(M)
    roots = _roots(M)
    gm0, w0 = _world(M, elems, ref, 0.0)
    floor0 = min(p[:, 1].min() for p in w0.values())
    feet = {eu: _low_face(p) for eu, p in w0.items() if p[:, 1].min() <= floor0 + 1.0}
    root0 = _root_pos(M, gm0, roots)
    L = float(anim.get("length") or 0)
    lowest, low_t, slide, slide_bone, travel = 1e9, 0.0, 0.0, "-", 0.0
    for t in np.arange(0, L + 1e-9, dt):
        gm, w = _world(M, elems, anim, t)
        m = min(p[:, 1].min() for p in w.values()) - floor0
        if m < lowest:
            lowest, low_t = m, float(t)
        shift = _root_pos(M, gm, roots) - root0
        travel = max(travel, float(np.linalg.norm(shift[[0, 2]])))
        for eu, ref_c in feet.items():
            p = w[eu]
            if p[:, 1].min() <= floor0 + 0.5:                      # 바닥에 닿아 있을 때만
                d = float(np.linalg.norm((_low_face(p) - ref_c - shift)[[0, 2]]))
                if d > slide:
                    slide, slide_bone = d, M.groups[M.elem_group[eu]]["name"]
    target = M.pose(ref, 0.0) if (ref and anim.get("override")) else {}

    def diff(t):
        worst = 0.0
        for u, (r, q) in M.pose(anim, t).items():
            r0, q0 = target.get(u, (np.zeros(3), np.zeros(3)))
            dr = (r - r0 + 180.0) % 360.0 - 180.0                 # 720° 회전 = 0° 로 봄
            worst = max(worst, float(np.abs(dr).max()), float(np.abs(q - q0).max()))
        return worst
    return dict(length=L, override=bool(anim.get("override")), floor=round(lowest, 2), floor_t=round(low_t, 2),
                travel=round(travel, 2), slide=round(slide, 2), slide_bone=slide_bone,
                start=round(diff(0.0), 2), end=round(diff(L), 2))


def default_ref(M):
    return M.animations().get("idle")


def default_compare(M, exclude=()):
    """비교 기준으로 쓸 팩 원래 공격 동작 이름 (없으면 None)."""
    for name, a in M.animations().items():
        if name not in exclude and ATTACK.search(name.lower()) and float(a.get("length") or 0) > 0:
            return name
    return None


def verdict(r, base=None):
    """주의할 점 목록. base = 팩 원래 동작의 수치(있으면 그보다 확실히 나쁠 때만)."""
    out = []
    if r["floor"] < ((min(base["floor"], 0) - 1.0) if base else -1.5):
        out.append(f"바닥 아래 {r['floor']}칸")
    if r["travel"] > (max(4.0, base["travel"] + 1.0) if base else 4.0):
        out.append(f"몸 이동 {r['travel']}칸 (lunge 와 같이 쓰면 끝날 때 뒤로 미끄러져 보임)")
    if r["slide"] > (max(2.0, base["slide"] * 1.5) if base else 2.0):
        out.append(f"발 끌림 {r['slide']}칸({r['slide_bone']})")
    if max(r["start"], r["end"]) > 1.0:
        out.append(f"시작·끝 끊김 {max(r['start'], r['end'])}")
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    M = bbpose.Model(argv[0])
    A = M.animations()
    ref_name = argv[argv.index("--ref") + 1] if "--ref" in argv else None
    cmp_name = argv[argv.index("--compare") + 1] if "--compare" in argv else None
    names = [a for i, a in enumerate(argv[1:], 1) if not a.startswith("--") and argv[i - 1] not in ("--ref", "--compare")]
    cmp_name = cmp_name or default_compare(M, exclude=set(names))
    ref = A[ref_name] if ref_name else default_ref(M)
    base = measure(M, A[cmp_name], ref) if cmp_name else None
    print(f"기준 자세: {ref_name or ('idle 0초' if ref else '뼈 기본 자세')} · 비교 기준: {cmp_name or '없음'} · 단위 칸(1/16 블록)")
    print("| 동작 | 덮어쓰기 | 바닥 아래(최저, 시각) | 몸 이동 | 발 끌림 | 시작·끝 끊김 | 판단 |\n|---|---|---|---|---|---|---|")
    rows = ([(cmp_name + " (팩 원래)", base)] if base else []) + [(n, measure(M, A[n], ref)) for n in names]
    for n, r in rows:
        v = verdict(r, base) if not n.endswith("(팩 원래)") else []
        print(f"| `{n}` | {'O' if r['override'] else ''} | {r['floor']} ({r['floor_t']}초) | {r['travel']} | {r['slide']} ({r['slide_bone']}) | "
              f"{r['start']} / {r['end']} | {'주의: ' + ', '.join(v) if v else ('기준' if n.endswith('(팩 원래)') else '좋음')} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
