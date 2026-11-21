"""다른 스킬·프로젝트 파일 찾기 (mob-modeling 스크립트 공용).

- bbpose.py(뼈대 자세 계산)·make_motion.find_blender 는 blender-motion 스킬의 것을 쓴다.
  찾는 순서: 이 폴더 → 옆 스킬 폴더(../../blender-motion/scripts) → ~/.gemini/skills/blender-motion/scripts
- 아트 기준 파일(2층 구조, standard.py): --std → 환경변수 MOB_MODELING_STD → 작업/모델링/기준.json → modeling/art_standard.json
  → art_standard.json → 이 스킬의 templates/art_standard.json (1층 기본값만, 프로필 없음)
- 작업 폴더: 기준 파일 paths.work → 작업/모델링(있으면) → modeling
- 모델 찾기: 기준 파일 paths.models → 작업/풀어본_자료 → 현재 폴더 아래 전체 (원본/·input/·.git 등 제외)
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)


def blender_motion_dir():
    for d in (HERE, os.path.join(SKILL, "..", "blender-motion", "scripts"),
              os.path.expanduser("~/.gemini/skills/blender-motion/scripts")):
        if os.path.isfile(os.path.join(d, "bbpose.py")):
            return os.path.abspath(d)
    raise SystemExit("blender-motion 스킬(bbpose.py)을 찾지 못함 — 로컬 하네스 install.py 로 함께 설치하세요")


def bbpose():
    d = blender_motion_dir()
    if d not in sys.path:
        sys.path.insert(0, d)
    import bbpose as mod
    return mod


def find_blender():
    d = blender_motion_dir()
    if d not in sys.path:
        sys.path.insert(0, d)
    import make_motion
    return make_motion.find_blender()


SKIP_DIRS = ("원본/", "input/", ".git/", "node_modules/", "__pycache__/")


def _paths():
    """기준 파일의 paths (없으면 빈 dict). standard.load 를 부르지 않는 가벼운 버전."""
    p = std_path()
    if not p:
        return {}
    try:
        return json.load(open(p, encoding="utf-8")).get("paths") or {}
    except Exception:
        return {}


def work_dir():
    w = _paths().get("work")
    if w:
        return w
    return os.path.join("작업", "모델링") if os.path.isdir(os.path.join("작업", "모델링")) else "modeling"


def model_index():
    """프로젝트의 모델 ID → 파일 (작업/모션/점검_원자료.json 이 있으면). 없으면 빈 사전."""
    files = {}
    p = os.path.join("작업", "모션", "점검_원자료.json")
    if os.path.isfile(p):
        d = json.load(open(p, encoding="utf-8"))
        for dg in d.get("dungeons", []):
            for m in dg.get("mobs", []):
                if m.get("model") and m["model"] not in files:
                    files[m["model"]] = m.get("file")
    return files


def resolve(token, files=None):
    """'<모델ID 또는 경로>[@배율][=이름표]' → dict(path, scale, key, label).
    ID 는 점검_원자료.json → 작업/풀어본_자료/**/<ID>.bbmodel → 현재 폴더 아래 전체(원본/·.git 제외) 순으로 찾는다.
    이름표가 없으면 모델 ID (던전마다 몹 이름이 달라 자동으로 붙이지 않는다)."""
    import glob
    label = None
    if "=" in token:
        token, label = token.split("=", 1)
    scale = 1.0
    if "@" in token:
        token, s = token.rsplit("@", 1)
        scale = float(s)
    if os.path.isfile(token):
        path = token
        mid = os.path.splitext(os.path.basename(token))[0]
    else:
        mid = token
        path = (files if files is not None else model_index()).get(mid)
        if not path or not os.path.isfile(path):
            roots = list(_paths().get("models") or []) + [os.path.join("작업", "풀어본_자료")]
            hits = []
            for rt in roots:
                hits = sorted(glob.glob(os.path.join(rt, "**", mid + ".bbmodel"), recursive=True))
                if hits:
                    break
            if not hits:
                hits = sorted(h for h in glob.glob(os.path.join("**", mid + ".bbmodel"), recursive=True)
                              if not any(s in h.replace("\\", "/") + "/" for s in SKIP_DIRS))
            if not hits:
                raise SystemExit(f"모델을 찾지 못함: {mid}")
            path = hits[0]
    return dict(path=os.path.abspath(path), scale=scale, key=mid, label=label or mid)


def std_path(explicit=None):
    for p in (explicit, os.environ.get("MOB_MODELING_STD"), os.path.join("작업", "모델링", "기준.json"),
              os.path.join("modeling", "art_standard.json"), "art_standard.json",
              os.path.join(SKILL, "templates", "art_standard.json")):
        if p and os.path.isfile(p):
            return p
    return None


def load_std(explicit=None):
    p = std_path(explicit)
    return (json.load(open(p, encoding="utf-8")), p) if p else ({}, None)
