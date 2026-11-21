"""Blockbench(.bbmodel) 모델을 읽어 뼈대와 애니메이션 목록, 동작의 '정점(가장 크게 움직이는 순간)'을 보여 준다.

사용:
  python -I inspect_model.py <모델.bbmodel>                 뼈대 + 애니메이션 표
  python -I inspect_model.py <모델.bbmodel> --anim attack   한 동작의 시간별 움직임 크기
  python -I inspect_model.py <모델.bbmodel> --keys attack   한 동작의 키프레임 값 (새 동작 설계 때 기준 값으로)
  python -I inspect_model.py <모델.bbmodel> --json          기계가 읽을 JSON

정점은 뼈 회전·위치 변화량(도/초)이 가장 큰 시각이다. 기존 동작을 MythicMobs 스킬에 붙일 때
'피해가 들어가는 틱'과 정점을 맞추는 데 쓴다: 지연 틱 = round((판정 시각 - 정점) × 20).
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True      # 스킬 폴더에 __pycache__ 를 남기지 않음
import numpy as np
import bbpose

CATS = [("대기", r"idle|stand|breath"), ("이동", r"walk|run|move|fly|swim|crawl|glide|hover"),
        ("사망", r"death|die|dead"), ("등장", r"spawn|appear|emerge|intro|awake|rise"),
        ("피격", r"hurt|hit|damaged|stagger"),
        ("공격", r"attack|atk|slash|swing|bite|claw|punch|slam|stab|combo|strike|smash|throw|shoot|cast|spell|charge|dash|leap|jump|spin|grab|sting|beam|breath|roar|stomp|summon|skill|laser|burrow|dive|wave|toss|tail|volley|bash|lunge|pierce|spit|drain|portal|card|shard|mirror|tentacle|sword|open|bump|ram|headbutt|peck|kick|pounce|swipe|whip|thrust|melee|crash")]


def category(name):
    n = name.lower()
    for c, rx in CATS:
        if re.search(rx, n):
            return c
    return "기타"


def motion_curve(model, anim, dt=0.05):
    """시각별 움직임 크기(도/초 + 칸/초×10)."""
    L = float(anim.get("length") or 0)
    if L <= 0:
        return [], []
    ts = np.arange(0, L + 1e-9, dt)
    poses = [model.pose(anim, t) for t in ts]
    out = []
    for i in range(len(ts) - 1):
        a, b = poses[i], poses[i + 1]
        e = 0.0
        for u in set(a) | set(b):
            ra, pa = a.get(u, (np.zeros(3), np.zeros(3)))
            rb, pb = b.get(u, (np.zeros(3), np.zeros(3)))
            e += float(np.linalg.norm(rb - ra)) + 10 * float(np.linalg.norm(pb - pa))
        out.append(e / dt)
    return list((ts[:-1] + dt / 2).round(3)), out


def peaks(model, anim, n=2):
    ts, es = motion_curve(model, anim)
    if not es:
        return []
    es = np.array(es)
    idx = [i for i in range(len(es)) if (i == 0 or es[i] >= es[i - 1]) and (i == len(es) - 1 or es[i] >= es[i + 1])]
    idx = sorted(idx, key=lambda i: -es[i])
    picked = []
    for i in idx:
        if all(abs(ts[i] - ts[j]) > 0.2 for j in picked):
            picked.append(i)
        if len(picked) >= n:
            break
    return [(ts[i], round(float(es[i]))) for i in sorted(picked)]


def main(argv):
    if not argv:
        print(__doc__); return 2
    path = argv[0]
    m = bbpose.Model(path)
    anims = m.animations()
    if "--keys" in argv:
        a = anims[argv[argv.index("--keys") + 1]]
        print(f"## {a['name']}  길이 {a.get('length')}  반복 {a.get('loop')}  덮어쓰기 {bool(a.get('override'))}")
        for key, an in (a.get("animators") or {}).items():
            for ch in ("rotation", "position", "scale"):
                ks = sorted((k for k in an.get("keyframes", []) if k.get("channel") == ch), key=lambda k: k["time"])
                if ks:
                    vals = "  ".join(f"{k['time']:.2f}:[" + ", ".join(str(round(bbpose._f(k["data_points"][0].get(c)), 2)) for c in "xyz") + "]" for k in ks)
                    print(f"- {an.get('name')} {ch}: {vals}")
        return 0
    if "--anim" in argv:
        a = anims[argv[argv.index("--anim") + 1]]
        ts, es = motion_curve(m, a)
        mx = max(es) if es else 1
        for t, e in zip(ts, es):
            print(f"{t:5.2f}s {'#' * int(40 * e / mx)}")
        return 0
    rows = []
    for name, a in anims.items():
        rows.append(dict(name=name, length=round(float(a.get("length") or 0), 3), loop=a.get("loop"), category=category(name),
                         override=bool(a.get("override")), bones=len(a.get("animators") or {}), peaks=peaks(m, a)))
    rest = bbpose.rest_pose(m, "idle") if "idle" in anims else {}
    if "--json" in argv:
        tree = [dict(name=g["name"], parent=m.groups[g["parent"]]["name"] if g["parent"] else None,
                     origin=g["origin"].round(2).tolist(), rotation=g["rotation"].round(2).tolist(), cubes=len(g["elements"]))
                for u, g in ((u, m.groups[u]) for u in m.order)]
        print(json.dumps(dict(model=os.path.basename(path), bones=tree, animations=rows, rest_pose_idle0=rest), ensure_ascii=False, indent=1))
        return 0
    print(f"## {os.path.basename(path)}  (형식 {m.m.get('meta', {}).get('model_format')}, 큐브 {len(m.elements)})")
    print("\n뼈대 (이름 · 기준점 · 기본 회전 · 큐브 수)")
    depth = {}
    for u in m.order:
        g = m.groups[u]
        d = depth[u] = depth[g["parent"]] + 1 if g["parent"] else 0
        rot = "" if not g["rotation"].any() else f" 회전{g['rotation'].round(1).tolist()}"
        print(f"{'  ' * d}- {g['name']}  {g['origin'].round(1).tolist()}{rot}  ({len(g['elements'])})")
    print("\n| 동작 | 길이(초) | 반복 | 분류 | 덮어쓰기 | 움직이는 뼈 | 정점(초) |\n|---|---|---|---|---|---|---|")
    for r in rows:
        pk = ", ".join(f"{t:.2f}" for t, _ in r["peaks"]) or "-"
        print(f"| `{r['name']}` | {r['length']} | {r['loop']} | {r['category']} | {'O' if r['override'] else ''} | {r['bones']} | {pk} |")
    if rest:
        print("\n기본 자세: idle 0초 값이 0 이 아닌 뼈 (팩이 모든 동작 키에 이 값을 깔아 둔 경우가 많다)")
        for b, ch in list(rest.items())[:15]:
            print(f"- {b}: " + ", ".join(f"{c} {v}" for c, v in ch.items()))
        if any(r["override"] for r in rows):
            print('→ 덮어쓰기 동작이 있음: 새 동작 설계 파일에 REST_FROM = "idle" 과 override=True 를 쓴다 (references/conventions.md 8장)')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
