#!/usr/bin/env python3
"""
Self-hosted GitHub profile asset generator.

Queries the GitHub GraphQL API (public data of the repository owner) and
renders every SVG used by the profile README:

  assets/header-banner-light.svg   animated wave banner (light text)
  assets/header-banner-dark.svg    animated wave banner (dark text)
  assets/typing-ticker-light.svg   rotating tagline (light text)
  assets/typing-ticker-dark.svg    rotating tagline (dark text)
  assets/profile-stats-light.svg   stats dashboard (light theme)
  assets/profile-stats-dark.svg    stats dashboard (dark theme)

Design goals:
  - ZERO runtime dependencies on third-party widget servers: every image is
    committed into this repository, so the README loads instantly and can
    never time out or rate-limit like the old vercel.app widgets did.
  - Uses only the Python standard library (runs on any GitHub Actions
    ubuntu runner without pip install).
  - Uses the automatic GITHUB_TOKEN: no personal access token required.

Usage:
  GITHUB_TOKEN=xxx python3 generate_assets.py --out assets
  python3 generate_assets.py --out preview --mock        # offline demo data
"""

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.request

API_URL = "https://api.github.com/graphql"

# --------------------------------------------------------------------------
# Palettes
# --------------------------------------------------------------------------

LIGHT = {
    "text": "#1f2328",        # GitHub light foreground
    "muted": "#59636e",
    "border": "#d1d9e0",
    "tile_fill": "#f6f8fa",
    "card_fill": "#ffffff",
    "accent_a": "#6366f1",    # indigo
    "accent_b": "#a855f7",    # violet
    "accent_c": "#ec4899",    # pink
}

DARK = {
    "text": "#e6edf3",        # GitHub dark foreground
    "muted": "#9198a1",
    "border": "#30363d",
    "tile_fill": "#151b23",
    "card_fill": "#0d1117",
    "accent_a": "#818cf8",
    "accent_b": "#c084fc",
    "accent_c": "#f472b6",
}

FONT = "ui-sans-serif, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

TICKER_PHRASES = [
    "Building open-source Android apps",
    "Kotlin Multiplatform enthusiast",
    "Author of RiMusic and RiPlay",
]


def fmt(n: int) -> str:
    """Format an integer the way GitHub does (comma thousands separator)."""
    return f"{n:,}"


def esc(s: str) -> str:
    """Escape text for XML/SVG."""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------------
# Tiny SVG icon set (simple line icons, stroke = accent color)
# --------------------------------------------------------------------------

def icon_commit(c: str, x: float, y: float, s: float = 20.0) -> str:
    """Commit icon: line - circle - line with a filled core."""
    cx, cy, r = x + s / 2, y + s / 2, s * 0.28
    return f'''<g stroke="{c}" stroke-width="1.8" fill="none" stroke-linecap="round">
  <line x1="{x:.1f}" y1="{cy:.1f}" x2="{cx - r:.1f}" y2="{cy:.1f}"/>
  <line x1="{cx + r:.1f}" y1="{cy:.1f}" x2="{x + s:.1f}" y2="{cy:.1f}"/>
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}"/>
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r * 0.38:.1f}" fill="{c}" stroke="none"/>
</g>'''


def icon_star(c: str, x: float, y: float, s: float = 20.0) -> str:
    cx, cy = x + s / 2, y + s / 2
    r1, r2 = s * 0.48, s * 0.20
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = r1 if i % 2 == 0 else r2
        pts.append(f"{cx + r * math.cos(ang):.2f},{cy + r * math.sin(ang):.2f}")
    return f'<polygon points="{" ".join(pts)}" fill="none" stroke="{c}" stroke-width="1.8" stroke-linejoin="round"/>'


