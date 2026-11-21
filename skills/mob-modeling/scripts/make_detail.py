"""디테일 보강 실행기: 설계 파일(.py) → 복사본 .bbmodel + 검사 + 라인업(앞·뒤) + 동작 자세 시트 + 보고표.

사용 (프로젝트 루트에서):
  python -I <스킬>/scripts/make_detail.py <설계.py> [--no-render] [--no-anims] [--gif] [--root 작업폴더]
  작업폴더 기본: 기준 파일 paths.work → 작업/모델링(있으면) → modeling
  --no-render : 수치·검사만 (1초 안팎) — 좌표를 고치며 반복할 때 먼저 쓴다. 결과는 미리보기/<이름>/초안.bbmodel 과
                보고표_초안.md 에만 쓴다 (OUT·보고표.md 는 렌더해서 본 판만 → 승인한 그림과 납품본이 어긋나지 않게)
  --no-anims  : 라인업만, 동작 자세 시트 생략
  --gif       : blender-motion 의 make_motion 으로 동작 GIF 도 만든다 (사용자에게 보낼 때)
설계 파일 (templates/detail_spec_example.py 참고):
  SRC = "원본.bbmodel"                     # 읽기만 한다
  OUT = "<작업폴더>/모델/<이름>.bbmodel"     # 없으면 이 경로. 렌더한 판은 미리보기/<이름>/이전/v<N>.bbmodel 로 보관
  RESHADE = dict(val=0.6, contrast=0.17)   # 있으면 원래 큐브 텍스처를 먼저 다시 칠한다 (reshade.py 인자: mode, protect=[…] 등)
  SCALE = 1.0                              # MythicMobs model{scale=}
  PROFILE = "fantasy"                      # 2층 스타일 프로필 (없으면 기준 파일 assign → default_profile)
  ROLE = "일반"                             # 일반 / 정예 / 보스 / 소환 (렌더 뼈 예산·큐브 밀도 하한이 달라짐)
  THEME = "dark"                           # 프로필의 테마 범위 (예: 어두운 테마)
  STD = "art_standard.json"                # 아트 기준 파일 (없으면 기본 찾기)
  COMPARE = ["skeleton_warrior=해골 전사"]  # 라인업에 같이 세울 기준 모델 (ID 또는 경로, =이름표)
  LABEL = "사당의 망령"
  PPB = 150                                # 라인업 1블록 = px
  ANIMS = ["attack", "walk"]               # 자세 시트로 볼 기존 동작 (다섯 순간 × 3/4·옆)
  def build(kit): ...                      # kit.material / kit.cube / kit.mirror_x / kit.cubes() / kit.colors()
출력: OUT, <root>/미리보기/<이름>/ 에 라인업 v<N> PNG, 동작 자세 시트 PNG, 보고표.md
"""
import argparse, glob, importlib.util, os, re, shutil, subprocess, sys, tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import kitbash, lint, lineup, reshade, skilldeps  # noqa: E402


def load_spec(path):
    spec = importlib.util.spec_from_file_location("detail_spec", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in ("SRC", "build"):
        if not hasattr(mod, k):
            raise SystemExit(f"설계 파일에 {k} 가 없음")
    return mod


def table(before, after):
    keys = [("cubes", "큐브"), ("flipped", "뒤집힌 큐브"), ("render_bones", "렌더 뼈"), ("height_blk", "키(칸)"),
            ("cubes_per_blk", "큐브/키"), ("px_per_block", "px/블록"), ("colors95", "색95"), ("val", "명도"), ("sat", "채도"),
            ("contrast", "명암 폭"), ("noise", "노이즈"), ("edge_dark", "테두리"), ("top_light", "위밝음")]
    out = ["| 항목 | 원본 | 보강 |", "|---|---|---|"]
    for k, nm in keys:
        b = before["render_bones"] if k == "render_bones" else before["metrics"].get(k)
        a = after["render_bones"] if k == "render_bones" else after["metrics"].get(k)
        out.append(f"| {nm} | {b} | {a} |")
    return "\n".join(out)


def material_table(kit):
    """재질별: 계단 수, 칠한 픽셀, 실제로 쓴 색, 밝기 L·명도 V 범위, 평균 채도 (보이는 넓이가 아니라 텍스처 픽셀 기준)."""
    rows = ["| 재질 | 계단 | 칠한 픽셀 | 쓴 색 | 밝기 L | 명도 V | 채도 |", "|---|---|---|---|---|---|---|"]
    for nm, mt in kit.mats.items():
        rgb = reshade.from_oklab(mt["ramp"])
        L = mt["ramp"][:, 0]
        mx, mn = rgb.max(1), rgb.min(1)
        S = float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0).mean())
        st = kit.stats.get(nm, {"px": 0, "colors": set()})
        rows.append(f"| {nm} | {mt['steps']} | {st['px']} | {len(st['colors'])} | {L.min():.2f}~{L.max():.2f} | "
                    f"{mx.min():.2f}~{mx.max():.2f} | {S:.2f} |")
    return "\n".join(rows)


