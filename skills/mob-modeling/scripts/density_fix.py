"""텍셀 밀도 맞추기: 모든 본체 면을 같은 픽셀 밀도(기본 1 px/유닛 = 인게임 16 px/블록)로 다시 깐다.

사용 (프로젝트 루트에서):
  python -I <스킬>/scripts/density_fix.py <원본.bbmodel> <출력.bbmodel> [--scale 1.0] [--target 16]
  python -I <스킬>/scripts/density_fix.py --pow2 <원본.bbmodel> <출력.bbmodel>
  --scale : MythicMobs model{scale=}. 인게임 밀도 = 파일 밀도 × 16 ÷ scale 이므로 목표 파일 밀도 = target × scale ÷ 16
  --target: 목표 인게임 px/블록 (기본 16, 기준 파일 common.base_px_per_block 과 맞춘다)
  --pow2  : 밀도는 그대로 두고, 2의 거듭제곱이 아닌 텍스처의 캔버스만 늘린다 (UV 단위를 같이 늘려 보이는 모습은 그대로)
하는 일
- 지금 밀도가 목표보다 **촘촘하면**(픽셀이 잘게 보임): 면마다 목표 크기(면 길이 × 목표 밀도, 반올림)로 줄여 새 배치로.
  줄일 때는 블록마다 "블록 평균에 가장 가까운 원래 픽셀"을 고른다 → 새 색을 만들지 않아 팔레트가 유지된다.
  잔디테일(1~2px 눈·문양)은 사라질 수 있다 → 렌더로 보고 사람이 손볼 목록에 올린다.
- 지금 밀도가 목표보다 **성기면**(픽셀이 굵게 보임): 정수배 최근접 확대만 한다(보이는 모양은 그대로, 격자만 촘촘해짐).
  그 위에 reshade.py 로 1px 테두리·명암을 얹으면 1텍셀 구조가 생긴다. 1.2~1.8배 어긋남은 정수배로 못 맞춘다 → 다시 그리기·예외.
- **발광 짝 텍스처**(X.png 옆의 X_e.png, 면이 직접 쓰지 않는 OptiFine 식 발광 층)는 본체와 같은 배치·같은 원본 픽셀로
  함께 옮긴다 → 빛나는 자리가 어긋나지 않는다.
- 결과 텍스처는 2의 거듭제곱 크기, 면 UV 는 1:1 픽셀. 원본은 읽기만 한다.
"""
import argparse, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import bbtex, metrics, skilldeps  # noqa: E402
from kitbash import Packer, _num  # noqa: E402


emissive_pairs = bbtex.emissive_pairs          # (옛 이름 유지)


def face_box(face, tex, frame_h):
    """면 UV 가 덮는 픽셀 상자와 뒤집힘 (xa, xb, ya, yb, 가로뒤집힘, 세로뒤집힘)."""
    u0, v0, u1, v1 = [float(x) for x in face["uv"]]
    x0, x1 = sorted((u0 * tex["sx"], u1 * tex["sx"]))
    y0, y1 = sorted((v0 * tex["sy"], v1 * tex["sy"]))
    xa = int(math.floor(x0 + 1e-6)); xb = max(int(math.ceil(x1 - 1e-6)), xa + 1)
    ya = int(math.floor(y0 + 1e-6)); yb = max(int(math.ceil(y1 - 1e-6)), ya + 1)
    return xa, xb, ya, min(yb, frame_h), u0 > u1, v0 > v1


def patch_of(img, box):
    xa, xb, ya, yb, fx, fy = box
    p = img[ya:yb, xa:xb]
    if fx:
        p = p[:, ::-1]
    if fy:
        p = p[::-1, :]
    return p


def shrink(p, h, w):
    """대표색 축소: 각 목표 픽셀 = 해당 원본 블록에서 블록 평균에 가장 가까운 실제 픽셀.
    반환: (결과 RGBA, 고른 원본 좌표 [(j, i, y, x)]) — 발광 짝에 같은 좌표를 쓰려고."""
    H, W = p.shape[:2]
    out = np.zeros((h, w, 4), np.uint8)
    picks = []
    ys = np.linspace(0, H, h + 1); xs = np.linspace(0, W, w + 1)
    for j in range(h):
        y0, y1 = int(ys[j]), max(int(ys[j + 1]), int(ys[j]) + 1)
        for i in range(w):
            x0, x1 = int(xs[i]), max(int(xs[i + 1]), int(xs[i]) + 1)
            blk = p[y0:y1, x0:x1]
            flat = blk.reshape(-1, 4)
            vis = np.nonzero(flat[:, 3] >= 128)[0]
            if len(vis) * 2 < len(flat):          # 절반 넘게 투명 → 투명
                continue
            m = flat[vis, :3].astype(float).mean(0)
            k = vis[np.argmin(((flat[vis, :3] - m) ** 2).sum(1))]
            by, bx = divmod(int(k), blk.shape[1])
            out[j, i] = flat[k]
            picks.append((j, i, y0 + by, x0 + bx))
    return out, picks


