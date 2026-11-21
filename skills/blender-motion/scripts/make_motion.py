"""동작 설계 파일(.py)을 .bbmodel 복사본에 넣고, 수치 검사와 Blender 미리보기(GIF·장면 PNG), 동작표를 만든다.

사용:
  python -I make_motion.py <설계.py> [--out <폴더>] [--blender <실행파일>] [--no-render] [--only 동작1,동작2]
                           [--side] [--top] [--fps 20] [--cell 300] [--overwrite-existing]
  python -I make_motion.py --existing <모델.bbmodel> <동작이름> [--out <폴더>] [--side] [--top]
      → 이미 있는 동작을 그대로 렌더 (방향·부호 검증용, 모델은 쓰지 않음)

설계 파일 (templates/motion_spec_example.py 참고):
  BASE      = 원본 .bbmodel 경로 (절대, 또는 현재 폴더·설계 파일 폴더 기준 상대)
  REST_FROM = "idle"   (선택) 기존 동작의 0초 자세를 기본 자세로 깔기. 팩 동작 키에 기본 자세 값이 들어 있을 때
  COMPARE   = "bump"   (선택) 수치 검사 때 같이 잴 팩 원래 공격 동작
  ANIMS = {"동작": dict(length=초, hits=[판정 시각…], desc="설명", loop="once", override=False, rest_from=None,
                        tracks={"뼈": {"rotation": [(t, [x, y, z]), …], "position": […]}})}

출력 (--out 기본값 = 설계 파일 폴더의 상위 폴더):
  <out>/모델/<모델>.bbmodel              원본 + 새 동작 (원본 파일은 고치지 않음)
  <out>/미리보기/<모델>/<동작>.gif         움직이는 미리보기, 판정 프레임은 빨간 테두리
  <out>/미리보기/<모델>/<동작>_장면.png     주요 장면 한 줄 (첫·끝·판정 프레임 포함). --side/--top 이면 _옆_/_위_ 도
  <out>/미리보기/<모델>/동작표.md          길이·판정 틱(MythicMobs delay 용)·수치 검사
종료 코드: 0 성공 · 2 입력 오류(이름 겹침 등) · 3 Blender 없음(모델·동작표만 씀)
"""
import argparse, glob, hashlib, importlib.util, json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True          # 설계 파일 옆에 __pycache__ 를 만들지 않음
import bbpose
import pose_check

