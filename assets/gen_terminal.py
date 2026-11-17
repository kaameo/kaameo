#!/usr/bin/env python3
"""Generate assets/terminal.svg — a fastfetch-style terminal for the GitHub profile.

Static text lives in the CONTENT section. Blog posts (RSS) and GitHub stats are
fetched at build time; .github/workflows/update-terminal.yml reruns this daily.

Local run:  python3 assets/gen_terminal.py
            (uses $GITHUB_TOKEN, else your `gh` CLI login; without either the stats section is skipped)
"""
import json
import os
import subprocess
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
from pathlib import Path

# ── CONTENT ─────────────────────────────────────────────────────────────
USER = "kaameo"
BLOG = "https://kaameo.github.io"  # README wraps the image in a link to this URL
FEED = f"{BLOG}/rss.xml"
CLICK_HINT = "# ← click"  # comment typed after the final `open` command
MAX_POSTS = 3

SYSTEM = [  # (key, value)
    ("Host", "South Korea"),
    ("Kernel", "Software Engineer"),
    ("Email", "kaameo12@gmail.com"),
    ("LinkedIn", "linkedin.com/in/jinsu-hong"),
]
STACK = [
    ("Languages.Programming", "Java, TypeScript, Go"),
    ("Frameworks", "Spring Boot, NestJS"),
    ("Data & Messaging", "Kafka, PostgreSQL"),
    ("Infra", "Docker, Kubernetes"),
]
ABOUT = [  # **bold** → highlighted
    "대용량 트래픽과 **이벤트 기반 아키텍처**에 관심이 많습니다",
    "주로 **Spring Boot**와 **NestJS**로 서버를 만듭니다",
    "**Kafka, Kubernetes** 등 분산 시스템을 공부합니다",
    "요즘은 **Spring AI**로 벡터 검색, RAG를 실험하고 있습니다",
]

# Capybara with a tangerine hat (from icons/귤 모자를 쓴 통통한 카피바라.png) — one letter per cell, see ART_STYLE
ART = """
.................................LLLLLL.......................
................................LLLLO.........................
.............................OOOOOOOO.........................
.........................TTTTTTTTTTTO.........................
........................TTBTTTTTTTTTT.........................
.......................OTTTTTTTTTTTTTT........................
...............OMMMM...OTTTTTTTTTTTTTOOOMMMM..................
.............OMCMCMMMOOOOOOTTTTTTTOOOOBOOOOMM.................
.............OCMMMMMBBBBBBOOOOOOOOOBBBBBBBOOOO................
.............OOMMMBBBBBBBBBBBBBBBBBBBBBBBBBBOO................
.............OOOOBBBBBBBBBBBBBBBBBBBBBBBBBBBBB................
.............BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB...............
...........OBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBOOOO..............
..........BBBBBBBBBBBBBBOOOOOOBBBMMMMMMMMMMBOOOOBO............
.........OBBBBBBBBBCCCBBBBBBBBBBMMMMMOMMMOMMMBBBCCC...........
........OBBBBBBBBCCCCCCCBBBBBBBMMMMMMOOOOOMMMBBBCCCO..........
.......OBBBBBBBBBCCCCCCBBBBBBBMMMMMMMMMOOMMMMMBBBBBB..........
......BBBBBBBBBBBBBBBBBBBBBBBBMMMMMMMMMOMMMMMBBBBBBB..........
....OBBBBBBBBBBBBBBBBBBBBBBBBBBMMMMMMMMMMMMMMBBBBBBO..........
...BBBBBBBBBBBBBBBBBBBBBBBBBBBBBMMMMMMMMMMMMBBBBBBOO..........
..OBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB...P..PPPPPPPPPPPPPPP
.OBBBBBBBBBBBBBBBBBBBBOBBBBBBBBBBBBBBBBB..PPPPPPPPPPPPPPPPPPPP
.BBBBBBBBBBBBBBBBBBBOOOOOOOOOBBBBBBBBBBB.PPPPPPPPPPPPPPPPPPPP.
OBBBBBBBBBBBBBBBBBBBBBBBBBBOOOOBBBOOOO..PPPPPPPPPPPPPPPPPPPPP.
OBBBBBBBBBBBBBBBBBBBBBBBBBBBBOOOOOOMMM..PPPPPPPPPPPPPPPPPPPP..
OBBBBBBBBBBBBBBBBBBBBBBBBMMMMMOOOOMMMM.PPPPPPPPPPPPPPPPPPPP...
.OBBBBBBBOOBBBBBBBBBBBBBMMOOOOOOOOMOOOPPPPPPPPPPPPPPPPPPKKK...
..OBBBBBBBOOOBBBBBBBBOOOOOOOOOOOOOOOOPPPPPPPPPPKKKKKKKKKKK....
....OBBBBBBBOOOOOOOOKKKKKKKKKKKKKKKKOPPPPPPPPKKKKKKKKKKKK.....
"""
ART_STYLE = {  # letter → (char, color); each cell is ART_PX_W chars × ART_PX_H rows, '.' = empty
    "O": (":", "#7a5543"),  # outline (eyes, mouth, arm)
    "B": ("%", "#e6965c"),  # body
    "M": ("#", "#b36c41"),  # muzzle · ears · paws
    "C": ("*", "#f08a5d"),  # cheeks
    "T": ("@", "#fc7a02"),  # tangerine
    "L": ("&", "#6fbf40"),  # leaf
    "P": ("=", "#d5cccc"),  # laptop lid
    "K": ("-", "#9e9696"),  # laptop base · shadow
}

