"""큐브 부품 덧붙이기 + 부품 텍스처 칠하기 (코드로 디테일 살리기).

보통은 make_detail.py 가 설계 파일(<작업폴더>/설계/<모델>.py)의 build(kit) 를 불러 쓴다:
    def build(kit):
        kit.defaults(edge=0, center=0)                  # (선택) 모든 부품의 칠하기 기본값 — 프로필 paint 묶음이 먼저 깔린다
        kit.material("robe", "#c8d2d6", half=0.22)     # 재질: 기준색·계단 폭 (hue_shift=0, palette=[…], opts={…} 선택)
        kit.cube("b", [-4, 10, -3], [4, 21, 3], "robe", kind="cloth", name="robe_torso", skip=["up"])
직접 쓸 때: kit = Kit(원본경로); build(kit); kit.save(출력경로)   (원본은 읽기만, 출력=원본이면 멈춤)

규칙 (1층 공통 규칙과 맞춤)
- 크기는 정수(1 = 1/16 블록). 위치는 정수이거나, 그 뼈의 기존 큐브가 쓰는 격자(예: x 가 ±4.5 인 모델이면 .5)
  → 부품 픽셀이 몸 픽셀과 같은 격자에 놓인다. 부품 텍스처 밀도 = 원본 모델 밀도 → 믹셀 없음.
- 회전은 ModelEngine 규칙: 한 축만, 0·±22.5·±45 도. 부호: x− 면 뒤(+z) 끝이 올라가고, z+ 면 +x 끝이 올라간다.
- 새 큐브는 기존 뼈에 붙인다(기존 동작이 그대로 움직인다). 뼈 이름·동작은 바꾸지 않는다.
- 텍스처 자리는 저장할 때 한꺼번에 잡는다: 원본 텍스처의 **연속된 빈 영역**에 1px 간격으로 모으고,
  그런 영역이 없으면 새 텍스처(원본 UV 해상도 크기)를 더한다. 발광 층(_e)·애니메이션 텍스처에는 넣지 않는다.
- 칠하기: 재질별 색 계단(reshade.ramp) 위에 부품 종류별 무늬 + 1px 테두리 + 위 밝게 + 약한 얼룩.
  종류: plain(맨살·뼈·나무), cloth(천: 세로 주름·찢어진 밑단), metal(금속: 윗줄 반짝임·모서리 징),
        hair(머리카락: 세로 가닥·들쭉날쭉 끝), glow(빛: 테두리 없이 가운데 밝게), dark(구멍·그림자 속)
  옵션 우선순위: cube(opts=…) > material(opts=…) > kit.defaults(…) > 프로필 paint 묶음 > 기본값(DEFAULT_OPTS)
- glow 부품은 발광 짝 텍스처(X_e.png)의 같은 자리에도 칠한다(OptiFine 식 발광). 짝이 없는 새 텍스처에는 짝을 만든다.
"""
import math, os, sys, zlib, uuid as _uuid
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import bbtex  # noqa: E402
import reshade  # noqa: E402

ALLOWED_ROT = (0.0, 22.5, -22.5, 45.0, -45.0)
FACES = ("north", "east", "south", "west", "up", "down")
DEFAULT_OPTS = {"grad": 0.16, "center": 0.08, "noise": 0.06, "edge": 1.0, "shift": 0.0, "folds": 1.0,
                "hem": True, "hem_depth": 2, "rivets": True, "bands": 0}
# 크기 조건: 테두리는 4×4 이상 면(금속은 3×3 이상), 가운데 밝게는 5×5 이상, 징은 5×5 이상.
# cloth 의 bands=N 은 N px 마다 가로 줄(미라 붕대·감은 천), folds=0 이면 세로 주름 없음.
PAD = 1        # 부품 면 사이 간격(px) — mipmap 번짐 방지


def _hex_lab(h):
    h = h.strip("#")
    rgb = np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])
    return reshade.to_oklab(rgb[None])[0]


def _face_px(fname, size):
    """면의 (가로, 세로) = 큐브 크기 (유닛). Blockbench 기본 UV 방향."""
    sx, sy, sz = size
    return {"north": (sx, sy), "south": (sx, sy), "east": (sz, sy), "west": (sz, sy),
            "up": (sx, sz), "down": (sx, sz)}[fname]


