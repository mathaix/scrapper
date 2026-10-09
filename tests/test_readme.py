import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()


def test_readme_opens_with_title_pitch_and_live_link():
    lines = README.splitlines()
    assert lines[0] == "# Signals"
    head = README.split("## Setup")[0]
    assert "https://claramap--signals.modal.run" in head
    assert "unauthenticated" in head or "no login" in head
    assert "Web scraper" not in README


def test_readme_doc_links_resolve():
    links = re.findall(r"\]\((?!https?://)([^)#]+)\)", README)
    assert {"docs/prd-signals-db.md", "docs/designs/final.md", "docs/research-signals-practice.md", "AGENTS.md"} <= set(links)
    for link in links:
        assert (ROOT / link).exists(), link


def test_planned_items_link_issues():
    planned = README.split("Planned")[1].split("## How it works")[0]
    assert "issues/6" in planned and "issues/13" in planned


def test_readme_shows_logo_that_exists():
    m = re.search(r'<img src="([^"]+)" alt="([^"]+)"', README)
    assert m, "README has no logo image with alt text"
    src, alt = m.groups()
    assert "logo" in alt.lower()
    logo = ROOT / src
    assert logo.exists()
    assert "<svg" in logo.read_text()
    assert README.index(src) < README.index("## What it captures")