def pose_times(M, an, n=5):
    """자세 시트 시각: 고르게 n 개 + 크기(scale) 키가 1 이 아닌 시각(잔상·늘어남 키). 최대 8개."""
    L = float(an["length"])
    ts = {round(L * k / (n - 1), 2) for k in range(n)}
    for anm in (an.get("animators") or {}).values():
        for k in anm.get("keyframes", []):
            if k.get("channel") == "scale":
                v = [float(k["data_points"][0].get(c, 1) or 1) for c in "xyz"]
                if any(abs(x - 1) > 1e-3 for x in v):
                    ts.add(round(float(k.get("time", 0)), 2))
    out = sorted(t for t in ts if 0 <= t <= L + 1e-6)
    while len(out) > 8:                      # 많으면 고른 시각부터 줄인다
        out.pop(len(out) // 2)
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-anims", action="store_true")
    ap.add_argument("--gif", action="store_true")
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    a.root = a.root or skilldeps.work_dir()
    sp = load_spec(a.spec)
    src = sp.SRC
    name = os.path.splitext(os.path.basename(src))[0]
    out = getattr(sp, "OUT", None) or os.path.join(a.root, "모델", name + ".bbmodel")
    if os.path.abspath(out) == os.path.abspath(src):
        raise SystemExit("OUT 이 원본과 같음 — 원본은 고치지 않는다")
    scale = float(getattr(sp, "SCALE", 1.0))
    theme = getattr(sp, "THEME", None)
    profile, role = getattr(sp, "PROFILE", None), getattr(sp, "ROLE", None)
    label = getattr(sp, "LABEL", name)
    prev = os.path.join(a.root, "미리보기", name)
    os.makedirs(prev, exist_ok=True)

    # 고친 회차: 렌더해서 본 판만 이전/v<N>.bbmodel 로 보관한다 (--no-render 로 고쳐 본 중간 판은 남기지 않음)
    olddir = os.path.join(prev, "이전")
    vers = sorted(int(m.group(1)) for f in glob.glob(os.path.join(olddir, "v*.bbmodel"))
                  for m in [re.search(r"v(\d+)\.bbmodel$", f)] if m)
    last = os.path.join(olddir, f"v{vers[-1]}.bbmodel") if vers else None
    ver = (vers[-1] + 1) if vers else 1

    import standard
    std, used = standard.load(getattr(sp, "STD", None))
    prof_name, role_r, theme_r = standard.pick(std, name, profile, role, theme)
    paint = dict(((std.get("profiles") or {}).get(prof_name) or {}).get("paint") or {}) if prof_name else {}
    if a.no_render:
        out = os.path.join(prev, "초안.bbmodel")           # 납품본(OUT)은 렌더해서 본 판만
    kit = kitbash.Kit(src, seed=name, paint=paint)
    if getattr(sp, "RESHADE", None):
        rs = {k: v for k, v in paint.items() if k in ("edge", "center", "noise", "grad")}   # 프로필 칠하기 묶음이 기본
        rs.update(sp.RESHADE)
        with tempfile.TemporaryDirectory() as td:          # 다시 칠한 텍스처만 가져오고 임시 파일은 지운다
            tmp = os.path.join(td, name + ".bbmodel")
            reshade.reshade(src, tmp, **rs)
            kit.replace_texture(tmp)
    sp.build(kit)
    kit.save(out)
    before = lint.lint(src, scale, std, theme, profile, role)
    after = lint.lint(out, scale, std, theme, profile, role)
    judged = after["profile"] and (after["profile"] + (f"·{after['role']}" if after["role"] else "")
                                   + (f"·{after['theme']}" if after["theme"] else ""))
    print(f"저장: {out}  (새 큐브 {kit.count}개, 부품 밀도 {kit.density} px/유닛, 아트 기준: {used or '없음'}, "
          f"스타일: {judged or '없음(1층만)'})")
    key = lambda o: tuple(sorted([(o[0], o[1]), (o[2], o[3])]))          # noqa: E731
    old_ov = {key(o) for o in before["overlaps"]}
    new_ov = [o for o in after["overlaps"] if key(o) not in old_ov]
    if new_ov:
        after["warn"].insert(0, f"[새 겹침] 부품이 새로 만든 같은 평면 겹침 {len(new_ov)}쌍 (0 이어야 함): "
                             + ", ".join(f"{a}.{fa}×{b}.{fb}" for a, fa, b, fb, _ in new_ov[:8]) + (" …" if len(new_ov) > 8 else ""))
    for x in after["err"] + after["warn"]:
        print("  -", x)
    print("  결과 본체 색(상위 8):", ", ".join(f"{h} {r:.0%}" for h, r, _, _ in kit.colors(8, original=False)))
    _, eff = standard.effective(std, name, profile, role, theme)
    part_colors = sum(len(v["colors"]) for v in kit.stats.values())
    c95_hi = (eff.get("colors95") or [None, None])[1]
    budget = f"색 예산: 원본 색95 {before['metrics'].get('colors95')} + 부품이 쓴 색 {part_colors} → 결과 색95 {after['metrics'].get('colors95')}" \
        + (f" (상한 {c95_hi})" if c95_hi else "")
    print("  " + budget)

    slash = lambda x: str(x).replace("\\", "/")          # noqa: E731  (보고표 경로를 / 로)
    src_s, out_s, spec_s, last_s = slash(src), slash(out), slash(a.spec), slash(last) if last else None
    lines = [f"# {label} — 디테일 보강 결과 " + ("(초안, 렌더 안 함)" if a.no_render else f"(v{ver})"), "",
             f"- 원본: `{src_s}` (읽기만 함)", f"- 결과: `{out_s}`" + (f" (직전에 렌더한 판: `{last_s}`)" if last_s else ""),
             f"- 설계: `{spec_s}`", f"- 새 큐브 {kit.count}개, 새 부품 텍스처 {len(kit.tex)}장 (나머지는 원본 텍스처 빈 곳)",
             f"- 판정 기준: `{used}` — 1층 공통 규칙 + 2층 스타일 프로필 **{judged or '없음'}**", "",
             "## 수치", "", table(before, after), "",
             "## 재질 (center 는 OKLab 밝기 L, 아트 기준의 명도는 HSV V)", "", material_table(kit), "", f"- {budget}",
             "- 색이 상한에 걸리면: 재질 수·계단(steps)을 줄이거나 `palette=[…]` 로 원본 색을 다시 쓴다", "", "## 검사 (보강본)", ""]
    lines += ([f"- 오류: {x}" for x in after["err"]] + [f"- 경고: {x}" for x in after["warn"]]) or ["- 통과"]
    if not a.no_render:
        files = skilldeps.model_index()
        specs = [skilldeps.resolve(t, files) for t in getattr(sp, "COMPARE", [])]
        specs.append(dict(path=os.path.abspath(src), scale=scale, key=name + "_원본", label=f"{label} (원본)"))
        if last and open(last, "rb").read() != open(out, "rb").read():
            specs.append(dict(path=os.path.abspath(last), scale=scale, key=name + "_직전", label=f"{label} (v{ver - 1})"))
        specs.append(dict(path=os.path.abspath(out), scale=scale, key=name + "_보강", label=f"{label} (v{ver})"))
        png = os.path.join(prev, f"{name}_라인업_v{ver}.png")
        _, eff = standard.effective(std, name, profile, role, theme)
        dark = theme_r == "dark" or (eff.get("val") and eff["val"][1] is not None and eff["val"][1] < 0.4) \
            or any("평균 명도" in w for w in after["warn"] + before["warn"])
        views = [(28, ""), (208, "뒤")] + ([(28, "어두운 곳 50%", 0.5)] if dark else [])
        lineup.render(specs, png, f"{label} — 원본 → 보강 v{ver} (같은 축척, 마인크래프트 조명, 둘째 줄 뒷모습"
                      + (", 셋째 줄 어두운 곳)" if dark else ")"), stats=True, ppb=getattr(sp, "PPB", 150), per_row=6, views=views)
        lines += ["", "## 그림", "", f"- 라인업 (앞·뒤{'·어두운 곳' if dark else ''}): `{slash(png)}`"]
        zpng = os.path.join(prev, f"{name}_확대_v{ver}.png")          # 원본·이번 판만 크게 (얼굴·작은 부품 판정용)
        zspecs = [dict(specs[-2 if not (last and len(specs) >= 3 and specs[-2]['key'].endswith('_직전')) else -3]),
                  dict(specs[-1])]
        lineup.render(zspecs, zpng, f"{label} — 확대 (원본 / v{ver}, 앞·뒤)", ppb=int(getattr(sp, "PPB", 150) * 2.2),
                      per_row=2, player=False, views=[(28, ""), (208, "뒤")])
        lines.append(f"- 확대 (원본·이번 판): `{slash(zpng)}`")
        os.makedirs(olddir, exist_ok=True)
        shutil.copy2(out, os.path.join(olddir, f"v{ver}.bbmodel"))        # 이번에 렌더해서 본 판 보관 → 다음 실행의 '직전 판'
        if not a.no_anims:
            M = kit_model(out)
            for anim in getattr(sp, "ANIMS", []):
                an = M.animations().get(anim)
                if not an:
                    lines.append(f"- 동작 `{anim}`: 모델에 없음")
                    continue
                ts = pose_times(M, an)
                ps = [dict(path=os.path.abspath(out), scale=scale, key=f"{name}_{anim}_{i}", label=f"{anim} {t:.2f}초",
                           pose=[anim, t]) for i, t in enumerate(ts)]
                apng = os.path.join(prev, f"{name}_자세_{anim}_v{ver}.png")
                lineup.render(ps, apng, f"{label} — {anim} {len(ts)}순간 (크기 키 포함, 위 3/4·아래 옆)", ppb=getattr(sp, "PPB", 150),
                              per_row=len(ts), player=False, views=[(28, ""), (90, "옆")])
                ok = os.path.isfile(apng)
                lines.append(f"- 동작 `{anim}` 자세 시트: " + (f"`{slash(apng)}`" if ok else "렌더 실패"))
                if a.gif:
                    mm = os.path.join(skilldeps.blender_motion_dir(), "make_motion.py")
                    r = subprocess.run([sys.executable, "-I", mm, "--existing", out, anim, "--side", "--out", a.root],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace")
                    gif = os.path.join(a.root, "미리보기", name, f"검증_{anim}.gif")
                    lines.append(f"  - GIF: " + (f"`{gif}`" if os.path.isfile(gif) and r.returncode == 0
                                                 else f"실패 ({(r.stdout + r.stderr).strip()[-200:]})"))
    lines += ["", "## 확인 상태", "", ("- 수치만 확인함 (렌더 안 함 — 초안). " if a.no_render else "- 수치·렌더로 확인함. ")
              + "**인게임 미확인** (시험 서버에서 /meg reload 뒤 확인)", ""]
    rp = os.path.join(prev, "보고표_초안.md" if a.no_render else "보고표.md")
    open(rp, "w", encoding="utf-8").write("\n".join(lines))
    print("보고표:", rp)


def kit_model(path):
    return skilldeps.bbpose().Model(path)


if __name__ == "__main__":
    main(sys.argv[1:])
