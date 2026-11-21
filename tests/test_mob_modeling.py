"""mob-modeling 스킬 스크립트 시험 (Blender 없이 돌아가는 부분). 실행: python tests/test_mob_modeling.py

작은 가짜 모델로 핵심 규칙을 붙잡아 둔다:
- 밀도 계산(1 px/유닛 = 인게임 16 px/블록, 배율 반영), 검사 항목(태그 뼈·회전·텍스처 크기·Bezier·히트박스)
- 부품 덧붙이기: 정수 좌표·회전 규칙·없는 뼈 거부, 원본 덮어쓰기 거부, 거울 짝, 새 텍스처 배치
- 다시 칠하기(adjust): 상징색 색상 유지, 투명도 유지 / 밀도 맞추기: 새 색 없이 줄이기, 정수배 키우기
"""
import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "mob-modeling", "scripts")
sys.path.insert(0, SCRIPTS)
sys.dont_write_bytecode = True      # 스킬 폴더에 __pycache__ 를 남기지 않음 (설치기가 폴더째 복사하므로)
import numpy as np  # noqa: E402
import bbtex, density_fix, kitbash, lineup, lint, metrics, reshade, skilldeps, standard  # noqa: E402

TMP = tempfile.mkdtemp()


def tex_png(arr):
    return bbtex.encode_png(arr)


def face_uv(x, y, w, h):
    return {"uv": [x, y, x + w, y + h], "texture": 0}


def make_model(path, tex, uv=16, bones=None, rot=None, bezier=False, hit_y=0.0):
    """뼈 body(큐브 4x8x4, 1 px/유닛) + hitbox. bones: 추가 뼈 이름 → 큐브 하나씩."""
    def cube(uid, frm, to, x0):
        sx, sy, sz = (to[i] - frm[i] for i in range(3))
        f = {"north": face_uv(x0, 0, sx, sy), "south": face_uv(x0 + sx, 0, sx, sy),
             "east": face_uv(x0, sy, sz, sy), "west": face_uv(x0 + sz, sy, sz, sy),
             "up": face_uv(x0, 2 * sy, sx, sz), "down": face_uv(x0 + sx, 2 * sy, sx, sz)}
        e = {"uuid": uid, "name": uid, "type": "cube", "from": frm, "to": to, "origin": [0, 0, 0], "faces": f}
        if rot:
            e["rotation"] = rot
        return e
    els = [cube("body_c", [-2, 0, -2], [2, 8, 2], 0)]
    outl = [{"uuid": "g-body", "name": "body", "origin": [0, 0, 0], "rotation": [0, 0, 0], "children": ["body_c"]}]
    for i, b in enumerate(bones or []):
        c = cube(f"{b}_c", [2, 0, -1], [4, 6, 1], 8)
        els.append(c)
        outl.append({"uuid": f"g-{b}", "name": b, "origin": [3, 6, 0], "rotation": [0, 0, 0], "children": [c["uuid"]]})
    els.append({"uuid": "hb", "name": "hitbox", "type": "cube", "from": [-4, 0, -4], "to": [4, 16, 4], "origin": [0, 0, 0], "faces": {}})
    outl.append({"uuid": "g-hit", "name": "hitbox", "origin": [0, hit_y, 0], "rotation": [0, 0, 0], "children": ["hb"]})
    kf = [{"channel": "rotation", "data_points": [{"x": 0, "y": 0, "z": 0}], "time": 0, "interpolation": "bezier" if bezier else "linear"}]
    m = {"meta": {"format_version": "4.10", "model_format": "free", "box_uv": False},
         "resolution": {"width": uv, "height": uv}, "elements": els, "outliner": outl,
         "textures": [{"name": "t.png", "uuid": "tex-0", "uv_width": uv, "uv_height": uv, "source": tex_png(tex)}],
         "animations": [{"uuid": "a1", "name": "attack", "loop": "once", "length": 1.0,
                         "animators": {"g-body": {"name": "body", "type": "bone", "keyframes": kf}}}]}
    json.dump(m, open(path, "w", encoding="utf-8"))
    return path


def solid(size, rgb=(150, 150, 150), alpha=255):
    a = np.zeros((size, size, 4), np.uint8)
    a[..., :3] = rgb
    a[..., 3] = alpha
    return a


