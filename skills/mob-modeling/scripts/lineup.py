"""모델 라인업 렌더: 여러 몹을 인게임 크기로 나란히 세워 마인크래프트 엔티티 조명으로 그리고, 이름표·수치를 붙인다.

사용 (프로젝트 루트에서):
  python -I <스킬>/scripts/lineup.py --out <그림.png> [--title 제목] [--stats] [--ppb 110] [--per-row 6]
         [--bright 1.0] [--no-player] <모델> <모델> ...
  모델 = <모델ID 또는 .bbmodel 경로>[@배율][=이름표]
    - 모델ID 는 작업/모션/점검_원자료.json 의 파일을 먼저 쓰고, 없으면 작업/풀어본_자료 아래에서 찾는다.
    - 배율 = MythicMobs model{scale=} (없으면 1). 이름표가 없으면 모델 ID (예: skeleton_warrior=해골 전사).
  --stats : 이름표 아래에 큐브 수·색 수·명도·명암 폭·테두리 어두움을 적는다 (metrics.py 와 같은 값)
  --ppb   : 1블록 = 몇 px (모든 줄이 같은 배율 → 줄이 달라도 크기 비교가 맞다)
  --per-row: 한 줄에 세울 모델 수 (넘치면 다음 줄)
  --bright: 0.5 처럼 주면 어두운 던전 느낌 (조명 곱)
  --yaw 28: 모델을 돌리는 각도 (0 = 정면, 90 = 옆, 180 = 뒤)
  --back  : 줄마다 뒷모습 줄을 하나 더 (등 망토·뒤 장식 확인)
  --bind  : 대기 동작 첫 자세 대신 Blockbench 기본(bind) 자세로 세운다
"""
import glob, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import metrics  # noqa: E402


def find_blender():
    import skilldeps
    return skilldeps.find_blender()


def index():
    import skilldeps
    return {}, skilldeps.model_index()


def resolve(token, names=None, files=None):
    import skilldeps
    return skilldeps.resolve(token, files)


