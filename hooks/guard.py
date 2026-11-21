"""Gemini CLI BeforeTool 훅: 원본 보호(H1), 위험 명령 차단/승인(H2), 읽기 전용 모드(H3), 하네스 자기 보호.

입력: Gemini CLI가 stdin으로 주는 JSON (tool_name, tool_input, cwd).
출력: 막거나 승인이 필요할 때만 {"decision": "deny", "reason": ..., "systemMessage": ...} JSON을 stdout에 쓴다. 통과 시 아무것도 쓰지 않는다.
훅 자체가 실패하면 작업을 막지 않도록 조용히 통과한다(오류는 로그에만 남김).
"""
import fnmatch
import hashlib
import json
import os
import re
import shlex
import sys
import time

EDIT_TOOLS = {"write_file", "replace", "edit_file", "multi_edit", "Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"run_shell_command", "Bash", "PowerShell"}
HOME = os.path.expanduser("~")
GEMINI_HOME = os.environ.get("GEMINI_CONFIG_DIR") or os.path.join(HOME, ".gemini")
HARNESS_DIR = os.path.join(GEMINI_HOME, "hooks", "local-harness")
LOG_PATH = os.path.join(GEMINI_HOME, "local-harness-log", "events.jsonl")

# H2: 언제나 사용자에게 먼저 묻는 명령
DANGEROUS = [
    (r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b", "폴더째 지우는 rm -r"),
    (r"\bRemove-Item\b[^|;]*-Recurse", "폴더째 지우는 Remove-Item -Recurse"),
    (r"\b(rd|rmdir)\s+/s\b", "폴더째 지우는 rd /s"),
    (r"\bdel\s+(/[a-zA-Z]\s+)*/s\b", "하위까지 지우는 del /s"),
    (r"\bshutil\.rmtree\b", "폴더째 지우는 shutil.rmtree"),
    (r"\brmSync\([^)]*recursive\s*:\s*true", "폴더째 지우는 fs.rmSync(recursive)"),
    (r"\brimraf\b", "폴더째 지우는 rimraf"),
    (r"\bgit\s+reset\s+--hard\b", "커밋하지 않은 변경을 버리는 git reset --hard"),
    (r"\bgit\s+push\b[^|;&]*(--force\b|--force-with-lease\b|\s-f\b)", "원격 기록을 덮어쓰는 git push --force"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*f", "추적하지 않는 파일을 지우는 git clean -f"),
    (r"\bgit\s+checkout\s+(--\s+)?\.(\s|$)", "모든 변경을 버리는 git checkout ."),
    (r"\bgit\s+restore\s+(--\S+\s+)*\.(\s|$)", "모든 변경을 버리는 git restore ."),
    (r"\bformat(\.com)?\s+[a-zA-Z]:", "드라이브 포맷"),
]

# 셸 명령이 파일을 바꾸는지 대략 판단하는 패턴 (H1·H3에서 사용)
MUTATING = re.compile(
    r"(\brm\b|\bdel\b|\berase\b|\brd\b|\brmdir\b|\bmv\b|\bcp\b|\bmove\b|\bcopy\b|\bren\b|\brename\b"
    r"|\bRemove-Item\b|\bMove-Item\b|\bCopy-Item\b|\bRename-Item\b|\bSet-Content\b|\bAdd-Content\b"
    r"|\bOut-File\b|\bNew-Item\b|\bClear-Content\b|\bsed\s+-i\b|\btruncate\b|\btouch\b|\bmkdir\b"
    r"|\bgit\s+(commit|checkout|reset|restore|clean|stash|merge|rebase|pull|apply|rm|mv)\b"
    r"|\b(npm|pnpm|yarn|pip)\s+(install|uninstall|add|remove)\b|\btee\b|(^|[^0-9&>])>>?(?!&)"
    # 셸 동사 없이 파일을 바꾸는 방법: 인라인 코드(python -c, node -e, 히어독), .NET, find, 압축 풀기, 내려받기
    r"|\bopen\([^)]*,\s*['\"][wax]|\.write(_text|_bytes)?\(|\b(write|append)File(Sync)?\("
    r"|\bos\.(remove|unlink|rename|replace|rmdir|removedirs|makedirs|mkdir|truncate)\b|\bshutil\.\w+"
    r"|\b(unlink|rm|rename|copyFile|mkdir|cp)Sync\b|\.unlink\(|\.rename\(|\.rmdir\(|\.touch\("
    r"|\[(System\.)?IO\.(File|Directory)\]::\w*(Write|Append|Delete|Move|Copy|Create|Replace)"
    r"|\s-delete\b|\bExpand-Archive\b|\bunzip\b|\btar\s+-?[a-zA-Z]*x|\b7z\s+[xe]\b|-OutFile\b|\bwget\b|\bcurl\b[^|;&]*\s-[oO]\b)",
    re.IGNORECASE,
)

# 하네스 자기 보호: 셸 명령이 이 경로를 바꾸려 하면 확인 (경로는 소문자·슬래시로 비교)
SELF_PATHS = (".gemini/settings.json", ".gemini/settings.local.json", ".gemini/hooks/local-harness",
              ".gemini/protected.txt", ".gemini/readonly", ".gemini/approvals.json")

PROJECT_SELF = (".gemini/protected.txt", ".gemini/readonly", ".gemini/approvals.json")

# 사용자 승인 기록. 워크벤치가 사용자 답(1회·항상)을 받아 프로젝트 .gemini/ 에 쓴다. AI는 직접 못 쓴다.
APPROVALS = "approvals.json"


def read_input():
    raw = sys.stdin.buffer.read()
    return json.loads(raw.decode("utf-8-sig") or "{}")


def emit(decision, reason):
    msg = "[로컬 하네스] " + reason
    if decision == "ask" and "approvals.json" not in reason:
        msg += " (승인 등록: .gemini/approvals.json)"
    out = {
        "decision": "deny",
        "reason": msg,
        "systemMessage": msg,
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


def log(event):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        event["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def norm(p):
    return os.path.normcase(os.path.abspath(p)).replace("\\", "/")


def find_root(start):
    """start에서 위로 올라가며 .gemini/protected.txt 또는 .gemini/READONLY가 있는 폴더를 찾는다. 홈과 드라이브 루트에서 멈춘다."""
    cur = os.path.abspath(start)
    home = norm(HOME)
    while True:
        claude_dir = os.path.join(cur, ".gemini")
        if norm(cur) != home and (
            os.path.isfile(os.path.join(claude_dir, "protected.txt"))
            or os.path.isfile(os.path.join(claude_dir, "READONLY"))
        ):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur or norm(cur) == home:
            return None
        cur = parent


def read_patterns(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        lines = [ln.strip().replace("\\", "/") for ln in f]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def rel_to(root, path):
    rel = os.path.relpath(os.path.abspath(path), root).replace("\\", "/")
    return None if rel.startswith("../") or rel == ".." else rel


def has_glob(s):
    return any(c in s for c in "*?[")


def matches(rel, pattern):
    # Windows·Mac 파일 시스템은 대소문자를 구별하지 않으므로 비교도 구별하지 않는다(Input/a.txt 우회 방지)
    rel = rel.lower()
    pat = pattern.lower()
    pat = pat[2:] if pat.startswith("./") else pat
    if pat.endswith("/"):
        return rel == pat[:-1] or rel.startswith(pat)
    if fnmatch.fnmatchcase(rel, pat):
        return True
    # 폴더 이름만 적은 경우(예: input) 그 아래 전부
    return "/" not in pat and not any(c in pat for c in "*?[") and rel.startswith(pat + "/")


def literal_prefix(pattern):
    """'인계용/*.zip' -> '인계용/' 처럼 글롭 문자 앞까지. 셸 명령 안에서 찾는 데 쓴다."""
    cut = len(pattern)
    for i, c in enumerate(pattern):
        if c in "*?[":
            cut = i
            break
    return pattern[:cut].strip()


def check_edit(tool_input, root, cwd):
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)

    # 하네스 자기 보호: 훅이나 설정을 고쳐 가드를 끄는 것을 막지 않고 묻는다
    n = norm(path).lower()
    if n.startswith(norm(HARNESS_DIR).lower() + "/") or n == norm(os.path.join(GEMINI_HOME, "settings.json")).lower():
        return ("ask", f"하네스 훅이나 전역 설정을 고치려 합니다: {path}")
    # 승인 기록은 사람(워크벤치)만 쓴다. AI가 스스로 승인을 만들 수 없게 어디서든 차단
    if n.endswith("/.gemini/" + APPROVALS):
        return ("deny", "승인 기록(.gemini/approvals.json)은 사용자와 워크벤치만 씁니다. AI가 직접 고칠 수 없습니다.")

    if not root:
        return None
    rel = rel_to(root, path)
    if rel is None:
        return None
    if rel.startswith(".gemini/") and rel.split("/")[1] in ("protected.txt", "READONLY"):
        return ("ask", f"하네스 보호 설정 파일을 고치려 합니다: {rel}")

    for pat in read_patterns(os.path.join(root, ".gemini", "protected.txt")):
        if matches(rel, pat):
            return ("deny", f"보호된 원본입니다({pat}). 복사본을 만들어 작업하세요: {rel}")

    readonly = os.path.join(root, ".gemini", "READONLY")
    if os.path.isfile(readonly):
        allowed = read_patterns(readonly)
        if not any(matches(rel, pat) for pat in allowed):
            hint = f" 쓸 수 있는 곳: {', '.join(allowed)}" if allowed else ""
            return ("deny", f"읽기 전용 모드입니다(.gemini/READONLY). 파일을 고치지 마세요: {rel}.{hint}")
    return None


PATH_BOUNDARY = " \t\"'=(;|&<>,"


def path_forms(p):
    """C:/a/b 와 Git Bash식 /c/a/b 두 가지 모양 (소문자, 슬래시)."""
    p = p.replace("\\", "/").rstrip("/").lower()
    forms = [p]
    if re.match(r"^[a-z]:/", p):
        forms.append("/" + p[0] + p[2:])
    return forms


WORD_END = " \t\n;|&)"
HEREDOC = re.compile(r"(<<-?\s*(['\"]?)(\w+)\2[^\n]*\n)(.*?)(\n\3[ \t]*(?:\n|$))", re.S)


def strip_heredoc_bodies(cmd):
    """cat·tee로 파일에 쓰는 히어독의 본문은 파일 내용이지 경로가 아니므로 검사에서 뺀다.
    대상(cat > input/a.txt <<EOF)은 첫 줄에 남는다. python·node·bash로 넘기는 본문은 코드라서 그대로 검사한다."""
    def repl(m):
        line_start = max(cmd.rfind("\n", 0, m.start()), cmd.rfind(";", 0, m.start()),
                         cmd.rfind("&&", 0, m.start())) + 1
        if re.match(r"\s*(cat|tee)\b", cmd[line_start:m.start()].lstrip("& ")):
            return m.group(1) + m.group(5).lstrip("\n")
        return m.group(0)
    return HEREDOC.sub(repl, cmd)


def _standalone_word(low, i, j):
    """low[i:j]가 셸 낱말 하나로 서 있는가: 앞뒤가 공백·명령 구분자, 또는 그 낱말만 감싼 따옴표."""
    pre = low[i - 1] if i > 0 else " "
    post = low[j] if j < len(low) else " "
    if pre in "\"'":
        if post != pre:
            return False
        pre = low[i - 2] if i > 1 else " "
        post = low[j + 1] if j + 1 < len(low) else " "
    return (pre in " \t\n;|&(=" or i == 0) and (post in WORD_END)


def root_path_hits(cmd, prefix, root):
    """명령 안에서 prefix가 프로젝트 맨 위 경로를 가리키는 곳마다, 그 자리의 경로 토큰(프로젝트 기준, 소문자)을 낸다.
    '작업/던전/서버/plugins/...'처럼 다른 폴더 아래의 같은 이름은 무시한다."""
    low = cmd.lower()
    roots = [r + "/" for r in path_forms(root)]
    pre = prefix.lower()
    # 'input/' 은 끝 슬래시 없이 폴더 자체를 가리키는 'input' 도 본다
    forms = [(pre, False)] + ([(pre.rstrip("/"), True)] if pre.endswith("/") and len(pre) > 1 else [])
    for needle, need_end in forms:
        start = 0
        while True:
            i = low.find(needle, start)
            if i < 0:
                break
            start = i + 1
            j = i + len(needle)
            if need_end and j < len(low) and low[j] not in PATH_BOUNDARY:
                continue
            before = low[:i]
            if need_end and not any(before.endswith(r) for r in roots) and not _standalone_word(low, i, j):
                # 슬래시 없는 폴더 이름(input)은 셸 낱말로 따로 설 때만 경로로 본다.
                # 코드 속 querySelector('input'), <input> 같은 것은 무시 (2026-11-14 오탐)
                continue
            if (i == 0 or before[-1] in PATH_BOUNDARY
                    or (before.endswith("./") and (len(before) == 2 or before[-3] in PATH_BOUNDARY))
                    or any(before.endswith(r) for r in roots)):
                k = i
                while k < len(low) and low[k] not in PATH_BOUNDARY:
                    k += 1
                yield low[i:k]


def mentions_root_path(cmd, prefix, root):
    return next(root_path_hits(cmd, prefix, root), None) is not None


def touches_pattern(cmd, pat, root):
    """셸 명령이 보호 패턴을 건드릴 수 있으면 True.
    글롭이 없는 패턴(input/, 원본.pdf)은 이름이 나오기만 하면 True.
    글롭 패턴(plugins/*.jar)은 맞는 파일, 폴더 자체(cd·mv plugins), 와일드카드(plugins/*)일 때만 True.
    plugins/MythicMobs/config.yml 처럼 패턴에 안 맞는 다른 파일은 통과시켜 확인 창 오탐을 줄인다."""
    prefix = literal_prefix(pat)
    if not prefix:
        return False
    if not has_glob(pat):
        return mentions_root_path(cmd, prefix, root)
    pat_l = pat.lower()
    pat_l = pat_l[2:] if pat_l.startswith("./") else pat_l
    dir_part = prefix.lower().rstrip("/")
    for tok in root_path_hits(cmd, prefix, root):
        tok = tok[2:] if tok.startswith("./") else tok
        for r in path_forms(root):
            if tok.startswith(r + "/"):
                tok = tok[len(r) + 1:]
        if tok.rstrip("/") == dir_part or has_glob(tok) or fnmatch.fnmatchcase(tok, pat_l):
            return True
    return False


# 임시 폴더 안만 지우는 rm -r / Remove-Item -Recurse 는 묻지 않는다
RECURSIVE_DELETE = re.compile(
    r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b|\bRemove-Item\b[^|;]*-Recurse", re.IGNORECASE)
PS_VALUE_FLAGS = {"-erroraction", "-ea", "-warningaction", "-include", "-exclude", "-filter"}


def temp_dirs():
    import tempfile
    dirs = {tempfile.gettempdir(), os.environ.get("TEMP", ""), os.environ.get("TMP", ""), "/tmp"}
    out = []
    for d in dirs:
        if d:
            out.extend(path_forms(d))
            rp = os.path.realpath(d)
            if rp != d:
                out.extend(path_forms(rp))
    return out


def in_temp(path, temps):
    if "$" in path or "`" in path or ".." in path.replace("\\", "/").split("/"):
        return False  # 변수·상위 폴더 이동이 섞이면 실제 위치를 알 수 없으니 묻는다
    p = path.replace("\\", "/").lower()
    return any(p.startswith(t + "/") and len(p) > len(t) + 1 for t in temps)


def delete_targets(segment):
    import shlex
    try:
        toks = shlex.split(segment, posix=True)
    except ValueError:
        return None
    idx = next((i for i, t in enumerate(toks) if t.lower() in ("rm", "remove-item")), None)
    if idx is None:
        return None
    targets, skip = [], False
    for t in toks[idx + 1:]:
        if skip:
            skip = False
            continue
        tl = t.lower()
        if tl in PS_VALUE_FLAGS:
            skip = True
            continue
        if t.startswith("-"):
            continue
        targets.append(t)
    return targets


def only_temp_deletes(command):
    """명령 속 '폴더째 지우기'가 전부 임시 폴더 안의 절대 경로만 지우면 True."""
    temps = temp_dirs()
    segments = re.split(r"&&|\|\||[;|\n]", command)
    found = False
    for seg in segments:
        if not RECURSIVE_DELETE.search(seg):
            continue
        found = True
        targets = delete_targets(seg.strip())
        if not targets or not all(in_temp(t, temps) for t in targets):
            return False
    return found


def check_shell_by_mention(command, root):
    """예전 방식(이름이 나오면 확인). 명령을 낱말로 나누지 못했을 때만 쓰는 안전한 대체 경로."""
    for pattern, label in DANGEROUS:
        if re.search(pattern, command, re.IGNORECASE):
            if "폴더째" in label and only_temp_deletes(command):
                continue
            return ("ask", f"{label} 명령입니다. 실행해도 되는지 확인이 필요합니다.")
    if not MUTATING.search(command):
        return None
    cmd = strip_heredoc_bodies(command).replace("\\", "/")
    low = cmd.lower()
    for sp in SELF_PATHS:
        if sp in low:
            return ("ask", f"하네스 설정·훅·보호 목록({sp})을 바꿀 수 있는 명령입니다. 확인이 필요합니다.")
    if not root:
        return None
    for pat in read_patterns(os.path.join(root, ".gemini", "protected.txt")):
        if touches_pattern(cmd, pat, root):
            return ("ask", f"보호된 원본({pat})을 건드릴 수 있는 명령입니다. 원본을 바꾸지 않는지 확인이 필요합니다.")
    return None


# ───────────────────────── 쓰는 대상 분석 (v1.7) ─────────────────────────
# 명령에 이름이 "나오는지"가 아니라, 명령이 실제로 "쓰거나 지우는 대상"이 어디인지로 판단한다.
#   - 읽기, 커밋 메시지·문서 글 속 낱말, 다른 폴더 작업은 묻지 않는다
#   - 같은 명령 안에서 정한 변수(S="...")는 풀어서 본다
#   - 임시 폴더 안만 지우는 폴더 삭제는 묻지 않는다

class Unparsable(Exception):
    pass


HEREDOC_ANY = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
INTERPRETERS = {"python", "python3", "py", "node", "deno", "bun", "ruby", "perl"}
SUB_SHELLS = {"bash", "sh", "zsh"}
POWERSHELLS = {"powershell", "pwsh"}
WRAPPERS = {"sudo", "env", "command", "nohup", "time", "exec", "builtin", "nice"}
NULL_DEVICES = {"/dev/null", "$null", "nul", "null"}
REDIRECTS = {">", ">>", "&>", "&>>", ">|", "1>", "2>", "1>>", "2>>"}
CD_CMDS = {"cd", "set-location", "sl", "pushd", "chdir"}
REMOVE_CMDS = {"rm", "del", "erase", "rd", "rmdir", "unlink", "remove-item", "ri", "shred"}
MOVE_CMDS = {"mv", "move", "move-item", "mi", "ren", "rename", "rename-item", "rni"}
COPY_CMDS = {"cp", "copy", "copy-item", "cpi", "install", "ln", "robocopy", "xcopy"}
CREATE_CMDS = {"mkdir", "md", "new-item", "ni", "touch", "truncate", "tee", "clear-content", "clc"}
CONTENT_CMDS = {"set-content", "sc", "add-content", "ac", "out-file"}
PS_PATH_PARAMS = {"-path", "-literalpath", "-destination", "-filepath", "-destinationpath", "-outfile"}
PS_VALUE_PARAMS = {"-filter", "-include", "-exclude", "-erroraction", "-ea", "-itemtype", "-type", "-value",
                   "-encoding", "-name", "-newname", "-uri", "-warningaction", "-depth", "-delimiter", "-method",
                   "-headers", "-body", "-inputobject", "-credential"}
VALUE_OPTS = {"truncate": {"-s"}, "mkdir": {"-m"}, "cp": {"-S", "--suffix"}, "mv": {"-S", "--suffix"},
              "sed": {"-e", "-f"}, "tar": {"-f", "-C", "--directory"}, "unzip": {"-d", "-x"},
              "curl": {"-H", "-d", "-X", "-u", "-A", "-e", "--data", "--header", "-F", "-w"},
              "wget": {"-P", "-O", "--header"}, "find": set()}
TARGET_OPTS = {"cp": {"-t", "--target-directory"}, "mv": {"-t", "--target-directory"}, "ln": {"-t"},
               "install": {"-t"}}

# 코드(python -c, node -e, 히어독으로 넘긴 코드) 안에서 파일을 바꾸는 호출
CODE_WRITE = re.compile(
    r"\bopen\s*\((?:[^()]|\([^()]*\))*?,\s*(?:mode\s*=\s*)?['\"][rbt]*[wax][+bt]*['\"]"
    r"|\.write_(?:text|bytes)\s*\(|\b(?:write|append)File(?:Sync)?\s*\("
    r"|\bos\.(?:remove|unlink|rename|replace|rmdir|removedirs|makedirs|mkdir|truncate)\s*\("
    r"|\bshutil\.(?:copy\w*|move|rmtree|make_archive|unpack_archive)\s*\("
    r"|\b(?:unlink|rm|rename|copyFile|mkdir|cp|rmdir|truncate)(?:Sync)?\s*\("
    r"|\.(?:unlink|rename|rmdir|touch|mkdir|extractall|save|savefig|to_csv|to_excel)\s*\("
    r"|\bZipFile\s*\((?:[^()]|\([^()]*\))*?,\s*['\"][wax]['\"]"
    r"|\[(?:System\.)?IO\.(?:File|Directory)\]::\w*(?:Write|Append|Delete|Move|Copy|Create|Replace)\w*\s*\(")
CODE_RDELETE = re.compile(r"\bshutil\.rmtree\s*\(|\brmSync\s*\([^()]*recursive\s*:\s*true|\brimraf\b"
                          r"|\bfs\.rm\s*\([^()]*recursive\s*:\s*true|\bRemove-Item\b[^\n;|]*-Recurse", re.I)
PY_LITERAL = re.compile(r"(?:[rRbBuUfF]{0,2})(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')"
                        r"|#[^\n]*")
JS_LITERAL = re.compile(r"(`(?:\\.|[^`\\])*`|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')|//[^\n]*|/\*[\s\S]*?\*/")


def _unquote_literal(s):
    s = re.sub(r"^[rRbBuUfF]{0,2}", "", s)
    for q in ('"""', "'''", '"', "'", "`"):
        if s.startswith(q) and s.endswith(q) and len(s) >= 2 * len(q):
            return s[len(q):-len(q)]
    return s


def code_skeleton(code, lang):
    """문자열·주석을 빼낸 코드 뼈대와 문자열 목록. 짧은 문자열('w', 'rb')은 뼈대에 남겨 모드 판정에 쓴다."""
    pat = PY_LITERAL if lang == "py" else JS_LITERAL
    out, lits, last = [], [], 0
    for m in pat.finditer(code):
        out.append(code[last:m.start()])
        tok = m.group(0)
        if tok.startswith(("#", "//", "/*")):
            out.append(" ")
        else:
            val = _unquote_literal(tok)
            lits.append(val)
            out.append(tok if len(val) <= 3 else f"'\x00{len(lits) - 1}\x00'")
        last = m.end()
    out.append(code[last:])
    return "".join(out), lits


def path_like(s):
    if not s or len(s) > 300 or "\n" in s:
        return False
    t = s.replace("\\", "/")
    if re.match(r"^([a-zA-Z]:/|/[a-zA-Z]/|~/|/tmp/|\./|\.\./)", t):
        return True
    if re.search(r"\s", t):
        return False
    return "/" in t or bool(re.search(r"\.[A-Za-z0-9]{1,6}$", t))


def win_fwd(s):
    """Windows 역슬래시를 슬래시로. 따옴표·$ 앞의 이스케이프 역슬래시는 남긴다."""
    return re.sub(r"\\(?![\"\\$`\n])", "/", s)


def split_outside_quotes(text):
    """따옴표 밖의 줄바꿈을 ; 로 바꾼다 (줄마다 다른 명령)."""
    out, q, i = [], None, 0
    while i < len(text):
        c = text[i]
        if q:
            if c == "\\" and q == '"' and i + 1 < len(text):
                out.append(text[i:i + 2]); i += 2; continue
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == "\n":
            out.append(" ; "); i += 1; continue
        elif c == "#" and (i == 0 or text[i - 1] in " \t;"):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        out.append(c); i += 1
    return "".join(out)


def expand_vars(text, env):
    """같은 명령 안에서 정한 변수와 알려진 환경 변수를 푼다. 못 푸는 것은 그대로 둔다($가 남으면 '모름')."""
    def sub(m):
        name = m.group(1) or m.group(2) or m.group(3)
        key = name.lower() if m.group(3) else name
        if key in env:
            return env[key]
        return m.group(0)
    return re.sub(r"\$\{(\w+)\}|\$(?!env:)([A-Za-z_]\w*)|\$env:(\w+)", sub, text, flags=re.I)


def collect_assignments(text, env):
    for m in re.finditer(r"(?:^|[\s;&|(])(?:export\s+|local\s+)?([A-Za-z_]\w*)=(\"[^\"]*\"|'[^']*'|[^\s;&|\"'`]*)"
                         r"|\$([A-Za-z_]\w*)\s*=\s*(\"[^\"]*\"|'[^']*')", text):
        name = m.group(1) or m.group(3)
        val = m.group(2) if m.group(1) else m.group(4)
        if val and val[0] in "\"'":
            raw, quoted_single = val[1:-1], val[0] == "'"
        else:
            raw, quoted_single = val, False
        if "$(" in raw or "`" in raw:
            continue
        env[name] = raw if quoted_single else expand_vars(raw, env)


def base_env():
    import tempfile
    env = {}
    home = HOME.replace("\\", "/")
    for k in ("HOME", "USERPROFILE"):
        env[k] = home
        env[k.lower()] = home
    tmp = tempfile.gettempdir().replace("\\", "/")
    for k in ("TEMP", "TMP", "TMPDIR"):
        env[k] = os.environ.get(k, tmp).replace("\\", "/")
        env[k.lower()] = env[k]
    return env


def resolve(p, cwd):
    """경로 문자열 → 소문자·슬래시 절대 경로. 변수가 남았거나 장치 이름이면 None."""
    if p is None:
        return None
    p = p.strip().strip("\"'")
    if not p or "$" in p or "`" in p or p.lower() in NULL_DEVICES or p.startswith("-"):
        return None
    p = p.replace("\\", "/")
    if p == "~" or p.startswith("~/"):
        p = HOME.replace("\\", "/") + p[1:]
    m = re.match(r"^/([a-zA-Z])(/|$)", p)
    if m:
        p = m.group(1) + ":/" + p[3:]
    elif p == "/tmp" or p.startswith("/tmp/"):
        import tempfile
        p = tempfile.gettempdir().replace("\\", "/") + p[4:]
    if not re.match(r"^([a-zA-Z]:/|/)", p) and cwd:
        p = cwd.rstrip("/") + "/" + p
    return os.path.normpath(p).replace("\\", "/").lower()


def rel_in_root(abs_low, root):
    if abs_low is None or not root:
        return None
    r = root.replace("\\", "/").rstrip("/").lower()
    if abs_low == r:
        return ""
    if abs_low.startswith(r + "/"):
        return abs_low[len(r) + 1:]
    return None


def _seg_overlap(a, b):
    return a == b or fnmatch.fnmatchcase(a, b) or fnmatch.fnmatchcase(b, a) or (has_glob(a) and has_glob(b))


def protected_hit(rel, pats):
    """프로젝트 기준 경로(rel, 소문자)를 쓰거나 지우면 보호 원본에 닿는가. 닿으면 그 패턴."""
    for pat in pats:
        pl = pat.lower()
        pl = pl[2:] if pl.startswith("./") else pl
        if rel in ("", "."):
            return pat  # 프로젝트 전체
        if not has_glob(rel):
            if matches(rel, pat):
                return pat
            if literal_prefix(pl).startswith(rel.rstrip("/") + "/"):
                return pat  # 보호 원본을 품은 폴더 자체(mv plugins, rm -r 원본의 상위)
            continue
        ts, ps = rel.strip("/").split("/"), pl.rstrip("/").split("/")
        dir_pat = pl.endswith("/") or ("/" not in pl and not has_glob(pl))
        n = min(len(ts), len(ps))
        if all(_seg_overlap(ts[i], ps[i]) for i in range(n)) and (len(ts) <= len(ps) or dir_pat):
            return pat
    return None


def _split_segments(tokens):
    seg = []
    for t in tokens:
        if t in (";", "&&", "||", "|", "&", "(", ")", ";;", "|&"):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(t)
    if seg:
        yield seg


def tokenize(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|<>()")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError as e:
        raise Unparsable(str(e))


class Findings:
    def __init__(self):
        self.writes = []      # (원래 표기, 절대 경로 또는 None)
        self.rdeletes = []    # 폴더째 지우기 대상
        self.code_rdelete_unknown = False


def analyze_code(code, lang, argv, cwd, env, f):
    """인라인 코드: 파일을 바꾸는 호출이 있을 때, 그 호출이 있는 줄의 경로만 대상으로 본다."""
    if lang == "sh":
        analyze_shell(code, cwd, env, f, depth=1)
        return
    skel, lits = code_skeleton(code, "py" if lang == "py" else "js")
    assigns = {}
    for m in re.finditer(r"\b(\w+)\s*=\s*(?:Path\s*\(|os\.path\.join\s*\(|r?)?\s*'\x00(\d+)\x00'", skel):
        assigns[m.group(1)] = lits[int(m.group(2))]
    for m in re.finditer(r"\b(\w+)\s*=\s*(?:Path\s*\()?\s*sys\.argv\[(\d+)\]", skel):
        i = int(m.group(2)) - 1
        if 0 <= i < len(argv):
            assigns[m.group(1)] = argv[i]
    # p = os.path.join(root, d) / Path(root) / d → "root 아래 어딘가"
    for m in re.finditer(r"\b(\w+)\s*=\s*(?:os\.path\.join\s*\(\s*|Path\s*\(\s*)(\w+)\s*[,)]", skel):
        if m.group(2) in assigns:
            assigns[m.group(1)] = assigns[m.group(2)].rstrip("/\\") + "/*"

    def window(pos):
        a = max(skel.rfind("\n", 0, pos), skel.rfind(";", 0, pos)) + 1
        b = skel.find("\n", pos)
        return skel[a:len(skel) if b < 0 else b]

    def candidates(win):
        out = [lits[int(i)] for i in re.findall(r"\x00(\d+)\x00", win)]
        out += [a or b for a, b in re.findall(r"'([^'\x00]{1,3})'|\"([^\"\x00]{1,3})\"", win)]  # 짧은 조각('원본')
        out += [argv[int(i) - 1] for i in re.findall(r"sys\.argv\[(\d+)\]", win) if 0 < int(i) <= len(argv)]
        out += [argv[int(i) - 2] for i in re.findall(r"process\.argv\[(\d+)\]", win) if 1 < int(i) <= len(argv) + 1]
        out += [assigns[w] for w in re.findall(r"\b(\w+)\b", win) if w in assigns]
        return [c for c in out if not is_fragment(c) and (path_like(c) or c.strip("/").lower() in TOP_NAMES)]

    for m in CODE_WRITE.finditer(skel):
        for c in candidates(window(m.start())):
            f.writes.append((c, resolve(expand_vars(c, env), cwd)))
    for m in CODE_RDELETE.finditer(skel):
        cands = candidates(window(m.start()))
        if not cands:
            f.code_rdelete_unknown = True  # 지울 대상을 그 줄에서 알 수 없음 → 묻는다
        for c in cands:
            f.rdeletes.append((c, resolve(expand_vars(c, env), cwd)))


def is_fragment(s):
    """sp + '/a.json' 처럼 이어 붙이는 조각이나 구분자뿐인 문자열은 경로로 보지 않는다."""
    t = s.replace("\\", "/")
    if t in ("/", ".", "./", "..", ""):
        return True
    if re.match(r"^\.[A-Za-z0-9]{1,6}$", t) or re.match(r"^[\w-]*[_-]$", t):
        return True  # '.png' 같은 확장자, 'concept_' 같은 이름 앞부분
    return t.startswith("/") and t.count("/") < 2 and not re.match(r"^(/[a-zA-Z]/|/tmp$|/dev/)", t)


TOP_NAMES = set()  # 보호 패턴의 맨 위 이름(예: 원본, input). 코드 속 os.path.join(R, "원본") 같은 조각을 잡는다


def _positionals(args, cmd, ps_style):
    """옵션을 뺀 위치 인자와 PowerShell 경로 매개변수 값."""
    pos, named, skip_next = [], [], None
    vopts = VALUE_OPTS.get(cmd, set())
    topts = TARGET_OPTS.get(cmd, set())
    i = 0
    while i < len(args):
        a = args[i]
        al = a.lower()
        if ps_style and al.startswith("-") and len(a) > 2:
            key = al.split(":")[0]
            if key in PS_PATH_PARAMS:
                if ":" in a:
                    named.append((key, a.split(":", 1)[1]))
                elif i + 1 < len(args):
                    named.append((key, args[i + 1])); i += 1
            elif key in PS_VALUE_PARAMS and ":" not in a:
                i += 1
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            if a in topts and i + 1 < len(args):
                named.append(("target", args[i + 1])); i += 2; continue
            if a in vopts and i + 1 < len(args):
                i += 2; continue
            i += 1
            continue
        if cmd in ("rd", "rmdir", "del", "erase", "xcopy", "robocopy") and re.match(r"^/[a-zA-Z]{1,2}$", a):
            i += 1
            continue
        pos.append(a)
        i += 1
    return pos, named


def is_recursive_rm(cmd, args):
    for a in args:
        al = a.lower()
        if re.match(r"^-[a-zA-Z]{1,4}$", a) and re.search(r"[rR]", a[1:]):
            return True
        if al in ("--recursive",) or al.startswith("-recurse"):
            return True
        if cmd in ("rd", "rmdir", "del", "erase") and al == "/s":
            return True
    return False


def analyze_segment(words, redirect_targets, heredoc, cwd, env, f):
    """명령 하나(낱말 목록). 바뀐 cwd를 돌려준다."""
    for t in redirect_targets:
        f.writes.append((t, resolve(t, cwd)))
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
        words = words[1:]  # 앞쪽 변수 지정
    while words and words[0].lower() in WRAPPERS:
        words = words[1:]
        while words and (words[0].startswith("-") or re.match(r"^[A-Za-z_]\w*=", words[0])):
            words = words[1:]
    if words and words[0].lower() == "timeout":
        words = words[1:]
        while words and (words[0].startswith("-") or re.match(r"^\d+[smhd]?$", words[0])):
            words = words[1:]
    if not words:
        return cwd
    raw_cmd = words[0]
    cmd = os.path.basename(raw_cmd.replace("\\", "/")).lower()
    cmd = re.sub(r"\.(exe|cmd|bat)$", "", cmd)
    cmd = re.sub(r"^python3\.\d+$", "python", cmd)
    args = words[1:]
    ps_style = "-" in cmd and cmd[0].isalpha() or cmd in ("ri", "mi", "cpi", "ni", "sc", "ac", "clc", "rni", "sl")
    ps_style = ps_style or any(a.lower().startswith(("-recurse", "-force", "-path", "-literalpath", "-destination"))
                               for a in args)

    if cmd in CD_CMDS:
        pos, named = _positionals(args, cmd, ps_style)
        target = named[0][1] if named else (pos[0] if pos else "~")
        return None if target == "-" else resolve(target, cwd)  # 못 풀면 None = 위치 모름

    if cmd == "xargs":
        rest = list(args)
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in ("-I", "-n", "-P", "-d", "-L", "-s") else rest[1:]
        if rest and os.path.basename(rest[0]).lower() in REMOVE_CMDS | MOVE_CMDS:
            f.writes.append(("<xargs 입력>", None))
            if is_recursive_rm(rest[0].lower(), rest[1:]):
                f.rdeletes.append(("<xargs 입력>", None))
        return cwd

    if cmd in REMOVE_CMDS:
        pos, named = _positionals(args, cmd, ps_style)
        targets = pos + [v for _, v in named]
        rec = is_recursive_rm(cmd, args)
        for t in targets:
            f.writes.append((t, resolve(t, cwd)))
            if rec:
                f.rdeletes.append((t, resolve(t, cwd)))
        return cwd
    if cmd in MOVE_CMDS:
        pos, named = _positionals(args, cmd, ps_style)
        for t in pos + [v for _, v in named]:
            f.writes.append((t, resolve(t, cwd)))
        return cwd
    if cmd in COPY_CMDS:
        pos, named = _positionals(args, cmd, ps_style)
        dests = [v for k, v in named if k in ("target", "-destination")]
        if not dests and pos:
            dests = [pos[1]] if cmd in ("robocopy", "xcopy") and len(pos) > 1 else [pos[-1]] if len(pos) > 1 else []
        for t in dests:
            f.writes.append((t, resolve(t, cwd)))
        return cwd
    if cmd in CREATE_CMDS:
        pos, named = _positionals(args, cmd, ps_style)
        for t in pos + [v for _, v in named]:
            f.writes.append((t, resolve(t, cwd)))
        return cwd
    if cmd in CONTENT_CMDS:
        pos, named = _positionals(args, cmd, ps_style)
        tl = [v for _, v in named] or pos[:1]
        for t in tl:
            f.writes.append((t, resolve(t, cwd)))
        return cwd
    if cmd in ("sed", "perl") and any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in args):
        pos, named = _positionals(args, "sed", False)
        has_script_opt = any(a in ("-e", "-f") for a in args) or cmd == "perl"
        files = pos if has_script_opt else pos[1:]
        for t in files:
            f.writes.append((t, resolve(t, cwd)))
        return cwd
    if cmd == "unzip":
        d = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "-d"), ".")
        f.writes.append((d, resolve(d, cwd)))
        return cwd
    if cmd in ("tar", "bsdtar") and any(re.match(r"^-?[a-zA-Z]*x", a) or a == "--extract" for a in args[:2]):
        d = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-C", "--directory")), ".")
        f.writes.append((d, resolve(d, cwd)))
        return cwd
    if cmd in ("7z", "7za", "7zr") and args and args[0].lower() in ("x", "e"):
        d = next((a[2:] for a in args if a.startswith("-o")), ".")
        f.writes.append((d, resolve(d, cwd)))
        return cwd
    if cmd == "expand-archive":
        pos, named = _positionals(args, cmd, True)
        d = next((v for k, v in named if k == "-destinationpath"), pos[1] if len(pos) > 1 else ".")
        f.writes.append((d, resolve(d, cwd)))
        return cwd
    if cmd == "curl":
        for i, a in enumerate(args):
            if a in ("-o", "--output") and i + 1 < len(args):
                f.writes.append((args[i + 1], resolve(args[i + 1], cwd)))
            elif a == "-O" or a == "--remote-name":
                f.writes.append((".", resolve(".", cwd)))
        return cwd
    if cmd == "wget":
        o = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-O", "-P")), ".")
        f.writes.append((o, resolve(o, cwd)))
        return cwd
    if cmd in ("invoke-webrequest", "iwr", "invoke-restmethod", "irm"):
        pos, named = _positionals(args, cmd, True)
        for k, v in named:
            if k == "-outfile":
                f.writes.append((v, resolve(v, cwd)))
        return cwd
    if cmd == "find":
        if "-delete" in args or ("-exec" in args and any(a in ("rm", "/bin/rm") for a in args)):
            starts = []
            for a in args:
                if a.startswith(("-", "(", "!")):
                    break
                starts.append(a)
            rec = "-exec" in args and is_recursive_rm("rm", args[args.index("-exec") + 1:])
            for t in starts or ["."]:
                f.writes.append((t, resolve(t, cwd)))
                if rec:
                    f.rdeletes.append((t, resolve(t, cwd)))
        return cwd
    if cmd == "dd":
        for a in args:
            if a.startswith("of="):
                f.writes.append((a[3:], resolve(a[3:], cwd)))
        return cwd
    if cmd == "git" and args:
        sub = args[0].lower()
        rest = [a for a in args[1:] if not a.startswith("-")]
        if sub in ("rm", "mv"):
            for t in rest:
                f.writes.append((t, resolve(t, cwd)))
        elif sub in ("checkout", "restore") and "--" in args:
            for t in args[args.index("--") + 1:]:
                f.writes.append((t, resolve(t, cwd)))
        elif sub == "clean":
            f.writes.append((".", resolve(".", cwd)))
        return cwd

    # 인라인 코드
    if cmd in INTERPRETERS:
        lang = "py" if cmd in ("python", "py", "python3") else "js"
        code, argv, script = None, [], False
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-c", "-e", "--eval", "-p", "--print") and i + 1 < len(args):
                code, argv = args[i + 1], args[i + 2:]
                break
            if a == "-":
                code, argv = heredoc, args[i + 1:]
                break
            if a in ("-m", "-W", "-X", "--require", "-r") and i + 1 < len(args):
                if a == "-m":
                    script = True
                    break
                i += 2
                continue
            if not a.startswith("-"):
                script = True  # 스크립트 파일 실행: 무엇을 쓰는지 알 수 없으므로 판단하지 않는다(통과)
                break
            i += 1
        if code is None and not script and heredoc is not None:
            code = heredoc  # python <<EOF
        if code:
            analyze_code(code, lang, argv, cwd, env, f)
        return cwd
    if cmd in SUB_SHELLS:
        if "-c" in args and args.index("-c") + 1 < len(args):
            analyze_shell(args[args.index("-c") + 1], cwd, env, f, depth=1)
        elif heredoc is not None and not any(not a.startswith("-") for a in args):
            analyze_shell(heredoc, cwd, env, f, depth=1)
        return cwd
    if cmd in POWERSHELLS:
        for i, a in enumerate(args):
            if a.lower() in ("-command", "-c") and i + 1 < len(args):
                analyze_shell(" ".join(args[i + 1:]), cwd, env, f, depth=1)
                break
        return cwd
    return cwd


DOTNET_WRITE = re.compile(r"\[(?:System\.)?IO\.(?:File|Directory)\]::\w*(?:Write|Append|Delete|Move|Copy|Create|Replace)\w*"
                          r"\s*\(\s*['\"]([^'\"]+)['\"]", re.I)


def analyze_shell(text, cwd, env, f, depth=0):
    if depth > 3:
        raise Unparsable("too deep")
    bodies = []

    def take(m):
        bodies.append(m.group(3))
        return m.group(0)[:m.group(0).index("\n") + 1]  # 첫 줄(<<EOF 와 그 뒤)만 남긴다

    shell = HEREDOC_ANY.sub(take, text)
    shell = win_fwd(shell)
    collect_assignments(shell, env)
    shell = expand_vars(shell, env)
    for m in DOTNET_WRITE.finditer(shell):  # [IO.File]::WriteAllText('x', ...)
        f.writes.append((m.group(1), resolve(m.group(1), cwd)))
    shell = re.sub(r"(?<![<\w])\d?>&\d", " ", shell)  # 2>&1
    shell = split_outside_quotes(shell)
    tokens = tokenize(shell)
    body_iter = iter(bodies)
    for seg in _split_segments(tokens):
        words, redirs, heredoc, i = [], [], None, 0
        while i < len(seg):
            t = seg[i]
            if t in REDIRECTS or (t.endswith(">") and t.rstrip(">").isdigit() is False and set(t) <= set("&>|")):
                if i + 1 < len(seg):
                    tgt = seg[i + 1]
                    if tgt.lower() not in NULL_DEVICES:
                        redirs.append(tgt)
                i += 2
                continue
            if t in ("<<", "<<-"):
                heredoc = next(body_iter, "")
                i += 2
                continue
            if t in ("<", "<<<"):
                i += 2
                continue
            if t.isdigit() and i + 1 < len(seg) and seg[i + 1] in REDIRECTS | {">"}:
                i += 1
                continue
            words.append(t)
            i += 1
        cwd = analyze_segment(words, redirs, heredoc, cwd, env, f)
    return cwd


def is_temp_inside(abs_low):
    if not abs_low:
        return False
    return any(abs_low.startswith(t + "/") and len(abs_low) > len(t) + 1 for t in temp_dirs()
               if re.match(r"^([a-z]:/|/)", t))


def check_shell(command, root, cwd=None):
    pats = read_patterns(os.path.join(root, ".gemini", "protected.txt")) if root else []
    TOP_NAMES.clear()
    TOP_NAMES.update(p.lower().lstrip("./").split("/")[0].rstrip("*") for p in pats if p.strip("./"))
    TOP_NAMES.discard("")

    f = Findings()
    start = (cwd or root or os.getcwd()).replace("\\", "/")
    try:
        shell_only = HEREDOC_ANY.sub(lambda m: m.group(0)[:m.group(0).index("\n") + 1], command)
        analyze_shell(command, start, base_env(), f)
    except Exception:
        # 낱말로 못 나누거나 분석 중 오류가 나면 예전 방식(이름이 나오면 확인)으로 안전하게 판단
        return check_shell_by_mention(command, root)

    # H2: 되돌리기 어려운 git·포맷 명령 (히어독 본문·따옴표 안 글은 제외)
    plain = re.sub(r"\"(?:\\.|[^\"\\])*\"|'[^']*'", " ", shell_only)
    for pattern, label in DANGEROUS:
        if "폴더째" in label or "하위까지" in label:
            continue
        if re.search(pattern, plain, re.IGNORECASE):
            return ("ask", f"{label} 명령입니다. 실행해도 되는지 확인이 필요합니다.")

    # 하네스 자기 보호: 쓰는 대상이 설정·훅·보호 목록일 때만
    for raw, ab in f.writes + f.rdeletes:
        low = (ab or win_fwd(raw)).replace("\\", "/").lower()
        temp_project = bool(ab and is_temp_inside(ab) and rel_in_root(ab, root) is None
                            and not ab.startswith(norm(GEMINI_HOME).lower()))
        for sp in SELF_PATHS:
            if temp_project and sp in PROJECT_SELF:
                continue  # 임시 폴더의 시험용 프로젝트(.gemini/protected.txt 등)는 이 프로젝트 설정이 아니다
            if sp in low:
                return ("ask", f"하네스 설정·훅·보호 목록({sp})을 바꿀 수 있는 명령입니다. 확인이 필요합니다.")

    # H1: 보호 원본 (쓰는 대상이 프로젝트 안 보호 경로일 때만)
    if root and pats:
        for raw, ab in f.writes + f.rdeletes:
            rel = rel_in_root(ab, root)
            if rel is None and ab is None:
                # 변수를 못 푼 대상: 보호 폴더 이름이 경로 맨 앞에 그대로 나올 때만 확인
                r = win_fwd(raw).lower().lstrip("./")
                hit = next((p for p in pats if r.startswith(literal_prefix(p.lower()).lstrip("./")) and literal_prefix(p)), None)
            else:
                hit = protected_hit(rel, pats) if rel is not None else None
            if hit:
                return ("ask", f"보호된 원본({hit})을 바꾸거나 지울 수 있는 명령입니다({raw}). 원본을 바꾸지 않는지 확인이 필요합니다.")

    # H2: 폴더째 지우기 — 임시 폴더 안의 확실한 경로만 통과
    if f.code_rdelete_unknown:
        return ("ask", "코드가 폴더째 지우는데 지울 대상을 알 수 없습니다. 실행해도 되는지 확인이 필요합니다.")
    for raw, ab in f.rdeletes:
        # 통과 조건: 절대 경로로 적었고(변수는 풀어서), 임시 폴더 안이고, 지금 프로젝트 폴더 안은 아님
        absolute = bool(re.match(r"^([a-zA-Z]:[\\/]|/|~/)", raw.strip("\"'")))
        if not (absolute and is_temp_inside(ab) and rel_in_root(ab, root) is None):
            return ("ask", f"폴더째 지우는 명령입니다({raw}). 실행해도 되는지 확인이 필요합니다.")

    # H3: 읽기 전용
    if root and os.path.isfile(os.path.join(root, ".gemini", "READONLY")):
        allowed = read_patterns(os.path.join(root, ".gemini", "READONLY"))
        for raw, ab in f.writes:
            rel = rel_in_root(ab, root)
            if ab is None or (rel is not None and not any(matches(rel, p) for p in allowed)):
                return ("ask", "읽기 전용 모드인데 파일을 바꿀 수 있는 명령입니다. 확인이 필요합니다.")
        if MUTATING.search(command) and not f.writes:
            return ("ask", "읽기 전용 모드인데 파일을 바꿀 수 있는 명령입니다. 확인이 필요합니다.")
    return None


def command_hash(command):
    return hashlib.sha256(command.strip().replace("\r\n", "\n").encode("utf-8")).hexdigest()


def touches_self(command, cwd=None):
    r = check_shell(command, None, cwd)
    return bool(r and "하네스 설정" in r[1])


def consume_approval(root, command):
    """사용자가 미리 승인한 바로 그 명령이면 승인 항목을 돌려주고 사용 횟수를 올린다 (1회 승인은 한 번 쓰면 끝).
    명령 문자열이 한 글자라도 다르면, 만료됐으면, 파일이 깨졌거나 기록을 못 남기면 None → 원래대로 확인 요청."""
    if not root:
        return None
    path = os.path.join(root, ".gemini", APPROVALS)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        items = data.get("approvals", [])
    except (OSError, ValueError, AttributeError):
        return None
    h, now = command_hash(command), time.time()
    for a in items:
        if not isinstance(a, dict) or a.get("sha256") != h:
            continue
        if a.get("expires") and now > float(a["expires"]):
            continue
        if a.get("once", True) and int(a.get("used", 0)) >= 1:
            continue
        a["used"] = int(a.get("used", 0)) + 1
        a["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except OSError:
            return None  # 사용 기록을 못 남기면 1회 승인이 여러 번 쓰일 수 있으니 통과시키지 않는다
        return a
    return None


def main():
    data = read_input()
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    root = find_root(os.environ.get("GEMINI_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or cwd)

    if tool in EDIT_TOOLS:
        result = check_edit(tool_input, root, cwd)
    elif tool in SHELL_TOOLS:
        command = tool_input.get("command", "")
        dir_path = tool_input.get("dir_path")
        shell_cwd = os.path.abspath(os.path.join(cwd, dir_path)) if dir_path else cwd
        result = check_shell(command, root, shell_cwd)
        # 확인 요청 대상이라도 사용자가 이 명령을 미리 승인했으면 통과 (하네스 설정 변경은 승인으로 못 엶)
        if result and result[0] == "ask" and not touches_self(command, shell_cwd):
            a = consume_approval(root, command)
            if a:
                kind = "1회" if a.get("once", True) else "항상"
                log({"tool": tool, "decision": "approved", "reason": f"사용자 승인({kind}) 사용: {result[1]}",
                     "card": a.get("card"), "cwd": cwd})
                result = None
    else:
        result = None

    if result:
        decision, reason = result
        what = tool_input.get("command") or tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        # 다음 조정을 위해 무엇이 걸렸는지 앞부분만 남긴다 (이 PC의 로컬 기록에만)
        log({"tool": tool, "decision": decision, "reason": reason, "cwd": cwd, "input": what[:300]})
        emit(decision, reason)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 훅 오류로 작업 전체가 멈추지 않게 한다
        log({"error": repr(e)})
    sys.exit(0)
