"""new-project 틀 시험. 실행: python tests/test_new_project.py"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skills", "new-project", "scripts", "new_project.py")
GUARD = os.path.join(HERE, "..", "hooks", "guard.py")


def run(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return p.stdout.decode("utf-8")


def main():
    fails = []

    def expect(name, ok):
        print(f"[{'OK ' if ok else 'FAIL'}] {name}")
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "한글 새 프로젝트")
        out = run(proj, "--dry-run")
        expect("미리보기는 아무것도 안 만듦", not os.path.exists(proj) and "미리보기" in out)

        out = run(proj)
        need = ["GEMINI.md", "PROGRESS.md", "INDEX.md", ".gemini/protected.txt", ".gemini/READONLY.example",
                "docs/QUALITY_BAR.md", "docs/FEEDBACK_LOG.md", "input/README.md", "work/README.md",
                "output/README.md", "output/확정/README.md", "handoff/README.md", "review/README.md",
                "prompts/README.md"]
        missing = [n for n in need if not os.path.isfile(os.path.join(proj, *n.split("/")))]
        expect(f"표준 파일 {len(need)}개 생성 (빠진 것: {missing})", not missing)
        lines = [ln for ln in out.splitlines() if ln.startswith("만듦")]
        expect("보호 목록은 맨 마지막에 씀", lines and lines[-1].replace("\\", "/").endswith(".gemini/protected.txt"))
        with open(os.path.join(proj, "GEMINI.md"), encoding="utf-8") as f:
            text = f.read()
        expect("이름 자리표시 채움", "한글 새 프로젝트" in text and "{{" not in text)

        with open(os.path.join(proj, "PROGRESS.md"), "w", encoding="utf-8") as f:
            f.write("내가 쓴 기록")
        out = run(proj)
        with open(os.path.join(proj, "PROGRESS.md"), encoding="utf-8") as f:
            expect("다시 돌려도 기존 파일을 덮어쓰지 않음", f.read() == "내가 쓴 기록" and "만듦 0" in out)

        open(os.path.join(tmp, "x"), "w").close()
        old = os.path.join(tmp, "기존")
        os.makedirs(old)
        open(os.path.join(old, "자료.pdf"), "w").close()
        out = run(old, "--dry-run")
        expect("기존 파일을 원본 후보로 알림", "자료.pdf" in out)

        # 만든 틀에서 훅 보호가 실제로 켜지는지
        import json
        home = tempfile.mkdtemp(prefix="harness-home-")
        payload = {"tool_name": "replace", "tool_input": {"file_path": os.path.join(proj, "input", "README.md")}, "cwd": proj}
        env = dict(os.environ, GEMINI_PROJECT_DIR=proj, GEMINI_CONFIG_DIR=os.path.join(home, ".gemini"), USERPROFILE=home, HOME=home)
        p = subprocess.run([sys.executable, GUARD], input=json.dumps(payload).encode(), capture_output=True, env=env)
        expect("만든 틀에서 input/ 수정이 차단됨", b'"deny"' in p.stdout)
        payload["tool_input"]["file_path"] = os.path.join(proj, "output", "확정", "a.pdf")
        p = subprocess.run([sys.executable, GUARD], input=json.dumps(payload).encode(), capture_output=True, env=env)
        expect("만든 틀에서 output/확정/ 수정이 차단됨", b'"deny"' in p.stdout)

    print()
    print("전부 통과" if not fails else f"실패 {len(fails)}건: {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
