"""로컬 제미나이 하네스 설치기 (Windows·Mac·Linux 공용).

  python install.py              설치 또는 갱신
  python install.py --dry-run    바꾸지 않고 할 일만 출력
  python install.py --uninstall  이 하네스가 설치한 것만 제거
  python install.py --check      설치 상태 점검 (훅 Python 경로, 정본·설치본 차이, 훅 동작)

설치하는 것
  ~/.gemini/skills/<스킬>          표지 파일(.local-harness)이 있는 폴더만 교체. 같은 이름의 남의 스킬은 건너뜀
  ~/.gemini/hooks/local-harness/   훅 스크립트
  ~/.gemini/GEMINI.md              표지 주석 사이 블록만 넣거나 교체. 나머지 내용은 그대로
  ~/.gemini/settings.json          훅 연결만 추가 (BeforeTool, SessionStart). 바꾸기 전에 백업 파일을 만든다
"""
import argparse
import json
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
GEMINI_HOME = os.environ.get("GEMINI_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".gemini")
MARKER = ".local-harness"
HOOK_DIR = os.path.join(GEMINI_HOME, "hooks", "local-harness")
BLOCK_START = "<!-- local-harness:start (로컬 하네스가 관리하는 블록. 직접 고치면 다음 설치 때 덮어씀) -->"
BLOCK_END = "<!-- local-harness:end -->"
BEFORETOOL_MATCHER = "write_file|replace|edit_file|multi_edit|run_shell_command"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class Plan:
    def __init__(self, dry):
        self.dry = dry

    def say(self, msg):
        print(("[미리보기] " if self.dry else "") + msg)


def fwd(p):
    return p.replace("\\", "/")


def hook_command(script):
    return f'"{fwd(sys.executable)}" "{fwd(os.path.join(HOOK_DIR, script))}"'


# ---------- 스킬 ----------
def install_skills(plan, uninstall=False):
    src_root = os.path.join(REPO, "skills")
    dst_root = os.path.join(GEMINI_HOME, "skills")
    for name in sorted(os.listdir(src_root)):
        src = os.path.join(src_root, name)
        if not os.path.isfile(os.path.join(src, "SKILL.md")):
            continue
        dst = os.path.join(dst_root, name)
        ours = os.path.isfile(os.path.join(dst, MARKER))
        if os.path.exists(dst) and not ours:
            plan.say(f"건너뜀: 스킬 {name} (같은 이름의 다른 스킬이 이미 있음)")
            continue
        if uninstall:
            if ours:
                plan.say(f"제거: 스킬 {name}")
                if not plan.dry:
                    shutil.rmtree(dst)
            continue
        plan.say(("갱신" if ours else "설치") + f": 스킬 {name}")
        if not plan.dry:
            if ours:
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            with open(os.path.join(dst, MARKER), "w", encoding="utf-8") as f:
                f.write(f"installed from {REPO} at {time.strftime('%Y-%m-%d %H:%M')}\n")


# ---------- 훅 파일 ----------
def install_hook_files(plan, uninstall=False):
    if uninstall:
        if os.path.isdir(HOOK_DIR):
            plan.say(f"제거: {HOOK_DIR}")
            if not plan.dry:
                shutil.rmtree(HOOK_DIR)
        return
    plan.say(f"복사: 훅 스크립트 -> {HOOK_DIR}")
    if not plan.dry:
        os.makedirs(HOOK_DIR, exist_ok=True)
        for fn in os.listdir(os.path.join(REPO, "hooks")):
            if fn.endswith(".py"):
                shutil.copy2(os.path.join(REPO, "hooks", fn), os.path.join(HOOK_DIR, fn))


# ---------- GEMINI.md ----------
def install_gemini_md(plan, uninstall=False):
    path = os.path.join(GEMINI_HOME, "GEMINI.md")
    current = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as f:
            current = f.read()
    has_block = BLOCK_START in current and BLOCK_END in current
    if has_block:
        before, rest = current.split(BLOCK_START, 1)
        after = rest.split(BLOCK_END, 1)[1]
    else:
        before, after = current, ""

    if uninstall:
        if has_block:
            plan.say(f"제거: {path}의 하네스 블록")
            new = (before.rstrip() + "\n" + after.lstrip()).strip()
            if not plan.dry:
                if new:
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(new + "\n")
                else:
                    os.remove(path)
        return

    with open(os.path.join(REPO, "global", "GEMINI.md"), encoding="utf-8-sig") as f:
        body = f.read().strip()
    block = f"{BLOCK_START}\n{body}\n{BLOCK_END}"
    if has_block:
        new = before + block + after
        plan.say(f"갱신: {path}의 하네스 블록")
    elif current.strip():
        new = current.rstrip() + "\n\n" + block + "\n"
        plan.say(f"추가: 기존 {path} 끝에 하네스 블록")
    else:
        new = block + "\n"
        plan.say(f"생성: {path}")
    if not plan.dry:
        os.makedirs(GEMINI_HOME, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)


# ---------- settings.json ----------
def strip_ours(groups):
    kept = []
    for g in groups or []:
        hooks = [h for h in g.get("hooks", []) if "local-harness" not in h.get("command", "")]
        if hooks:
            kept.append(dict(g, hooks=hooks))
    return kept


def install_settings(plan, uninstall=False):
    path = os.path.join(GEMINI_HOME, "settings.json")
    settings = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as f:
            text = f.read().strip()
        settings = json.loads(text) if text else {}
    hooks = settings.get("hooks", {})

    for event in ("BeforeTool", "SessionStart"):
        hooks[event] = strip_ours(hooks.get(event))
    if not uninstall:
        hooks["BeforeTool"].append({
            "matcher": BEFORETOOL_MATCHER,
            "hooks": [{
                "name": "local-harness-guard",
                "type": "command",
                "command": hook_command("guard.py"),
                "timeout": 10000,
                "description": "원본 보호, 위험 명령 차단, 읽기 전용 모드, 하네스 자기 보호",
            }],
        })
        hooks["SessionStart"].append({
            "matcher": "*",
            "hooks": [{
                "name": "local-harness-session-restore",
                "type": "command",
                "command": hook_command("session_restore.py"),
                "timeout": 10000,
                "description": "세션 시작/재개 시 PROGRESS.md, 읽기 전용, 보호 목록 주입",
            }],
        })
    hooks = {k: v for k, v in hooks.items() if v}
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)

    plan.say(("제거" if uninstall else "연결") + f": {path}의 훅 (BeforeTool, SessionStart)")
    if not plan.dry:
        if os.path.isfile(path):
            backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(path, backup)
            plan.say(f"백업: {backup}")
        os.makedirs(GEMINI_HOME, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.write("\n")


# ---------- 점검 ----------
def check():
    """설치 상태 점검. 훅은 실패하면 조용히 통과하도록 만들어서, 꺼져도 티가 안 난다. 그래서 따로 본다."""
    import filecmp
    import re as _re
    import subprocess
    problems = []
    path = os.path.join(GEMINI_HOME, "settings.json")
    try:
        with open(path, encoding="utf-8-sig") as f:
            settings = json.load(f)
    except (OSError, ValueError) as e:
        settings = {}
        problems.append(f"settings.json을 읽지 못함: {e}")
    for event in ("BeforeTool", "SessionStart"):
        cmds = [h.get("command", "") for g in settings.get("hooks", {}).get(event, []) for h in g.get("hooks", [])
                if "local-harness" in h.get("command", "")]
        if not cmds:
            problems.append(f"{event} 훅이 연결되어 있지 않음 → install.py 다시 실행")
            continue
        exe = (_re.findall(r'"([^"]+)"', cmds[0]) or [""])[0]
        if not os.path.isfile(exe):
            problems.append(f"{event} 훅의 Python이 없음: {exe} (Python을 다시 깔았거나 옮김) → install.py 다시 실행")
        else:
            print(f"OK  {event} 훅 Python: {exe}")
    for fn in ("guard.py", "session_restore.py"):
        src, dst = os.path.join(REPO, "hooks", fn), os.path.join(HOOK_DIR, fn)
        if not os.path.isfile(dst):
            problems.append(f"훅 파일 없음: {dst}")
        elif not filecmp.cmp(src, dst, shallow=False):
            problems.append(f"설치된 훅이 정본과 다름: {fn} → install.py 다시 실행")
        else:
            print(f"OK  훅 파일 {fn} = 정본")
    for name in sorted(os.listdir(os.path.join(REPO, "skills"))):
        src = os.path.join(REPO, "skills", name, "SKILL.md")
        dst = os.path.join(GEMINI_HOME, "skills", name, "SKILL.md")
        if not os.path.isfile(src):
            continue
        if not os.path.isfile(dst):
            problems.append(f"스킬 미설치: {name}")
        elif not filecmp.cmp(src, dst, shallow=False):
            problems.append(f"설치된 스킬이 정본과 다름: {name} (설치본을 직접 고쳤으면 정본에 옮긴 뒤 재설치)")
        else:
            print(f"OK  스킬 {name} = 정본")
    # 훅이 실제로 판정을 내리는지: rm -rf 는 언제나 차단(deny)이어야 한다
    guard = os.path.join(HOOK_DIR, "guard.py")
    if os.path.isfile(guard):
        payload = json.dumps({"tool_name": "run_shell_command", "tool_input": {"command": "rm -rf build"}, "cwd": REPO}).encode()
        r = subprocess.run([sys.executable, guard], input=payload, capture_output=True, timeout=20)
        if b'"deny"' in r.stdout:
            print("OK  훅 판정 동작 (rm -rf → 차단/승인 필요)")
        else:
            problems.append("훅이 판정을 내지 않음 (rm -rf 가 통과됨)")
    print()
    if problems:
        print(f"문제 {len(problems)}건:")
        for p in problems:
            print(" - " + p)
    else:
        print("점검 통과")
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser(description="로컬 제미나이 하네스 설치기")
    ap.add_argument("--dry-run", action="store_true", help="바꾸지 않고 할 일만 출력")
    ap.add_argument("--uninstall", action="store_true", help="이 하네스가 설치한 것만 제거")
    ap.add_argument("--check", action="store_true", help="설치 상태 점검(훅 Python 경로, 정본과 설치본 차이, 훅 동작)")
    args = ap.parse_args()

    if args.check:
        sys.exit(check())

    plan = Plan(args.dry_run)
    plan.say(f"대상: {GEMINI_HOME}")
    plan.say(f"Python: {sys.executable}")
    install_skills(plan, args.uninstall)
    install_hook_files(plan, args.uninstall)
    install_gemini_md(plan, args.uninstall)
    install_settings(plan, args.uninstall)
    if not args.dry_run:
        print("완료. 이미 열려 있는 Gemini CLI 세션은 새로 시작해야 훅과 스킬이 적용됩니다.")


if __name__ == "__main__":
    main()
