"""모델 스타일 수치 (조사 노트 our_models_audit 과 같은 정의) + 아트 기준 판정 (1층 공통 + 2층 스타일 프로필).

사용: python -I metrics.py <모델ID 또는 .bbmodel>[@배율] ... [--profile 이름] [--role 일반|정예|보스|소환] [--theme dark]
                          [--std 기준.json] [--json 출력.json]
  기준 파일 찾기: skilldeps.std_path (--std > MOB_MODELING_STD > 작업/모델링/기준.json > modeling/art_standard.json
                  > art_standard.json > 스킬 templates/art_standard.json)
  배율 = MythicMobs model{scale=} (없으면 1). 인게임 px/블록 = 밀도 × 16 ÷ 배율.
출력: 모델마다 큐브 수, 키(블록), px/블록, 색 수(95%), 명도 V, 명암 폭, 노이즈, 테두리 어두움, 위밝음과
      기준 범위를 벗어난 항목.
"""
import json, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import bbtex  # noqa: E402
import skilldeps  # noqa: E402


def _bbpose():
    import skilldeps
    return skilldeps.bbpose()


def _wmedian(v, w):
    if not len(v):
        return float("nan")
    o = np.argsort(v)
    v, w = np.asarray(v)[o], np.asarray(w)[o]
    c = np.cumsum(w) / np.sum(w)
    return float(v[np.searchsorted(c, 0.5)])


def measure(path, scale=1.0, show_vfx=False):
    bbpose = _bbpose()
    model = bbpose.Model(path)
    m = model.m
    texs = bbtex.textures(m)
    elems = bbtex.body_elements(model, show_vfx)
    gm = model.group_mats()
    corners = []
    dens_v, dens_w = [], []
    rects = []
    for eu in elems:
        e = model.elements[eu]
        a, b = np.array(e["from"], float), np.array(e["to"], float)
        inf = e.get("inflate", 0) or 0
        lo, hi = np.minimum(a, b) - inf, np.maximum(a, b) + inf
        M = model.element_mat(eu, gm)
        for cx in (lo[0], hi[0]):
            for cy in (lo[1], hi[1]):
                for cz in (lo[2], hi[2]):
                    corners.append((M @ np.array([cx, cy, cz, 1.0]))[:3])
        sz = np.abs(b - a)
        for fname, f in (e.get("faces") or {}).items():
            if fname not in bbtex.FACE_AXES or not f.get("uv"):
                continue
            ti = bbtex.face_tex(f, texs)
            if ti is None:
                continue
            tx = texs[ti]
            au, av = bbtex.FACE_AXES[fname]
            gw, gh = sz[au], sz[av]
            rot = int(f.get("rotation", 0) or 0) % 360
            if rot in (90, 270):
                gw, gh = gh, gw
            area = gw * gh
            if area < 1e-6:
                continue
            uv = f["uv"]
            du, dv = abs(uv[2] - uv[0]) * tx["sx"], abs(uv[3] - uv[1]) * tx["sy"]
            if gw > 1e-6 and du > 1e-6:
                dens_v.append(du / gw); dens_w.append(area / 2)
            if gh > 1e-6 and dv > 1e-6:
                dens_v.append(dv / gh); dens_w.append(area / 2)
            rects.append((ti,) + bbtex.face_rect(f, tx) + (fname, rot, uv[1] > uv[3]))
    corners = np.array(corners) if corners else np.zeros((1, 3))
    ext = (corners.max(0) - corners.min(0)) / 16.0 * scale
    dens = _wmedian(dens_v, dens_w)
    if dens_v and dens == dens:
        dv, dw = np.array(dens_v), np.array(dens_w)
        mixel = float(dw[(dv / dens < 0.8) | (dv / dens > 1.25)].sum() / dw.sum())
    else:
        mixel = None

    # 색 (본체 면이 덮는 픽셀만)
    pix, pairs_n, dl_sum = [], 0, 0.0
    edge, grads, seen = [], [], set()
    for tx in texs:
        if tx["img"] is None:
            continue
        H = tx["img"].shape[0] // tx["frames"]
        img = tx["img"][:H]
        mk = np.zeros(img.shape[:2], bool)
        for (ti, x0, y0, x1, y1, *_r) in rects:
            if ti == tx["idx"]:
                mk[y0:y1 + 1, x0:x1 + 1] = True
        vis = mk & (img[..., 3] >= 16)
        if not vis.any():
            continue
        rgb = img[..., :3].astype(float) / 255.0
        L = bbtex.luma(rgb)
        pix.append(img[vis][:, :3])
        for a_, b_, d in ((vis[:, :-1], vis[:, 1:], np.abs(L[:, :-1] - L[:, 1:])),
                          (vis[:-1, :], vis[1:, :], np.abs(L[:-1, :] - L[1:, :]))):
            va = a_ & b_
            pairs_n += int(va.sum()); dl_sum += float(d[va].sum())
        alpha = img[..., 3] >= 16
        for (ti, x0, y0, x1, y1, fname, rot, flip) in rects:
            if ti != tx["idx"] or (ti, x0, y0, x1, y1) in seen:
                continue
            seen.add((ti, x0, y0, x1, y1))
            if x1 - x0 + 1 < 4 or y1 - y0 + 1 < 4 or y1 >= H or x1 >= img.shape[1]:
                continue
            sub, al = L[y0:y1 + 1, x0:x1 + 1], alpha[y0:y1 + 1, x0:x1 + 1]
            ring = np.zeros_like(al); ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
            if (al & ring).sum() >= 4 and (al & ~ring).sum() >= 4:
                li, lb = sub[al & ~ring].mean(), sub[al & ring].mean()
                edge.append((li - lb) / max(li, 0.05))
            if fname in bbtex.SIDE and rot == 0:
                hh = sub.shape[0] // 2
                top, bot = (sub[:hh], sub[-hh:]) if not flip else (sub[-hh:], sub[:hh])
                at, ab = (al[:hh], al[-hh:]) if not flip else (al[-hh:], al[:hh])
                if at.sum() >= 2 and ab.sum() >= 2:
                    grads.append((top[at].mean() - bot[ab].mean()) / max(sub[al].mean(), 0.05))
    flipped = sum(1 for eu in elems if bbtex.is_flipped(model.elements[eu]))
    out = dict(file=os.path.basename(path), scale=scale, cubes=len(elems) - flipped, flipped=flipped, bones=len(model.groups),
               height_blk=round(float(ext[1]), 2), max_blk=round(float(ext.max()), 2),
               px_per_unit=round(dens, 3) if dens == dens else None,
               px_per_block=round(dens * 16 / scale, 1) if dens == dens else None,
               mixel_share=round(mixel, 3) if mixel is not None else None,
               textures=[f"{t['name']}:{t['w']}x{t['h'] // t['frames']}" for t in texs])
    if pix:
        P = np.concatenate(pix).astype(np.int64)
        codes = (P[:, 0] << 16) | (P[:, 1] << 8) | P[:, 2]
        _, cnt = np.unique(codes, return_counts=True)
        cs = np.cumsum(np.sort(cnt)[::-1]) / cnt.sum()
        rgb = P / 255.0
        mx, mn = rgb.max(1), rgb.min(1)
        S = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
        Lp = bbtex.luma(rgb)
        out.update(colors=int(len(cnt)), colors95=int(np.searchsorted(cs, 0.95) + 1),
                   black_share=round(float((mx < 0.04).mean()), 4),
                   val=round(float(mx.mean()), 3), sat=round(float(S.mean()), 3),
                   contrast=round(float(Lp.std()), 3), noise=round(dl_sum / pairs_n, 4) if pairs_n else None)
    if edge:
        ed = np.array(edge)
        out.update(edge_dark=round(float(np.mean(ed > 0.08)), 3), edge_dark_med=round(float(np.median(ed)), 3))
    if grads:
        out.update(top_light=round(float(np.median(grads)), 3))
    out["cubes_per_blk"] = round(out["cubes"] / max(out["height_blk"], 0.25), 1)
    return out