# ── STYLE & LAYOUT ──────────────────────────────────────────────────────
FG, MUTED, DIM, GREEN, BLUE, CYAN, ORANGE = (
    "#e6edf3", "#8b949e", "#484f58", "#3fb950", "#58a6ff", "#79c0ff", "#ffa657",
)
PROMPT = [(f"{USER}@github", GREEN), (":", FG), ("~", BLUE), ("$ ", FG)]
FONT = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', "
        "D2Coding, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Noto Sans KR', monospace")

FONT_SIZE, LINE_H = 14, 20
CELL = FONT_SIZE * 0.6                      # monospace advance width
ART_FONT, ART_LINE, ART_PX_W, ART_PX_H = 9, 10, 1, 1
PAD_X, W = 24, 980
INFO_X = 410                               # left edge of the info column
INFO_END = W - PAD_X                       # right edge (values are right-aligned)
INFO_COLS = int((INFO_END - INFO_X) / CELL)
COL_SPLIT = INFO_X + (INFO_END - INFO_X) * 0.55  # two-column stats rows
KST = timezone(timedelta(hours=9), "KST")


# ── DATA ────────────────────────────────────────────────────────────────
def http(url, data=None, headers=None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "profile-terminal", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read()


def graphql(token, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    res = json.loads(http("https://api.github.com/graphql", body, {"Authorization": f"bearer {token}"}))
    if res.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {res['errors']}")
    return res["data"]


def blog_posts():
    posts = []
    for item in ET.fromstring(http(FEED)).iter("item"):
        title, pub = item.findtext("title"), item.findtext("pubDate")
        if title and pub:
            posts.append((parsedate_to_datetime(pub).date(), title.strip()))
    return sorted(posts, reverse=True)[:MAX_POSTS]


PROFILE_QUERY = """query($login: String!) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    repositoriesContributedTo(includeUserRepositories: false,
                              contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) { totalCount }
    repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100) {
      totalCount nodes { stargazerCount } }
    contributionsCollection { contributionCalendar {
      totalContributions weeks { contributionDays { contributionCount } } } }
  }
}"""


def github_stats(token):
    user = graphql(token, PROFILE_QUERY, {"login": USER})["user"]

    # all-time commits: contributionsCollection spans at most one year, so query each year
    now = datetime.now(timezone.utc)
    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    years = "\n".join(
        f'y{y}: contributionsCollection(from: "{max(created, datetime(y, 1, 1, tzinfo=timezone.utc)):%Y-%m-%dT%H:%M:%SZ}", '
        f'to: "{min(now, datetime(y, 12, 31, 23, 59, 59, tzinfo=timezone.utc)):%Y-%m-%dT%H:%M:%SZ}") '
        f'{{ totalCommitContributions }}'
        for y in range(created.year, now.year + 1))
    per_year = graphql(token, f'query($login: String!) {{ user(login: $login) {{ {years} }} }}', {"login": USER})["user"]

    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [d["contributionCount"] for w in cal["weeks"] for d in w["contributionDays"]]
    longest = run = 0
    for count in days:
        run = run + 1 if count else 0
        longest = max(longest, run)
    current = 0
    for count in reversed(days[:-1] if days and not days[-1] else days):  # no commits *yet* today keeps the streak
        if not count:
            break
        current += 1

    return {
        "repos": user["repositories"]["totalCount"],
        "contributed": user["repositoriesContributedTo"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in user["repositories"]["nodes"]),
        "followers": user["followers"]["totalCount"],
        "commits": sum(v["totalCommitContributions"] for v in per_year.values()),
        "contributions": cal["totalContributions"],
        "streak": current,
        "longest": longest,
    }


# ── TEXT HELPERS ────────────────────────────────────────────────────────
def width(text: str) -> float:
    """Approximate rendered width; wide (CJK) glyphs are over-estimated so dot leaders never collide."""
    return sum(FONT_SIZE if unicodedata.east_asian_width(c) in "WF" else CELL for c in text)


def rich(s: str, base=FG, hi=ORANGE):
    """'a **b** c' → [(a, base), (b, hi), (c, base)]"""
    return [(part, hi if i % 2 else base) for i, part in enumerate(s.split("**")) if part]


def spans(parts) -> str:
    return "".join(f'<tspan fill="{c}">{escape(s)}</tspan>' for s, c in parts)


# An info row is a list of text pieces: (x, anchor, parts)
def kv(key, value, x0=INFO_X, x1=INFO_END, key_color=ORANGE, value_parts=None):
    value_parts = value_parts or [(value, CYAN)]
    head = f". {key}: "
    room = (x1 - x0) - width(head) - width("".join(s for s, _ in value_parts)) - CELL
    dots = "." * max(int(room / CELL), 2)
    return [(x0, "start", [(". ", DIM), (f"{key}:", key_color), (" " + dots, DIM)]), (x1, "end", value_parts)]


def header(title, title_color=FG, prefix="- "):
    text = f"{prefix}{title} "
    return [(INFO_X, "start", [(prefix, DIM), (title, title_color), (" " + "-" * (INFO_COLS - len(text)), DIM)])]


def plain(parts):
    return [(INFO_X, "start", parts)]


def info_rows(posts, stats):
    rows = [header(f"{USER}@github", BLUE, prefix="")]
    rows += [kv(k, v) for k, v in SYSTEM] + [None]
    rows += [header("Skills")] + [kv(k, v) for k, v in STACK] + [None]
    rows += [header("About")] + [plain([(". ", DIM), *rich(s)]) for s in ABOUT] + [None]
    rows += [header("Blog")]
    rows += [kv(f"{d:%Y-%m-%d}", None, key_color=MUTED, value_parts=[(t, FG)]) for d, t in posts] \
        or [plain([("(no posts yet)", MUTED)])]
    if stats:
        left, right = (INFO_X, COL_SPLIT - CELL * 2), (COL_SPLIT + CELL * 2, INFO_END)
        sep = (COL_SPLIT, "middle", [("|", DIM)])

        def pair(lk, lv, rk, rv):
            return kv(lk, None, *left, value_parts=lv) + [sep] + kv(rk, None, *right, value_parts=rv)

        contributed = [(f" {{Contributed: {stats['contributed']}}}", MUTED)] if stats["contributed"] else []
        rows += [None, header("GitHub Stats")]
        rows += [
            pair("Repos", [(f"{stats['repos']}", CYAN), *contributed], "Stars", [(f"{stats['stars']:,}", CYAN)]),
            pair("Commits", [(f"{stats['commits']:,}", CYAN)], "Followers", [(f"{stats['followers']:,}", CYAN)]),
            pair("Contributions", [(f"{stats['contributions']:,}", GREEN), (" /yr", MUTED)],
                 "Streak", [(f"{stats['streak']}d", GREEN), (f" / max {stats['longest']}d", MUTED)]),
        ]
    return rows


def art_rows():
    rows = []
    for line in ART.strip().splitlines():
        runs = []
        for px in line:
            ch, color = ART_STYLE.get(px, (" ", FG))
            text = ch * ART_PX_W
            if runs and runs[-1][1] == color:
                runs[-1] = (runs[-1][0] + text, color)
            else:
                runs.append((text, color))
        rows += [runs] * ART_PX_H
    return rows


# ── RENDER ──────────────────────────────────────────────────────────────
def render(posts, stats, now):
    out, t = [], 0.5

    def reveal(attr, at):
        return f'<set attributeName="{attr}" to="1" begin="{at:.2f}s" fill="freeze"/>'

    def text(x, y, inner, at, anchor="start", style=""):
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        s = f' style="{style}"' if style else ""
        out.append(f'<text x="{x:.1f}" y="{y}"{a}{s} opacity="0" xml:space="preserve">{reveal("opacity", at)}{inner}</text>')

    def typed(parts, start, per_char=0.07):
        """Reveal (text, color) parts one character at a time; returns (tspans, end time)."""
        chars = [(ch, color) for s, color in parts for ch in s]
        inner = "".join(f'<tspan fill="{color}" fill-opacity="0">{reveal("fill-opacity", start + i * per_char)}{escape(ch)}</tspan>'
                        for i, (ch, color) in enumerate(chars))
        return inner, start + len(chars) * per_char

    # Last login + typed command
    y = 64
    text(PAD_X, y, spans([(f"Last login: {now:%a %b %d %H:%M:%S} on ttys000", MUTED)]), 0.1)
    y += LINE_H  # every terminal line sits on the LINE_H grid
    cmd, t_end = typed([("fastfetch", FG)], t + 0.3)
    text(PAD_X, y, spans(PROMPT) + cmd, t)
    t = t_end + 0.35

    # fastfetch output: info column + avatar art, revealed top → bottom
    rows = info_rows(posts, stats)
    top = y + LINE_H  # output starts on the very next line
    block_h = (len(rows) - 1) * LINE_H
    art = art_rows()
    art_h = (len(art) - 1) * ART_LINE
    content_h = max(block_h, art_h)  # art may be taller than the info column (e.g. no stats)
    reveal_at = lambda yy: t + (yy - top) / max(content_h, 1) * 0.9  # noqa: E731

    for i, row in enumerate(rows):
        ry = top + i * LINE_H
        for x, anchor, parts in row or []:
            if anchor == "start" and x == INFO_X and width("".join(s for s, _ in parts)) > INFO_END - INFO_X:
                print(f"warning: row may overflow: {''.join(s for s, _ in parts)!r}", file=sys.stderr)
            text(x, ry, spans(parts), reveal_at(ry), anchor)

    art_top = top + max(block_h - art_h, 0) // 2  # never above the output block
    art_x = PAD_X + (INFO_X - PAD_X - 40 - len(ART.strip().splitlines()[0]) * ART_PX_W * ART_FONT * 0.6) / 2
    for i, runs in enumerate(art):
        ay = art_top + i * ART_LINE
        text(art_x, ay, spans(runs), reveal_at(ay), style=f"font-size:{ART_FONT}px")

    # final prompt: `open <blog>  # ← click` typed out, then a blinking cursor
    t += 1.1
    y = top + content_h + LINE_H * 2  # one blank line after the output
    cmd, t_end = typed([(f"open {BLOG}", FG), (f"  {CLICK_HINT} ", MUTED)], t + 0.4, per_char=0.05)
    cursor = (f'<tspan fill="{FG}" fill-opacity="0">█<animate attributeName="fill-opacity" values="1;1;0;0" '
              f'keyTimes="0;0.5;0.5;1" dur="1s" begin="{t_end:.2f}s" repeatCount="indefinite"/></tspan>')
    text(PAD_X, y, spans(PROMPT) + cmd + cursor, t)
    t = t_end

    h = y + 26
    desc = f"{USER} — " + ", ".join(v for _, v in SYSTEM + STACK) + ". " + " ".join(s.replace("**", "") for s in ABOUT)
    nl = "\n"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" role="img" aria-labelledby="t d">
<title id="t">{USER}@github — fastfetch</title>
<desc id="d">{escape(desc)}</desc>
<style>text {{ font-family: {FONT}; font-size: {FONT_SIZE}px; white-space: pre; }}</style>
<rect x="0.5" y="0.5" width="{W - 1}" height="{h - 1}" rx="10" fill="#0d1117" stroke="#30363d"/>
<path d="M0.5 36 V10.5 a10 10 0 0 1 10 -10 H{W - 10.5} a10 10 0 0 1 10 10 V36 Z" fill="#161b22"/>
<line x1="0.5" y1="36" x2="{W - 0.5}" y2="36" stroke="#30363d"/>
<circle cx="22" cy="18" r="6" fill="#ff5f56"/><circle cx="42" cy="18" r="6" fill="#ffbd2e"/><circle cx="62" cy="18" r="6" fill="#27c93f"/>
<text x="{W // 2}" y="23" text-anchor="middle" fill="{MUTED}" style="font-size:13px">{USER}@github: ~ — zsh</text>
{nl.join(out)}
</svg>
""", t


def local_gh_token():
    """Fallback for local runs: reuse the GitHub CLI login if available."""
    try:
        res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() or None if res.returncode == 0 else None


def main():
    token = os.environ.get("GITHUB_TOKEN") or local_gh_token()
    if not token:
        print("warning: no GITHUB_TOKEN and no `gh` login — skipping stats", file=sys.stderr)
    svg, end = render(blog_posts(), github_stats(token) if token else None, datetime.now(KST))
    out = Path(__file__).with_name("terminal.svg")
    out.write_text(svg, encoding="utf-8")
    print(f"wrote {out} (animation ends at {end:.1f}s)")


if __name__ == "__main__":
    main()
