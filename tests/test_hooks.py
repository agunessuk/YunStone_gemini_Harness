"""훅 동작 시험. 실행: python tests/test_hooks.py

임시 프로젝트 폴더를 만들고 훅에 가짜 입력을 넣어 판정을 확인한다.
막혀야 할 것은 막히고, 막히면 안 되는 것은 통과하는지 둘 다 본다.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "..", "hooks", "guard.py")
RESTORE = os.path.join(HERE, "..", "hooks", "session_restore.py")


FAKE_HOME = tempfile.mkdtemp(prefix="harness-home-")  # 시험 기록이 실제 events.jsonl에 섞이지 않게


def run(script, payload, project):
    env = dict(os.environ, GEMINI_PROJECT_DIR=project, GEMINI_CONFIG_DIR=os.path.join(FAKE_HOME, ".gemini"), USERPROFILE=FAKE_HOME, HOME=FAKE_HOME)
    p = subprocess.run([sys.executable, script], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       capture_output=True, env=env, timeout=20)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    out = p.stdout.decode("utf-8").strip()
    return json.loads(out) if out else None


def decision(project, tool, tool_input):
    res = run(GUARD, {"tool_name": tool, "tool_input": tool_input, "cwd": project}, project)
    return res["decision"] if res else "pass"


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def approve(project, command, once=True, expires_in=3600, card="C0001"):
    """워크벤치가 쓰는 것과 같은 형식으로 사용자 승인을 남긴다."""
    import hashlib
    import time
    path = os.path.join(project, ".gemini", "approvals.json")
    data = {"approvals": []}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    h = hashlib.sha256(command.strip().replace("\r\n", "\n").encode("utf-8")).hexdigest()
    data["approvals"].append({"sha256": h, "command": command, "once": once,
                              "expires": time.time() + expires_in, "used": 0, "card": card})
    write(path, json.dumps(data, ensure_ascii=False))


def approval_used(project, command):
    import hashlib
    with open(os.path.join(project, ".gemini", "approvals.json"), encoding="utf-8") as f:
        data = json.load(f)
    h = hashlib.sha256(command.encode("utf-8")).hexdigest()
    return [a.get("used", 0) for a in data["approvals"] if a["sha256"] == h]


def main():
    failures = []

    def expect(name, got, want):
        mark = "OK " if got == want else "FAIL"
        print(f"[{mark}] {name}: {got} (기대 {want})")
        if got != want:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "한글 프로젝트")
        write(os.path.join(proj, ".gemini", "protected.txt"), "# 원본\ninput/\n인계용/*.zip\n원본.pdf\n")
        write(os.path.join(proj, "input", "a.txt"), "x")

        p = lambda *xs: os.path.join(proj, *xs)
        # H1 원본 보호: 파일 도구
        expect("보호 폴더 안 파일 수정", decision(proj, "replace", {"file_path": p("input", "a.txt")}), "deny")
        expect("보호 ZIP 덮어쓰기", decision(proj, "write_file", {"file_path": p("인계용", "pack.zip")}), "deny")
        expect("보호 파일 이름 일치", decision(proj, "write_file", {"file_path": p("원본.pdf")}), "deny")
        expect("보호 아닌 파일 수정", decision(proj, "write_file", {"file_path": p("work", "a.txt")}), "pass")
        expect("보호 폴더 밖 같은 이름", decision(proj, "write_file", {"file_path": p("work", "input", "a.txt")}), "pass")
        expect("인계용 폴더의 md는 통과", decision(proj, "write_file", {"file_path": p("인계용", "README.md")}), "pass")
        expect("보호 목록 파일 수정은 확인", decision(proj, "replace", {"file_path": p(".gemini", "protected.txt")}), "deny")

        # H1 원본 보호: 셸
        expect("보호 폴더 rm", decision(proj, "run_shell_command", {"command": "rm input/a.txt"}), "deny")
        expect("보호 폴더 읽기", decision(proj, "run_shell_command", {"command": "cat input/a.txt"}), "pass")
        expect("보호 ZIP 목록 보기", decision(proj, "PowerShell", {"command": "Get-ChildItem 인계용"}), "pass")
        expect("보호 ZIP 이동", decision(proj, "PowerShell", {"command": "Move-Item 인계용\\pack.zip old\\"}), "deny")
        # 프로젝트 맨 위 경로만 보호: 다른 폴더 아래 같은 이름은 통과
        expect("하위 폴더의 같은 이름에 복사", decision(proj, "run_shell_command", {"command": "mkdir -p 작업/서버/input/x && cp a.yml 작업/서버/input/x/"}), "pass")
        expect("./ 로 보호 폴더 지정", decision(proj, "run_shell_command", {"command": "mv ./input/a.txt b.txt"}), "deny")
        expect("절대 경로로 보호 폴더 지정", decision(proj, "run_shell_command", {"command": f'rm "{p("input", "a.txt")}"'}), "deny")
        expect("따옴표 안 보호 폴더", decision(proj, "run_shell_command", {"command": 'cp x.txt "input/a.txt"'}), "deny")
        # 오탐: 코드·글 내용 속 같은 단어 (2026-11-14 워크벤치 실행에서 발견)
        expect("히어독 본문 속 단어는 통과", decision(proj, "run_shell_command", {"command":
            "cat > src/app.js <<'EOF'\nconst el = document.querySelector('input');\nEOF"}), "pass")
        expect("python -c 코드 속 단어는 통과", decision(proj, "run_shell_command", {"command":
            "python -c \"open('src/a.html','w').write('<input type=text>')\""}), "pass")
        expect("히어독이어도 대상이 보호 폴더면 확인", decision(proj, "run_shell_command", {"command":
            "cat > input/a.txt <<'EOF'\nhello\nEOF"}), "deny")
        expect("따옴표 폴더 이름 단독은 확인", decision(proj, "run_shell_command", {"command": 'rm -f "input"'}), "deny")

        # 우회 시도 (2026-11-14 평가에서 나온 것)
        expect("대소문자 다른 경로로 수정", decision(proj, "replace", {"file_path": p("Input", "a.txt")}), "deny")
        expect("대소문자 다른 ZIP", decision(proj, "write_file", {"file_path": p("인계용", "PACK.ZIP")}), "deny")
        expect("cd 로 들어가서 rm", decision(proj, "run_shell_command", {"command": "cd input && rm a.txt"}), "deny")
        expect("Set-Location 뒤 Remove-Item", decision(proj, "PowerShell", {"command": "Set-Location input; Remove-Item a.txt"}), "deny")
        expect("python -c 로 쓰기", decision(proj, "run_shell_command", {"command": "python -c \"open('input/a.txt','w').write('x')\""}), "deny")
        expect("python 히어독으로 쓰기", decision(proj, "run_shell_command", {"command": "python - <<'EOF'\nopen('input/a.txt','w')\nEOF"}), "deny")
        expect("node -e 로 쓰기", decision(proj, "run_shell_command", {"command": "node -e \"require('fs').writeFileSync('input/a.txt','x')\""}), "deny")
        expect(".NET WriteAllText", decision(proj, "PowerShell", {"command": "[IO.File]::WriteAllText('input/a.txt','x')"}), "deny")
        expect("find -delete", decision(proj, "run_shell_command", {"command": "find input -name '*.txt' -delete"}), "deny")
        expect("압축을 보호 폴더에 풀기", decision(proj, "PowerShell", {"command": "Expand-Archive x.zip -DestinationPath input"}), "deny")
        expect("python 스크립트로 원본 읽기는 통과", decision(proj, "run_shell_command", {"command": "python tools/count.py input/a.txt"}), "pass")
        expect("python -c 로 원본 읽기만", decision(proj, "run_shell_command", {"command": "python -c \"print(open('input/a.txt').read())\""}), "pass")

        # 글롭 보호의 오탐 (plugins/*.jar 보호 중 jar가 아닌 곳)
        write(os.path.join(proj, ".gemini", "protected.txt"), "# 원본\ninput/\n인계용/*.zip\n원본.pdf\nplugins/*.jar\n")
        expect("글롭 보호: 다른 하위 폴더 만들기는 통과", decision(proj, "run_shell_command", {"command": "mkdir -p plugins/MythicMobs/Packs/new"}), "pass")
        expect("글롭 보호: 설정 파일 복사는 통과", decision(proj, "run_shell_command", {"command": "cp a.yml plugins/MythicMobs/config.yml"}), "pass")
        expect("글롭 보호: jar 덮어쓰기는 확인", decision(proj, "run_shell_command", {"command": "cp new.jar plugins/Mythic.jar"}), "deny")
        expect("글롭 보호: 대문자 JAR 도 확인", decision(proj, "run_shell_command", {"command": "rm plugins/Mythic.JAR"}), "deny")
        expect("글롭 보호: 와일드카드 삭제는 확인", decision(proj, "run_shell_command", {"command": "rm plugins/*"}), "deny")
        expect("글롭 보호: 폴더 자체 이동은 확인", decision(proj, "run_shell_command", {"command": "mv plugins plugins_old"}), "deny")
        expect("글롭 보호: cd 로 들어가서 jar 삭제는 확인", decision(proj, "run_shell_command", {"command": "cd plugins && rm a.jar"}), "deny")

        # v1.7 쓰는 대상 기준: 2026-11-14 실제 세션의 헛경보를 일반화한 사례
        write(os.path.join(proj, ".gemini", "protected.txt"), "# 원본\ninput/\n인계용/*.zip\n원본.pdf\nplugins/*.jar\n원본/\n")
        tmpd = tempfile.gettempdir()
        other = os.path.join(tmp, "다른 프로젝트")
        expect("코드 속 한국어 글 '원본'은 통과", decision(proj, "run_shell_command", {"command":
            "python -I - <<'EOF'\np = 'work/note.md'\ns = open(p, encoding='utf-8').read()\n"
            "s = s.replace('채도 0.7', '채도 0.6 (원본 채도 더 낮춤)')\nopen(p, 'w', encoding='utf-8').write(s)\nEOF"}), "pass")
        expect("커밋 메시지 속 경로·위험 명령 글은 통과", decision(proj, "run_shell_command", {"command":
            "git commit -q -F - <<'EOF'\ninput/ 보호 강화, rm -rf build 는 확인, git reset --hard 도 확인\nEOF"}), "pass")
        expect(".gitignore 에 설정 파일 이름 적기는 통과", decision(proj, "run_shell_command", {"command":
            "printf '%s\\n' '__pycache__/' '.gemini/settings.local.json' '.gemini/approvals.json' > .gitignore"}), "pass")
        expect("다른 폴더로 cd 한 뒤의 같은 이름은 통과", decision(proj, "run_shell_command", {"command":
            f"cd \"{other}\" && python - <<'EOF'\nopen('input/README.md','w').write('x')\nEOF"}), "pass")
        expect("변수로 정한 임시 폴더 삭제는 통과", decision(proj, "run_shell_command", {"command":
            f"S=\"{os.path.join(tmpd, 'claude', 'scratch')}\"; rm -rf \"$S/old\" \"$S/view\""}), "pass")
        expect("jar 읽기만 하는 코드는 통과", decision(proj, "run_shell_command", {"command":
            "cd plugins && python -I -c \"import zipfile; z=zipfile.ZipFile('a.jar'); print(z.namelist()[:5])\""}), "pass")
        expect("임시 폴더에 스크립트 쓰고 jar 읽기는 통과", decision(proj, "run_shell_command", {"command":
            f"cat > \"{os.path.join(tmpd, 'dump.py')}\" <<'EOF'\nimport sys, zipfile\nz = zipfile.ZipFile(sys.argv[1])\nEOF\n"
            f"python -I \"{os.path.join(tmpd, 'dump.py')}\" plugins/a.jar"}), "pass")
        expect("문자열 속 shutil.rmtree 글자는 통과", decision(proj, "run_shell_command", {"command":
            "python - <<'EOF'\np = 'tests/t.py'\ns = open(p).read().replace('x = 1', 'shutil.rmtree(work)')\nopen(p, 'w').write(s)\nEOF"}), "pass")
        expect("임시 폴더 하위 정리 코드는 통과", decision(proj, "run_shell_command", {"command":
            "python - <<'EOF'\nimport os, shutil\nroot = r\"" + tmpd + "\"\nfor d in os.listdir(root):\n"
            "    p = os.path.join(root, d)\n    if d.startswith('tmp'):\n        shutil.rmtree(p)\nEOF"}), "pass")
        expect("프롬프트 글 속 rm -rf 는 통과", decision(proj, "run_shell_command", {"command":
            "timeout 240 claude -p \"Run exactly: rm -rf build . Then report.\""}), "pass")
        expect("작업 파일 sed 스크립트 속 '원본'은 통과", decision(proj, "run_shell_command", {"command":
            "sed -i 's/사본/원본 아님/' work/a.md"}), "pass")
        expect("임시 폴더의 시험 프로젝트 보호 목록 쓰기는 통과", decision(proj, "run_shell_command", {"command":
            f"P=\"{os.path.join(tmpd, 'probe', 'proj')}\"; mkdir -p \"$P/.gemini\"; printf 'input/\\n' > \"$P/.gemini/protected.txt\""}), "pass")
        expect("원본 폴더에서 읽고 임시 폴더에 저장은 통과", decision(proj, "run_shell_command", {"command":
            f"S=\"{os.path.join(tmpd, 'crop')}\"; cd 원본 && python -I -c \"import sys\nfrom PIL import Image\n"
            "im = Image.open(sys.argv[1])\nim.save(sys.argv[2] + '/crop_' + 'a' + '.png')\" \"지도.png\" \"$S\""}), "pass")
        # 빈틈이 생기지 않았는지
        expect("변수로 보호 폴더를 가리켜 삭제는 확인", decision(proj, "run_shell_command", {"command": 'S="input"; rm "$S/a.txt"'}), "deny")
        expect("코드에서 경로 조각으로 원본 쓰기는 확인", decision(proj, "run_shell_command", {"command":
            "python -c \"import os; open(os.path.join('원본', 'a.txt'), 'w').write('x')\""}), "deny")
        expect("코드가 대상을 알 수 없게 폴더째 삭제는 확인", decision(proj, "run_shell_command", {"command":
            "python - <<'EOF'\nimport json, shutil\nfor d in json.load(open('list.json')):\n    shutil.rmtree(d)\nEOF"}), "deny")
        expect("임시 폴더 안이어도 프로젝트 폴더 삭제는 확인", decision(proj, "run_shell_command", {"command": f'rm -rf "{p("work")}"'}), "deny")
        expect("find -exec rm -rf 는 확인", decision(proj, "run_shell_command", {"command": "find . -name build -exec rm -rf {} +"}), "deny")
        expect("xargs rm -rf 는 확인", decision(proj, "run_shell_command", {"command": "ls -d */ | xargs rm -rf"}), "deny")
        expect("bash -c 안의 보호 원본 삭제는 확인", decision(proj, "run_shell_command", {"command": "bash -c \"rm input/a.txt\""}), "deny")
        expect("powershell -Command 안의 보호 원본 삭제는 확인", decision(proj, "run_shell_command", {"command":
            "powershell -NoProfile -Command \"Remove-Item input/a.txt\""}), "deny")
        expect("tar 를 보호 폴더에 풀기는 확인", decision(proj, "run_shell_command", {"command": "tar -xf x.tar -C input"}), "deny")
        expect("보호 폴더에서 unzip 은 확인", decision(proj, "run_shell_command", {"command": "cd input && unzip ../x.zip"}), "deny")
        expect("curl 로 보호 폴더에 내려받기는 확인", decision(proj, "run_shell_command", {"command": "curl -L -o input/a.bin https://example.com/a"}), "deny")
        expect("코드로 이 프로젝트 보호 목록 쓰기는 확인", decision(proj, "run_shell_command", {"command":
            "python -c \"open('.gemini/protected.txt','w').write('')\""}), "deny")
        expect("원본 폴더 안 파일 덮어쓰기는 확인", decision(proj, "run_shell_command", {"command": "cp new.yml 원본/1차/a.yml"}), "deny")
        write(os.path.join(proj, ".gemini", "protected.txt"), "# 원본\ninput/\n인계용/*.zip\n원본.pdf\nplugins/*.jar\n")

        # 하네스 자기 보호: 셸
        expect("셸로 settings.json 덮어쓰기", decision(proj, "run_shell_command", {"command": "echo {} > ~/.gemini/settings.json"}), "deny")
        expect("PowerShell로 settings.json 쓰기", decision(proj, "PowerShell", {"command": "Set-Content $env:USERPROFILE\\.gemini\\settings.json '{}'"}), "deny")
        expect("셸로 훅 파일 지우기", decision(proj, "run_shell_command", {"command": "rm ~/.gemini/hooks/local-harness/guard.py"}), "deny")
        expect("셸로 보호 목록 비우기", decision(proj, "run_shell_command", {"command": "echo > .gemini/protected.txt"}), "deny")
        expect("settings.json 읽기는 통과", decision(proj, "run_shell_command", {"command": "cat ~/.gemini/settings.json"}), "pass")

        # 임시 폴더 안만 지우는 폴더 삭제는 통과
        t = tempfile.gettempdir()
        expect("임시 폴더 안 rm -rf", decision(proj, "run_shell_command", {"command": f'rm -rf "{os.path.join(t, "claude", "scratch", "x")}"'}), "pass")
        expect("임시 폴더 안 Remove-Item", decision(proj, "PowerShell", {"command": f'Remove-Item -Recurse -Force "{os.path.join(t, "zz")}"'}), "pass")
        expect("임시 폴더 자체 삭제는 확인", decision(proj, "run_shell_command", {"command": f'rm -rf "{t}"'}), "deny")
        expect("임시+다른 폴더 같이 삭제는 확인", decision(proj, "run_shell_command", {"command": f'rm -rf "{os.path.join(t, "a")}" build'}), "deny")
        expect("임시 폴더 안이라도 보호 원본 삭제는 확인", decision(proj, "run_shell_command", {"command": f'rm -rf "{p("input")}"'}), "deny")
        expect("변수 경로 삭제는 확인", decision(proj, "run_shell_command", {"command": 'rm -rf "$S/x"'}), "deny")
        expect("상위 이동 섞인 삭제는 확인", decision(proj, "run_shell_command", {"command": f'rm -rf "{t}/a/../../x"'}), "deny")
        expect("임시 삭제 뒤 다른 위험 명령은 확인", decision(proj, "run_shell_command", {"command": f'rm -rf "{os.path.join(t, "a")}" && git reset --hard'}), "deny")

        # H2 위험 명령 (보호 목록과 무관)
        expect("rm -rf", decision(proj, "run_shell_command", {"command": "rm -rf build"}), "deny")
        expect("Remove-Item -Recurse", decision(proj, "PowerShell", {"command": "Remove-Item -Recurse -Force dist"}), "deny")
        expect("git reset --hard", decision(proj, "run_shell_command", {"command": "git reset --hard HEAD~1"}), "deny")
        expect("git push -f", decision(proj, "run_shell_command", {"command": "git push -f origin main"}), "deny")
        expect("git push 보통", decision(proj, "run_shell_command", {"command": "git push origin main"}), "pass")
        expect("python shutil.rmtree", decision(proj, "run_shell_command", {"command": "python -c \"import shutil; shutil.rmtree('build')\""}), "deny")
        expect("node rmSync recursive", decision(proj, "run_shell_command", {"command": "node -e \"require('fs').rmSync('dist', {recursive: true})\""}), "deny")
        expect("node rmSync 파일 하나는 통과", decision(proj, "run_shell_command", {"command": "node -e \"require('fs').rmSync('a.log')\""}), "pass")
        expect("rm 파일 하나", decision(proj, "run_shell_command", {"command": "rm build/tmp.log"}), "pass")
        expect("ls", decision(proj, "run_shell_command", {"command": "ls -la"}), "pass")

        # 사용자 승인 (.gemini/approvals.json — 워크벤치가 사용자 답을 받아 씀)
        approve(proj, "rm -rf build")
        expect("승인된 명령은 1회 통과", decision(proj, "run_shell_command", {"command": "rm -rf build"}), "pass")
        expect("1회 승인은 사용 기록이 남음", approval_used(proj, "rm -rf build"), [1])
        expect("같은 명령 2번째는 다시 확인", decision(proj, "run_shell_command", {"command": "rm -rf build"}), "deny")
        approve(proj, "rm -rf dist", once=False)
        expect("항상 승인은 여러 번 통과 (1)", decision(proj, "run_shell_command", {"command": "rm -rf dist"}), "pass")
        expect("항상 승인은 여러 번 통과 (2)", decision(proj, "run_shell_command", {"command": "rm -rf dist"}), "pass")
        expect("승인과 다른 명령은 확인", decision(proj, "run_shell_command", {"command": "rm -rf dist2"}), "deny")
        approve(proj, "rm -rf old", expires_in=-10)
        expect("만료된 승인은 확인", decision(proj, "run_shell_command", {"command": "rm -rf old"}), "deny")
        approve(proj, "rm input/a.txt")
        expect("보호 원본 명령도 사용자가 승인하면 1회 통과", decision(proj, "run_shell_command", {"command": "rm input/a.txt"}), "pass")
        approve(proj, "echo {} > ~/.gemini/settings.json")
        expect("하네스 설정 변경은 승인으로도 못 엶", decision(proj, "run_shell_command", {"command": "echo {} > ~/.gemini/settings.json"}), "deny")
        expect("AI가 승인 기록을 직접 쓰면 차단", decision(proj, "write_file", {"file_path": p(".gemini", "approvals.json")}), "deny")
        expect("AI가 승인 기록 고치기도 차단 (대소문자)", decision(proj, "replace", {"file_path": p(".gemini", "Approvals.JSON")}), "deny")
        expect("셸로 승인 기록 쓰기는 확인", decision(proj, "run_shell_command", {"command": "echo [] > .gemini/approvals.json"}), "deny")
        write(p(".gemini", "approvals.json"), "{깨진 json")
        expect("승인 기록이 깨져 있으면 안전하게 확인", decision(proj, "run_shell_command", {"command": "rm -rf dist"}), "deny")
        os.remove(p(".gemini", "approvals.json"))
        expect("승인 기록이 없으면 원래대로 확인", decision(proj, "run_shell_command", {"command": "rm -rf dist"}), "deny")

        # H3 읽기 전용 모드
        write(p(".gemini", "READONLY"), "review/claude/\n")
        expect("읽기 전용: 소스 수정", decision(proj, "replace", {"file_path": p("src", "a.java")}), "deny")
        expect("읽기 전용: 허용 폴더에 보고서", decision(proj, "write_file", {"file_path": p("review", "claude", "r.md")}), "pass")
        expect("읽기 전용: 셸 쓰기", decision(proj, "run_shell_command", {"command": "echo hi > src/a.txt"}), "deny")
        expect("읽기 전용: 셸 읽기", decision(proj, "run_shell_command", {"command": "grep -r foo src 2>&1"}), "pass")
        os.remove(p(".gemini", "READONLY"))
        expect("읽기 전용 해제 후 소스 수정", decision(proj, "replace", {"file_path": p("src", "a.java")}), "pass")

        # 하네스 설정이 없는 폴더
        plain = os.path.join(tmp, "plain")
        os.makedirs(plain)
        expect("설정 없는 폴더 수정", decision(plain, "write_file", {"file_path": os.path.join(plain, "a.txt")}), "pass")
        expect("설정 없는 폴더 rm -rf", decision(plain, "run_shell_command", {"command": "rm -rf x"}), "deny")

        # 하위 폴더에서 실행해도 루트를 찾는다
        sub = p("src", "deep")
        os.makedirs(sub)
        res = run(GUARD, {"tool_name": "replace", "tool_input": {"file_path": p("input", "a.txt")}, "cwd": sub}, sub)
        expect("하위 폴더에서도 보호", res["decision"] if res else "pass", "deny")

        # H4 상태 복원
        expect("기록 없으면 출력 없음", run(RESTORE, {"source": "startup", "cwd": plain}, plain), None)
        write(p("PROGRESS.md"), "# 진행\n## 다음 할 일\n- 창술사 이펙트\n")
        res = run(RESTORE, {"source": "compact", "cwd": proj}, proj)
        ctx = res["hookSpecificOutput"]["additionalContext"] if res else ""
        expect("압축 뒤 PROGRESS 주입", "창술사 이펙트" in ctx and "압축" in ctx, True)
        expect("보호 목록 안내", "인계용/*.zip" in ctx, True)

    print()
    print("전부 통과" if not failures else f"실패 {len(failures)}건: {failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