def ranges_for(std, theme=None, profile=None, role=None):
    """(옛 이름 유지) 스타일 범위. 옛 형식 기준 dict 도 받는다."""
    import standard
    d = standard.convert(std)
    prof = profile or d.get("default_profile")
    return standard.ranges(d, prof, role, theme) if prof else {}


def judge(r, std, theme=None, profile=None, role=None, model_id=None):
    """2층 스타일 판정 메시지 목록 (옛 형식 기준 dict 도 받는다)."""
    import standard
    _, msgs = standard.judge_style(r, standard.convert(std), model_id, profile, role, theme)
    return msgs


COLS = [("cubes", "큐브"), ("height_blk", "키"), ("cubes_per_blk", "큐브/키"), ("px_per_block", "px/블록"),
        ("colors95", "색95"), ("val", "명도"), ("contrast", "명암폭"), ("noise", "노이즈"), ("edge_dark", "테두리"),
        ("top_light", "위밝음")]


def main(argv):
    import standard
    opts = {"--std": None, "--json": None, "--theme": None, "--profile": None, "--role": None}
    items, i = [], 0
    while i < len(argv):
        if argv[i] in opts:
            opts[argv[i]] = argv[i + 1]; i += 2; continue
        items.append(argv[i]); i += 1
    d, used = standard.load(opts["--std"])
    print(f"(아트 기준: {used or '없음 — 1층 공통 규칙만'})")
    rows = []
    files = skilldeps.model_index()
    for it in items:
        sp = skilldeps.resolve(it, files)
        r = measure(sp["path"], sp["scale"])
        mid = os.path.splitext(os.path.basename(sp["path"]))[0]
        prof, sty = standard.judge_style(r, d, mid, opts["--profile"], opts["--role"], opts["--theme"])
        com = [m for _, m, ex in standard.judge_common(r, d, mid, opts["--role"] or standard.entry(d, mid).get("role")) if not ex]
        r["profile"] = prof
        r["off"] = [f"[공통] {m}" for m in com] + [f"[{prof}] {m}" for m in sty]
        rows.append(r)
    head = "| 모델 | 프로필 | " + " | ".join(h for _, h in COLS) + " | 기준 밖 |"
    print(head); print("|" + "---|" * (len(COLS) + 3))
    for r in rows:
        print(f"| {r['file']} | {r.get('profile') or '-'} | " + " | ".join(str(r.get(k, "")) for k, _ in COLS)
              + f" | {'; '.join(r['off']) or '-'} |")
    if opts["--json"]:
        json.dump(rows, open(opts["--json"], "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