def _num(v):
    """정수로 떨어지면 정수로 (파일을 깔끔하게)."""
    return int(round(v)) if abs(v - round(v)) < 1e-9 else round(v, 4)


def _frac(v):
    f = round(float(v) - math.floor(float(v)), 4)
    return 0.0 if f in (0.0, 1.0) else f


class Packer:
    """선반(shelf) 방식 사각형 배치. pad: 사각형 사이 간격."""
    def __init__(self, w, h, pad=0):
        self.w, self.h, self.pad = w, h, pad
        self.x = self.y = self.row_h = 0

    def alloc(self, w, h):
        p = self.pad
        if w > self.w or h > self.h:
            return None
        if self.x + w > self.w:
            self.x, self.y, self.row_h = 0, self.y + self.row_h + p, 0
        if self.y + h > self.h:
            return None
        r = (self.x, self.y)
        self.x += w + p
        self.row_h = max(self.row_h, h)
        return r


def largest_empty_rect(occ):
    """차 있지 않은(False) 칸으로 된 가장 넓은 직사각형 (x, y, w, h). 행별 높이 막대 + 스택."""
    H, W = occ.shape
    hist = np.zeros(W, int)
    best = (0, 0, 0, 0)
    for y in range(H):
        hist = np.where(occ[y], 0, hist + 1)
        stack = []
        for x in range(W + 1):
            hgt = hist[x] if x < W else 0
            start = x
            while stack and stack[-1][1] >= hgt:
                sx, sh = stack.pop()
                if sh * (x - sx) > best[2] * best[3]:
                    best = (sx, y - sh + 1, x - sx, sh)
                start = sx
            stack.append((start, hgt))
    return best


