"""텍스처 다시 칠하기 (형태는 그대로, 텍스처만): 재질별 색 계단(ramp)으로 명암·테두리·결을 아트 기준에 맞춘다.

사용 (프로젝트 루트에서):
  python -I <스킬>/scripts/reshade.py <원본.bbmodel> <출력.bbmodel> [옵션]
옵션 (목표 --val·--contrast 는 그 몹 스타일 프로필 범위 안에서 고른다: lint.py 의 [스타일] 줄):
  --val 0.58       목표 평균 명도(V). 어두운 테마는 0.36 정도
  --contrast 0.18  목표 명암 폭(밝기 표준편차)
  --steps 6        재질마다 색 계단 수 (색 수 = 재질 수 × 계단 수)
  --edge 1.5       면 테두리 1px(와 찢어진 구멍 둘레)를 계단 몇 칸만큼 어둡게 (0 이면 안 함)
  --grad 0.16      옆면 위→아래 밝기 차 (위가 밝음)
  --noise 0.06     픽셀 결 (1~2px 얼룩, 계단에 맞춰 떨어짐)
  --center 0.10    5px 이상 면의 가운데를 조금 밝게 (테두리 있는 스타일. 평면 스타일이면 0)
  --keep 0.75      원래 명암을 얼마나 살릴지 (1 = 원래 무늬 그대로, 0 = 구조 음영만)
  --tint #RRGGBB:0.2  모든 재질 색을 던전 테마 색 쪽으로 조금 당김 (선택, adjust·ramp 둘 다)
  --seed 1         결 무늬 시드 (같은 시드 → 같은 결과)
  --protect #e07a1f,#ffd34a  이 색과 가까운 재질은 다시 칠하지 않는다 (몹의 상징색: 호박 주황, 눈빛 등)
  --sat 0.40       (선택) 목표 평균 채도 — adjust 는 어두워지는 곳의 채도를 조금 올리므로 프로필 채도 상한에 걸리면 준다
  --mode adjust    기본: 원래 색상을 두고 밝기 위주로 조정(어두운 곳 채도 +25%, 밝은 곳 −15% 까지) → 상징색이 바뀌지 않는다
         ramp      재질마다 새 색 계단으로 다시 칠함 (단조로운 텍스처: 색95 ≤ 6 또는 명암 폭 < 0.08. 색이 바뀔 수 있음 → 렌더로 꼭 확인)
원본은 읽기만 한다. 출력은 원본 복사본에 텍스처만 바꾼 것.
"""
import argparse, colorsys, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import bbtex  # noqa: E402
import metrics  # noqa: E402

# ── OKLab (지각 균일 색공간) ─────────────────────────
def srgb_to_lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def to_oklab(rgb):
    r, g, b = np.moveaxis(srgb_to_lin(rgb), -1, 0)
    l = np.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = np.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = np.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return np.stack([0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                     1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                     0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s], -1)


def from_oklab(lab):
    L, a, b = np.moveaxis(lab, -1, 0)
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = np.stack([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s], -1)
    return lin_to_srgb(rgb)


def in_gamut(L, C, h):
    """채도 C 를 sRGB 안에 들어올 때까지 줄인 (L, a, b)."""
    for _ in range(24):
        lab = np.array([L, C * math.cos(h), C * math.sin(h)])
        rgb = _lab_to_lin(lab)
        if (rgb >= -1e-4).all() and (rgb <= 1 + 1e-4).all():
            return lab
        C *= 0.85
    return np.array([L, 0.0, 0.0])


def _lab_to_lin(lab):
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return np.array([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                     -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                     -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s])


def _hex_lab(h):
    h = h.strip("#")
    rgb = np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])
    return to_oklab(rgb[None])[0]


def lerp_angle(a, b, t):
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return a + d * t


SHADOW_H, LIGHT_H = math.radians(285), math.radians(85)   # 그림자는 보라·파랑 쪽, 밝은 곳은 노랑 쪽


