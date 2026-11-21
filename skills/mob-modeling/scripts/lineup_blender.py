"""Blender 안에서 실행: 여러 .bbmodel 을 인게임 크기로 나란히 세우고, 마인크래프트 엔티티 조명으로 렌더한다.

사용 (lineup.py 가 대신 부른다):
  blender -b --factory-startup --python lineup_blender.py -- <설정.json>
설정: {"models": [{"path", "scale", "key"}], "out": png, "height": px, "yaw": 도, "pitch": 도,
       "player": true, "bright": 1.0, "show_vfx": false, "pose": {"key": [동작이름, 초]}}
출력: out PNG + out 옆 <out>.json (모델별 화면 x 위치, 키) — 이름표는 lineup.py 가 붙인다.

조명: 마인크래프트 자바판 엔티티 조명과 같은 식
  밝기 = min(1, (max(0, n·L0) + max(0, n·L1)) × 0.6 + 0.4),  L0 = (0.2, 1, -0.7), L1 = (-0.2, 1, 0.7) (정규화, MC 좌표)
  → 윗면 1.0, 앞·뒤 0.74, 옆 0.50, 아랫면 0.40. 텍스처는 최근접(픽셀 그대로), 반투명 없이 알파 0.5 기준 자름.
"""
import json, math, os, sys, tempfile
import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
cfg = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
for d in (cfg.get("bbpose_dir") or "", HERE, os.path.join(HERE, "..", "..", "blender-motion", "scripts"),
          os.path.expanduser("~/.gemini/skills/blender-motion/scripts")):
    if d and os.path.isfile(os.path.join(d, "bbpose.py")):
        sys.path.insert(0, d)
        break
import bbpose, bbtex  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
try:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
except TypeError:
    scene.render.engine = "BLENDER_EEVEE"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.eevee.taa_render_samples = 16
BRIGHT = float(cfg.get("bright", 1.0))

# Blockbench(Y 위, 1칸 = 1/16 블록) → Blender(Z 위, 1 = 1블록): (x, y, z) → (x, -z, y)
C = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float) @ np.diag([1 / 16, 1 / 16, 1 / 16, 1])


def mc_dir(x, y, z):
    v = np.array([x, -z, y], float)
    return tuple(v / np.linalg.norm(v))


L0, L1 = mc_dir(0.2, 1.0, -0.7), mc_dir(-0.2, 1.0, 0.7)