def icon_pr(c: str, x: float, y: float, s: float = 20.0) -> str:
    """Pull-request icon: two branches with an arrow."""
    lx, rx = x + s * 0.28, x + s * 0.72
    return f'''<g stroke="{c}" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="{lx:.1f}" cy="{y + s * 0.18:.1f}" r="2.4"/>
  <circle cx="{lx:.1f}" cy="{y + s * 0.82:.1f}" r="2.4"/>
  <circle cx="{rx:.1f}" cy="{y + s * 0.82:.1f}" r="2.4"/>
  <line x1="{lx:.1f}" y1="{y + s * 0.18 + 2.4:.1f}" x2="{lx:.1f}" y2="{y + s * 0.82 - 2.4:.1f}"/>
  <line x1="{rx:.1f}" y1="{y + s * 0.82 - 2.4:.1f}" x2="{rx:.1f}" y2="{y + s * 0.30:.1f}"/>
  <polyline points="{rx - 3.4:.1f},{y + s * 0.30 + 3.4:.1f} {rx:.1f},{y + s * 0.30:.1f} {rx + 3.4:.1f},{y + s * 0.30 + 3.4:.1f}"/>
</g>'''


def icon_issue(c: str, x: float, y: float, s: float = 20.0) -> str:
    cx, cy = x + s / 2, y + s / 2
    return f'''<g stroke="{c}" stroke-width="1.8" fill="none">
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{s * 0.44:.1f}"/>
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{s * 0.12:.1f}" fill="{c}" stroke="none"/>
</g>'''


def icon_repo(c: str, x: float, y: float, s: float = 20.0) -> str:
    """Repository icon: a book."""
    return f'''<g stroke="{c}" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round">
  <rect x="{x + s * 0.15:.1f}" y="{y + s * 0.12:.1f}" width="{s * 0.7:.1f}" height="{s * 0.76:.1f}" rx="2.5"/>
  <line x1="{x + s * 0.15:.1f}" y1="{y + s * 0.32:.1f}" x2="{x + s * 0.85:.1f}" y2="{y + s * 0.32:.1f}"/>
  <line x1="{x + s * 0.42:.1f}" y1="{y + s * 0.22:.1f}" x2="{x + s * 0.70:.1f}" y2="{y + s * 0.22:.1f}"/>
</g>'''


def icon_people(c: str, x: float, y: float, s: float = 20.0) -> str:
    return f'''<g stroke="{c}" stroke-width="1.8" fill="none" stroke-linecap="round">
  <circle cx="{x + s * 0.40:.1f}" cy="{y + s * 0.32:.1f}" r="{s * 0.20:.1f}"/>
  <path d="M {x + s * 0.10:.1f},{y + s * 0.86:.1f} a {s * 0.30:.1f},{s * 0.30:.1f} 0 0 1 {s * 0.60:.1f},0"/>
  <circle cx="{x + s * 0.76:.1f}" cy="{y + s * 0.30:.1f}" r="{s * 0.15:.1f}"/>
  <path d="M {x + s * 0.72:.1f},{y + s * 0.84:.1f} a {s * 0.24:.1f},{s * 0.24:.1f} 0 0 1 {s * 0.22:.1f},-{s * 0.16:.1f}"/>
</g>'''


# --------------------------------------------------------------------------
# SVG builders
# --------------------------------------------------------------------------

def wave_path(width: float, base_y: float, amp: float, phase: float,
              overdraw: float = 80.0) -> str:
    """A smooth sine-like wave path string spanning [-overdraw, width+overdraw]."""
    x0 = -overdraw
    x1 = width + overdraw
    step = 40.0
    pts = []
    x = x0
    while x <= x1:
        y = base_y + amp * math.sin(x / 110.0 + phase)
        pts.append((x, y))
        x += step
    # Build a smooth cubic path through the points
    d = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(1, len(pts)):
        px, py = pts[i - 1]
        cx_, cy_ = pts[i]
        mx = (px + cx_) / 2
        d.append(f"C {mx:.1f} {py:.1f} {mx:.1f} {cy_:.1f} {cx_:.1f} {cy_:.1f}")
    return " ".join(d)


