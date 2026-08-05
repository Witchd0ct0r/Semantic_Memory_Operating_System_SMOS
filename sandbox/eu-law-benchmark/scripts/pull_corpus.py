"""Pulls Regulation (EU) 2018/1807 (in full) and GDPR Articles 1-4 from live
EUR-Lex into corpus/. Each regulation's full consolidated text lives at one
URL (verified 2026-08-05 — not one URL per article), so this makes exactly 2
HTTP requests, sleeping 10s between them per eur-lex.europa.eu/robots.txt's
Crawl-delay. Reusable under EUR-Lex's CC BY 4.0 legislative-text reuse policy
(Decision 2011/833/EU) — see SOURCE.md for the attribution this script writes.

Re-run any time to refresh the corpus; overwrites existing files.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_HEADERS = {"User-Agent": "Mozilla/5.0 (SMOS eu-law-benchmark research fetch)"}
_CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
_ART_DIV_ID_RE = re.compile(r"^art_\d+$")


def _fetch_articles(celex: str, keep: set[int] | None) -> tuple[str, list[int]]:
    url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{celex}"
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    parts: list[str] = []
    found: list[int] = []
    for div in soup.find_all("div", id=_ART_DIV_ID_RE, class_="eli-subdivision"):
        num = int(div["id"].split("_", 1)[1])
        if keep is not None and num not in keep:
            continue
        parts.append(div.get_text(separator="\n", strip=True))
        found.append(num)
    return "\n\n".join(parts), found


def main() -> None:
    _CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    text_1807, found_1807 = _fetch_articles("32018R1807", keep=None)
    (_CORPUS_DIR / "reg_2018_1807.txt").write_text(text_1807, encoding="utf-8")
    print(f"Reg 2018/1807: wrote articles {found_1807}")

    time.sleep(10)  # robots.txt Crawl-delay: 10

    text_gdpr, found_gdpr = _fetch_articles("32016R0679", keep={1, 2, 3, 4})
    (_CORPUS_DIR / "gdpr_articles_1-4.txt").write_text(text_gdpr, encoding="utf-8")
    print(f"GDPR: wrote articles {found_gdpr}")

    (_CORPUS_DIR / "SOURCE.md").write_text(
        "# Corpus source\n\n"
        "Pulled from EUR-Lex (eur-lex.europa.eu), English-language text:\n"
        "- Regulation (EU) 2018/1807 on the free flow of non-personal data, "
        "CELEX 32018R1807, Articles 1-9 (full text).\n"
        "- Regulation (EU) 2016/679 (GDPR), CELEX 32016R0679, Articles 1-4 "
        "(Chapter I, General provisions) only.\n\n"
        "Reuse: permitted for commercial or non-commercial purposes under "
        "Decision 2011/833/EU, CC BY 4.0 (source acknowledgement required) — "
        "see https://eur-lex.europa.eu/content/legal-notice/legal-notice.html.\n\n"
        "Re-pull with: sandbox/eu-law-benchmark/scripts/pull_corpus.py\n",
        encoding="utf-8",
    )
    print(f"\nWrote 2 corpus files + SOURCE.md into {_CORPUS_DIR}")


if __name__ == "__main__":
    main()