def mc_material(name, img=None, color=None, img_e=None):
    """텍스처(또는 단색) × 마인크래프트 엔티티 조명 → 방출(Emission). 색 계산은 sRGB 공간 곱셈과 같게 f^2.2 를 곱한다.
    img_e: 발광 짝 텍스처(X_e.png) — 조명과 상관없이 그대로 더한다 (OptiFine 발광처럼 보이게)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    N, Lk = nt.nodes.new, nt.links.new
    out = N("ShaderNodeOutputMaterial")
    geo = N("ShaderNodeNewGeometry")

    def mth(op, a, b=None, c=None):
        n = N("ShaderNodeMath"); n.operation = op
        for i, v in enumerate((a, b, c)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                Lk(v, n.inputs[i])
        return n.outputs[0]

    def dot(vec):
        n = N("ShaderNodeVectorMath"); n.operation = "DOT_PRODUCT"
        Lk(geo.outputs["Normal"], n.inputs[0]); n.inputs[1].default_value = vec
        return n.outputs["Value"]

    s = mth("ADD", mth("MAXIMUM", dot(L0), 0.0), mth("MAXIMUM", dot(L1), 0.0))
    f = mth("MINIMUM", mth("MULTIPLY_ADD", s, 0.6, 0.4), 1.0)
    f = mth("POWER", mth("MULTIPLY", f, BRIGHT), 2.2)
    sc = N("ShaderNodeVectorMath"); sc.operation = "SCALE"
    em = N("ShaderNodeEmission")
    if img is not None:
        tn = N("ShaderNodeTexImage"); tn.image = img; tn.interpolation = "Closest"
        Lk(tn.outputs["Color"], sc.inputs[0])
        alpha = mth("GREATER_THAN", tn.outputs["Alpha"], 0.5)
    else:
        sc.inputs[0].default_value = color
        alpha = None
    Lk(f, sc.inputs["Scale"])
    if img_e is not None:
        te = N("ShaderNodeTexImage"); te.image = img_e; te.interpolation = "Closest"
        ea = N("ShaderNodeVectorMath"); ea.operation = "SCALE"
        Lk(te.outputs["Color"], ea.inputs[0]); Lk(te.outputs["Alpha"], ea.inputs["Scale"])
        add = N("ShaderNodeVectorMath"); add.operation = "ADD"
        Lk(sc.outputs["Vector"], add.inputs[0]); Lk(ea.outputs["Vector"], add.inputs[1])
        Lk(add.outputs["Vector"], em.inputs["Color"])
    else:
        Lk(sc.outputs["Vector"], em.inputs["Color"])
    if alpha is not None:
        tr = N("ShaderNodeBsdfTransparent"); mix = N("ShaderNodeMixShader")
        Lk(alpha, mix.inputs[0]); Lk(tr.outputs[0], mix.inputs[1]); Lk(em.outputs[0], mix.inputs[2])
        Lk(mix.outputs[0], out.inputs["Surface"])
    else:
        Lk(em.outputs[0], out.inputs["Surface"])
    mat.use_backface_culling = True
    try:
        mat.surface_render_method = "DITHERED"
    except Exception:
        mat.blend_method = "CLIP"
    return mat


FACES = {"north": [(1, 1, 0), (0, 1, 0), (0, 0, 0), (1, 0, 0)], "south": [(0, 1, 1), (1, 1, 1), (1, 0, 1), (0, 0, 1)],
         "east": [(1, 1, 1), (1, 1, 0), (1, 0, 0), (1, 0, 1)], "west": [(0, 1, 0), (0, 1, 1), (0, 0, 1), (0, 0, 0)],
         "up": [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)], "down": [(0, 0, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0)]}
tmp = tempfile.mkdtemp()


def build(spec, k):
    """모델 하나 → Blender 오브젝트 목록 (기본 자세 또는 지정 동작의 한 순간)."""
    M = bbpose.Model(spec["path"])
    texs = bbtex.textures(M.m, decode=False)
    pairs = bbtex.emissive_pairs(texs)

    def load_img(t):
        if not t["png"]:
            return None
        pth = os.path.join(tmp, f"m{k}_t{t['idx']}.png")
        with open(pth, "wb") as fh:
            fh.write(t["png"])
        return bpy.data.images.load(pth)
    mats = []
    for t in texs:
        img = load_img(t)
        img_e = load_img(texs[pairs[t["idx"]]]) if (img and t["idx"] in pairs and cfg.get("emissive", True)) else None
        mats.append(mc_material(f"m{k}_{t['idx']}", img, img_e=img_e) if img else None)
    pose = spec.get("pose") or (cfg.get("pose") or {}).get(spec.get("key", ""))
    if not pose and cfg.get("rest", True) and "idle" in M.animations():
        pose = ["idle", 0.0]          # 팩이 기본 자세를 idle 에 깔아 둔 경우 인게임 모습 (bind 자세는 인게임에 안 나옴)
    anim = M.animations().get(pose[0]) if pose else None
    gm = M.group_mats(anim, float(pose[1]) if pose else 0.0)
    S = np.diag([spec.get("scale", 1.0)] * 3 + [1.0])
    objs = []
    for eu in bbtex.body_elements(M, cfg.get("show_vfx", False)):
        e = M.elements[eu]
        a, b = np.array(e["from"], float), np.array(e["to"], float)
        inf = e.get("inflate", 0) or 0
        # Blockbench 처럼 from/to 를 순서 그대로 쓴다: from>to 인 축(양면용 뒤집힌 복사본)은 면이 저절로 안쪽을 향한다
        lo, hi = (a, b) if bbtex.is_flipped(e) else (np.minimum(a, b) - inf, np.maximum(a, b) + inf)
        verts, faces, uvs, fm = [], [], [], []
        for fname, corners in FACES.items():
            f = (e.get("faces") or {}).get(fname)
            if not f or not f.get("uv"):
                continue
            ti = bbtex.face_tex(f, texs)
            if ti is None or mats[ti] is None:
                continue
            t = texs[ti]
            base = len(verts)
            for cx, cy, cz in corners:
                verts.append((hi[0] if cx else lo[0], hi[1] if cy else lo[1], hi[2] if cz else lo[2]))
            faces.append([base + 3, base + 2, base + 1, base])
            u0, v0, u1, v1 = f["uv"]
            q = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
            r = int(f.get("rotation", 0) or 0) // 90 % 4
            q = q[r:] + q[:r] if r else q
            fr = t["frames"]
            uvs.append([(q[j][0] / t["uv_w"], 1 - q[j][1] / t["uv_h"] / fr) for j in (3, 2, 1, 0)])
            fm.append(ti)
        if not faces:
            continue
        me = bpy.data.meshes.new(e.get("name", "cube"))
        me.from_pydata(verts, [], faces)
        uvl = me.uv_layers.new()
        for pi, poly in enumerate(me.polygons):
            for j, li in enumerate(poly.loop_indices):
                uvl.data[li].uv = uvs[pi][j]
        used = sorted(set(fm))
        for ti in used:
            me.materials.append(mats[ti])
        for pi, poly in enumerate(me.polygons):
            poly.material_index = used.index(fm[pi])
        ob = bpy.data.objects.new(e.get("name", "cube"), me)
        scene.collection.objects.link(ob)
        ob.matrix_world = Matrix((S @ C @ M.element_mat(eu, gm)).tolist())
        objs.append(ob)
    return objs


def bbox(objs):
    bpy.context.view_layer.update()
    pts = np.array([list(ob.matrix_world @ v.co) for ob in objs for v in ob.data.vertices])
    return pts.min(0), pts.max(0)


def move(objs, d):
    for ob in objs:
        ob.matrix_world = Matrix.Translation(Vector(d)) @ ob.matrix_world


groups = []
for k, spec in enumerate(cfg["models"]):
    objs = build(spec, k)
    if not objs:
        print("빈 모델:", spec["path"]); continue
    groups.append((spec, objs))

# 플레이어 크기 비교용 (키 1.8블록, 단색)
if cfg.get("player", True):
    pm = mc_material("player", color=(0.33, 0.36, 0.42))
    parts = [((-0.25, -0.125, 0.0), (0.0, 0.125, 0.75)), ((0.0, -0.125, 0.0), (0.25, 0.125, 0.75)),
             ((-0.25, -0.125, 0.75), (0.25, 0.125, 1.375)), ((-0.5, -0.125, 0.75), (-0.25, 0.125, 1.375)),
             ((0.25, -0.125, 0.75), (0.5, 0.125, 1.375)), ((-0.25, -0.25, 1.375), (0.25, 0.25, 1.875))]
    pobjs = []
    for lo, hi in parts:
        bpy.ops.mesh.primitive_cube_add(size=1)
        ob = bpy.context.active_object
        ob.scale = [(h - l) for l, h in zip(lo, hi)]; ob.location = [(h + l) / 2 for l, h in zip(lo, hi)]
        ob.data.materials.append(pm)
        pobjs.append(ob)
    bpy.context.view_layer.update()
    for ob in pobjs:
        ob.data.transform(ob.matrix_basis); ob.matrix_basis = Matrix.Identity(4)
    groups.insert(0, ({"key": "__player__", "scale": 1.0}, pobjs))

# 모델마다 yaw 만큼 돌려 3/4 시점으로 (카메라는 정면) → 모두 같은 바닥선에 선다
yaw, pitch = math.radians(cfg.get("yaw", 28)), math.radians(cfg.get("pitch", 16))
for spec, objs in groups:
    if spec.get("key") == "__player__":
        continue
    lo, hi = bbox(objs)
    c = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0))
    R = Matrix.Translation(c) @ Matrix.Rotation(yaw, 4, "Z") @ Matrix.Translation(-c)
    for ob in objs:
        ob.matrix_world = R @ ob.matrix_world


SLOTS = []      # 칸 양 끝 x (이름표가 잘리지 않게 화면 범위에 넣는다)


def layout(slot_min):
    """앞(+Y)에서 보면 화면 오른쪽이 -X → -X 쪽으로 늘어놓는다. 칸 폭 = max(모델 폭, slot_min) + 간격."""
    cursor, out = 0.0, []
    SLOTS.clear()
    for spec, objs in groups:
        lo, hi = bbox(objs)
        wdt = hi[0] - lo[0]
        slot = max(wdt, slot_min)
        move(objs, (cursor - (slot - wdt) / 2 - hi[0], -(lo[1] + hi[1]) / 2, -lo[2]))
        lo2, hi2 = bbox(objs)
        out.append((spec, objs, lo2, hi2))
        SLOTS.append((cursor - slot, cursor))
        cursor -= slot + float(cfg.get("gap", 0.35))
    return out


PPB = float(cfg.get("px_per_blk", 110))       # 1블록 = 몇 px (줄이 여러 개여도 같은 값 → 크기 비교가 맞다)
placed = layout(float(cfg.get("label_px", 0)) / PPB)
allo = np.min([p[2] for p in placed], 0); alhi = np.max([p[3] for p in placed], 0)

# 바닥: 1블록 바둑판 (크기 눈금)
pad = 2.0
bpy.ops.mesh.primitive_plane_add(size=1, location=((allo[0] + alhi[0]) / 2, (allo[1] + alhi[1]) / 2, -0.002))
fl = bpy.context.active_object
fl.scale = (alhi[0] - allo[0] + pad * 2, alhi[1] - allo[1] + pad * 2, 1)
fmat = bpy.data.materials.new("floor"); fmat.use_nodes = True
nt = fmat.node_tree; nt.nodes.clear()
o = nt.nodes.new("ShaderNodeOutputMaterial"); tc = nt.nodes.new("ShaderNodeTexCoord")
sep = nt.nodes.new("ShaderNodeSeparateXYZ"); nt.links.new(tc.outputs["Object"], sep.inputs[0])
# 오브젝트 좌표 → 월드 블록 좌표: x·scale + 중심
def _m(op, a, b):
    n = nt.nodes.new("ShaderNodeMath"); n.operation = op
    for i, v in enumerate((a, b)):
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            nt.links.new(v, n.inputs[i])
    return n.outputs[0]
wx = _m("FLOOR", _m("ADD", _m("MULTIPLY", sep.outputs["X"], fl.scale[0]), fl.location[0]), 0)
wy = _m("FLOOR", _m("ADD", _m("MULTIPLY", sep.outputs["Y"], fl.scale[1]), fl.location[1]), 0)
chk = _m("FLOORED_MODULO", _m("ADD", wx, wy), 2.0)
mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"
nt.links.new(chk, mix.inputs["Factor"])
mix.inputs[6].default_value = (0.115, 0.12, 0.135, 1); mix.inputs[7].default_value = (0.095, 0.1, 0.112, 1)
em = nt.nodes.new("ShaderNodeEmission"); nt.links.new(mix.outputs[2], em.inputs["Color"])
nt.links.new(em.outputs[0], o.inputs["Surface"])
fl.data.materials.append(fmat)

scene.world = bpy.data.worlds.new("bg")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.022, 0.024, 0.03, 1)

# 카메라: 직교, 앞(+Y 쪽, 모델 정면) 오른쪽 yaw 도, 위에서 pitch 도
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam); scene.camera = cam
cam.data.type = "ORTHO"
cam.data.sensor_fit = "HORIZONTAL"      # ortho_scale = 화면 가로 폭
cam.data.clip_end = 2000
fwd = Vector((0.0, -math.cos(pitch), -math.sin(pitch)))
cam.rotation_euler = fwd.to_track_quat("-Z", "Y").to_euler()
bpy.context.view_layer.update()
inv = cam.matrix_world.inverted()
pts = []
for x0, x1 in SLOTS:                     # 칸 끝도 화면 안에 (작은 몹의 긴 이름표가 잘리지 않게)
    for x in (x0, x1):
        pts.append(inv @ Vector((x, 0.0, 0.0)))
for _, objs, lo, hi in placed:
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                pts.append(inv @ Vector((x, y, z)))
pts = np.array([list(p) for p in pts])
cmin, cmax = pts.min(0), pts.max(0)
mg = 0.35                                       # 위아래·양옆 여백 (블록)
w, h = (cmax[0] - cmin[0]) + 2 * mg, (cmax[1] - cmin[1]) + 2 * mg
W, H = max(64, int(round(w * PPB))), max(64, int(round(h * PPB)))
scene.render.resolution_x, scene.render.resolution_y = W, H
cam.data.ortho_scale = W / PPB
center_cam = Vector(((cmin[0] + cmax[0]) / 2, (cmin[1] + cmax[1]) / 2, cmax[2] + 30))
cam.location = cam.matrix_world @ center_cam
bpy.context.view_layer.update()
scene.render.image_settings.file_format = "PNG"
bpy.ops.render.render(write_still=False)        # 예열: 첫 렌더는 셰이더·텍스처가 덜 올라와 틀릴 때가 있어 버린다
scene.render.filepath = cfg["out"]
bpy.ops.render.render(write_still=True)

from bpy_extras.object_utils import world_to_camera_view
info = []
for spec, objs, lo, hi in placed:
    c = world_to_camera_view(scene, cam, Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0)))
    info.append(dict(key=spec.get("key"), x=c.x * W, y=(1 - c.y) * H, height_blk=round(float(hi[2] - lo[2]), 2)))
json.dump(dict(width=W, height=H, models=info), open(cfg["out"] + ".json", "w", encoding="utf-8"), ensure_ascii=False)
import shutil
shutil.rmtree(tmp, ignore_errors=True)
print("LINEUP_DONE", W, H)
