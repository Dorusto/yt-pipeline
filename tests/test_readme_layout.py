import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TOOLS = ROOT / "docs/guides/tools.md"
SVG = ROOT / "docs/images/web-app.svg"

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "#", "mailto:")


def readme_text():
    return README.read_text(encoding="utf-8")


def relative_links(text):
    for target in LINK.findall(text):
        if target.startswith(SKIP_PREFIXES):
            continue
        yield target.split("#", 1)[0]


def test_readme_exists_and_is_short():
    assert README.is_file()
    assert len(readme_text().splitlines()) <= 90


def test_readme_links_to_tools_and_user_guide():
    text = readme_text()
    assert "docs/guides/tools.md" in text
    assert "docs/guides/user-guide.md" in text


def test_readme_relative_links_resolve():
    missing = [target for target in relative_links(readme_text()) if not (ROOT / target).is_file()]
    assert missing == [], f"unresolved README links: {missing}"


def test_tools_reference_exists_and_is_long():
    assert TOOLS.is_file()
    assert len(TOOLS.read_text(encoding="utf-8").splitlines()) > 100


def test_web_app_svg_parses():
    assert SVG.is_file()
    ET.parse(SVG)
