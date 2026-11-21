"""아트 기준 2층 구조 (mob-modeling).

1층 공통 규칙 — 어떤 스타일이든 지킨다. 값은 COMMON(스킬 기본) 위에 프로젝트 파일의 "common" 으로 조금 고칠 수 있다.
   엔진 규칙(lint.py), 텍셀 밀도 하나(base_px_per_block), 믹셀·소수 크기 큐브·겹침·텍스처 장수,
   색 위생(고유 색 상한 = 부드러운 그라데이션 금지, 순흑 비율, 색95 상한), 어두운 곳 가독성(명도 하한), 렌더 뼈 예산.
2층 스타일 프로필 — 프로젝트가 고른 기준 몹에서 make_profile.py 로 잰 범위(명도·채도·명암 폭·노이즈·테두리·위 밝음·색 수·큐브 밀도).
   프로필은 여러 개 둘 수 있다(권장 2~3). 몹마다 assign 으로 프로필·역할·테마를 정한다.

프로젝트 파일 (art_standard.json, version 2):
{
  "version": 2,
  "common":   {"base_px_per_block": 16, ...},                       # 생략하면 COMMON
  "profiles": {"fantasy": {"_설명": "...", "reference": [...], "ranges": {...},
                           "roles": {"보스": {"cubes_per_blk": [8, null]}}, "themes": {"dark": {"val": [0.3, 0.6]}}}},
  "default_profile": "fantasy",
  "assign":     {"<모델ID>": {"profile": "fantasy", "role": "일반", "theme": "dark"}},
  "exceptions": {"<모델ID>": {"px_per_block": "레이드 보스, 사용자 승인 2026-11-14"}},
  "paths":      {"work": "modeling", "models": ["models"]}
}
옛 형식(맨 위에 ranges·dark_theme_ranges)은 프로필 "default" 하나로 읽는다.
"""
import copy, json, math, os

COMMON = {
    "base_px_per_block": 16,      # 인게임 텍셀 밀도 (바닐라 16x). 32x 서버면 32
    "density_warn": 0.10,         # 기준에서 ±10% 넘으면 경고
    "density_fail": 0.25,         # ±25% 넘으면 불합격(경고 '큼')
    "mixel_share_max": 0.10,      # 한 모델 안에서 밀도가 중앙값 ±25% 밖인 넓이 비율
    "frac_cube_share_max": 0.10,  # 소수 크기 큐브 비율 (UV 를 편 뒤 통째 확대·축소한 흔적)
    "unique_colors_max": 64,      # 고유 색이 이보다 많으면 부드러운 그라데이션·사진 질감 의심
    "colors95_max": 24,           # 픽셀 95% 를 덮는 색 수 상한
    "black_share_max": 0.05,      # 거의 순흑(명도 < 0.04) 픽셀 비율 — 눈구멍·입 안(해골 2~3%)은 정상, 넓은 검정 면만 잡는다
    "overlap_area_warn": 16,      # 원래 모델의 같은 평면 겹침 넓이 합(px²)이 이 이상이면 경고 (부품 보강이 새로 만든 겹침은 0 이어야 함)
    "val_min": 0.25,              # 어두운 테마도 어두운 던전에서 읽히는 평균 명도 하한
    "body_textures_max": 2,       # 본체 1 + 발광 1
    "render_bones": {"일반": 24, "정예": 32, "보스": 96, "소환": 16},   # 보유 몹 47종 분포(상위 25~10%)에서 — 이상치만 걸리게
}
STYLE_KEYS = ["val", "sat", "contrast", "noise", "edge_dark", "top_light", "colors95", "cubes_per_blk"]
TOL = {"val": 0.08, "sat": 0.10, "contrast": 0.04, "noise": 0.02, "edge_dark": 0.15, "top_light": 0.08}
NAMES = {"val": "명도", "sat": "채도", "contrast": "명암 폭", "noise": "노이즈", "edge_dark": "테두리", "top_light": "위밝음",
         "colors95": "색95", "cubes_per_blk": "큐브/키", "px_per_block": "px/블록"}


