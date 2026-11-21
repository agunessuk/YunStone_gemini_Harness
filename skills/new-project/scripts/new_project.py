"""하네스 표준 프로젝트 틀 만들기.

  python new_project.py <프로젝트 폴더> [--name 이름] [--dry-run]

- 이미 있는 파일은 덮어쓰지 않고 "건너뜀"으로 알린다. 지우거나 옮기지 않는다.
- 폴더마다 README.md를 넣고(용도 한 줄), 보호 목록(.gemini/protected.txt)은 맨 마지막에 쓴다.
- 기존 파일이 있던 폴더면, 표준 폴더 밖에 있는 항목을 목록으로 보여 준다(원본 후보). 옮기는 것은 사용자가 정한다.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "template")

FOLDERS = {
    "input": "받은 원본 자료. 보호되어 Gemini가 고칠 수 없다. 작업은 `work/`에 복사해서 한다.",
    "work": "작업본, 중간 산출물, 실험 파일. 자유롭게 고친다.",
    "output": "사용자에게 보낼 결과물. 사용자가 확정한 것은 `output/확정/`으로 옮긴다.",
    "output/확정": "사용자가 확정한 결과물. 보호된다. 바꿀 일이 있으면 새 버전을 `output/`에 만든다.",
    "handoff": "다른 AI·PC로 넘길 인계본(`handoff-pack`). 만든 `.zip`은 보호된다. 고칠 일이 있으면 새 버전(`이름_vN_YYYYMMDD.zip`)으로.",
    "review": "검수 의견. `review/gpt/`(GPT 검수), `review/gemini/`(Gemini 분석 보고서, 읽기 전용 모드에서도 쓸 수 있는 곳).",
    "prompts": "다른 AI에게 줄 프롬프트. `prompts/gpt/<주제>_YYYYMMDD.md`.",
    "docs": "품질 기준(`QUALITY_BAR.md`), 피드백 기록(`FEEDBACK_LOG.md`), 결정·보관 문서.",
}
LAST = os.path.join(".gemini", "protected.txt")  # 보호가 먼저 켜지면 README를 못 쓰므로 마지막에

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def template_files():
    out = []
    for dirpath, _, files in os.walk(TEMPLATE):
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), TEMPLATE)
            out.append(rel)
    out.sort(key=lambda r: (r == LAST, r))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--name", help="프로젝트 이름 (기본: 폴더 이름)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    target = os.path.abspath(a.target)
    name = a.name or os.path.basename(target.rstrip("\\/"))
    date = time.strftime("%Y-%m-%d")
    existing = sorted(os.listdir(target)) if os.path.isdir(target) else []
    tag = "[미리보기] " if a.dry_run else ""
    made, skipped = [], []

    def put(rel, text):
        dst = os.path.join(target, rel)
        if os.path.exists(dst):
            skipped.append(rel)
            return
        made.append(rel)
        if not a.dry_run:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    for folder, desc in FOLDERS.items():
        title = folder.split("/")[-1]
        put(os.path.join(*folder.split("/"), "README.md"), f"# {title}\n\n{desc}\n")
    for rel in template_files():
        with open(os.path.join(TEMPLATE, rel), encoding="utf-8") as f:
            text = f.read().replace("{{PROJECT}}", name).replace("{{DATE}}", date)
        put(rel, text)

    print(f"{tag}대상: {target}  (이름: {name})")
    for r in made:
        print(f"{tag}만듦: {r}")
    for r in skipped:
        print(f"{tag}건너뜀(이미 있음): {r}")
    standard = set(FOLDERS) | {".gemini", "GEMINI.md", "PROGRESS.md", "INDEX.md"}
    others = [e for e in existing if e not in standard and not e.startswith(".")]
    if others:
        print(f"\n표준 폴더 밖에 있던 항목 {len(others)}개 (원본이면 input/으로 옮기거나 protected.txt에 경로를 적으세요):")
        for e in others:
            print("  - " + e)
    print(f"\n{tag}완료: 만듦 {len(made)}, 건너뜀 {len(skipped)}")


if __name__ == "__main__":
    main()