class Kit:
    def __init__(self, src, seed="kit", paint=None):
        self.src = src
        self.m = bbtex.load(src)
        self.seed = seed
        res = self.m.get("resolution") or {}
        self.res = (int(res.get("width", 16)), int(res.get("height", 16)))
        self.mats = {}
        self.base_opts = dict(DEFAULT_OPTS)
        if paint:
            self.base_opts.update({k: v for k, v in paint.items() if not k.startswith("_")})
        self.requests = []        # 면 요청 (저장 때 자리를 잡는다)
        self.tex = []             # 새 부품 텍스처 [(번호, RGBA 배열)]
        self.count = 0
        self.groups = {}
        self._index_groups()
        self._canvas = None       # 원본 텍스처 {번호: [RGBA, 빈칸 지도, sx, sy, 바뀜]}
        self._laid = False
        self._warned_bones = set()
        self.stats = {}           # 재질별 {픽셀, 색} (칠한 뒤)
        self.density = self._density()

    # ── 설정 ──
    def defaults(self, **opts):
        """이 모델의 모든 부품 칠하기 기본값 (예: 평면 스타일이면 edge=0, center=0, noise=0, grad=0)."""
        self.base_opts.update(opts)

    def material(self, name, hexcolor, half=0.22, steps=6, center=None, hue_shift=None, palette=None, opts=None):
        """재질 = 색 계단. center 는 OKLab 밝기 L. hue_shift=0 이면 색상을 옮기지 않고 밝기만(팔레트가 좁은 팩).
        palette=[hex…] 를 주면 계단 색을 그 팔레트에서 가장 가까운 색으로 맞춘다(팩 팔레트 밖 색을 안 만든다)."""
        lab = _hex_lab(hexcolor)
        c = lab[0] if center is None else center
        if hue_shift is None:
            hue_shift = float(self.base_opts.get("hue_shift", 1.0))       # 프로필 칠하기 묶음 → 기본 1
        rp = reshade.ramp(lab, c, half, steps, hue=hue_shift)
        if palette:
            pal = np.array([_hex_lab(h) for h in palette])
            rp = np.array([pal[np.argmin(((pal - x) ** 2).sum(1))] for x in rp])
        self.mats[name] = dict(ramp=rp, steps=steps, opts=dict(opts or {}))

    # ── 원본 읽기 ──
    def _density(self):
        """원본 모델 밀도(px/유닛)를 0.5·1·2·4 중 가까운 값으로. 많이 벗어나면 density_fix 먼저 하라고 알린다."""
        import metrics
        try:
            d = metrics.measure(self.src)["px_per_unit"] or 1.0
        except Exception:
            return 1.0
        best = min((0.5, 1.0, 2.0, 4.0), key=lambda c: abs(math.log(d / c)))
        if abs(d / best - 1) > 0.1:
            print(f"주의: 원본 밀도 {d:.3f} px/유닛 — 부품은 {best} 로 맞춘다. density_fix.py 로 밀도를 먼저 맞추길 권함")
        return best

    def _index_groups(self):
        side = {g["uuid"]: g for g in self.m.get("groups", []) if isinstance(g, dict) and "uuid" in g}

        def walk(node):
            if isinstance(node, dict):
                nm = node.get("name") or side.get(node.get("uuid"), {}).get("name")
                if nm:
                    self.groups.setdefault(nm, node)
                for c in node.get("children", []):
                    walk(c)
        for n in self.m.get("outliner", []):
            walk(n)

    def _grid(self, bone):
        """그 뼈의 기존 큐브 좌표 소수 부분 (축별). 큐브가 없으면 모델 전체."""
        els = {e["uuid"]: e for e in self.m.get("elements", []) if e.get("type", "cube") == "cube"}
        mine = [els[c] for c in self.groups[bone].get("children", []) if isinstance(c, str) and c in els]
        src = mine or list(els.values())
        out = [{0.0}, {0.0}, {0.0}]
        for e in src:
            for i in range(3):
                out[i].add(_frac(e["from"][i])); out[i].add(_frac(e["to"][i]))
        return out

    def bone_origin(self, bone):
        node = self.groups[bone]
        side = {g["uuid"]: g for g in self.m.get("groups", []) if isinstance(g, dict) and "uuid" in g}
        return list(node.get("origin") or side.get(node.get("uuid"), {}).get("origin") or [0, 0, 0])

    def cubes(self, bone=None):
        """기존(원본) 큐브 목록 [(뼈, 이름, from, to, 회전, 뒤집힘)] — 겹칠 자리를 피해 설계할 때 본다."""
        import metrics
        bb = metrics._bbpose().Model(self.src)
        out = []
        for eu, e in bb.elements.items():
            g = bb.groups.get(bb.elem_group.get(eu), {}).get("name")
            if bone and g != bone:
                continue
            out.append((g, e.get("name"), e["from"], e["to"], e.get("rotation"), bbtex.is_flipped(e)))
        return out

    def colors(self, n=8, original=True):
        """본체 면이 실제로 쓰는 픽셀의 주요 색 n 개 [(hex, 비율, OKLab L, 명도 V)] — 새 부품 재질을 원래 색에서 고를 때.
        original=True 면 원본 파일, False 면 지금(다시 칠한 뒤). 면이 안 쓰는 픽셀·발광 층(_e)은 세지 않는다."""
        import metrics
        path = self.src
        if not original:
            import tempfile
            td = tempfile.mkdtemp()
            path = os.path.join(td, "now.bbmodel")
            bbtex.save(self.m, path)
        bb = metrics._bbpose().Model(path)
        texs = bbtex.textures(bb.m)
        masks = {t["idx"]: np.zeros((t["h"] // t["frames"], t["w"]), bool) for t in texs if t["img"] is not None}
        for eu in bbtex.body_elements(bb):
            for fname, f in (bb.elements[eu].get("faces") or {}).items():
                ti = bbtex.face_tex(f, texs) if f.get("uv") else None
                if ti is None or ti not in masks:
                    continue
                x0, y0, x1, y1 = bbtex.face_rect(f, texs[ti])
                masks[ti][max(0, y0):y1 + 1, max(0, x0):x1 + 1] = True
        if not original:
            import shutil
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        pix = []
        for t in texs:
            if t["idx"] in masks:
                img = t["img"][: masks[t["idx"]].shape[0]]
                pix.append(img[masks[t["idx"]] & (img[..., 3] >= 128)][:, :3])
        if not pix or not sum(len(p) for p in pix):
            return []
        P = np.concatenate(pix)
        codes, cnt = np.unique(P, axis=0, return_counts=True)
        o = np.argsort(-cnt)[:n]
        lab = reshade.to_oklab(codes[o] / 255.0)
        return [("#%02x%02x%02x" % tuple(int(v) for v in codes[i]), round(float(cnt[i] / cnt.sum()), 3),
                 round(float(lab[j][0]), 3), round(float(codes[i].max()) / 255, 3)) for j, i in enumerate(o)]

    def replace_texture(self, path_with_textures):
        """원본 큐브의 텍스처를 다른 파일(예: reshade 결과)의 것으로 바꾼다 (같은 모델에서 나온 파일). 부품 배치 전에."""
        if self._laid:
            raise RuntimeError("replace_texture 는 저장 전에 불러야 한다")
        other = bbtex.load(path_with_textures)
        for i, t in enumerate(other.get("textures", [])):
            if i < len(self.m["textures"]):
                self.m["textures"][i]["source"] = t.get("source", self.m["textures"][i].get("source"))

    # ── 부품 ──
    def _uid(self, *parts):
        return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "/".join(str(p) for p in (self.seed,) + parts)))

    def cube(self, bone, frm, to, mat, kind="plain", name=None, skip=(), rot=None, origin=None, opts=None):
        """bone 에 큐브 하나. 크기는 정수, 위치는 정수 또는 그 뼈의 격자. skip: 텍스처를 주지 않을 면. rot: (축, 각도)."""
        frm, to = [float(v) for v in frm], [float(v) for v in to]
        size = [to[i] - frm[i] for i in range(3)]
        if bone not in self.groups:
            raise KeyError(f"뼈 없음: {bone} (있는 뼈: {', '.join(self.groups)})")
        for s in size:
            if abs(s - round(s)) > 1e-6:
                raise ValueError(f"크기는 정수만 (믹셀 방지): {name} {size}")
        if min(size) < 0:
            raise ValueError(f"from > to: {name}")
        grid = self._grid(bone)
        for i in range(3):
            if _frac(frm[i]) not in grid[i]:
                raise ValueError(f"위치가 뼈 '{bone}' 의 격자에 안 맞음: {name} {'xyz'[i]}={frm[i]} "
                                 f"(허용 소수 부분: {sorted(grid[i])}) — 몸 픽셀과 반 칸 어긋난다")
        if mat not in self.mats:
            raise KeyError(f"재질 없음: {mat}")
        has = [c for c in self.groups[bone].get("children", []) if isinstance(c, str)]
        if not has and bone not in self._warned_bones:
            self._warned_bones.add(bone)
            print(f"알림: 뼈 '{bone}' 에는 큐브가 없었다 → 부품을 붙이면 렌더 뼈(디스플레이 엔티티)가 1개 는다. "
                  f"태그 뼈(ir_·il_ 등)의 부모라면 무기 큐브는 팔 뼈에 붙인다")
        rotation = [0.0, 0.0, 0.0]
        if rot:
            ax, ang = rot
            if float(ang) not in ALLOWED_ROT:
                raise ValueError(f"ModelEngine 회전은 0·±22.5·±45 만: {name} {ang}")
            rotation["xyz".index(ax)] = float(ang)
        self.count += 1
        eu = self._uid("elem", self.count, name)
        faces = {}
        for f in FACES:
            fw, fh = _face_px(f, size)
            faces[f] = {"uv": [0, 0, 0, 0], "texture": None}
            if f in skip or fw < 1e-6 or fh < 1e-6:
                continue
            pw, ph = max(1, int(round(fw * self.density))), max(1, int(round(fh * self.density)))
            self.requests.append(dict(face=faces[f], fname=f, w=pw, h=ph, mat=mat, kind=kind, opts=dict(opts or {}), cid=self.count))
        e = {"name": name or f"{kind}_{self.count}", "box_uv": False, "rescale": False, "locked": False,
             "render_order": "default", "allow_mirror_modeling": True, "from": frm, "to": to, "autouv": 0,
             "color": self.count % 8, "origin": list(origin) if origin else [(frm[i] + to[i]) / 2 for i in range(3)],
             "faces": faces, "type": "cube", "uuid": eu}
        if any(rotation):
            e["rotation"] = rotation
        self.m["elements"].append(e)
        self.groups[bone].setdefault("children", []).append(eu)
        return eu

    def mirror_x(self, bone, frm, to, mat, kind="plain", name=None, skip=(), rot=None, origin=None, opts=None):
        """x 를 뒤집은 짝 (좌우 대칭 부품). east/west 를 바꾸고 y·z 축 회전 부호를 뒤집는다."""
        f2, t2 = [-to[0], frm[1], frm[2]], [-frm[0], to[1], to[2]]
        sk = [{"east": "west", "west": "east"}.get(s, s) for s in skip]
        r2 = None
        if rot:
            ax, ang = rot
            r2 = (ax, -ang if ax in ("y", "z") else ang)
        o2 = [-origin[0], origin[1], origin[2]] if origin else None
        return self.cube(bone, f2, t2, mat, kind, name, sk, r2, o2, opts)

    # ── 텍스처 자리 잡기 ──
    def _ensure_canvas(self):
        if self._canvas is not None:
            return
        self._canvas = {}
        texs = bbtex.textures(self.m)
        self._pairs = bbtex.emissive_pairs(texs)
        occ = {t["idx"]: np.zeros((t["h"] // t["frames"], t["w"]), bool) for t in texs if t["img"] is not None}
        for e in self.m.get("elements", []):
            for f in (e.get("faces") or {}).values():
                if not f.get("uv") or f.get("texture") is None:
                    continue
                ti = bbtex.face_tex(f, texs)
                if ti is None or ti not in occ:
                    continue
                x0, y0, x1, y1 = bbtex.face_rect(f, texs[ti])
                occ[ti][max(0, y0):y1 + 1, max(0, x0):x1 + 1] = True
        emissive = set(self._pairs.values())
        for t in texs:
            if t["img"] is None or t["frames"] > 1 or t["idx"] in emissive or \
                    os.path.splitext(t["name"].lower())[0].endswith("_e"):
                continue
            img = t["img"].copy()
            o = occ[t["idx"]]
            o |= img[: o.shape[0], :, 3] > 0
            self._canvas[t["idx"]] = [img, o, t["sx"], t["sy"], False]
        self._e_imgs = {b: texs[e]["img"].copy() for b, e in self._pairs.items() if b in self._canvas}

    def _new_texture(self):
        w, h = int(self.res[0] * self.density), int(self.res[1] * self.density)      # px (uv 크기 = 원본 해상도)
        idx = len(self.m["textures"])
        name = f"{os.path.splitext(os.path.basename(self.src))[0]}_detail{len(self.tex) + 1}.png"
        t = {"path": "", "name": name, "folder": "", "namespace": "", "id": str(idx), "width": w, "height": h,
             "uv_width": self.res[0], "uv_height": self.res[1], "particle": False, "use_as_default": False, "layers_enabled": False,
             "sync_to_project": "", "render_mode": "default", "render_sides": "auto", "frame_time": 1,
             "frame_order_type": "loop", "frame_order": "", "frame_interpolate": False, "visible": True,
             "internal": True, "saved": False, "uuid": self._uid("tex", idx), "relative_path": "", "source": ""}
        self.m["textures"].append(t)
        self.tex.append([idx, np.zeros((h, w, 4), np.uint8)])
        return self.tex[-1]

    def layout(self):
        """면 요청 전부의 텍스처 자리: 원본 텍스처의 연속된 빈 영역 하나에 1px 간격으로 → 안 되면 새 텍스처."""
        if self._laid:
            return
        self._ensure_canvas()
        reqs = sorted(self.requests, key=lambda r: (-r["h"], -r["w"]))
        remaining = list(reqs)
        # 원본 텍스처의 넓은 빈 영역부터 차례로 (영역마다 1px 간격 선반 배치) → 남는 것만 새 텍스처
        for ti, c in self._canvas.items():
            for _ in range(6):
                if not remaining:
                    break
                x0, y0, w0, h0 = largest_empty_rect(c[1])
                if w0 * h0 < 36:
                    break
                pk, rest, used = Packer(w0, h0, PAD), [], 0
                for r in remaining:
                    spot = pk.alloc(r["w"], r["h"])
                    if spot:
                        x, y = x0 + spot[0], y0 + spot[1]
                        r.update(ti=ti, x=x, y=y, sx=c[2], sy=c[3])
                        used += 1
                    else:
                        rest.append(r)
                if not used:
                    break
                # 이 영역은 통째로 찼다고 표시 (간격까지) → 다음 영역은 다른 곳
                c[1][y0:y0 + h0, x0:x0 + w0] = True
                c[4] = True
                remaining = rest
        if remaining:
            pk, cur = None, None
            for r in remaining:
                spot = pk.alloc(r["w"], r["h"]) if pk else None
                if not spot:
                    cur = self._new_texture()
                    pk = Packer(cur[1].shape[1], cur[1].shape[0], PAD)
                    spot = pk.alloc(r["w"], r["h"])
                    if not spot:
                        raise ValueError(f"면이 너무 큼 {r['w']}x{r['h']} (텍스처 {self.res})")
                r.update(ti=cur[0], x=spot[0], y=spot[1], sx=self.density, sy=self.density)
        for r in reqs:
            r["face"]["uv"] = [_num(r["x"] / r["sx"]), _num(r["y"] / r["sy"]),
                               _num((r["x"] + r["w"]) / r["sx"]), _num((r["y"] + r["h"]) / r["sy"])]
            r["face"]["texture"] = r["ti"]
        self._laid = True

    # ── 칠하기 ──
    def paint(self):
        self.layout()
        rng_base = zlib.crc32(str(self.seed).encode("utf-8")) % 9973      # 실행마다 같은 무늬 (hash() 는 실행마다 다름)
        new_e = {}
        for r in self.requests:
            ti, x, y, w, h, fname, kind, cid = r["ti"], r["x"], r["y"], r["w"], r["h"], r["fname"], r["kind"], r["cid"]
            opts = dict(self.base_opts); opts.update(self.mats[r["mat"]]["opts"]); opts.update(r["opts"])   # 칠할 때 합친다
            img = self._canvas[ti][0] if ti in self._canvas else next(t[1] for t in self.tex if t[0] == ti)
            ramp = self.mats[r["mat"]]["ramp"]
            steps = len(ramp)
            st = 1.0 / (steps - 1)
            yy, xx = np.mgrid[0:h, 0:w]
            s = np.zeros((h, w))
            side = fname in ("north", "south", "east", "west")
            seed = rng_base + cid * 31 + FACES.index(fname) * 7
            n1 = reshade.hash_noise(h, w, seed)
            n2 = np.kron(reshade.hash_noise((h + 1) // 2, (w + 1) // 2, seed + 3), np.ones((2, 2)))[:h, :w]
            alpha = np.full((h, w), 255, np.uint8)
            big = (w >= 4 and h >= 4) or (kind == "metal" and w >= 3 and h >= 3)
            border = np.zeros((h, w), bool)
            if big:
                border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
            if side and h > 1:
                s += (0.5 - yy / (h - 1)) * opts["grad"]
            if fname == "up":
                s += 0.5 * st
            if fname == "down":
                s -= 0.8 * st
            if w >= 5 and h >= 5 and kind != "glow":
                d = np.minimum(np.minimum(xx, w - 1 - xx), np.minimum(yy, h - 1 - yy))
                s += np.clip((d - 1) / 3, 0, 1) * opts["center"]
            s += (0.4 * n1 + 0.6 * n2) * opts["noise"] / 2
            if kind == "cloth":
                if side and w >= 3:
                    # 주름: 불규칙 간격(3~5px)의 어두운 줄 + 바로 옆 밝은 줄, 밑으로 갈수록 진하게
                    col = np.zeros(w)
                    rr = reshade.hash_noise(1, 64, seed + 17)[0]
                    c, j = int((rr[0] + 1) * 1.5), 1
                    while c < w:
                        col[c] -= 1.0
                        if c + 1 < w:
                            col[c + 1] += 0.6
                        c += 3 + int((rr[j % 64] + 1) * 1.0)
                        j += 1
                    depth_k = 0.6 + 0.6 * (yy / max(h - 1, 1))
                    s += col[None, :] * depth_k * st * opts["folds"]
                if opts.get("bands"):
                    n = max(2, int(opts["bands"]))
                    off = int((reshade.hash_noise(1, 1, seed + 23)[0][0] + 1) * n / 2)
                    row = ((yy + off) % n == 0)
                    s -= row * 0.9 * st
                    s += (((yy + off) % n) == 1) * 0.4 * st
                    if opts["hem"] and h >= 3:
                        cut = (reshade.hash_noise(1, w, seed + 11)[0] + 1) / 2
                        depth = np.floor(cut * (opts["hem_depth"] + 1)).astype(int)
                        for c in range(w):
                            if depth[c] > 0:
                                alpha[h - depth[c]:, c] = 0
                            rw = h - depth[c] - 1
                            if 0 <= rw < h:
                                border[rw, c] = True
            elif kind == "metal":
                if big or h >= 2:          # 두께 2px 이상이면 윗줄 반짝임·아랫줄 그늘
                    s[0, :] += 1.2 * st
                    s[-1, :] -= 0.8 * st
                if w >= 5 and h >= 5 and opts["rivets"]:
                    for (ry, rx) in ((1, 1), (1, w - 2), (h - 2, 1), (h - 2, w - 2)):
                        s[ry, rx] += 2.0 * st
            elif kind == "hair":
                strand = reshade.hash_noise(1, w, seed + 5)[0]
                s += strand[None, :] * 1.2 * st
                if side and opts["hem"]:
                    cut = (reshade.hash_noise(1, w, seed + 13)[0] + 1) / 2
                    depth = np.floor(cut * (opts.get("hem_depth", 3) + 1)).astype(int)
                    for c in range(w):
                        if depth[c] > 0:
                            alpha[h - depth[c]:, c] = 0
            elif kind == "glow":
                # 빛: 가장자리는 진한 색, 가운데로 갈수록 거의 흰색 (테두리 어둡게 안 함)
                d = np.minimum(np.minimum(xx, w - 1 - xx), np.minimum(yy, h - 1 - yy))
                s = -0.15 + np.clip(d / max(1.0, min(w, h) / 2 - 0.5), 0, 1) * 0.65
                border[:] = False
            elif kind == "dark":
                s -= 1.5 * st
            if kind != "glow":
                s -= border * opts["edge"] * st
            s += opts["shift"] * st
            k = np.clip(np.round((s + 0.5) * (steps - 1)), 0, steps - 1).astype(int)
            rgb = np.round(reshade.from_oklab(ramp[k]) * 255).astype(np.uint8)
            img[y:y + h, x:x + w, :3] = rgb
            img[y:y + h, x:x + w, 3] = alpha
            stt = self.stats.setdefault(r["mat"], {"px": 0, "colors": set()})
            vis = alpha > 0
            stt["px"] += int(vis.sum())
            stt["colors"].update(map(tuple, rgb[vis].reshape(-1, 3).tolist()))
            # 발광 층: glow 는 같은 자리에 칠하고, 나머지는 그 자리를 비운다(빈 곳에 남은 발광 픽셀이 새 부품을 빛나게 하지 않게)
            if ti in self._canvas and ti in self._e_imgs:
                eimg = self._e_imgs[ti]
                eimg[y:y + h, x:x + w] = 0
                if kind == "glow":
                    eimg[y:y + h, x:x + w, :3] = rgb
                    eimg[y:y + h, x:x + w, 3] = alpha
            elif kind == "glow":
                arr = new_e.setdefault(ti, np.zeros_like(img))
                arr[y:y + h, x:x + w, :3] = rgb
                arr[y:y + h, x:x + w, 3] = alpha
        for idx, arr in self.tex:
            self.m["textures"][idx]["source"] = bbtex.encode_png(arr)
        for idx, c in self._canvas.items():
            if c[4]:                                     # 빈 곳에 부품을 넣은 원본 텍스처
                self.m["textures"][idx]["source"] = bbtex.encode_png(c[0])
                if idx in self._e_imgs:
                    self.m["textures"][self._pairs[idx]]["source"] = bbtex.encode_png(self._e_imgs[idx])
        for idx, arr in new_e.items():                   # 새 부품 텍스처의 발광 짝 (면이 직접 안 씀)
            base = dict(self.m["textures"][idx])
            base.update({"name": os.path.splitext(base["name"])[0] + "_e.png", "uuid": self._uid("tex_e", idx),
                         "id": str(len(self.m["textures"])), "source": bbtex.encode_png(arr)})
            self.m["textures"].append(base)

    def save(self, out):
        if os.path.abspath(out) == os.path.abspath(self.src):
            raise SystemExit("원본에 덮어쓰지 않는다")
        self.paint()
        bbtex.save(self.m, out)
        return out
