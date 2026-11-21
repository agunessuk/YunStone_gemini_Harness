"""Blockbench(.bbmodel) 뼈대·애니메이션 자세 계산 (numpy 만 씀 — Blender 안에서도, 밖에서도 돌아감).

Blockbench 규칙을 그대로 따른다 (references/conventions.md):
- 그룹(뼈)·큐브 회전: 도(degree), 오일러 순서 ZYX → 행렬 Rz·Ry·Rx
- 애니메이션 회전 키프레임: 화면 값 (x, y, z) 를 기본 회전에 더하되 x·y 는 부호를 뒤집어 더한다
- 애니메이션 위치 키프레임: x 는 부호를 뒤집고 y·z 는 그대로 더한다 (단위: 1/16 블록)
- 애니메이션 크기(scale) 키프레임: 배율 그대로 (기본 1), 뼈 기준점에서 회전 다음에 적용
- 보간: linear / catmullrom / step (bezier 는 linear 로 근사)
- 파일 형식: Blockbench 4.12 이후(형식 5.0)는 outliner 에 uuid·children 만 있고 뼈 정보는 groups 에 따로 있다. 둘 다 읽는다.
"""
import json, math
import uuid as _uuid
import numpy as np


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0        # molang 식 등은 0 으로 (미리보기용)


def rot_matrix(rx, ry, rz):
    rx, ry, rz = (math.radians(a) for a in (rx, ry, rz))
    X = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Y = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    Z = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    return Z @ Y @ X


def T(v):
    m = np.eye(4); m[:3, 3] = v; return m


def R4(r):
    m = np.eye(4); m[:3, :3] = r; return m


class Model:
    def __init__(self, path):
        self.path = path
        self.m = json.load(open(path, encoding="utf-8"))
        self.groups = {}      # uuid → dict(name, origin, rotation, parent, elements)
        self.order = []       # 부모가 먼저 오는 순서
        side = {g["uuid"]: g for g in self.m.get("groups", []) if isinstance(g, dict) and "uuid" in g}

        def walk(node, parent):
            if isinstance(node, str):
                if parent:
                    self.groups[parent]["elements"].append(node)
                return
            if not isinstance(node, dict):
                return
            info = dict(side.get(node.get("uuid"), {}))
            info.update({k: v for k, v in node.items() if k != "children"})
            g = dict(uuid=info["uuid"], name=info.get("name", info["uuid"][:8]),
                     origin=np.array(info.get("origin", [0, 0, 0]), float),
                     rotation=np.array(info.get("rotation", [0, 0, 0]) or [0, 0, 0], float), parent=parent, elements=[])
            self.groups[g["uuid"]] = g; self.order.append(g["uuid"])
            for c in node.get("children", []):
                walk(c, g["uuid"])
        for n in self.m.get("outliner", []):
            walk(n, None)
        self.by_name = {g["name"]: u for u, g in self.groups.items()}
        self.elements = {e["uuid"]: e for e in self.m.get("elements", []) if e.get("type", "cube") == "cube"}
        self.elem_group = {eu: gu for gu, g in self.groups.items() for eu in g["elements"]}

    def animations(self):
        return {a["name"]: a for a in self.m.get("animations", [])}

    # ── 키프레임 보간 ─────────────────────────────
    @staticmethod
    def _channel(kfs, ch):
        return sorted(((k["time"], np.array([_f(k["data_points"][0].get(a)) for a in "xyz"]), k.get("interpolation", "linear"))
                       for k in kfs if k.get("channel") == ch), key=lambda p: p[0])

    @staticmethod
    def _sample(pts, t):
        if not pts:
            return np.zeros(3)
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for i in range(len(pts) - 1):
            t0, v0, it0 = pts[i]; t1, v1, it1 = pts[i + 1]
            if t0 <= t <= t1:
                if t1 == t0:
                    return v1
                a = (t - t0) / (t1 - t0)
                if it0 == "step":
                    return v0
                if it0 == "catmullrom" or it1 == "catmullrom":
                    p0 = pts[i - 1][1] if i > 0 else v0
                    p3 = pts[i + 2][1] if i + 2 < len(pts) else v1
                    a2, a3 = a * a, a * a * a
                    return 0.5 * ((2 * v0) + (-p0 + v1) * a + (2 * p0 - 5 * v0 + 4 * v1 - p3) * a2 + (-p0 + 3 * v0 - 3 * v1 + p3) * a3)
                return v0 + (v1 - v0) * a
        return pts[-1][1]

    def pose(self, anim, t):
        """시간 t 의 뼈별 (회전 더할 값, 위치 더할 값) — Blockbench 화면 값 그대로."""
        out = {}
        for key, an in (anim.get("animators") or {}).items():
            if an.get("type", "bone") != "bone":
                continue
            u = key if key in self.groups else self.by_name.get(an.get("name"))
            if not u:
                continue
            kfs = an.get("keyframes", [])
            out[u] = (self._sample(self._channel(kfs, "rotation"), t), self._sample(self._channel(kfs, "position"), t))
        return out

    def scales(self, anim, t):
        """시간 t 의 뼈별 크기 배율 (scale 키가 있는 뼈만)."""
        out = {}
        for key, an in (anim.get("animators") or {}).items():
            if an.get("type", "bone") != "bone":
                continue
            u = key if key in self.groups else self.by_name.get(an.get("name"))
            pts = self._channel(an.get("keyframes", []), "scale")
            if u and pts:
                out[u] = self._sample(pts, t)
        return out

    def group_mats(self, anim=None, t=0.0):
        """뼈마다 모델 공간 4×4 행렬."""
        p = self.pose(anim, t) if anim else {}
        sc = self.scales(anim, t) if anim else {}
        mats = {}
        for u in self.order:
            g = self.groups[u]
            dr, dp = p.get(u, (np.zeros(3), np.zeros(3)))
            eul = g["rotation"] + np.array([-dr[0], -dr[1], dr[2]])
            pos = np.array([-dp[0], dp[1], dp[2]])
            S = np.diag(list(sc[u]) + [1.0]) if u in sc else np.eye(4)
            local = T(g["origin"] + pos) @ R4(rot_matrix(*eul)) @ S @ T(-g["origin"])
            mats[u] = (mats[g["parent"]] @ local) if g["parent"] else local
        return mats

    def element_mat(self, eu, mats):
        e = self.elements[eu]
        r = e.get("rotation")
        if isinstance(r, dict):          # 옛 형식 {angle, axis, origin}
            a, ax = r.get("angle", 0), r.get("axis", "y")
            o = np.array(r.get("origin", [0, 0, 0]), float)
            em = T(o) @ R4(rot_matrix(a if ax == "x" else 0, a if ax == "y" else 0, a if ax == "z" else 0)) @ T(-o)
        elif isinstance(r, list) and any(r):
            o = np.array(e.get("origin", [0, 0, 0]), float)
            em = T(o) @ R4(rot_matrix(*r)) @ T(-o)
        else:
            em = np.eye(4)
        gu = self.elem_group.get(eu)
        return (mats[gu] if gu else np.eye(4)) @ em


