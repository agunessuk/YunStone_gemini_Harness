"""Blender 안에서 실행: .bbmodel 을 텍스처째 세우고 애니메이션 한 개를 프레임별 그림으로 렌더한다.

사용 (make_motion.py 가 대신 부른다):
  blender -b --factory-startup --python blender_preview.py -- <모델.bbmodel> <동작이름|-> <출력폴더> [fps=20] [크기=320] [front|side|top]
출력: <출력폴더>/<동작이름>_000.png …   (동작이름 '-' 는 기본 자세 한 장)

시점: front = 정면 오른쪽 35° 위에서 본 3/4 (모델 정면은 Blockbench -Z), side = +X 쪽 옆 (화면 오른쪽이 모델 앞),
      top = 위에서 내려다봄 (화면 위쪽이 모델 정면)
렌더: Workbench + 텍스처 색, 직교 카메라. 화면 없이(-b) 돈다.
"""
import base64, math, os, sys, tempfile
import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True      # 스킬 폴더에 __pycache__ 를 남기지 않음
import bbpose

argv = sys.argv[sys.argv.index("--") + 1:]
path, anim_name, outdir = argv[0], argv[1], argv[2]
fps = int(argv[3]) if len(argv) > 3 else 20
size = int(argv[4]) if len(argv) > 4 else 320
view = argv[5] if len(argv) > 5 else "front"
os.makedirs(outdir, exist_ok=True)

M = bbpose.Model(path)
anim = M.animations().get(anim_name) if anim_name != "-" else None
if anim_name != "-" and anim is None:
    raise SystemExit(f"동작 없음: {anim_name}")
# Blockbench(Y 위, 1칸=1/16) → Blender(Z 위, 미터): (x, y, z) → (x, -z, y)
C = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float) @ np.diag([1 / 16, 1 / 16, 1 / 16, 1])

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

mats = []
tmp = tempfile.mkdtemp()
res = M.m.get("resolution", {})
for i, t in enumerate(M.m.get("textures", [])):
    src = t.get("source", "")
    img = None
    if src.startswith("data:image"):
        p = os.path.join(tmp, f"t{i}.png")
        with open(p, "wb") as fh:
            fh.write(base64.b64decode(src.split(",", 1)[1]))
        img = bpy.data.images.load(p)
    mat = bpy.data.materials.new(f"tex{i}")
    mat.use_nodes = True
    nt = mat.node_tree; bsdf = nt.nodes["Principled BSDF"]
    if img:
        tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img; tn.interpolation = "Closest"
        nt.links.new(tn.outputs["Color"], bsdf.inputs["Base Color"]); nt.links.new(tn.outputs["Alpha"], bsdf.inputs["Alpha"])
    mats.append((mat, (t.get("uv_width") or res.get("width", 16)), (t.get("uv_height") or res.get("height", 16))))

FACES = {"north": [(1, 1, 0), (0, 1, 0), (0, 0, 0), (1, 0, 0)], "south": [(0, 1, 1), (1, 1, 1), (1, 0, 1), (0, 0, 1)],
         "east": [(1, 1, 1), (1, 1, 0), (1, 0, 0), (1, 0, 1)], "west": [(0, 1, 0), (0, 1, 1), (0, 0, 1), (0, 0, 0)],
         "up": [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)], "down": [(0, 0, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0)]}

objs = []
for eu, e in M.elements.items():
    if e.get("visibility") is False or (e.get("name") or "").lower() == "hitbox":
        continue
    a, b = np.array(e["from"], float), np.array(e["to"], float)
    inf = e.get("inflate", 0) or 0
    lo, hi = np.minimum(a, b) - inf, np.maximum(a, b) + inf
    verts, faces, uvs, fmats = [], [], [], []
    for fname, corners in FACES.items():
        f = e.get("faces", {}).get(fname)
        if not f or not isinstance(f.get("texture"), int) or f["texture"] >= len(mats):
            continue
        base = len(verts)
        for cx, cy, cz in corners:
            verts.append((hi[0] if cx else lo[0], hi[1] if cy else lo[1], hi[2] if cz else lo[2]))
        faces.append([base + 3, base + 2, base + 1, base])
        mat, uw, uh = mats[f["texture"]]
        u0, v0, u1, v1 = f.get("uv", [0, 0, uw, uh])
        q = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        r = int(f.get("rotation", 0) or 0) // 90 % 4
        q = q[r:] + q[:r] if r else q
        uvs.append([(q[k][0] / uw, 1 - q[k][1] / uh) for k in (3, 2, 1, 0)])
        fmats.append(f["texture"])
    if not faces:
        continue
    me = bpy.data.meshes.new(e.get("name", "cube"))
    me.from_pydata(verts, [], faces)
    uvl = me.uv_layers.new()
    for pi, poly in enumerate(me.polygons):
        for k, li in enumerate(poly.loop_indices):
            uvl.data[li].uv = uvs[pi][k]
    used = sorted(set(fmats))
    for ti in used:
        me.materials.append(mats[ti][0])
    for pi, poly in enumerate(me.polygons):
        poly.material_index = used.index(fmats[pi])
    ob = bpy.data.objects.new(e.get("name", "cube"), me)
    scene.collection.objects.link(ob)
    objs.append((eu, ob))