def take(p, picks, h, w):
    out = np.zeros((h, w, 4), np.uint8)
    for j, i, y, x in picks:
        out[j, i] = p[y, x]
    return out


def grow(p, k):
    return np.repeat(np.repeat(p, k, axis=0), k, axis=1)


def next_pow2(n):
    return 1 << max(0, int(n - 1).bit_length())


def pad_pow2(src, dst, verbose=True):
    """2의 거듭제곱이 아닌 텍스처: 캔버스를 오른쪽·아래로 늘리고 UV 단위도 같은 비율로 늘린다 → 면 UV 는 그대로, 밀도 그대로."""
    m = bbtex.load(src)
    texs = bbtex.textures(m)
    changed = []
    for t in texs:
        if t["img"] is None:
            continue
        H1 = t["h"] // t["frames"]
        W2, H2 = next_pow2(t["w"]), next_pow2(H1)
        if (W2, H2) == (t["w"], H1):
            continue
        img = np.zeros((H2 * t["frames"], W2, 4), np.uint8)
        for f in range(t["frames"]):
            img[f * H2:f * H2 + H1, :t["w"]] = t["img"][f * H1:(f + 1) * H1]
        tj = m["textures"][t["idx"]]
        tj.update({"source": bbtex.encode_png(img), "width": W2, "height": H2 * t["frames"],
                   "uv_width": t["uv_w"] * W2 / t["w"], "uv_height": t["uv_h"] * H2 / H1})
        changed.append(f"{t['name']} {t['w']}x{H1}→{W2}x{H2}")
    bbtex.save(m, dst)
    if verbose:
        print("캔버스 늘림: " + (", ".join(changed) if changed else "없음 (모두 2의 거듭제곱)"))
    return dst