def convert(d):
    """옛 형식 → version 2."""
    d = copy.deepcopy(d or {})
    if d.get("version") == 2:
        return d
    prof = {"_설명": d.get("_설명", "옛 형식에서 옮김"), "ranges": d.pop("ranges", {}) or {}}
    prof["ranges"].pop("px_per_block", None)          # 텍셀 밀도는 1층 공통 규칙으로 옮겨 감
    if "dark_theme_ranges" in d:
        prof["themes"] = {"dark": d.pop("dark_theme_ranges")}
    out = {"version": 2, "common": {}, "profiles": {}, "assign": {}, "exceptions": {}, "paths": {}}
    if prof["ranges"]:
        out["profiles"]["default"] = prof
        out["default_profile"] = "default"
    for k in ("notes", "theme_accents", "_출처"):
        if k in d:
            out[k] = d[k]
    return out


def load(explicit=None):
    """(기준 dict v2, 경로). 찾는 순서는 skilldeps.std_path."""
    import skilldeps
    p = skilldeps.std_path(explicit)
    if not p:
        return convert({}), None
    return convert(json.load(open(p, encoding="utf-8"))), p


def save(d, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def common(d):
    c = copy.deepcopy(COMMON)
    for k, v in ((d or {}).get("common") or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c


def entry(d, model_id):
    """assign 항목 (없으면 빈 dict). 모델 ID 는 파일 이름 (대소문자 구분 안 함)."""
    a = {k: v for k, v in ((d or {}).get("assign") or {}).items() if not k.startswith("_")}
    if model_id in a:
        return a[model_id]
    low = {k.lower(): v for k, v in a.items()}
    return low.get((model_id or "").lower(), {})


def pick(d, model_id=None, profile=None, role=None, theme=None):
    """(프로필 이름, 역할, 테마) — 인자 > assign > default_profile."""
    e = entry(d, model_id)
    prof = profile or e.get("profile") or (d or {}).get("default_profile")
    if prof and prof not in ((d or {}).get("profiles") or {}):
        raise SystemExit(f"프로필 없음: {prof} (있는 프로필: {', '.join((d or {}).get('profiles') or {}) or '없음'})")
    return prof, (role or e.get("role")), (theme or e.get("theme"))


def ranges(d, profile, role=None, theme=None):
    p = ((d or {}).get("profiles") or {}).get(profile) or {}
    rg = {k: v for k, v in (p.get("ranges") or {}).items() if not k.startswith("_")}
    for over in (((p.get("roles") or {}).get(role) if role else None), ((p.get("themes") or {}).get(theme) if theme else None)):
        if over:
            rg.update({k: v for k, v in over.items() if not k.startswith("_")})
    return rg


def _excepted(d, model_id, key):
    ex = ((d or {}).get("exceptions") or {})
    e = ex.get(model_id) or {k.lower(): v for k, v in ex.items()}.get((model_id or "").lower()) or {}
    return e.get(key)


def judge_style(r, d, model_id=None, profile=None, role=None, theme=None):
    """2층: (프로필 이름 또는 None, [메시지]). 프로필이 없으면 스타일 판정 없음."""
    prof, role, theme = pick(d, model_id, profile, role, theme)
    if not prof:
        return None, []
    out = []
    for k, (lo, hi) in ranges(d, prof, role, theme).items():
        v = r.get(k)
        if v is None or _excepted(d, model_id, k):
            continue
        if lo is not None and v < lo:
            out.append(f"{NAMES.get(k, k)} {v} < {lo}")
        elif hi is not None and v > hi:
            out.append(f"{NAMES.get(k, k)} {v} > {hi}")
    return prof, out


def judge_common(r, d, model_id=None, role=None, render_bones=None):
    """1층 수치 규칙: [(키, 메시지)]. 예외 승인된 키는 빼고 '예외' 메시지로."""
    c = common(d)
    out = []

    def add(key, msg):
        why = _excepted(d, model_id, key)
        out.append((key, f"(예외 승인: {why}) {msg}" if why else msg, bool(why)))

    ppb = r.get("px_per_block")
    if ppb:
        dev = ppb / c["base_px_per_block"] - 1
        if abs(dev) > c["density_fail"]:
            add("px_per_block", f"텍셀 밀도 {ppb} px/블록 — 기준 {c['base_px_per_block']} 에서 {dev:+.0%} (픽셀 크기가 다른 몹과 확 다름)")
        elif abs(dev) > c["density_warn"]:
            add("px_per_block", f"텍셀 밀도 {ppb} px/블록 — 기준 {c['base_px_per_block']} 에서 {dev:+.0%}")
    if (r.get("mixel_share") or 0) > c["mixel_share_max"]:
        add("mixel_share", f"한 모델 안 밀도 혼합(믹셀) 넓이 {r['mixel_share']:.0%} > {c['mixel_share_max']:.0%}")
    if (r.get("colors") or 0) > c["unique_colors_max"]:
        add("colors", f"고유 색 {r['colors']} > {c['unique_colors_max']} (부드러운 그라데이션·사진 질감 의심)")
    if (r.get("colors95") or 0) > c["colors95_max"]:
        add("colors95", f"색95 {r['colors95']} > {c['colors95_max']}")
    if (r.get("black_share") or 0) > c["black_share_max"]:
        add("black_share", f"거의 순흑 픽셀 {r['black_share']:.1%} > {c['black_share_max']:.0%} (그림자도 재질의 어두운 색으로)")
    if r.get("val") is not None and r["val"] < c["val_min"]:
        add("val", f"평균 명도 {r['val']} < {c['val_min']} (어두운 던전에서 안 보임)")
    if render_bones is not None and role and role in c["render_bones"] and render_bones > c["render_bones"][role]:
        add("render_bones", f"렌더 뼈 {render_bones} > {role} 예산 {c['render_bones'][role]} (디스플레이 엔티티 수)")
    return out


def paint_bundle(refs):
    """기준 몹 수치 → 칠하기 기본값 묶음 (kitbash 부품·reshade 다시 칠하기가 쓴다). 평면 스타일이면 테두리·결 없음."""
    def mean(k):
        vs = [r[k] for r in refs if r.get(k) is not None]
        return sum(vs) / len(vs) if vs else 0.0
    edge = mean("edge_dark") >= 0.3
    return {"_설명": "make_profile 이 기준 몹에서 정한 칠하기 기본값 (손으로 고쳐도 됨)",
            "edge": 1.0 if edge else 0.0, "center": 0.08 if edge else 0.0,
            "noise": 0.06 if mean("noise") >= 0.04 else 0.0, "grad": 0.16 if mean("top_light") >= 0.08 else 0.06,
            "hue_shift": 1.0 if mean("colors95") >= 8 else 0.0}


def effective(d, model_id=None, profile=None, role=None, theme=None):
    """1층과 2층이 겹치는 실효 목표 {키: [하한, 상한]} — 명도·색95 처럼 두 층이 다 정하는 항목."""
    c = common(d)
    prof, role, theme = pick(d, model_id, profile, role, theme)
    rg = ranges(d, prof, role, theme) if prof else {}
    out = {}
    lo, hi = (rg.get("val") or [None, None])
    out["val"] = [max(v for v in (lo, c["val_min"]) if v is not None), hi]
    lo, hi = (rg.get("colors95") or [None, None])
    out["colors95"] = [lo, min(v for v in (hi, c["colors95_max"]) if v is not None)]
    for k, v in rg.items():
        out.setdefault(k, v)
    return prof, out


def make_profile(refs, note=""):
    """기준 몹 수치 목록 → 프로필. 범위 = [기준 최소 − 여유, 기준 최대 + 여유]. 기준 몹이 적으면 여유가 범위를 지킨다."""
    rg = {}
    for k in ("val", "sat", "contrast", "noise", "edge_dark", "top_light"):
        vs = [r[k] for r in refs if r.get(k) is not None]
        if not vs:
            continue
        lo, hi = min(vs) - TOL[k], max(vs) + TOL[k]
        lo = round(max(lo, 0.0 if k != "top_light" else -1.0), 3)
        hi = round(min(hi, 1.0), 3)
        rg[k] = [lo, hi]
    c95 = [r["colors95"] for r in refs if r.get("colors95")]
    if c95:
        rg["colors95"] = [max(3, int(math.floor(min(c95) * 0.7))), int(math.ceil(max(c95) * 1.4))]
    cpb = [r["cubes_per_blk"] for r in refs if r.get("cubes_per_blk")]
    if cpb:
        floor = round(min(cpb) * 0.6, 1)
        rg["cubes_per_blk"] = [floor, None]
    prof = {"_설명": note or "make_profile.py 로 기준 몹에서 잰 범위", "reference": [r["file"] for r in refs], "ranges": rg,
            "roles": {}, "paint": paint_bundle(refs)}
    if cpb:   # 큰 몹은 키가 커서 높이당 큐브가 적게 나온다 → 정예·보스는 하한을 낮춘다
        prof["roles"] = {"정예": {"cubes_per_blk": [round(floor * 0.8, 1), None]},
                         "보스": {"cubes_per_blk": [round(floor * 0.5, 1), None]},
                         "소환": {"cubes_per_blk": [None, None]}}
    return prof