def ramp(base_lab, center, half, steps, tint=None, hue=1.0):
    """재질 하나의 색 계단: 어두운 → 밝은. 색상은 그림자 쪽으로 푸르게, 밝은 쪽으로 따뜻하게 조금 옮긴다.
    hue: 그 옮기는 정도 (1 = 기본, 0 = 색상·채도 그대로 밝기만 — 팔레트가 좁은 평면 스타일 팩)."""
    L0, a0, b0 = base_lab
    C0 = math.hypot(a0, b0)
    h0 = math.atan2(b0, a0) if C0 > 1e-6 else SHADOW_H
    if tint is not None:
        tl, tC, th, amt = tint
        h0 = lerp_angle(h0, th, amt) if C0 > 0.02 else th
        C0 = C0 * (1 - amt) + tC * amt
    out = []
    for i in range(steps):
        u = i / (steps - 1) * 2 - 1                      # -1 그림자 … +1 밝음
        L = float(np.clip(center + half * u, 0.08, 0.97))
        if u < 0:
            h = lerp_angle(h0, SHADOW_H, 0.28 * -u * hue)
            C = (C0 if (C0 > 0.02 or hue == 0) else 0.018 * hue) * (1 + 0.25 * -u * hue)
        else:
            h = lerp_angle(h0, LIGHT_H, 0.18 * u * hue)
            C = (C0 if (C0 > 0.02 or hue == 0) else 0.010 * hue) * (1 - 0.15 * u * hue)
        out.append(in_gamut(L, C, h))
    return np.array(out)


def hash_noise(h, w, seed):
    y, x = np.mgrid[0:h, 0:w]
    v = np.sin((x * 12.9898 + y * 78.233 + seed * 37.719)) * 43758.5453
    return (v - np.floor(v)) * 2 - 1


def maps_for(model, texs, elems):
    """텍스처마다 면 정보 지도: 테두리, 옆면 세로 위치(0 위 … 1 아래), 윗면/아랫면."""
    out = {}
    for tx in texs:
        if tx["img"] is None:
            continue
        H = tx["img"].shape[0] // tx["frames"]; W = tx["img"].shape[1]
        out[tx["idx"]] = dict(face=np.zeros((H, W), bool), border=np.zeros((H, W), bool),
                              ypos=np.full((H, W), np.nan), updown=np.zeros((H, W)), center=np.zeros((H, W)))
    seen = set()
    for eu in elems:
        e = model.elements[eu]
        for fname, f in (e.get("faces") or {}).items():
            if fname not in bbtex.FACE_AXES or not f.get("uv"):
                continue
            ti = bbtex.face_tex(f, texs)
            if ti is None or ti not in out:
                continue
            x0, y0, x1, y1 = bbtex.face_rect(f, texs[ti])
            mp = out[ti]
            H, W = mp["face"].shape
            if x0 >= W or y0 >= H:
                continue
            x1, y1 = min(x1, W - 1), min(y1, H - 1)
            key = (ti, x0, y0, x1, y1)
            mp["face"][y0:y1 + 1, x0:x1 + 1] = True
            if key in seen:
                continue
            seen.add(key)
            if x1 - x0 + 1 >= 4 and y1 - y0 + 1 >= 4:
                mp["border"][y0, x0:x1 + 1] = mp["border"][y1, x0:x1 + 1] = True
                mp["border"][y0:y1 + 1, x0] = mp["border"][y0:y1 + 1, x1] = True
            if x1 - x0 + 1 >= 5 and y1 - y0 + 1 >= 5:      # 가운데로 갈수록 0 → 1
                yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1]
                d = np.minimum(np.minimum(xx - x0, x1 - xx), np.minimum(yy - y0, y1 - yy)).astype(float)
                mp["center"][y0:y1 + 1, x0:x1 + 1] = np.maximum(mp["center"][y0:y1 + 1, x0:x1 + 1], np.clip((d - 1) / 3, 0, 1))
            rot = int(f.get("rotation", 0) or 0) % 360
            if fname in bbtex.SIDE and rot in (0, 180) and y1 > y0:
                yy = (np.arange(y0, y1 + 1) - y0) / (y1 - y0)
                if (f["uv"][1] > f["uv"][3]) != (rot == 180):
                    yy = 1 - yy
                mp["ypos"][y0:y1 + 1, x0:x1 + 1] = yy[:, None]
            elif fname == "up":
                mp["updown"][y0:y1 + 1, x0:x1 + 1] = 1
            elif fname == "down":
                mp["updown"][y0:y1 + 1, x0:x1 + 1] = -1
    return out