def build_banner(p: dict, uid: str) -> str:
    W, H = 820, 190
    grad = (f'<linearGradient id="g-{uid}" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{p["accent_a"]}"/>'
            f'<stop offset="0.55" stop-color="{p["accent_b"]}"/>'
            f'<stop offset="1" stop-color="{p["accent_c"]}"/>'
            f'</linearGradient>')
    waves = []
    layers = [(150, 16, 0.0, 0.30), (162, 12, 1.8, 0.55), (174, 9, 3.4, 1.0)]
    for base_y, amp, phase, op in layers:
        d = wave_path(W, base_y, amp, phase)
        # close the path down to the bottom of the canvas
        d += f" L {W + 80:.0f} {H} L -80 {H} Z"
        waves.append(f'<path d="{d}" fill="url(#g-{uid})" opacity="{op}"/>')
    waves_svg = (f'<g>'
                 f'<animateTransform attributeName="transform" type="translate" '
                 f'values="0 0; -34 0; 0 0" dur="14s" repeatCount="indefinite" '
                 f'calcMode="spline" keySplines="0.45 0 0.55 1; 0.45 0 0.55 1"/>'
                 + "".join(waves) + '</g>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="fast4x profile banner">
<defs>{grad}</defs>
<text x="{W / 2}" y="70" text-anchor="middle" font-family="{FONT}" font-size="40" font-weight="700" fill="{p["text"]}">Hi, I'm fast4x</text>
<text x="{W / 2}" y="100" text-anchor="middle" font-family="{FONT}" font-size="14" font-weight="500" letter-spacing="4" fill="{p["muted"]}">KOTLIN &#183; ANDROID &#183; MULTIPLATFORM</text>
{waves_svg}
</svg>'''


def build_ticker(p: dict, uid: str) -> str:
    W, H = 820, 44
    n = len(TICKER_PHRASES)
    cycle = 12.0
    seg = cycle / n
    texts = []
    for i, phrase in enumerate(TICKER_PHRASES):
        begin = i * seg
        # crossfade: fade in during first 18% of the slot, hold, fade out at the end
        op = "1" if i == 0 else "0"
        texts.append(
            f'<text x="{W / 2}" y="{H / 2 + 5}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="15" font-weight="500" '
            f'font-style="italic" fill="{p["muted"]}" opacity="{op}">'
            f'{esc(phrase)}'
            f'<animate attributeName="opacity" dur="{cycle}s" begin="{begin}s" '
            f'values="0;1;1;0" keyTimes="0;0.18;0.82;1" repeatCount="indefinite"/>'
            f'</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="tagline">
{''.join(texts)}
</svg>'''


def build_stats(p: dict, uid: str, stats: dict, generated_on: str) -> str:
    W, H = 820, 334
    M, GAP = 24, 17
    tile_w = (W - 2 * M - 2 * GAP) / 3          # 246
    tile_h = 110
    head_y = 40                                  # baseline of the header row
    rows_y = [58, 58 + tile_h + 14]              # 58, 182

    grad = (f'<linearGradient id="n-{uid}" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{p["accent_a"]}"/>'
            f'<stop offset="0.5" stop-color="{p["accent_b"]}"/>'
            f'<stop offset="1" stop-color="{p["accent_c"]}"/>'
            f'</linearGradient>')

    tiles = [
        (icon_commit, stats["commits"], "Commits &#183; past year"),
        (icon_star,   stats["stars"],   "Stars earned"),
        (icon_pr,     stats["prs"],     "Pull requests"),
        (icon_issue,  stats["issues"],  "Issues opened"),
        (icon_repo,   stats["repos"],   "Public repositories"),
        (icon_people, stats["followers"], "Followers"),
    ]

    tiles_svg = []
    for idx, (icon_fn, value, label) in enumerate(tiles):
        row, col = divmod(idx, 3)
        x = M + col * (tile_w + GAP)
        y = rows_y[row]
        accent = p["accent_b"] if idx % 2 == 0 else p["accent_a"]
        tiles_svg.append(f'''<g>
  <rect x="{x:.1f}" y="{y}" width="{tile_w:.1f}" height="{tile_h}" rx="12"
        fill="{p["tile_fill"]}" stroke="{p["border"]}" stroke-width="1"/>
  {icon_fn(accent, x + 18, y + 16)}
  <text x="{x + 18:.1f}" y="{y + 74}" font-family="{FONT}" font-size="30"
        font-weight="700" fill="url(#n-{uid})">{fmt(value)}</text>
  <text x="{x + 18:.1f}" y="{y + 96}" font-family="{FONT}" font-size="12"
        font-weight="500" letter-spacing="0.6" fill="{p["muted"]}">{label}</text>
</g>''')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub profile statistics for fast4x">
<defs>{grad}</defs>
<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="16"
      fill="{p["card_fill"]}" fill-opacity="0.35" stroke="{p["border"]}" stroke-width="1"/>
<text x="{M}" y="{head_y}" font-family="{FONT}" font-size="13" font-weight="700"
      letter-spacing="2.5" fill="{p["text"]}">GITHUB PROFILE STATS</text>
<circle cx="{W - 182:.1f}" cy="{head_y - 4}" r="3.5" fill="#3fb950">
  <animate attributeName="opacity" values="1;0.35;1" dur="2.4s" repeatCount="indefinite"/>
</circle>
<text x="{W - M}" y="{head_y}" text-anchor="end" font-family="{FONT}" font-size="11"
      font-weight="500" fill="{p["muted"]}">auto-updated {generated_on}</text>
{''.join(tiles_svg)}
</svg>'''


# --------------------------------------------------------------------------
# GitHub GraphQL
# --------------------------------------------------------------------------

QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    login
    name
    createdAt
    followers { totalCount }
    repositories(first: 100, after: $after, ownerAffiliations: OWNER,
                 isFork: false, orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { stargazerCount }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalRepositoryContributions
    }
  }
}
"""


def graphql(login: str, token: str, retries: int = 3) -> dict:
    """Run the paginated GraphQL query and return a flat stats dict."""
    stars = 0
    repos_total = 0
    after = None
    contrib = None
    followers = 0

    while True:
        variables = {"login": login, "after": after}
        payload = json.dumps({"query": QUERY, "variables": variables}).encode()
        req = urllib.request.Request(
            API_URL, data=payload, method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "profile-stats-generator",
            })
        body = None
        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read().decode())
                break
            except Exception as exc:  # network hiccup / rate limit -> retry
                if attempt == retries:
                    sys.exit(f"ERROR: GitHub API request failed after {retries} tries: {exc}")
                time.sleep(5 * attempt)
        if body is None:
            continue

        if "errors" in body and not body.get("data"):
            sys.exit(f"ERROR: GraphQL query failed: {body['errors']}")
        user = body["data"]["user"]
        if user is None:
            sys.exit(f"ERROR: user '{login}' not found")

        page = user["repositories"]
        repos_total = page["totalCount"]
        stars += sum(node["stargazerCount"] for node in page["nodes"])
        contrib = user["contributionsCollection"]
        followers = user["followers"]["totalCount"]
        if page["pageInfo"]["hasNextPage"]:
            after = page["pageInfo"]["endCursor"]
        else:
            return {
                "commits": contrib["totalCommitContributions"],
                "prs": contrib["totalPullRequestContributions"],
                "issues": contrib["totalIssueContributions"],
                "repos": repos_total,
                "stars": stars,
                "followers": followers,
            }


def mock_stats() -> dict:
    """Offline demo data (only used with --mock, never on the real profile)."""
    return {"commits": 917, "prs": 142, "issues": 57,
            "repos": 38, "stars": 4680, "followers": 707}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate profile SVG assets")
    ap.add_argument("--out", default="assets", help="output directory")
    ap.add_argument("--user", default=os.environ.get("GITHUB_REPOSITORY_OWNER"),
                    help="GitHub login (defaults to the repository owner)")
    ap.add_argument("--mock", action="store_true",
                    help="generate with demo data, without calling the API")
    args = ap.parse_args()

    if not args.mock:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("ERROR: set the GITHUB_TOKEN environment variable")
        if not args.user:
            sys.exit("ERROR: no user given (pass --user or run inside GitHub Actions)")
        stats = graphql(args.user, token)
    else:
        stats = mock_stats()

    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(args.out, exist_ok=True)

    files = {
        "header-banner-light.svg": build_banner(LIGHT, "bl"),
        "header-banner-dark.svg": build_banner(DARK, "bd"),
        "typing-ticker-light.svg": build_ticker(LIGHT, "tl"),
        "typing-ticker-dark.svg": build_ticker(DARK, "td"),
        "profile-stats-light.svg": build_stats(LIGHT, "sl", stats, today),
        "profile-stats-dark.svg": build_stats(DARK, "sd", stats, today),
    }
    for name, svg in files.items():
        path = os.path.join(args.out, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"wrote {path} ({os.path.getsize(path)} bytes)")
    print(f"stats: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
