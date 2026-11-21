"""blender-motion 스킬 스크립트 시험 (Blender 없이 돌아가는 부분). 실행: python tests/test_blender_motion.py

부호 규칙(애니메이션 회전 x·y 반전, 위치 x 반전)은 실제 모델의 기존 동작을 Blender 로 렌더해 Blockbench 와
같게 보이는 것을 확인한 뒤 정한 값이다. 이 시험은 그 규칙이 바뀌지 않게 붙잡아 둔다.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "blender-motion", "scripts")
sys.path.insert(0, SCRIPTS)
sys.dont_write_bytecode = True      # 스킬 폴더에 __pycache__ 를 남기지 않음 (설치기가 폴더째 복사하므로)
import numpy as np  # noqa: E402
import bbpose  # noqa: E402


def kf(ch, t, x, y, z, interp="linear"):
    return {"channel": ch, "data_points": [{"x": x, "y": y, "z": z}], "time": t, "interpolation": interp}


def model_json(new_format=False):
    """뼈 2개(root > arm), 큐브 1개, 동작 swing(arm x 0→90 회전, root 위치 x 0→2)."""
    arm = {"uuid": "g-arm", "name": "arm", "origin": [1, 8, 1], "rotation": [0, 0, 0]}
    root = {"uuid": "g-root", "name": "root", "origin": [0, 0, 0], "rotation": [0, 0, 0]}
    m = {
        "meta": {"format_version": "5.0" if new_format else "4.10", "model_format": "free"},
        "resolution": {"width": 16, "height": 16},
        "elements": [{"uuid": "e1", "name": "arm_cube", "type": "cube", "from": [0, 0, 0], "to": [2, 8, 2], "origin": [1, 8, 1], "faces": {}}],
        "textures": [],
        "animations": [{"uuid": "a1", "name": "swing", "loop": "once", "length": 1.0, "animators": {
            "g-arm": {"name": "arm", "type": "bone", "keyframes": [kf("rotation", 0, 0, 0, 0), kf("rotation", 1, 90, 0, 0)]},
            "g-root": {"name": "root", "type": "bone", "keyframes": [kf("position", 0, 0, 0, 0), kf("position", 1, 2, 0, 0)]}}}],
    }
    if new_format:      # 4.12+ 형식: outliner 에는 uuid·children 만, 뼈 정보는 groups 에
        m["groups"] = [root, arm]
        m["outliner"] = [{"uuid": "g-root", "children": [{"uuid": "g-arm", "children": ["e1"]}]}]
    else:
        m["outliner"] = [dict(root, children=[dict(arm, children=["e1"])])]
    return m


def write(tmp, name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return p


def run(script, *args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, script), *args], capture_output=True, env=env, timeout=60, cwd=cwd)
    return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


def main():
    fails = []

    def expect(name, ok):
        print(f"[{'OK ' if ok else 'FAIL'}] {name}")
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        for fmt in (False, True):
            label = "5.0 형식" if fmt else "4.x 형식"
            M = bbpose.Model(write(tmp, f"m{int(fmt)}.bbmodel", model_json(fmt)))
            a = M.animations()["swing"]
            expect(f"{label}: 뼈 이름 읽기", set(M.by_name) == {"root", "arm"})
            rot, _ = M.pose(a, 0.5)[M.by_name["arm"]]
            expect(f"{label}: linear 보간 0.5초 = 45도", np.allclose(rot, [45, 0, 0]))
            gm = M.group_mats(a, 1.0)
            tip = M.element_mat("e1", gm) @ np.array([1, 0, 1, 1])
            # 회전 x=+90 은 화면 값 → 실제 오일러는 -90. 위치 x=+2 → 실제 이동 -2.
            expect(f"{label}: 회전 x 부호 반전 + 위치 x 부호 반전", np.allclose(tip[:3], [-1, 8, 9], atol=1e-6))

        # catmullrom: 끝점은 키 값 그대로
        pts = [(0, np.zeros(3), "catmullrom"), (1, np.array([10.0, 0, 0]), "catmullrom"), (2, np.zeros(3), "catmullrom")]
        expect("catmullrom 끝점 통과", np.allclose(bbpose.Model._sample(pts, 1.0), [10, 0, 0]))

        # make_animation 구조
        M = bbpose.Model(write(tmp, "s.bbmodel", model_json()))
        an = bbpose.make_animation(M, "poke", 0.5, {"arm": {"rotation": [(0, [0, 0, 0]), (0.5, [-30, 0, 0])]}})
        ok = an["name"] == "poke" and "g-arm" in an["animators"] and len(an["animators"]["g-arm"]["keyframes"]) == 2
        expect("make_animation: 뼈 uuid 로 묶인 키프레임", ok)
        try:
            bbpose.make_animation(M, "x", 1, {"없는뼈": {"rotation": [(0, [0, 0, 0])]}})
            expect("없는 뼈 이름은 거부", False)
        except KeyError:
            expect("없는 뼈 이름은 거부", True)

        # make_motion: 원본 보호, 복사본에 새 동작, 이름 겹침 거부
        work = os.path.join(tmp, "작업", "모션")
        os.makedirs(os.path.join(work, "동작설계"))
        base = write(tmp, "원본.bbmodel", model_json())
        before = hashlib.sha256(open(base, "rb").read()).hexdigest()
        spec = os.path.join(work, "동작설계", "원본.py")
        with open(spec, "w", encoding="utf-8") as fh:
            fh.write(f"BASE = {base!r}\nANIMS = {{'poke': dict(length=0.5, hits=[0.25], desc='찌르기', "
                     f"tracks={{'arm': {{'rotation': [(0, [0, 0, 0]), (0.25, [-60, 0, 0]), (0.5, [0, 0, 0])]}}}})}}\n")
        code, out = run("make_motion.py", spec, "--no-render")
        copy = os.path.join(work, "모델", "원본.bbmodel")
        names = [x["name"] for x in json.load(open(copy, encoding="utf-8"))["animations"]] if os.path.isfile(copy) else []
        expect(f"make_motion: 복사본에 기존+새 동작 ({names})", code == 0 and names == ["swing", "poke"])
        expect("make_motion: 원본 파일은 그대로", hashlib.sha256(open(base, "rb").read()).hexdigest() == before)
        table = os.path.join(work, "미리보기", "원본", "동작표.md")
        expect("make_motion: 동작표에 판정 지연 5틱", os.path.isfile(table) and "| 5 |" in open(table, encoding="utf-8").read())
        with open(spec, "w", encoding="utf-8") as fh:
            fh.write(f"BASE = {base!r}\nANIMS = {{'swing': dict(length=1, hits=[], tracks={{'arm': {{'rotation': [(0, [0, 0, 0])]}}}})}}\n")
        code, out = run("make_motion.py", spec, "--no-render")
        expect("make_motion: 원본에 있는 동작 이름은 거부(종료 코드 2)", code == 2 and "이미 있는 동작" in out)

        # inspect_model: 표와 정점
        code, out = run("inspect_model.py", base)
        expect("inspect_model: 동작 표 출력", code == 0 and "`swing`" in out and "arm" in out)

        # ── 기본 자세(REST_FROM)·덮어쓰기(override): 팩 동작 키에 기본 자세가 들어 있는 모델 ──
        mj = model_json()
        mj["animations"].append({"uuid": "a2", "name": "idle", "loop": "loop", "length": 2.0, "animators": {
            "g-arm": {"name": "arm", "type": "bone", "keyframes": [kf("rotation", 0, 7.5, 0, 0)]},
            "g-root": {"name": "root", "type": "bone", "keyframes": [kf("position", 0, 0, -0.5, 0)]}}})
        base2 = write(tmp, "기본자세.bbmodel", mj)
        M2 = bbpose.Model(base2)
        rest = bbpose.rest_pose(M2, "idle")
        expect("rest_pose: idle 0초의 0 아닌 값만", rest == {"arm": {"rotation": [7.5, 0.0, 0.0]}, "root": {"position": [0.0, -0.5, 0.0]}})
        tr = bbpose.apply_rest({"arm": {"rotation": [(0, [0, 0, 0]), (0.5, [10, 0, 0])]}}, rest, 1.0)
        expect("apply_rest: 설계 키에 기본 자세 더함", tr["arm"]["rotation"] == [(0, [7.5, 0.0, 0.0]), (0.5, [17.5, 0.0, 0.0])])
        expect("apply_rest: 설계에 없는 뼈는 기본 자세로 고정", tr["root"]["position"] == [(0, [0.0, -0.5, 0.0]), (1.0, [0.0, -0.5, 0.0])])
        a1 = bbpose.make_animation(M2, "poke", 1.0, tr, override=True, seed="m")
        a2 = bbpose.make_animation(M2, "poke", 1.0, tr, override=True, seed="m")
        expect("make_animation: override 칸", a1["override"] is True)
        expect("make_animation: seed 를 주면 uuid 가 매번 같음", json.dumps(a1) == json.dumps(a2))

        spec2 = os.path.join(work, "동작설계", "기본자세.py")
        with open(spec2, "w", encoding="utf-8") as fh:
            fh.write(f"BASE = {base2!r}\nREST_FROM = 'idle'\nCOMPARE = 'swing'\nANIMS = {{'poke': dict(length=1.0, hits=[0.5], override=True, "
                     f"tracks={{'arm': {{'rotation': [(0, [0, 0, 0]), (0.5, [-60, 0, 0]), (1.0, [0, 0, 0])]}}}})}}\n")
        code, out = run("make_motion.py", spec2, "--no-render")
        copy2 = json.load(open(os.path.join(work, "모델", "기본자세.bbmodel"), encoding="utf-8"))
        poke = next(x for x in copy2["animations"] if x["name"] == "poke")
        arm_keys = sorted((k for k in poke["animators"]["g-arm"]["keyframes"]), key=lambda k: k["time"])
        expect("make_motion REST_FROM: 키가 절대값(7.5 → −52.5 → 7.5)",
               [k["data_points"][0]["x"] for k in arm_keys] == [7.5, -52.5, 7.5] and poke["override"] is True)
        table2 = open(os.path.join(work, "미리보기", "기본자세", "동작표.md"), encoding="utf-8").read()
        expect("make_motion: 동작표에 수치 검사와 기준 동작", "수치 검사" in table2 and "(팩 원래)" in table2)
        expect("make_motion: 설계 폴더에 __pycache__ 를 만들지 않음", not os.path.exists(os.path.join(work, "동작설계", "__pycache__")))

        # 장면 시트 프레임 고르기: 첫·끝·판정 프레임은 꼭 들어감
        import make_motion
        fr = make_motion.pick_frames(33, {19})
        expect(f"pick_frames: 0·19·32 포함 ({fr})", 0 in fr and 19 in fr and 32 in fr and len(fr) <= 11)

        # 수치 검사: 몸을 3칸 내리면 바닥 아래 −3, 시작 0 / 끝 3 (더하기 동작)
        import pose_check
        mj3 = model_json()
        mj3["animations"] = [{"uuid": "a3", "name": "sink", "loop": "once", "length": 1.0, "animators": {
            "g-root": {"name": "root", "type": "bone", "keyframes": [kf("position", 0, 0, 0, 0), kf("position", 1, 0, -3, 0)]}}}]
        M3 = bbpose.Model(write(tmp, "sink.bbmodel", mj3))
        r = pose_check.measure(M3, M3.animations()["sink"], None)
        expect(f"pose_check: 바닥 아래 −3, 끝 끊김 3 ({r['floor']}, {r['end']})", abs(r["floor"] + 3) < 1e-6 and abs(r["end"] - 3) < 1e-6)
        expect("pose_check: 주의 판단", any("바닥 아래" in v for v in pose_check.verdict(r)))
        # 크기(scale) 키: 뼈 기준점에서 배율 (팩 공격 동작의 팔 늘이기 등이 렌더·검사에 반영)
        mj4 = model_json()
        mj4["animations"][0]["animators"]["g-arm"]["keyframes"] += [kf("scale", 0, 1, 1, 1), kf("scale", 1, 1, 2, 1)]
        M4 = bbpose.Model(write(tmp, "scale.bbmodel", mj4))
        a4 = M4.animations()["swing"]
        o4 = np.array([1, 8, 1.0])
        p40 = (M4.element_mat("e1", M4.group_mats(a4, 0.0)) @ np.array([1, 0, 1, 1.0]))[:3]
        p41 = (M4.element_mat("e1", M4.group_mats(a4, 1.0)) @ np.array([1, 0, 1, 1.0]))[:3]
        d0, d1 = np.linalg.norm(p40 - o4), np.linalg.norm(p41 - (o4 + np.array([-2, 0, 0])))   # 루트 위치 x 2 → 부호 반전 규칙으로 −2
        expect(f"bbpose: 크기 키 1→2 로 팔 끝까지 8→16 칸 ({d0:.2f}, {d1:.2f})", abs(d0 - 8) < 1e-6 and abs(d1 - 16) < 1e-6)

    print(f"\n{'통과' if not fails else '실패 ' + str(len(fails)) + '개'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