def materials(lab, k_max=6, merge=0.035):
    """재질 묶기: 무채색 하나 + 유채색은 (a, b) 평면 k-means (색상과 채도를 함께 봄 → 밀짚 베이지와 호박 주황이 갈린다).
    가까운 묶음(중심 거리 < merge)은 합친다. 반환: 픽셀별 재질 번호, 재질별 대표 색, 재질별 마스크."""
    C = np.hypot(lab[:, 1], lab[:, 2])
    chroma = C > 0.025
    groups = []
    if (~chroma).any():
        groups.append(~chroma)
    if chroma.any():
        ab = lab[chroma][:, 1:3]
        uq = np.unique(np.round(ab, 3), axis=0)
        k = int(min(k_max, len(uq)))
        rng = np.random.default_rng(7)
        cent = [ab[rng.integers(len(ab))]]
        for _ in range(1, k):                                   # k-means++ 시작점
            d = np.min([((ab - c) ** 2).sum(1) for c in cent], 0)
            if d.sum() <= 0:
                break
            cent.append(ab[rng.choice(len(ab), p=d / d.sum())])
        cent = np.array(cent)
        for _ in range(30):
            lbl = np.argmin(((ab[:, None, :] - cent[None]) ** 2).sum(2), 1)
            new = np.array([ab[lbl == j].mean(0) if (lbl == j).any() else cent[j] for j in range(len(cent))])
            if np.allclose(new, cent, atol=1e-5):
                break
            cent = new
        # 가까운 중심 합치기
        alias = list(range(len(cent)))
        for i in range(len(cent)):
            for j in range(i):
                if np.linalg.norm(cent[i] - cent[j]) < merge:
                    alias[i] = alias[j]
                    break
        lbl = np.array([alias[x] for x in lbl])
        idx = np.where(chroma)[0]
        for j in sorted(set(lbl)):
            g = np.zeros(len(lab), bool); g[idx[lbl == j]] = True
            groups.append(g)
    lab_id = np.zeros(len(lab), int)
    reps = []
    for gi, g in enumerate(groups):
        lab_id[g] = gi
        reps.append(np.median(lab[g], 0))
    return lab_id, np.array(reps), groups


