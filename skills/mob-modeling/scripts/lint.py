"""모델 검사 (읽기 전용): [엔진] ModelEngine R4 규칙 + [공통] 1층 공통 규칙 + [스타일 …] 2층 스타일 프로필.

사용 (프로젝트 루트에서):
  python -I <스킬>/scripts/lint.py <모델ID 또는 .bbmodel>[@배율] ... [--profile 이름] [--role 일반|정예|보스|소환]
                                  [--theme dark] [--std 기준.json] [--md 결과.md]
  프로필·역할·테마를 안 주면 기준 파일의 assign(모델별 지정) → default_profile 순으로 정한다.
판정: 오류(서버에서 깨질 가능성이 큼) / 경고(기준 밖이거나 인게임 확인 필요) / 통과. 예외 승인된 항목은 '정보'로 내려간다.
근거: ModelEngine 공식 위키(2026-11 갱신본), references/common-rules.md, references/engine-rules.md
"""
import json, math, os, re, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import bbtex  # noqa: E402
import metrics  # noqa: E402
import skilldeps  # noqa: E402
import standard  # noqa: E402

ALLOWED = (0.0, 22.5, 45.0)
EMPTY_TAGS = re.compile(r"^(ih_|ir_|il_|l_|seg_|tl_|tlf_|mount)", re.I)     # 큐브가 없어야 하는 태그 뼈
HINT = {"l_": "리드 연결 지점", "ih_": "머리 아이템", "ir_": "주손 아이템", "il_": "보조손 아이템",
        "seg_": "체인 마디(R4.0.7+)", "tl_": "꼬리 체인(R4.0.7+)", "tlf_": "꼬리 체인", "mount": "탑승 지점"}


def pow2(n):
    return n > 0 and (n & (n - 1)) == 0


def is_emissive(name):
    return os.path.splitext((name or "").lower())[0].endswith("_e")