def render(specs, out, title="", stats=False, ppb=110, per_row=6, bright=1.0, player=True, yaw=28, back=False, views=None,
           rest=True):
    """specs: [dict(path, scale, key, label[, pose=[동작, 초]])] → 이름표 붙인 라인업 PNG.
    back=True 면 줄마다 뒷모습(yaw+180) 줄을 하나 더. views=[(각도, 표시[, 밝기]), …] 로 시점·밝기를 직접 줄 수도 있다.
    rest=True 면 동작을 안 준 몹은 대기 동작(idle) 0초 자세로 세운다(팩이 기본 자세를 idle 에 깔아 둔 경우 인게임 모습)."""
    import skilldeps
    global LABEL_PX
    try:                                   # 칸 폭 = 가장 긴 이름표(또는 수치 줄) + 여백 → 이름표끼리 겹치지 않게
        f22 = font(22)
        tags = [v[1] for v in (views or [])] + (["뒤"] if back else [])
        longest_tag = max([f22.getlength(f" ({t})") for t in tags if t] + [0])
        LABEL_PX = int(max([210] + [f22.getlength(s["label"]) + longest_tag + 24 for s in specs]))
    except Exception:
        LABEL_PX = 210
    for k, s in enumerate(specs):          # 같은 모델을 여러 번 세울 때 key 가 겹치지 않게
        if sum(1 for x in specs[:k] if x["key"] == s["key"]):
            s["key"] = f"{s['key']}#{k}"
    blender = find_blender()
    if not blender:
        raise SystemExit("Blender 를 찾지 못함 (blender-motion 스킬의 find_blender 규칙)")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    rows = []
    for r0 in range(0, len(specs), per_row):
        row = specs[r0:r0 + per_row]
        vlist = views or ([(yaw, ""), (yaw + 180, "뒤")] if back else [(yaw, "")])
        for vi, v in enumerate(vlist):
            view_yaw, tag = v[0], v[1]
            vb = v[2] if len(v) > 2 else bright
            raw = out[:-4] + f"_raw{r0}_{vi}.png"
            cfg = dict(models=[dict(path=s["path"], scale=s["scale"], key=s["key"], pose=s.get("pose")) for s in row],
                       out=raw, px_per_blk=ppb, bright=vb, player=player, label_px=LABEL_PX, yaw=view_yaw, rest=rest,
                       bbpose_dir=skilldeps.blender_motion_dir())
            with tempfile.TemporaryDirectory() as td:
                cp = os.path.join(td, "lineup.json")
                json.dump(cfg, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
                r = subprocess.run([blender, "-b", "--factory-startup", "--python", os.path.join(HERE, "lineup_blender.py"), "--", cp],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
            if "LINEUP_DONE" not in r.stdout or not os.path.isfile(raw):
                print(r.stdout[-3000:], r.stderr[-3000:])
                raise SystemExit("렌더 실패")
            info = json.load(open(raw + ".json", encoding="utf-8"))
            rows.append(label_row(raw, info, row, stats and vi == 0, tag))
            os.remove(raw); os.remove(raw + ".json")
    stack(rows, title, out)
    print("저장:", os.path.relpath(out))
    return out


def main(argv):
    out, title, stats, ppb, per_row, bright, player, yaw, back, rest = None, "", False, 110, 6, 1.0, True, 28.0, False, True
    items, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--out": out = argv[i + 1]; i += 2; continue
        if a == "--title": title = argv[i + 1]; i += 2; continue
        if a == "--ppb": ppb = float(argv[i + 1]); i += 2; continue
        if a == "--per-row": per_row = int(argv[i + 1]); i += 2; continue
        if a == "--bright": bright = float(argv[i + 1]); i += 2; continue
        if a == "--yaw": yaw = float(argv[i + 1]); i += 2; continue
        if a == "--back": back = True; i += 1; continue
        if a == "--bind": rest = False; i += 1; continue
        if a == "--stats": stats = True; i += 1; continue
        if a == "--no-player": player = False; i += 1; continue
        items.append(a); i += 1
    if not out or not items:
        raise SystemExit(__doc__)
    import skilldeps
    files = skilldeps.model_index()
    return render([skilldeps.resolve(t, files) for t in items], out, title, stats, ppb, per_row, bright, player, yaw, back,
                  rest=rest)


LABEL_PX = 210


def font(sz):
    from PIL import ImageFont
    for f in ("C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf", "/System/Library/Fonts/AppleSDGothicNeo.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"):
        if os.path.isfile(f):
            return ImageFont.truetype(f, sz)
    return ImageFont.load_default()


def label_row(raw, info, specs, stats, tag=""):
    """렌더 한 줄 + 아래 이름표 띠. tag='뒤' 면 이름표에 (뒤) 를 붙인다."""
    from PIL import Image, ImageDraw
    im = Image.open(raw).convert("RGB")
    W, H = im.size
    by_key = {s["key"]: s for s in specs}
    f_name, f_stat = font(22), font(15)
    band = 36 + (3 * 21 if stats else 0) + 10
    canvas = Image.new("RGB", (W, H + band), (24, 26, 31))
    canvas.paste(im, (0, 0))
    d = ImageDraw.Draw(canvas)
    for mi in info["models"]:
        x = mi["x"]
        if mi["key"] == "__player__":
            d.text((x, H + 10), "플레이어", font=f_stat, fill=(150, 155, 165), anchor="mt")
            d.text((x, H + 31), "1.8칸", font=f_stat, fill=(150, 155, 165), anchor="mt")
            continue
        s = by_key[mi["key"]]
        d.text((x, H + 6), s["label"] + (f" ({tag})" if tag else ""), font=f_name, fill=(240, 240, 245), anchor="mt")
        if stats:
            r = metrics.measure(s["path"], s["scale"])

            def f2(v, n=2):
                return "-" if v is None else f"{v:.{n}f}".lstrip("0")
            lines = [f"큐브 {r['cubes']} · 키 {mi['height_blk']:.1f} · {r.get('px_per_block') or 0:.0f}px",
                     f"{r.get('colors95')}색 · 명도 {f2(r.get('val'))} · 명암 {f2(r.get('contrast'))}",
                     f"노이즈 {f2(r.get('noise'), 3)} · 테두리 {f2(r.get('edge_dark'))}"]
            y = H + 36
            for ln in lines:
                d.text((x, y), ln, font=f_stat, fill=(170, 175, 185), anchor="mt")
                y += 21
    return canvas


def stack(rows, title, out):
    from PIL import Image, ImageDraw
    W = max(r.size[0] for r in rows)
    top = 52 if title else 0
    Ht = top + sum(r.size[1] for r in rows) + 8 * (len(rows) - 1)
    canvas = Image.new("RGB", (W, Ht), (24, 26, 31))
    d = ImageDraw.Draw(canvas)
    if title:
        d.text((16, 12), title, font=font(28), fill=(235, 235, 240))
    y = top
    for r in rows:
        canvas.paste(r, (0, y))
        y += r.size[1] + 8
    canvas.save(out)


if __name__ == "__main__":
    main(sys.argv[1:])