def reshade(path_in, path_out, val=0.58, contrast=0.18, steps=6, edge=1.0, grad=0.16, noise=0.06, keep=0.75,
            tint=None, seed=1, center=0.10, protect=(), mode="adjust", sat=None, verbose=True):
    bbpose = metrics._bbpose()
    model = bbpose.Model(path_in)
    m = model.m
    texs = bbtex.textures(m)
    elems = bbtex.body_elements(model)
    maps = maps_for(model, texs, elems)
    tint_p = None
    if tint:
        hexs, _, amt = tint.partition(":")
        rgb = np.array([int(hexs.strip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)])
        L, a, b = to_oklab(rgb[None])[0]
        tint_p = (L, math.hypot(a, b), math.atan2(b, a), float(amt or 0.2))

    # 모델 전체 본체 픽셀 모으기 (모든 텍스처)
    pix = []
    for ti, mp in maps.items():
        tx = texs[ti]
        H = mp["face"].shape[0]
        img = tx["img"][:H]
        vis = mp["face"] & (img[..., 3] >= 16)
        pix.append((ti, vis))
    all_rgb = np.concatenate([texs[ti]["img"][:maps[ti]["face"].shape[0]][vis][:, :3] for ti, vis in pix]) / 255.0
    lab_all = to_oklab(all_rgb)
    mid, reps, groups = materials(lab_all)
    # 강조색 (작고 진한 묶음: 눈·보석·불빛) 은 그대로 둔다
    keep_group = []
    prot = [_hex_lab(h) for h in protect]
    for gi, g in enumerate(groups):
        C = math.hypot(reps[gi][1], reps[gi][2])
        near = any(np.linalg.norm(reps[gi][1:] - p[1:]) < 0.05 for p in prot)   # 지킬 색과 가까운 재질은 그대로
        keep_group.append((C > 0.05 and g.mean() < 0.05) or near)
    # 재질별 원래 밝기 → 0..1 (5~95% 구간)
    t_all = np.zeros(len(lab_all))
    for gi, g in enumerate(groups):
        Lg = lab_all[g, 0]
        lo, hi = np.percentile(Lg, 5), np.percentile(Lg, 95)
        t_all[g] = np.clip((Lg - lo) / max(hi - lo, 0.04), 0, 1) if hi - lo > 0.015 else 0.5

    # 구조 음영 (테두리·위아래·결). 단위: 1.0 = 계단 전체, 계단 한 칸 = 1/(steps-1)
    step = 1.0 / max(steps - 1, 1)
    struct, details = [], []
    for ti, vis in pix:
        mp = maps[ti]
        img = texs[ti]["img"][:mp["face"].shape[0]]
        alpha = img[..., 3] >= 16
        # 투명 구멍 옆 픽셀도 테두리로 (찢어진 천 가장자리)
        hole = ~alpha
        nb = np.zeros_like(alpha)
        nb[1:, :] |= hole[:-1, :]; nb[:-1, :] |= hole[1:, :]; nb[:, 1:] |= hole[:, :-1]; nb[:, :-1] |= hole[:, 1:]
        rim = mp["border"] & vis                       # 면 사각형 테두리
        cut = nb & mp["face"] & vis & ~rim             # 투명 구멍 둘레 (찢어진 천)
        yp = mp["ypos"]
        g = np.where(np.isnan(yp), 0.0, (0.5 - np.nan_to_num(yp, nan=0.5)) * grad)   # 위 +grad/2 … 아래 -grad/2
        ud = mp["updown"] * 0.5 * step                                                # 윗면 반 칸 밝게, 아랫면 반 칸 어둡게
        hh, ww = mp["face"].shape
        n1 = hash_noise(hh, ww, seed + ti * 101)
        n2 = np.kron(hash_noise((hh + 1) // 2, (ww + 1) // 2, seed + ti * 101 + 7), np.ones((2, 2)))[:hh, :ww]
        nz = (0.4 * n1 + 0.6 * n2) * noise / 2                                        # 1~2px 얼룩
        s = g + ud + nz - rim * edge * step - cut * 0.5 * edge * step + mp["center"] * center
        # 그려진 디테일(눈·입·금·무늬): 주변 3×3 중앙값보다 확 다른 픽셀은 구조 음영을 약하게 → 원래 명암이 남는다
        Lmap = np.full((hh, ww), np.nan)
        Lmap[vis] = to_oklab(img[..., :3][vis] / 255.0)[:, 0]
        pad = np.pad(Lmap, 1, constant_values=np.nan)
        nbh = np.stack([pad[dy:dy + hh, dx:dx + ww] for dy in range(3) for dx in range(3)])
        import warnings
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(nbh, 0)
        detail = vis & (np.abs(np.nan_to_num(Lmap - med)) > 0.06)
        s = np.where(detail, s * 0.3, s)
        details.append(detail[vis])
        struct.append(s[vis])
    struct = np.concatenate(struct)
    detail_all = np.concatenate(details)

    STEP_L = 0.065                      # 계단 한 칸 ≈ OKLab 밝기 0.065

    def quantize(lab, n):
        """재질 하나의 색을 n 개로 모은다 (k-means, OKLab) → 픽셀아트처럼 색 수 제한."""
        u, inv = np.unique(np.round(lab, 4), axis=0, return_inverse=True)
        if len(u) <= n:
            return lab
        rng = np.random.default_rng(3)
        cent = u[rng.choice(len(u), n, replace=False)]
        for _ in range(25):
            lbl = np.argmin(((lab[:, None, :] - cent[None]) ** 2).sum(2), 1)
            new = np.array([lab[lbl == j].mean(0) if (lbl == j).any() else cent[j] for j in range(n)])
            if np.allclose(new, cent, atol=1e-5):
                break
            cent = new
        return cent[lbl]

    def render_adjust(shift, k, csc=1.0):
        """원래 색을 두고 밝기를 바꾼다: 대비 k 배 + 이동 + 구조 음영(계단 단위).
        어두워지는 곳은 살짝 푸르고 진하게(채도 +), 밝아지는 곳은 살짝 따뜻하고 옅게. csc: 채도 배율(--sat 목표용).
        tint 가 있으면 유채색 픽셀의 색상을 테마 색 쪽으로 당긴다."""
        out = np.zeros_like(all_rgb)
        for gi, g in enumerate(groups):
            if keep_group[gi]:
                out[g] = all_rgb[g]
                continue
            lab = lab_all[g].copy()
            Lm = lab[:, 0].mean()
            sw = np.where(detail_all[g], 0.3, 1.0)
            L2 = Lm + (lab[:, 0] - Lm) * k + shift + struct[g] * (steps - 1) * STEP_L * sw
            L2 = np.clip(L2, 0.06, 0.97)
            d = L2 - lab[:, 0]
            C = np.hypot(lab[:, 1], lab[:, 2]); h = np.arctan2(lab[:, 2], lab[:, 1])
            dn = np.clip(-d / 0.2, 0, 1); up = np.clip(d / 0.2, 0, 1)
            hs = np.array([lerp_angle(hh, SHADOW_H, 0.09 * a) if a > 0 else lerp_angle(hh, LIGHT_H, 0.06 * b)
                           for hh, a, b in zip(h, dn, up)])
            C2 = C * (1 + 0.25 * dn) * (1 - 0.15 * up) * csc
            if tint_p is not None:
                _, tC, th, amt = tint_p
                chrom = C > 0.02
                hs = np.where(chrom, np.array([lerp_angle(hh, th, amt) for hh in hs]), th)
                C2 = np.where(chrom, C2 * (1 - amt) + tC * amt, C2 + tC * amt * 0.5)
            lab2 = np.stack([L2, C2 * np.cos(hs), C2 * np.sin(hs)], 1)
            lab2 = quantize(lab2, steps + 1)
            out[g] = np.clip(from_oklab(lab2), 0, 1)
        return out

    def render(center_shift, half):
        out = np.zeros_like(all_rgb)
        idx = np.zeros(len(all_rgb), int)
        for gi, g in enumerate(groups):
            if keep_group[gi]:
                out[g] = all_rgb[g]
                continue
            base = reps[gi]
            r = ramp(base, float(np.clip(base[0] + center_shift, 0.15, 0.9)), half, steps, tint_p)
            kp = np.where(detail_all[g], 1.0, keep)       # 디테일 픽셀은 원래 명암을 다 살림
            sh = kp * (t_all[g] - 0.5) + struct[g]
            k = np.clip(np.round((sh + 0.5) * (steps - 1)), 0, steps - 1).astype(int)
            out[g] = from_oklab(r[k])
            idx[g] = k
        return out

    def stats(rgb):
        Y = bbtex.luma(rgb)
        return rgb.max(1).mean(), Y.std()

    # 목표 명도·명암에 맞게 중심 이동·계단 폭을 몇 번 조정
    if mode == "ramp":
        shift, half = 0.0, 0.16
        for _ in range(14):
            res = render(shift, half)
            v, c = stats(res)
            shift += (val - v) * 0.8
            half = float(np.clip(half * (contrast / max(c, 1e-3)) ** 0.7, 0.04, 0.45))
        res = render(shift, half)
    else:
        shift, half, csc = 0.0, 1.0, 1.0           # half 자리에 대비 배율 k, csc 채도 배율
        for _ in range(12):
            res = render_adjust(shift, half, csc)
            v, c = stats(res)
            shift += (val - v) * 0.8
            half = float(np.clip(half * (contrast / max(c, 1e-3)) ** 0.8, 0.5, 2.5))
            if sat is not None:
                s_now = float(np.where(res.max(1) > 0, (res.max(1) - res.min(1)) / np.maximum(res.max(1), 1e-9), 0).mean())
                csc = float(np.clip(csc * (sat / max(s_now, 1e-3)) ** 0.8, 0.2, 2.0))
        res = render_adjust(shift, half, csc)
    # 다시 텍스처에 쓰기
    pos = 0
    for ti, vis in pix:
        tx = texs[ti]
        H = maps[ti]["face"].shape[0]
        n = int(vis.sum())
        img = tx["img"].copy()
        frame = img[:H].copy()
        frame[vis, :3] = np.round(res[pos:pos + n] * 255).astype(np.uint8)
        pos += n
        img[:H] = frame
        m["textures"][ti]["source"] = bbtex.encode_png(img)
    bbtex.save(m, path_out)
    v_end, c_end = stats(res)
    if verbose and mode == "adjust" and (half <= 0.5 + 1e-6 or half >= 2.5 - 1e-6):
        print(f"알림: 대비 배율이 한계({half:.2f})에 걸림 — 명암 폭 {c_end:.3f} (목표 {contrast})")
    if verbose and abs(v_end - val) > 0.1 * max(val, 0.05):
        print(f"알림: 명도 {v_end:.3f} 가 목표 {val} 에서 10% 넘게 벗어남")
    if verbose and mode == "adjust" and c_end < 0.9 * contrast:
        print(f"알림: 원래 색만으로는 명암 폭이 {c_end:.3f} 까지만 올라감 (목표 {contrast}). 원본이 단조로우면"
              f"(색95 ≤ 6 또는 명암 폭 < 0.08) --mode ramp 를 쓰고, 상징색은 --protect 로 지킨다")
    if verbose:
        v, c = stats(res)
        print(f"[{mode}] 재질 {len(groups)}개 (그대로 둔 색 {sum(keep_group)}), 명도 {v:.3f}, 명암 {c:.3f}, "
              f"밝기 이동 {shift:+.3f}, " + (f"계단 폭 ±{half:.3f}" if mode == "ramp" else f"대비 ×{half:.2f}"))
    return path_out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--val", type=float, default=0.58); ap.add_argument("--contrast", type=float, default=0.18)
    ap.add_argument("--steps", type=int, default=6); ap.add_argument("--edge", type=float, default=1.0)
    ap.add_argument("--grad", type=float, default=0.16); ap.add_argument("--noise", type=float, default=0.06)
    ap.add_argument("--keep", type=float, default=0.75); ap.add_argument("--tint", default=None)
    ap.add_argument("--center", type=float, default=0.10)
    ap.add_argument("--protect", default="", help="그대로 둘 색 (쉼표로): 예 #e07a1f,#ffd34a — 몹의 상징색")
    ap.add_argument("--mode", default="adjust", choices=["adjust", "ramp"])
    ap.add_argument("--sat", type=float, default=None, help="목표 평균 채도 (예: 0.40) — 프로필 채도 범위에 맞출 때")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    if os.path.abspath(a.src) == os.path.abspath(a.dst):
        raise SystemExit("원본에 덮어쓰지 않는다. 출력 경로를 따로 주세요.")
    reshade(a.src, a.dst, a.val, a.contrast, a.steps, a.edge, a.grad, a.noise, a.keep, a.tint, a.seed, a.center,
            [x for x in a.protect.split(",") if x.strip()], a.mode, a.sat)
    r = metrics.measure(a.dst)
    print({k: r.get(k) for k in ("colors95", "val", "contrast", "noise", "edge_dark", "top_light")})


if __name__ == "__main__":
    main(sys.argv[1:])