def lint(path, scale=1.0, std=None, theme=None, profile=None, role=None):
    """std: 기준 dict (version 2 또는 옛 형식, None 이면 1층 기본값만)."""
    bbpose = metrics._bbpose()
    M = bbpose.Model(path)
    m = M.m
    mid = os.path.splitext(os.path.basename(path))[0]
    d = standard.convert(std or {})
    c = standard.common(d)
    err, warn, info = [], [], []
    fmt = (m.get("meta") or {}).get("model_format")
    if fmt != "free":
        warn.append(f"[엔진] 형식 {fmt} (ModelEngine 문서는 Generic Model=free 기준)")
    if any(e.get("type") == "mesh" for e in m.get("elements", [])):
        err.append("[엔진] 메시 요소 있음 (ModelEngine 미지원)")
    # 텍스처
    texs = bbtex.textures(m)
    for t in texs:
        if t["img"] is None:
            err.append(f"[엔진] 텍스처 '{t['name']}' 가 파일 안에 없음")
            continue
        h1 = t["h"] // t["frames"]
        if not (pow2(t["w"]) and pow2(h1)):
            warn.append(f"[엔진] 텍스처 '{t['name']}' {t['w']}x{h1} 가 2의 거듭제곱 아님 (아틀라스 mipmap 저하)")
        if abs(t["sx"] - round(t["sx"])) > 1e-6 and abs(1 / t["sx"] - round(1 / t["sx"])) > 1e-6:
            warn.append(f"[공통] 텍스처 '{t['name']}' 픽셀/UV 비율 {t['sx']:.3f} 이 정수배 아님")
    # 큐브 회전
    bad_rot = []
    for e in M.elements.values():
        r = e.get("rotation")
        if isinstance(r, dict):
            angs = [float(r.get("angle", 0))]
        elif isinstance(r, list):
            angs = [float(a) for a in r if abs(float(a)) > 1e-6]
        else:
            angs = []
        if len(angs) > 1 or any(min(abs(abs(a) - x) for x in ALLOWED) > 1e-3 for a in angs):
            bad_rot.append(e.get("name", "?"))
    if bad_rot:
        warn.append(f"[엔진] 큐브 회전 규칙 밖 {len(bad_rot)}개 (한 축·0/±22.5/±45 만): {', '.join(bad_rot[:6])}"
                    f"{' …' if len(bad_rot) > 6 else ''} — 인게임 확인 필요")
    # 태그 뼈·히트박스
    hit = None
    render_bones = 0
    for u in M.order:
        g = M.groups[u]
        nm = g["name"]
        if nm.lower() == "hitbox":
            hit = g
            continue
        if g["elements"]:
            render_bones += 1
        mt = EMPTY_TAGS.match(nm)
        if mt and g["elements"]:
            warn.append(f"[엔진] 뼈 '{nm}' 은 ModelEngine 태그({HINT.get(mt.group(1).lower(), mt.group(1))})인데 큐브 "
                        f"{len(g['elements'])}개 — 큐브가 안 보이거나 태그 동작이 붙을 수 있음, 인게임 확인 필요")
    if hit is None:
        warn.append("[엔진] hitbox 뼈 없음 (몹 크기 판정이 기본값)")
    else:
        if len(hit["elements"]) != 1:
            warn.append(f"[엔진] hitbox 큐브 {len(hit['elements'])}개 (1개여야 함)")
        else:
            e = M.elements[hit["elements"][0]]
            sx = abs(e["to"][0] - e["from"][0]); sz = abs(e["to"][2] - e["from"][2])
            if abs(sx - sz) > 1e-6:
                info.append(f"[엔진] hitbox 가로 {sx} ≠ 깊이 {sz} (넓은 쪽만 씀)")
        if hit["origin"][1] < 0:
            err.append("[엔진] hitbox 기준점 Y < 0 (질식 피해)")
    # 동작
    bez = sorted({a["name"] for a in m.get("animations", []) for an in (a.get("animators") or {}).values()
                  for k in an.get("keyframes", []) if k.get("interpolation") == "bezier"})
    if bez:
        warn.append(f"[엔진] Bezier 보간 동작 {len(bez)}개 (ModelEngine 은 linear 로 재생): {', '.join(bez[:5])}")
    # 겹쳐 깜박일 면, 뒤집힌 큐브, 텍스처 장수, 소수 크기 큐브
    ov = bbtex.overlaps(M, bbtex.body_elements(M))
    if ov:
        area = sum(o[4] for o in ov)
        msg = (f"[공통] 같은 평면에 겹친 면 {len(ov)}쌍, 넓이 {area:.0f}px² (깜박임·z-fighting): "
               + ", ".join(f"{a}.{fa}×{b}.{fb}" for a, fa, b, fb, _ in ov[:5]) + (" …" if len(ov) > 5 else ""))
        (warn if area >= c["overlap_area_warn"] else info).append(msg)
    nflip = sum(1 for e in M.elements.values() if bbtex.is_flipped(e))
    if nflip:
        warn.append(f"[공통] 뒤집힌 큐브(from>to, 안쪽 면 표시용) {nflip}개 — ModelEngine·인게임 표시 확인 필요, 큐브 수에서 뺌")
    pairs = bbtex.emissive_pairs(texs)
    if pairs:
        info.append("[공통] 발광 짝 텍스처(OptiFine 식, 면이 직접 안 씀): "
                    + ", ".join(f"{texs[b]['name']}→{texs[e]['name']}" for b, e in pairs.items())
                    + " — 본체 배치를 바꾸면 같이 옮겨야 한다 (density_fix 는 자동)")
    body_tex = [t for t in texs if not is_emissive(t["name"])]
    if len(body_tex) > c["body_textures_max"]:
        warn.append(f"[공통] 본체 텍스처 {len(body_tex)}장 > {c['body_textures_max']} (본체 1장 + 발광 1장, 부품은 빈 곳에 먼저)")

    def _frac(v):       # 두께 0.1 미만 판(겹침 방지용)은 빼고 본다
        return abs(v) >= 0.1 and abs(v - round(v)) > 1e-3
    frac = sum(1 for e in M.elements.values() if any(_frac(e["to"][i] - e["from"][i]) for i in range(3)))
    if M.elements and frac / len(M.elements) > c["frac_cube_share_max"]:
        warn.append(f"[공통] 소수 크기 큐브 {frac}/{len(M.elements)} (UV 를 편 뒤 통째 확대·축소한 흔적 → 밀도 불일치)")
    # 1층 수치 규칙 + 2층 스타일
    r = metrics.measure(path, scale)
    prof, role2, theme2 = standard.pick(d, mid, profile, role, theme)
    for key, msg, ex in standard.judge_common(r, d, mid, role2, render_bones):
        (info if ex else warn).append(f"[공통] {msg}")
    if prof:
        _, eff = standard.effective(d, mid, prof, role2, theme2)
        info.append("실효 목표(1층∩프로필): " + ", ".join(
            f"{standard.NAMES.get(k, k)} {('' if v[0] is None else v[0])}~{('' if v[1] is None else v[1])}"
            for k, v in eff.items() if k in ("val", "contrast", "edge_dark", "colors95", "cubes_per_blk")))
        _, msgs = standard.judge_style(r, d, mid, prof, role2, theme2)
        tag = prof + (f"·{role2}" if role2 else "") + (f"·{theme2}" if theme2 else "")
        warn += [f"[스타일 {tag}] {x}" for x in msgs]
    else:
        info.append("스타일 프로필 없음 → 1층 공통 규칙만 봄 (make_profile.py 로 기준 몹에서 프로필을 만든다)")
    info.append(f"큐브 {r['cubes']}, 렌더 뼈 {render_bones}, 키 {r['height_blk']}칸, {r.get('px_per_block')} px/블록, "
                f"색95 {r.get('colors95')}, 명도 {r.get('val')}, 명암 {r.get('contrast')}, 테두리 {r.get('edge_dark')}")
    rel = os.path.relpath(path).replace("\\", "/") if not os.path.relpath(path).startswith("..") else path
    return dict(file=rel, err=err, warn=warn, info=info, metrics=r, render_bones=render_bones,
                profile=prof, role=role2, theme=theme2, overlaps=ov)


def main(argv):
    opts = {"--std": None, "--md": None, "--theme": None, "--profile": None, "--role": None}
    items, i = [], 0
    while i < len(argv):
        if argv[i] in opts:
            opts[argv[i]] = argv[i + 1]; i += 2; continue
        items.append(argv[i]); i += 1
    d, used = standard.load(opts["--std"])
    print(f"(아트 기준: {used or '없음 — 1층 공통 규칙만'})")
    lines = []
    files = skilldeps.model_index()
    for it in items:
        sp = skilldeps.resolve(it, files)
        res = lint(sp["path"], sp["scale"], d, opts["--theme"], opts["--profile"], opts["--role"])
        verdict = "오류" if res["err"] else ("경고" if res["warn"] else "통과")
        lines.append(f"### {res['file']} — {verdict}" + (f"  (프로필 {res['profile']})" if res["profile"] else ""))
        lines += [f"- 오류: {x}" for x in res["err"]] + [f"- 경고: {x}" for x in res["warn"]] + [f"- 정보: {x}" for x in res["info"]]
        lines.append("")
    out = "\n".join(lines)
    print(out)
    if opts["--md"]:
        open(opts["--md"], "w", encoding="utf-8").write(out)


if __name__ == "__main__":
    main(sys.argv[1:])