def fix(src, dst, scale=1.0, target=16.0, verbose=True):
    bbpose = skilldeps.bbpose()
    M = bbpose.Model(src)
    m = M.m
    texs = bbtex.textures(m)
    pairs = emissive_pairs(texs)
    cur = metrics.measure(src, scale)
    want = target * scale / 16.0                      # 목표 파일 밀도 (px/유닛)
    have = cur["px_per_unit"]
    if not have:
        raise SystemExit("밀도를 잴 수 없음 (텍스처·UV 없음)")
    ratio = want / have
    mode = "shrink" if ratio < 0.9 else ("grow" if ratio > 1.1 else "keep")
    if mode == "keep":
        if verbose:
            print(f"이미 목표 범위 ({cur['px_per_block']} px/블록) — 바꾸지 않음")
        bbtex.save(m, dst)
        return dst
    if mode == "grow":
        k = int(round(ratio))
        if k < 2 or abs(ratio / k - 1) > 0.25:
            raise SystemExit(f"성긴 밀도 {cur['px_per_block']} px/블록 — 목표와 {ratio:.2f}배 차이라 정수배 확대로 못 맞춤. "
                             f"다시 그리기(사람·GPT) 또는 예외 승인 (references/common-rules.md 6장)")
        for i, t in enumerate(texs):                  # 발광 짝도 같은 배율 → 정렬 유지
            if t["img"] is None:
                continue
            m["textures"][i]["source"] = bbtex.encode_png(grow(t["img"], k))
            for key in ("width", "height"):
                if key in m["textures"][i]:
                    m["textures"][i][key] = int(m["textures"][i][key]) * k
        bbtex.save(m, dst)
        if verbose:
            print(f"성긴 밀도 {cur['px_per_block']} px/블록 → 텍스처만 {k}배 최근접 확대 (보이는 모양 그대로). "
                  f"이제 reshade.py 로 1px 구조를 얹거나 사람이 다시 그린다")
        return dst
    # shrink: 면마다 새 크기로 다시 배치
    jobs = []
    for eu in bbtex.body_elements(M, show_vfx=True):
        e = M.elements[eu]
        sz = np.abs(np.array(e["to"], float) - np.array(e["from"], float))
        for fname, f in (e.get("faces") or {}).items():
            if fname not in bbtex.FACE_AXES or not f.get("uv"):
                continue
            ti = bbtex.face_tex(f, texs)
            if ti is None or texs[ti]["img"] is None:
                continue
            au, av = bbtex.FACE_AXES[fname]
            gw, gh = sz[au], sz[av]
            if int(f.get("rotation", 0) or 0) % 180 == 90:
                gw, gh = gh, gw
            w, h = max(1, int(round(gw * want))), max(1, int(round(gh * want)))
            key = (ti, tuple(f["uv"]), w, h)
            jobs.append((f, ti, key, w, h))
    # 텍스처마다 따로 다시 깐다 (이름·순서 그대로). 발광 짝은 같은 배치·같은 원본 픽셀로.
    total, paired = 0, []
    for ti in sorted({j[1] for j in jobs}):
        mine = [j for j in jobs if j[1] == ti]
        keys = sorted({j[2] for j in mine}, key=lambda k: (-k[3], -k[2]))
        size = 16
        while True:          # 2의 거듭제곱 정사각 텍스처에 다 들어갈 때까지 키운다
            pk, place, ok = Packer(size, size), {}, True
            for key in keys:
                r = pk.alloc(key[2], key[3])
                if r is None:
                    ok = False
                    break
                place[key] = r
            if ok:
                break
            size *= 2
        t = texs[ti]
        fh = t["img"].shape[0] // t["frames"]
        te = texs[pairs[ti]] if ti in pairs else None
        atlas = np.zeros((size, size, 4), np.uint8)
        atlas_e = np.zeros((size, size, 4), np.uint8) if te is not None else None
        done = set()
        for (f, _ti, key, w, h) in mine:
            if key not in done:
                box = face_box(f, t, fh)
                p = patch_of(t["img"], box)
                if p.size:
                    x, y = place[key]
                    res, picks = shrink(p, h, w)
                    atlas[y:y + h, x:x + w] = res
                    if te is not None:
                        atlas_e[y:y + h, x:x + w] = take(patch_of(te["img"], box), picks, h, w)
                done.add(key)
            x, y = place[key]
            f["uv"] = [x, y, x + w, y + h]
        total += len(done)
        # UV 크기는 원래 값 그대로 두고 면 좌표만 비례로 (텍스처마다 UV 크기를 바꾸는 것은 ModelEngine 문서에 없다)
        kx, ky = t["uv_w"] / size, t["uv_h"] / size
        for (f, _ti, key, w, h) in mine:
            u0, v0, u1, v1 = f["uv"]
            f["uv"] = [_num(u0 * kx), _num(v0 * ky), _num(u1 * kx), _num(v1 * ky)]
        m["textures"][ti].update({"width": size, "height": size, "source": bbtex.encode_png(atlas)})
        if te is not None:
            m["textures"][te["idx"]].update({"width": size, "height": size, "source": bbtex.encode_png(atlas_e)})
            paired.append(te["name"])
        if t["frames"] > 1 and verbose:
            print(f"주의: 텍스처 '{t['name']}' 는 애니메이션 프레임 {t['frames']}장 → 첫 프레임만 옮김")
    for e in m.get("elements", []):
        e["box_uv"] = False
    m.setdefault("meta", {})["box_uv"] = False
    bbtex.save(m, dst)
    after = metrics.measure(dst, scale)
    if verbose:
        print(f"촘촘한 밀도 {cur['px_per_block']} → {after['px_per_block']} px/블록, 면 {total}개 다시 배치, "
              f"텍스처 {', '.join(after['textures'])}, 색95 {cur.get('colors95')} → {after.get('colors95')}"
              + (f", 발광 짝 같이 옮김: {', '.join(paired)}" if paired else ""))
    return dst


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--target", type=float, default=16.0)
    ap.add_argument("--pow2", action="store_true")
    a = ap.parse_args(argv)
    if os.path.abspath(a.src) == os.path.abspath(a.dst):
        raise SystemExit("원본에 덮어쓰지 않는다")
    if a.pow2:
        pad_pow2(a.src, a.dst)
    else:
        fix(a.src, a.dst, a.scale, a.target)


if __name__ == "__main__":
    main(sys.argv[1:])
