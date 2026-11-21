"""SessionStart 훅(H4): 세션 시작·재개·초기화(/clear) 뒤에 프로젝트 상태를 자동으로 넣어 준다.

- PROGRESS.md(프로젝트 루트 또는 .gemini/ 아래)가 있으면 앞부분을 넣는다.
- .gemini/READONLY가 있으면 읽기 전용 모드임을 알린다.
- 보호 목록(.gemini/protected.txt)이 있으면 목록을 알린다.
아무것도 없으면 아무것도 출력하지 않는다.
"""
import json
import os
import sys

MAX_CHARS = 6000


def read_text(path, limit=None):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    if limit and len(text) > limit:
        text = text[:limit] + f"\n\n…(이하 생략, 전체는 {path})"
    return text


def main():
    raw = sys.stdin.buffer.read()
    data = json.loads(raw.decode("utf-8-sig") or "{}")
    root = os.environ.get("GEMINI_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    source = data.get("source", "")

    parts = []
    for cand in (os.path.join(root, "PROGRESS.md"), os.path.join(root, ".gemini", "PROGRESS.md")):
        if os.path.isfile(cand):
            head = "문맥이 압축되었거나 세션을 이어갑니다. " if source in ("resume", "compact") else ""
            parts.append(f"## [로컬 하네스] 진행 기록\n{head}아래는 {cand} 내용입니다. "
                         "작업을 이어갈 때는 이 기록의 '다음 할 일'부터 확인하고, 실제 파일 상태와 다르면 파일을 기준으로 합니다.\n\n"
                         + read_text(cand, MAX_CHARS))
            break

    readonly = os.path.join(root, ".gemini", "READONLY")
    if os.path.isfile(readonly):
        allowed = [ln.strip() for ln in read_text(readonly).splitlines() if ln.strip() and not ln.startswith("#")]
        where = f" 쓸 수 있는 곳: {', '.join(allowed)}" if allowed else " 어떤 파일도 고치지 않습니다."
        parts.append("## [로컬 하네스] 읽기 전용 모드\n이 프로젝트는 지금 분석·검토만 합니다." + where)

    protected = os.path.join(root, ".gemini", "protected.txt")
    if os.path.isfile(protected):
        pats = [ln.strip() for ln in read_text(protected).splitlines() if ln.strip() and not ln.startswith("#")]
        if pats:
            parts.append("## [로컬 하네스] 보호된 원본\n다음 경로는 고치거나 지우거나 옮기지 않습니다. 필요하면 복사본에서 작업합니다: "
                         + ", ".join(pats))

    if parts:
        out = {"hookSpecificOutput": {"additionalContext": "\n\n".join(parts)}}
        sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