def checker(size, k, c1, c2):
    a = solid(size, c1)
    yy, xx = np.mgrid[0:size, 0:size]
    a[((yy // k + xx // k) % 2) == 1, :3] = c2
    return a


def test_metrics_density():
    p = make_model(os.path.join(TMP, "m1.bbmodel"), solid(16))
    r = metrics.measure(p)
    assert r["cubes"] == 1 and abs(r["px_per_unit"] - 1.0) < 1e-6 and abs(r["px_per_block"] - 16) < 1e-6, r
    assert abs(r["height_blk"] - 0.5) < 1e-6, r
    r2 = metrics.measure(p, scale=2.0)
    assert abs(r2["px_per_block"] - 8) < 1e-6, r2          # 배율 2 → 픽셀이 2배 굵게 보임


def test_lint_flags():
    tex = solid(24)                                          # 2의 거듭제곱 아님
    p = make_model(os.path.join(TMP, "m2.bbmodel"), tex, uv=24, bones=["l_arm"], rot=[10, 5, 0], bezier=True, hit_y=-1)
    res = lint.lint(p)
    w = " ".join(res["warn"]); e = " ".join(res["err"])
    assert "리드" in w, w
    assert "2의 거듭제곱" in w, w
    assert "회전 규칙 밖" in w, w
    assert "Bezier" in w, w
    assert "질식" in e, e
    ok = lint.lint(make_model(os.path.join(TMP, "m3.bbmodel"), solid(16)))
    assert not ok["err"] and not any("회전" in x or "리드" in x for x in ok["warn"]), ok


def test_kitbash_rules_and_output():
    src = make_model(os.path.join(TMP, "k.bbmodel"), solid(16))
    kit = kitbash.Kit(src, seed="t")
    kit.material("cloth", "#8899aa")
    for bad in (lambda: kit.cube("body", [0, 0, 0.5], [1, 1, 1], "cloth"),           # 소수 좌표 → 믹셀
                lambda: kit.cube("body", [0, 0, 0], [1, 1, 1], "cloth", rot=("x", 30)),  # ME 회전 규칙 밖
                lambda: kit.cube("nope", [0, 0, 0], [1, 1, 1], "cloth"),
                lambda: kit.cube("body", [0, 0, 0], [1, 1, 1], "nomat")):
        try:
            bad()
            raise AssertionError("거부해야 함")
        except (ValueError, KeyError):
            pass
    eu = kit.cube("body", [2, 0, -2], [3, 6, 2], "cloth", "cloth", "flap", skip=["west"])
    eu2 = kit.mirror_x("body", [2, 0, -2], [3, 6, 2], "cloth", "cloth", "flap_m", skip=["west"])
    try:
        kit.save(src)
        raise AssertionError("원본 덮어쓰기를 거부해야 함")
    except SystemExit:
        pass
    out = kit.save(os.path.join(TMP, "k_out.bbmodel"))
    m = json.load(open(out, encoding="utf-8"))
    els = {e["uuid"]: e for e in m["elements"]}
    assert eu in els and eu2 in els
    assert els[eu2]["from"] == [-3.0, 0.0, -2.0] and els[eu2]["to"] == [-2.0, 6.0, 2.0], els[eu2]
    assert els[eu]["faces"]["west"]["texture"] is None and els[eu2]["faces"]["east"]["texture"] is None   # skip 이 거울에서 바뀜
    body = next(n for n in m["outliner"] if n["name"] == "body")
    assert eu in body["children"] and eu2 in body["children"]
    assert len(m["textures"]) == 2 and m["textures"][1]["uv_width"] == 16
    img = bbtex.decode_png(m["textures"][1]["source"])
    x0, y0, x1, y1 = (int(v) for v in els[eu]["faces"]["north"]["uv"])
    assert (img[y0:y1, x0:x1, 3] > 0).any(), "칠한 픽셀이 있어야 함"
    r = metrics.measure(out)
    assert r["cubes"] == 3 and abs(r["px_per_unit"] - 1.0) < 1e-6, r     # 새 부품도 같은 밀도


def test_reshade_keeps_signature_color():
    tex = checker(16, 2, (224, 122, 31), (120, 120, 124))           # 주황 + 회색
    src = make_model(os.path.join(TMP, "r.bbmodel"), tex)
    out = reshade.reshade(src, os.path.join(TMP, "r_out.bbmodel"), val=0.5, contrast=0.2, verbose=False)
    a = bbtex.textures(bbtex.load(src))[0]["img"]; b = bbtex.textures(bbtex.load(out))[0]["img"]
    assert (a[..., 3] == b[..., 3]).all(), "투명도는 그대로"
    assert not (a[..., :3] == b[..., :3]).all(), "무언가 바뀌어야 함"
    orange = (a[..., 0] == 224)
    la, lb = reshade.to_oklab(a[orange][:, :3] / 255.0), reshade.to_oklab(b[orange][:, :3] / 255.0)
    ha = np.degrees(np.arctan2(la[:, 2], la[:, 1])); hb = np.degrees(np.arctan2(lb[:, 2], lb[:, 1]))
    d = np.abs((hb - ha + 180) % 360 - 180)
    assert np.median(d) < 15, f"주황 색상이 크게 바뀜: {np.median(d):.1f}도"


def test_density_fix_shrink_and_grow():
    dense = checker(64, 4, (200, 60, 60), (40, 40, 40))             # 4 px/유닛 (uv 16, 텍스처 64)
    src = make_model(os.path.join(TMP, "d.bbmodel"), dense, uv=16)
    m = json.load(open(src, encoding="utf-8"))
    m["textures"][0]["uv_width"] = m["textures"][0]["uv_height"] = 16
    json.dump(m, open(src, "w", encoding="utf-8"))
    before = metrics.measure(src)
    assert abs(before["px_per_unit"] - 4.0) < 1e-6, before
    out = density_fix.fix(src, os.path.join(TMP, "d_out.bbmodel"), verbose=False)
    after = metrics.measure(out)
    assert abs(after["px_per_unit"] - 1.0) < 0.15, after
    old = {tuple(c) for c in dense[..., :3].reshape(-1, 3)}
    new = bbtex.textures(bbtex.load(out))[0]["img"]
    newc = {tuple(c) for c in new[new[..., 3] > 0][:, :3]}
    assert newc <= old, "줄일 때 새 색을 만들면 안 됨"
    coarse = solid(8, (90, 140, 90))                                  # 0.5 px/유닛
    src2 = make_model(os.path.join(TMP, "c.bbmodel"), coarse, uv=16)
    out2 = density_fix.fix(src2, os.path.join(TMP, "c_out.bbmodel"), verbose=False)
    t2 = bbtex.textures(bbtex.load(out2))[0]
    assert t2["w"] == 16 and t2["uv_w"] == 16, t2                      # 정수배 키우기, UV 그대로


def test_lineup_token_and_std_fallback():
    p = make_model(os.path.join(TMP, "l.bbmodel"), solid(16))
    s = lineup.resolve(p + "@1.5=라벨", {}, {})
    assert s["scale"] == 1.5 and s["label"] == "라벨" and s["path"] == os.path.abspath(p), s
    cwd = os.getcwd()
    os.chdir(TMP)
    try:
        sp = skilldeps.std_path()
        assert sp and sp.endswith(os.path.join("templates", "art_standard.json")), sp
        d, _ = standard.load()
        assert d["version"] == 2 and not d.get("profiles"), d          # 범용 템플릿: 1층 기본값만, 프로필 없음
        assert standard.common(d)["base_px_per_block"] == 16
        assert skilldeps.work_dir() == "modeling"
    finally:
        os.chdir(cwd)


def test_two_layer_standard():
    old = {"ranges": {"px_per_block": [14, 18], "val": [0.4, 0.65]}, "dark_theme_ranges": {"val": [0.3, 0.65]}}
    d = standard.convert(old)
    assert d["version"] == 2 and d["default_profile"] == "default"
    assert "px_per_block" not in d["profiles"]["default"]["ranges"], "밀도는 1층으로 옮겨 감"
    assert standard.ranges(d, "default", theme="dark")["val"] == [0.3, 0.65]
    refs = [dict(file=f"r{i}", val=v, sat=0.4, contrast=0.2, noise=0.08, edge_dark=0.5, top_light=0.1, colors95=c, cubes_per_blk=k)
            for i, (v, c, k) in enumerate([(0.60, 12, 30.0), (0.62, 16, 36.0), (0.64, 14, 40.0)])]
    prof = standard.make_profile(refs, "시험")
    rg = prof["ranges"]
    assert rg["val"] == [0.52, 0.72] and rg["colors95"] == [8, 23] and rg["cubes_per_blk"] == [18.0, None], rg
    assert prof["roles"]["보스"]["cubes_per_blk"] == [9.0, None]
    d["profiles"]["p"] = prof
    d["assign"] = {"mob_a": {"profile": "p", "role": "보스", "theme": "dark"}}
    assert standard.pick(d, "MOB_A") == ("p", "보스", "dark"), "모델 ID 는 대소문자 구분 안 함"
    try:
        standard.pick(d, None, "없는프로필")
        raise AssertionError("없는 프로필은 멈춰야 함")
    except SystemExit:
        pass
    r = dict(px_per_block=9.0, colors=80, colors95=30, black_share=0.08, val=0.2)
    keys = {k for k, _, _ in standard.judge_common(r, d, "mob_a", "일반", render_bones=25)}
    assert keys == {"px_per_block", "colors", "colors95", "black_share", "val", "render_bones"}, keys
    d["exceptions"] = {"mob_a": {"px_per_block": "레이드 보스 승인"}}
    ex = {k: e for k, _, e in standard.judge_common(r, d, "mob_a", "일반", render_bones=25)}
    assert ex["px_per_block"] is True and ex["val"] is False


def test_make_profile_cli_and_lint_assign():
    work = tempfile.mkdtemp(dir=TMP)
    refs = []
    for i, g in enumerate((110, 130, 150)):
        refs.append(make_model(os.path.join(work, f"ref{i}.bbmodel"), checker(16, 2, (g, g, g), (g - 60, g - 60, g - 60))))
    target = make_model(os.path.join(work, "target.bbmodel"), solid(16, (40, 40, 40)))
    std = os.path.join(work, "art_standard.json")
    import make_profile
    make_profile.main(["--name", "p1", "--std", std] + refs)
    d = json.load(open(std, encoding="utf-8"))
    assert d["version"] == 2 and "p1" in d["profiles"] and d["default_profile"] == "p1", d
    make_profile.main(["--name", "p1", "--std", std, "--assign", "target", "--role", "정예"])
    d = json.load(open(std, encoding="utf-8"))
    assert d["assign"]["target"] == {"profile": "p1", "role": "정예"}, d["assign"]
    res = lint.lint(target, 1.0, d)
    assert res["profile"] == "p1" and res["role"] == "정예", res
    w = " ".join(res["warn"])
    assert "[스타일 p1·정예]" in w and "명암 폭" in w, w         # 단색 텍스처 → 기준 몹보다 명암 폭이 작다
    assert "[공통] 평균 명도" in w, w                              # 명도 0.16 < 1층 하한 0.25


def test_theme_ranges():
    std = {"ranges": {"val": [0.40, 0.65]}, "dark_theme_ranges": {"val": [0.30, 0.65]}}
    r = {"val": 0.33}
    assert metrics.judge(r, std), "일반 범위에서는 명도 0.33 이 기준 밖"
    assert not metrics.judge(r, std, "dark"), "어두운 테마 범위에서는 통과"


def test_overlaps_and_flipped():
    p = make_model(os.path.join(TMP, "o.bbmodel"), solid(16))
    m = json.load(open(p, encoding="utf-8"))
    dup = json.loads(json.dumps(m["elements"][0]))          # 같은 자리 같은 크기 큐브 → 모든 면이 같은 평면에서 겹침
    dup["uuid"] = "dup"; dup["name"] = "dup"
    flip = json.loads(json.dumps(m["elements"][0]))         # 뒤집힌 복사본 (from>to): 겹침 검사·큐브 수에서 뺀다
    flip["uuid"] = "flip"; flip["name"] = "flip"; flip["from"], flip["to"] = flip["to"], flip["from"]
    m["elements"] += [dup, flip]
    m["outliner"][0]["children"] += ["dup", "flip"]
    json.dump(m, open(p, "w", encoding="utf-8"))
    bb = skilldeps.bbpose().Model(p)
    ov = bbtex.overlaps(bb, bbtex.body_elements(bb))
    assert len(ov) == 6 and all({a, b} == {"body_c", "dup"} for a, _, b, _, _ in ov), ov
    r = metrics.measure(p)
    assert r["cubes"] == 2 and r["flipped"] == 1, r
    w = " ".join(lint.lint(p)["warn"])
    assert "같은 평면" in w and "뒤집힌 큐브" in w, w


def test_kitbash_uses_free_space_first():
    tex = np.zeros((32, 32, 4), np.uint8)
    tex[:16, :16] = (120, 120, 120, 255)                      # 원래 면은 왼쪽 위 16×16 안에만
    p = make_model(os.path.join(TMP, "f.bbmodel"), tex, uv=32)
    kit = kitbash.Kit(p, seed="free")
    kit.material("m", "#808080")
    kit.cube("body", [2, 0, -2], [3, 4, 2], "m", "plain", "part")
    out = kit.save(os.path.join(TMP, "f_out.bbmodel"))
    m = json.load(open(out, encoding="utf-8"))
    assert len(m["textures"]) == 1, "빈 곳이 있으면 새 텍스처를 만들지 않는다"
    img = bbtex.decode_png(m["textures"][0]["source"])
    e = next(e for e in m["elements"] if e["name"] == "part")
    x0, y0, x1, y1 = (int(v) for v in e["faces"]["north"]["uv"])
    assert (img[y0:y1, x0:x1, 3] > 0).all() and not (x1 <= 16 and y1 <= 16), (x0, y0, x1, y1)


def test_kitbash_stable_paint():
    """같은 설계 → 같은 텍스처 (무늬 시드가 실행마다 바뀌지 않음)."""
    src = make_model(os.path.join(TMP, "s.bbmodel"), solid(16))
    outs = []
    for i in range(2):
        kit = kitbash.Kit(src, seed="same")
        kit.material("c", "#8899aa")
        kit.cube("body", [2, 0, -2], [4, 6, 2], "c", "cloth", "flap")
        outs.append(json.load(open(kit.save(os.path.join(TMP, f"s_out{i}.bbmodel")), encoding="utf-8"))["textures"][-1]["source"])
    assert outs[0] == outs[1]


def test_density_fix_emissive_pair_and_pow2():
    """발광 짝(X_e.png)은 본체와 같은 배치로 옮겨지고, --pow2 는 캔버스만 늘린다."""
    dense = checker(64, 4, (200, 60, 60), (40, 40, 40))
    glow = np.zeros((64, 64, 4), np.uint8)
    glow[:8, :8] = (255, 255, 0, 255)                       # 본체 (0..8, 0..8) 픽셀만 빛남
    src = make_model(os.path.join(TMP, "e.bbmodel"), dense, uv=16)
    m = json.load(open(src, encoding="utf-8"))
    m["textures"].append({"name": "t_e.png", "uuid": "tex-e", "uv_width": 16, "uv_height": 16, "source": tex_png(glow)})
    m["textures"][0]["name"] = "t.png"
    json.dump(m, open(src, "w", encoding="utf-8"))
    out = density_fix.fix(src, os.path.join(TMP, "e_out.bbmodel"), verbose=False)
    t = bbtex.textures(bbtex.load(out))
    assert t[1]["w"] == t[0]["w"], "짝도 같은 크기로"
    face = json.load(open(out, encoding="utf-8"))["elements"][0]["faces"]["north"]
    x0, y0, x1, y1 = bbtex.face_rect(face, t[0])              # UV → 픽셀 (UV 크기는 원래 값 그대로 유지됨)
    assert abs(t[0]["uv_w"] - 16) < 1e-6, "텍스처 UV 크기는 바꾸지 않는다"
    lit = t[1]["img"][y0:y1 + 1, x0:x1 + 1, 3] > 0
    others = t[1]["img"][..., 3] > 0
    assert lit.any() and lit.sum() == others.sum(), "빛나는 픽셀은 모두 north 면(원래 왼쪽 위 자리)으로 따라와야 함"
    odd = solid(24)
    src2 = make_model(os.path.join(TMP, "p.bbmodel"), odd, uv=24)
    out2 = density_fix.pad_pow2(src2, os.path.join(TMP, "p_out.bbmodel"), verbose=False)
    t2 = bbtex.textures(bbtex.load(out2))[0]
    assert t2["w"] == 32 and abs(t2["uv_w"] - 32) < 1e-6 and abs(t2["sx"] - 1) < 1e-6, t2
    r1, r2 = metrics.measure(src2), metrics.measure(out2)
    assert abs(r1["px_per_unit"] - r2["px_per_unit"]) < 1e-6, "밀도 그대로"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("ok  ", t.__name__)
        except Exception as ex:  # noqa: BLE001
            failed += 1
            print("FAIL", t.__name__, "-", repr(ex))
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)          # 시험용 임시 폴더 정리
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    sys.exit(1 if failed else 0)