def rest_pose(model, anim_name, t=0.0):
    """기존 동작의 t 초 자세를 '기본 자세'로 읽는다 → {뼈이름: {"rotation": [x,y,z], "position": [...]}} (0 이 아닌 채널만).
    팩에 따라 모든 동작 키에 기본 자세 값이 들어 있다(예: 머리 x 7.5). 새 동작도 같은 값을 깔아야 인게임 자세가 맞는다."""
    a = model.animations().get(anim_name)
    if a is None:
        raise KeyError(f"기본 자세로 쓸 동작 없음: {anim_name}")
    out = {}
    for u, (r, p) in model.pose(a, t).items():
        ch = {}
        if np.abs(r).max() > 1e-6:
            ch["rotation"] = [round(float(x), 4) for x in r]
        if np.abs(p).max() > 1e-6:
            ch["position"] = [round(float(x), 4) for x in p]
        if ch:
            out[model.groups[u]["name"]] = ch
    return out


def apply_rest(tracks, rest, length):
    """설계 키(기본 자세 기준 상대값)에 기본 자세를 더해 절대값으로. 설계에 없는 뼈·채널은 기본 자세로 고정 키를 깐다."""
    out = {b: {ch: list(pts) for ch, pts in chans.items()} for b, chans in tracks.items()}
    for bone, chans in rest.items():
        for ch, v in chans.items():
            pts = out.setdefault(bone, {}).get(ch)
            if pts:
                out[bone][ch] = [(t, [a + b for a, b in zip(val, v)]) for t, val in pts]
            else:
                out[bone][ch] = [(0, list(v)), (length, list(v))]
    return out


def make_animation(model, name, length, tracks, loop="once", interp="catmullrom", override=False, seed=None):
    """tracks: {뼈이름: {"rotation": [(시간, [x,y,z]), ...], "position": [...]}}  (Blockbench 화면 값)
    override: True 면 다른 동작을 더하지 않고 덮어씀(팩의 공격 동작이 그렇게 돼 있을 때 맞춘다).
    seed: 주면 uuid 를 이름에서 정해진 값으로 만든다 → 다시 만들어도 파일 차이가 키 값 변화만 남는다."""
    def uid(*parts):
        return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "/".join(str(p) for p in (seed, name) + parts))) if seed else str(_uuid.uuid4())
    animators = {}
    for bone, chans in tracks.items():
        u = model.by_name.get(bone)
        if not u:
            raise KeyError(f"뼈 없음: {bone} (inspect_model.py 로 뼈 이름 확인)")
        kfs = []
        for ch, pts in chans.items():
            for i, (t, v) in enumerate(pts):
                kfs.append({"channel": ch, "data_points": [{"x": v[0], "y": v[1], "z": v[2]}], "uuid": uid(bone, ch, i),
                            "time": round(t, 4), "color": -1, "interpolation": interp})
        animators[u] = {"name": bone, "type": "bone", "keyframes": kfs}
    return {"uuid": uid(), "name": name, "loop": loop, "override": bool(override), "length": length, "snapping": 20,
            "selected": False, "anim_time_update": "", "blend_weight": "", "start_delay": "", "loop_delay": "", "animators": animators}