FONTS = ["C:/Windows/Fonts/malgun.ttf", "/System/Library/Fonts/AppleSDGothicNeo.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]
VIEW_SUFFIX = {"front": "", "side": "_옆", "top": "_위"}


def find_blender(explicit=None):
    """--blender > BLENDER_EXE > 프로젝트 안 휴대용(작업/외부도구/blender-*) > 표준 설치 위치 > PATH."""
    cands = [explicit, os.environ.get("BLENDER_EXE")]
    d = os.getcwd()
    for _ in range(6):
        for pat in ("작업/외부도구/blender-*/blender*", "외부도구/blender-*/blender*", "tools/blender-*/blender*"):
            cands += sorted(glob.glob(os.path.join(d, pat)), reverse=True)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    cands += sorted(glob.glob("C:/Program Files/Blender Foundation/Blender */blender.exe"), reverse=True)
    cands += ["/Applications/Blender.app/Contents/MacOS/Blender", shutil.which("blender")]
    for c in cands:
        if c and os.path.isfile(c) and os.path.basename(c).lower() in ("blender.exe", "blender"):
            return os.path.abspath(c)
    return None


def resolve(path, spec_dir):
    if os.path.isabs(path):
        return path
    for base in (os.getcwd(), spec_dir):
        p = os.path.join(base, path)
        if os.path.exists(p):
            return os.path.abspath(p)
    return os.path.abspath(path)


def rel(p):
    """보고·동작표에 쓸 경로: 현재 폴더 안이면 상대 경로 (인계본에서도 깨지지 않게)."""
    r = os.path.relpath(p, os.getcwd())
    return p if r.startswith("..") else r.replace("\\", "/")


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def pick_frames(n, hit_idx, k=8):
    """장면 시트에 넣을 프레임: 고르게 k 장 + 판정 프레임 + 마지막 프레임(항상)."""
    if n <= 0:
        return []
    step = max(1, (n - 1) // (k - 1)) if k > 1 else n
    idx = set(range(0, n, step)) | {h for h in hit_idx if 0 <= h < n} | {n - 1}
    return sorted(idx)


def render(blender, model_path, anim, hits, outdir, fps, cell, view, label):
    """Blender 로 프레임을 그리고 GIF·장면 PNG 를 만든다. 성공하면 장면 PNG 경로."""
    from PIL import Image, ImageDraw, ImageFont
    tmp = os.path.join(outdir, "_frames", f"{anim}_{view}")
    shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    r = subprocess.run([blender, "-b", "--factory-startup", "--python", os.path.join(HERE, "blender_preview.py"), "--",
                        model_path, anim, tmp, str(fps), str(max(320, cell)), view],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    frames = sorted(glob.glob(os.path.join(tmp, f"{anim}_*.png")))
    if not frames:
        print(f"[렌더 실패] {anim} ({view})\n{(r.stdout or '')[-1200:]}\n{(r.stderr or '')[-800:]}")
        return None
    font = next((ImageFont.truetype(f, 17) for f in FONTS if os.path.exists(f)), None) or ImageFont.load_default()
    hit_idx = {}
    for h in hits:                                       # 판정 시각에 가장 가까운 프레임 → 정확한 시각을 함께 적음
        hit_idx.setdefault(min(len(frames) - 1, round(h * fps)), h)
    ims = []
    for i, f in enumerate(frames):
        im = Image.open(f).convert("RGB"); d = ImageDraw.Draw(im)
        txt = dict(font=font, stroke_width=3, stroke_fill=(0, 0, 0))
        d.text((8, 6), f"{label}  {i / fps:.2f}s", fill=(245, 245, 245), **txt)
        if i in hit_idx:
            d.rectangle([2, 2, im.width - 3, im.height - 3], outline=(235, 70, 60), width=5)
            d.text((8, 28), f"판정 {hit_idx[i]:.2f}s", fill=(255, 120, 100), **txt)
        ims.append(im)
    if view == "front":
        ims[0].save(os.path.join(outdir, f"{label}.gif"), save_all=True, append_images=ims[1:], duration=int(1000 / fps), loop=0)
    pick = pick_frames(len(ims), hit_idx)
    sheet = Image.new("RGB", (cell * len(pick), cell), (30, 32, 36))
    for j, i in enumerate(pick):
        sheet.paste(ims[i].resize((cell, cell)), (j * cell, 0))
    out = os.path.join(outdir, f"{label}{VIEW_SUFFIX[view]}_장면.png")
    sheet.save(out)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?")
    ap.add_argument("--existing", nargs=2, metavar=("MODEL", "ANIM"))
    ap.add_argument("--out"); ap.add_argument("--blender")
    ap.add_argument("--no-render", action="store_true"); ap.add_argument("--only")
    ap.add_argument("--side", action="store_true"); ap.add_argument("--top", action="store_true")
    ap.add_argument("--fps", type=int, default=20); ap.add_argument("--cell", type=int, default=300)
    ap.add_argument("--overwrite-existing", action="store_true")
    a = ap.parse_args(argv)
    views = ["front"] + (["side"] if a.side else []) + (["top"] if a.top else [])

    if a.existing:                      # 기존 동작 검증 렌더
        model_path, anim = os.path.abspath(a.existing[0]), a.existing[1]
        m = bbpose.Model(model_path)
        if anim not in m.animations():
            print(f"[오류] 동작 없음: {anim}. 있는 것: {', '.join(m.animations())}"); return 2
        blender = find_blender(a.blender)
        if not blender:
            print("[Blender 없음] --blender 또는 BLENDER_EXE 로 지정하세요 (SKILL.md 준비 단계)"); return 3
        name = os.path.splitext(os.path.basename(model_path))[0]
        outdir = os.path.join(os.path.abspath(a.out or os.getcwd()), "미리보기", name)
        os.makedirs(outdir, exist_ok=True)
        for view in views:
            print(f"검증 렌더: {rel(render(blender, model_path, anim, [], outdir, a.fps, a.cell, view, f'검증_{anim}') or '실패')}")
        shutil.rmtree(os.path.join(outdir, "_frames"), ignore_errors=True)
        return 0

    if not a.spec:
        ap.print_help(); return 2
    spec_path = os.path.abspath(a.spec)
    sp = importlib.util.spec_from_file_location("motion_spec", spec_path)
    mod = importlib.util.module_from_spec(sp); sp.loader.exec_module(mod)
    spec_dir = os.path.dirname(spec_path)
    base = resolve(mod.BASE, spec_dir)
    if not os.path.isfile(base):
        print(f"[오류] 원본 모델 없음: {base}"); return 2
    name = os.path.splitext(os.path.basename(base))[0]
    out_root = os.path.abspath(a.out or os.path.dirname(spec_dir))
    out_model = os.path.join(out_root, "모델", f"{name}.bbmodel")
    if os.path.normcase(out_model) == os.path.normcase(base):
        print("[오류] 출력이 원본과 같은 파일입니다. --out 을 바꾸세요."); return 2

    model = bbpose.Model(base)
    data = json.load(open(base, encoding="utf-8"))
    original = {x["name"] for x in data.get("animations", [])}
    clash = sorted(original & set(mod.ANIMS))
    if clash and not a.overwrite_existing:
        print(f"[오류] 원본에 이미 있는 동작 이름: {', '.join(clash)} — 이름을 바꾸거나, 사용자 허락 뒤 --overwrite-existing"); return 2
    before = sha(base)
    new = []
    try:
        for an, s in mod.ANIMS.items():
            tracks = s["tracks"]
            rest_from = s.get("rest_from", getattr(mod, "REST_FROM", None))
            if rest_from:
                tracks = bbpose.apply_rest(tracks, bbpose.rest_pose(model, rest_from), s["length"])
            new.append(bbpose.make_animation(model, an, s["length"], tracks, loop=s.get("loop", "once"),
                                             interp=s.get("interp", "catmullrom"), override=s.get("override", False), seed=name))
    except KeyError as e:
        print(f"[오류] {e}"); return 2
    data["animations"] = [x for x in data.get("animations", []) if x["name"] not in mod.ANIMS] + new
    os.makedirs(os.path.dirname(out_model), exist_ok=True)
    with open(out_model, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    assert sha(base) == before, "원본이 바뀜"           # 원본 보호 확인
    print(f"모델 저장: {rel(out_model)}  (원본 동작 {len(original)} + 새 동작 {len(new)})")

    # 수치 검사 (Blender 없이)
    copy = bbpose.Model(out_model)
    A = copy.animations()
    ref_name = getattr(mod, "REST_FROM", None) or ("idle" if "idle" in A else None)
    ref = A.get(ref_name) if ref_name else None
    cmp_name = getattr(mod, "COMPARE", None) or pose_check.default_compare(copy, exclude=set(mod.ANIMS))
    base_r = pose_check.measure(copy, A[cmp_name], ref) if cmp_name in A else None
    checks = {an: pose_check.measure(copy, A[an], ref) for an in mod.ANIMS}

    prev = os.path.join(out_root, "미리보기", name)
    os.makedirs(prev, exist_ok=True)
    rows = ["| 동작 | 길이(초) | 판정 시각(초) | 판정 지연(틱) | 덮어쓰기 | 설명 |", "|---|---|---|---|---|---|"]
    for an, s in mod.ANIMS.items():
        hs = s.get("hits", [])
        rows.append(f"| `{an}` | {s['length']} | {', '.join(str(h) for h in hs) or '-'} | "
                    f"{', '.join(str(round(h * 20)) for h in hs) or '-'} | {'O' if s.get('override') else ''} | {s.get('desc', '')} |")
    rows += ["", f"## 수치 검사 (기준 자세: {ref_name + ' 0초' if ref_name else '뼈 기본 자세'}, 단위 칸 = 1/16 블록)", "",
             "| 동작 | 바닥 아래(최저, 시각) | 몸 이동 | 발 끌림 | 시작·끝 끊김 | 판단 |", "|---|---|---|---|---|---|"]
    if base_r:
        rows.append(f"| `{cmp_name}` (팩 원래) | {base_r['floor']} ({base_r['floor_t']}초) | {base_r['travel']} | {base_r['slide']} ({base_r['slide_bone']}) | "
                    f"{base_r['start']} / {base_r['end']} | 기준 |")
    for an, r in checks.items():
        v = pose_check.verdict(r, base_r)
        rows.append(f"| `{an}` | {r['floor']} ({r['floor_t']}초) | {r['travel']} | {r['slide']} ({r['slide_bone']}) | {r['start']} / {r['end']} | "
                    f"{'주의: ' + ', '.join(v) if v else '좋음'} |")
        print(f"수치 검사 {an}: " + (", ".join(v) if v else "좋음"))
    with open(os.path.join(prev, "동작표.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# {name} 새 동작\n\n원본: `{rel(base)}`\n복사본: `{rel(out_model)}`\n\n" + "\n".join(rows) + "\n")
    if a.no_render:
        return 0
    blender = find_blender(a.blender)
    if not blender:
        print("[Blender 없음] 모델·동작표만 썼습니다. --blender 또는 BLENDER_EXE 로 지정하세요 (SKILL.md 준비 단계)"); return 3
    only = set(a.only.split(",")) if a.only else None
    for an, s in mod.ANIMS.items():
        if only and an not in only:
            continue
        for view in views:
            p = render(blender, out_model, an, s.get("hits", []), prev, a.fps, a.cell, view, an)
            if p:
                print(f"미리보기: {rel(p)}")
    shutil.rmtree(os.path.join(prev, "_frames"), ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
