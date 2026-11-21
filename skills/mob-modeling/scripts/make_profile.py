"""2층 스타일 프로필 만들기·몹 지정 (기준 파일에 쓴다).

사용 (프로젝트 루트에서):
  프로필 만들기:  python -I <스킬>/scripts/make_profile.py --name <프로필> [--note "설명"] [--default] <기준 몹> <기준 몹> ...
  몹 지정:        python -I <스킬>/scripts/make_profile.py --name <프로필> --assign <ID,ID,…> [--role 일반] [--theme dark]
  보기:           python -I <스킬>/scripts/make_profile.py --show
  공통: [--std 기준파일]  (없으면 작업/모델링/기준.json 또는 modeling/art_standard.json 을 템플릿에서 새로 만든다)

기준 몹 고르기 (references/style-profiles.md):
- 그 스타일로 "이렇게 보이면 좋겠다"는 몹 3개 이상. 플레이어가 자주 보는 몹이면 더 좋다.
- 같은 화면에 함께 나오는 몹(한 던전)은 한 프로필로 묶는다. 프로젝트 전체 프로필은 2~3개까지.
범위 = 기준 몹 최소 − 여유 ~ 최대 + 여유 (명도 ±0.08, 명암 폭 ±0.04, 노이즈 ±0.02, 테두리 ±0.15, 채도 ±0.10, 위밝음 ±0.08),
색95 = 최소×0.7 ~ 최대×1.4, 큐브/키 하한 = 최소×0.6 (정예 ×0.8, 보스 ×0.5, 소환 없음).
"""
import argparse, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
import metrics, skilldeps, standard  # noqa: E402


def target_path(explicit):
    p = skilldeps.std_path(explicit)
    tmpl = os.path.join(skilldeps.SKILL, "templates", "art_standard.json")
    if p and os.path.abspath(p) != os.path.abspath(tmpl):
        return p
    if explicit:
        return explicit
    return os.path.join("작업", "모델링", "기준.json") if os.path.isdir(os.path.join("작업", "모델링")) \
        else os.path.join("modeling", "art_standard.json")


def show(d, path):
    print(f"기준 파일: {path}")
    c = standard.common(d)
    print("1층 공통:", ", ".join(f"{k}={v}" for k, v in c.items() if not isinstance(v, dict)), "| 렌더 뼈", c["render_bones"])
    for nm, p in (d.get("profiles") or {}).items():
        mark = " (기본)" if d.get("default_profile") == nm else ""
        print(f"\n프로필 {nm}{mark}: {p.get('_설명', '')}\n  기준 몹: {', '.join(p.get('reference') or [])}")
        for k, v in (p.get("ranges") or {}).items():
            print(f"  {standard.NAMES.get(k, k)}: {v}")
        for role, ov in (p.get("roles") or {}).items():
            print(f"  역할 {role}: {ov}")
        for th, ov in (p.get("themes") or {}).items():
            print(f"  테마 {th}: {ov}")
    a = {k: v for k, v in (d.get("assign") or {}).items() if not k.startswith("_")}
    if a:
        print(f"\n몹 지정 {len(a)}개:")
        for k, v in sorted(a.items()):
            print(f"  {k}: {v}")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("refs", nargs="*")
    ap.add_argument("--name")
    ap.add_argument("--note", default="")
    ap.add_argument("--default", action="store_true", help="이 프로필을 기본(default_profile)으로")
    ap.add_argument("--assign", default="", help="이 프로필로 지정할 모델 ID (쉼표)")
    ap.add_argument("--role", default=None)
    ap.add_argument("--theme", default=None)
    ap.add_argument("--std", default=None)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args(argv)
    path = target_path(a.std)
    if os.path.isfile(path):
        d = standard.convert(json.load(open(path, encoding="utf-8")))
    else:
        d = standard.convert(json.load(open(os.path.join(skilldeps.SKILL, "templates", "art_standard.json"), encoding="utf-8")))
    if a.show:
        show(d, path)
        return
    if not a.name:
        raise SystemExit("--name 이 필요함")
    d.setdefault("profiles", {})
    if a.refs:
        files = skilldeps.model_index()
        rows = []
        for t in a.refs:
            sp = skilldeps.resolve(t, files)
            r = metrics.measure(sp["path"], sp["scale"])
            r["file"] = os.path.splitext(os.path.basename(sp["path"]))[0]
            rows.append(r)
        if len(rows) < 3:
            print(f"주의: 기준 몹 {len(rows)}개 — 3개 이상이면 범위가 덜 좁아진다")
        old = d["profiles"].get(a.name) or {}
        prof = standard.make_profile(rows, a.note or old.get("_설명", ""))
        if old.get("themes"):
            prof["themes"] = old["themes"]            # 손으로 넣은 테마 범위는 유지
        d["profiles"][a.name] = prof
        print("| 기준 몹 | " + " | ".join(standard.NAMES[k] for k in standard.STYLE_KEYS) + " |")
        print("|" + "---|" * (len(standard.STYLE_KEYS) + 1))
        for r in rows:
            print(f"| {r['file']} | " + " | ".join(str(r.get(k, "")) for k in standard.STYLE_KEYS) + " |")
        print(f"\n프로필 {a.name} 범위:")
        for k, v in prof["ranges"].items():
            print(f"  {standard.NAMES.get(k, k)}: {v}")
    elif a.name not in d["profiles"]:
        raise SystemExit(f"프로필 {a.name} 이 없음 — 먼저 기준 몹을 주고 만든다")
    if a.default or not d.get("default_profile"):
        d["default_profile"] = a.name
    for mid in [x.strip() for x in a.assign.split(",") if x.strip()]:
        e = {"profile": a.name}
        if a.role:
            e["role"] = a.role
        if a.theme:
            e["theme"] = a.theme
        d.setdefault("assign", {})[mid] = e
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak")             # 바로 전 판 하나는 남긴다
    standard.save(d, path)
    print(f"\n저장: {path}" + (" (이전 판: .bak)" if os.path.isfile(path + '.bak') else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
