"""문서(README.md, docs/**/*.md)의 상대 링크와 그림이 실제 파일·절을 가리키는지, 그림은 docs/photo 안에 있는지."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
LINK = re.compile(r"(!?)\[[^\]]*\]\(([^)\s]+)\)")


def anchors(path):
    """GitHub 절 앵커: 제목을 소문자로, 글자·숫자·공백·-·_만 남기고 공백은 -로."""
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if m:
            found.add(re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", m.group(1).strip().lower())))
    return found


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_links_and_images_resolve(doc):
    for image, target in LINK.findall(doc.read_text(encoding="utf-8")):
        if re.match(r"[a-z]+:", target) or target.startswith("#") and not target[1:]:
            continue
        path, _, anchor = target.partition("#")
        dest = (doc.parent / path).resolve() if path else doc
        assert dest.exists(), f"{doc.name}: 없는 파일 {target}"
        if image:
            assert dest.parent == ROOT / "docs" / "photo", f"{doc.name}: 그림은 docs/photo 안에 {target}"
        if anchor and dest.suffix == ".md":
            assert anchor in anchors(dest), f"{doc.name}: 없는 절 {target}"


def test_only_five_feature_pages_at_docs_top():
    assert sorted(p.name for p in (ROOT / "docs").glob("*.md")) == ["analysis.md", "collect.md", "dashboard.md", "integrations.md", "query.md"]