if not objs:
    raise SystemExit("그릴 큐브가 없음 (텍스처가 파일 안에 들어 있지 않거나 메시 전용 모델)")


def apply(t):
    gm = M.group_mats(anim, t)
    for eu, ob in objs:
        ob.matrix_world = Matrix((C @ M.element_mat(eu, gm)).tolist())


def _pts():
    bpy.context.view_layer.update()
    return np.array([list(ob.matrix_world @ v.co) for _, ob in objs for v in ob.data.vertices])


# 기본 자세(동작 없음)의 발밑을 바닥으로: 땅속에서 올라오는 등장 동작도 땅이 제자리에 있게
gm_rest = M.group_mats()
for eu, ob in objs:
    ob.matrix_world = Matrix((C @ M.element_mat(eu, gm_rest)).tolist())
rest = _pts()
apply(0.0)
pts = np.vstack([rest, _pts()])
lo, hi = pts.min(0), pts.max(0); ctr = (lo + hi) / 2; ext = float(max(hi - lo))
floor_z = min(0.0, float(rest[:, 2].min())) if rest[:, 2].min() > -0.2 else float(rest[:, 2].min())
cam_d = ext * 2.4
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scene.collection.objects.link(cam); scene.camera = cam
cam.data.type = "ORTHO"; cam.data.ortho_scale = ext * 1.45
if view == "top":           # 위에서 내려다봄: 화면 위쪽이 모델 정면(-Z). 좌우로 휘두르는 동작을 볼 때
    cam.location = (ctr[0], ctr[1], ctr[2] + cam_d)
    cam.rotation_euler = (0.0, 0.0, 0.0)
else:
    yaw = math.radians(35 if view == "front" else 90)
    cam.location = (ctr[0] + cam_d * math.sin(yaw), ctr[1] + cam_d * math.cos(yaw), ctr[2] + cam_d * 0.35)
    tgt = bpy.data.objects.new("tgt", None); scene.collection.objects.link(tgt); tgt.location = tuple(ctr)
    c = cam.constraints.new("TRACK_TO"); c.target = tgt; c.track_axis = "TRACK_NEGATIVE_Z"; c.up_axis = "UP_Y"
bpy.ops.mesh.primitive_plane_add(size=ext * 3, location=(ctr[0], ctr[1], floor_z - 0.001))
# 바닥은 중간 회색, 배경은 어둡게 → 검은 모델·흰 모델 모두 바닥과 구분된다
fm = bpy.data.materials.new("floor"); fm.diffuse_color = (0.46, 0.47, 0.50, 1); bpy.context.active_object.data.materials.append(fm)
scene.world = bpy.data.worlds.new("bg"); scene.world.color = (0.16, 0.17, 0.20)

scene.render.engine = "BLENDER_WORKBENCH"
sh = scene.display.shading
sh.light = "STUDIO"; sh.color_type = "TEXTURE"; sh.show_shadows = True
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.image_settings.file_format = "PNG"

length = float(anim["length"]) if anim else 0.0
n = max(1, int(round(length * fps)) + 1) if anim else 1
apply(0.0)
bpy.ops.render.render(write_still=False)      # 예열: 첫 렌더는 텍스처·셰이더가 덜 올라와 틀릴 때가 있어 버린다
for i in range(n):
    apply(min(length, i / fps))
    scene.render.filepath = os.path.join(outdir, f"{anim_name if anim else 'pose'}_{i:03d}.png")
    bpy.ops.render.render(write_still=True)
import shutil
shutil.rmtree(tmp, ignore_errors=True)
print(f"RENDER_DONE {n} frames")
