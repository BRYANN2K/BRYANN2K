#!/usr/bin/env python3
"""Build the profile's SVG cards in light and dark, with live data.

Cards: Cartouche hero (latest release), selected work, upstream
contributions (merged PRs on public repos the owner doesn't own) and the
career path. Text is set in the display font of the name banner, subset
per card and measured so wrapping and truncation stay exact.
Also rewrites the alt text between the CONTRIBUTIONS markers in README.md.
"""

import base64
import io
import json
import os
import re
import urllib.parse
import urllib.request
from functools import lru_cache
from html import escape
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "scripts/fonts/display.woff2"
OUT = ROOT / "assets/cards"
README = ROOT / "README.md"
USER = os.environ.get("GITHUB_USER", "BRYANN2K")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
MAX_CONTRIBUTIONS = 5

THEMES = {
    "dark": dict(
        fg="#f0f6fc", muted="#9198a1", faint="#656c76", line="#30363d",
        card_a="#161b22", card_b="#0d1117", accent="#a58bff", pill="#21262d",
        hero_a="#1a1c24", hero_b="#0d1117", hero_glow="#ff4d5e", hero_glow_op=".22",
        hero_dot="#ffffff", hero_dot_op=".06", hero_line="#2b2e38",
    ),
    "light": dict(
        fg="#1f2328", muted="#59636e", faint="#818b98", line="#d1d9e0",
        card_a="#ffffff", card_b="#f6f8fa", accent="#5726ca", pill="#eff2f5",
        hero_a="#fbf6ef", hero_b="#efe5d8", hero_glow="#ff4d5e", hero_glow_op=".16",
        hero_dot="#4b4f5e", hero_dot_op=".09", hero_line="#e2d6c6",
    ),
}
RED = "#ff4d5e"


# ---------------------------------------------------------------- data

def api(path):
    req = urllib.request.Request(f"https://api.github.com/{path}")
    req.add_header("Accept", "application/vnd.github+json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def latest_release(repo):
    try:
        return api(f"repos/{repo}/releases/latest")["tag_name"]
    except Exception:
        return None


def contributions():
    q = f"is:pr is:merged is:public author:{USER} -user:{USER}"
    items, page = [], 1
    while True:
        data = api(f"search/issues?q={urllib.parse.quote(q)}&per_page=100&page={page}")
        items += data["items"]
        if len(items) >= data["total_count"] or not data["items"]:
            break
        page += 1
    repos = {}
    for pr in items:
        name = pr["repository_url"].removeprefix("https://api.github.com/repos/")
        merged = pr["pull_request"]["merged_at"]
        entry = repos.setdefault(name, {"name": name, "count": 0, "last": merged})
        entry["count"] += 1
        entry["last"] = max(entry["last"], merged)
    ranked = sorted(repos.values(), key=lambda r: r["last"], reverse=True)[:MAX_CONTRIBUTIONS]
    for entry in ranked:
        info = api(f"repos/{entry['name']}")
        entry["description"] = (info.get("description") or "").strip()
    return ranked


# ---------------------------------------------------------------- type

@lru_cache(maxsize=None)
def _instance(wght, wdth, opsz):
    font = TTFont(FONT)
    return instancer.instantiateVariableFont(font, {"wght": wght, "wdth": wdth, "opsz": opsz})


def measure(text, size, wght=400, wdth=100, tracking=0.0):
    font = _instance(wght, wdth, max(12, min(36, round(size))))
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    upm = font["head"].unitsPerEm
    width = sum(hmtx[cmap.get(ord(c), cmap[ord("?")])][0] for c in text) * size / upm
    return width + tracking * max(len(text) - 1, 0)


def wrap(text, width, size, wght=400, wdth=100):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and measure(trial, size, wght, wdth) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def clip(text, width, size, wght=400, wdth=100):
    if measure(text, size, wght, wdth) <= width:
        return text
    while text and measure(text + "…", size, wght, wdth) > width:
        text = text[:-1].rstrip()
    return text + "…"


def font_face(texts):
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    font = TTFont(FONT)
    sub = subset.Subsetter(options)
    sub.populate(text="".join(texts) + " …")
    sub.subset(font)
    buf = io.BytesIO()
    font.flavor = "woff2"
    font.save(buf)
    data = base64.b64encode(buf.getvalue()).decode()
    return (f"@font-face{{font-family:D;src:url(data:font/woff2;base64,{data}) format('woff2');"
            "font-weight:300 900;font-stretch:100% 125%}")


class Svg:
    """Collects text so each card embeds only the glyphs it uses."""

    def __init__(self, w, h, label):
        self.w, self.h, self.label = w, h, label
        self.parts, self.texts, self.css = [], [], []

    def add(self, markup):
        self.parts.append(markup)

    def text(self, x, y, s, size, fill, wght=400, wdth=100, tracking=0, anchor="start", extra=""):
        self.texts.append(s)
        ls = f' letter-spacing="{tracking}"' if tracking else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
                 f'style="font-weight:{wght};font-stretch:{wdth}%"{ls} text-anchor="{anchor}"{extra}>'
                 f'{escape(s)}</text>')

    def render(self):
        style = font_face(self.texts) + "text{font-family:D,-apple-system,'Segoe UI',sans-serif}" + "".join(self.css)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{escape(self.label)}">\n'
                f'<style>{style}</style>\n' + "\n".join(self.parts) + "\n</svg>\n")


def frame(svg, t, w, h, r=18, uid="c"):
    svg.add(f'<defs><linearGradient id="{uid}bg" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{t["card_a"]}"/><stop offset="1" stop-color="{t["card_b"]}"/>'
            f'</linearGradient></defs>')
    svg.add(f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="{r}" fill="url(#{uid}bg)" stroke="{t["line"]}"/>')


def pill(svg, x, y, label, t, fill=None, color=None, size=12.5, h=26, wght=560):
    w = measure(label, size, wght) + 24
    svg.add(f'<rect x="{x}" y="{y}" width="{w:.1f}" height="{h}" rx="{h / 2}" '
            f'fill="{fill or t["pill"]}" stroke="{t["line"]}"/>')
    svg.text(x + w / 2, y + h / 2 + size * .36, label, size, color or t["muted"], wght, anchor="middle")
    return w


REDUCED = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"


# ---------------------------------------------------------------- cards

CARTRIDGE = """
<path d="M270 170 H682 L754 242 V800 Q754 830 724 830 H300 Q270 830 270 800 Z" fill="#4B4F5E"/>
<path d="M270 170 H682 L754 242 V262 H270 Z" fill="#fff" opacity=".08"/>
<g fill="#2E3039"><rect x="306" y="196" width="16" height="44" rx="8"/><rect x="342" y="196" width="16" height="44" rx="8"/><rect x="378" y="196" width="16" height="44" rx="8"/></g>
<rect x="318" y="290" width="388" height="376" rx="30" fill="#FF4D5E"/>
<g clip-path="url(#label)"><rect class="shine" x="140" y="250" width="120" height="460" fill="url(#shine)" transform="skewX(-18)"/></g>
<rect x="318" y="612" width="388" height="54" fill="#000" opacity=".14"/>
<g fill="#FFFFFF"><rect x="452" y="398" width="40" height="160"/><rect x="492" y="418" width="40" height="120"/><rect x="532" y="438" width="40" height="80"/><rect x="572" y="458" width="40" height="40"/></g>
<rect x="352" y="830" width="320" height="86" fill="#3A3D49"/>
<g fill="#E8B44A"><rect x="372" y="846" width="22" height="70"/><rect x="408" y="846" width="22" height="70"/><rect x="444" y="846" width="22" height="70"/><rect x="480" y="846" width="22" height="70"/><rect x="516" y="846" width="22" height="70"/><rect x="552" y="846" width="22" height="70"/><rect x="588" y="846" width="22" height="70"/><rect x="624" y="846" width="22" height="70"/></g>
"""


def hero(theme, version):
    t = THEMES[theme]
    W, H = 880, 340
    svg = Svg(W, H, "Cartouche: a free game launcher for Apple silicon Macs")
    svg.css.append(
        ".float{animation:float 6s ease-in-out infinite}"
        "@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}"
        ".shadow{transform-origin:715px 312px;animation:shadow 6s ease-in-out infinite}"
        "@keyframes shadow{0%,100%{transform:scaleX(1);opacity:.55}50%{transform:scaleX(.82);opacity:.3}}"
        ".shine{animation:shine 5.5s cubic-bezier(.6,0,.3,1) infinite}"
        "@keyframes shine{0%,55%{transform:skewX(-18deg) translateX(0)}100%{transform:skewX(-18deg) translateX(820px)}}"
        + REDUCED
    )
    svg.add(f"""<defs>
<linearGradient id="hbg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{t['hero_a']}"/><stop offset="1" stop-color="{t['hero_b']}"/></linearGradient>
<radialGradient id="glow" cx="715" cy="165" r="250" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{t['hero_glow']}" stop-opacity="{t['hero_glow_op']}"/><stop offset="1" stop-color="{t['hero_glow']}" stop-opacity="0"/></radialGradient>
<pattern id="dots" width="16" height="16" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="{t['hero_dot']}" fill-opacity="{t['hero_dot_op']}"/></pattern>
<radialGradient id="fade" cx="715" cy="170" r="330" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
<mask id="dotmask"><rect width="{W}" height="{H}" fill="url(#fade)"/></mask>
<linearGradient id="shine" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<clipPath id="label"><rect x="318" y="290" width="388" height="376" rx="30"/></clipPath>
<clipPath id="card"><rect width="{W}" height="{H}" rx="22"/></clipPath>
<filter id="soft" x="-50%" y="-200%" width="200%" height="500%"><feGaussianBlur stdDeviation="6"/></filter>
</defs>""")
    svg.add(f'<g clip-path="url(#card)"><rect width="{W}" height="{H}" fill="url(#hbg)"/>'
            f'<rect width="{W}" height="{H}" fill="url(#dots)" mask="url(#dotmask)"/>'
            f'<rect width="{W}" height="{H}" fill="url(#glow)"/></g>')
    svg.add(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="22" fill="none" stroke="{t["hero_line"]}"/>')

    # cartridge
    s = .3
    tx, ty = 715 - 512 * s, 48 - 170 * s
    svg.add('<g class="shadow"><ellipse cx="715" cy="312" rx="74" ry="8" fill="#000" opacity=".35" filter="url(#soft)"/></g>')
    svg.add(f'<g class="float"><g transform="translate({tx:.1f} {ty:.1f}) scale({s})">{CARTRIDGE}</g></g>')

    # copy
    x = 52
    eyebrow = "NOW BUILDING" + (f"  ·  {version}" if version else "")
    svg.add(f'<circle cx="{x + 4}" cy="66" r="4" fill="{RED}"/>')
    svg.text(x + 18, 70.5, eyebrow, 12, RED, 650, 100, 1.6)
    svg.text(x - 3, 134, "Cartouche", 64, t["fg"], 820, 112, -1.5)
    svg.text(x, 172, "A free game launcher for Apple silicon Macs.", 19, t["fg"], 520)
    for i, line in enumerate(wrap("Steam, Epic, GOG and Amazon games, Windows installers and console "
                                  "games, side by side in one native library.", 440, 15, 400)):
        svg.text(x, 200 + i * 22, line, 15, t["muted"], 400)
    cx = x
    for store in ("Steam", "Epic", "GOG", "Amazon", "Windows", "Console"):
        cx += pill(svg, cx, 254, store, t, size=12, h=26) + 8
    cmd = "brew install --cask bryann2k/cartouche/cartouche"
    svg.text(x, 308, "$", 12.5, RED, 700)
    svg.text(x + 14, 308, cmd, 12.5, t["faint"], 450, 100, .2)
    return svg.render()


def glyph(kind, x, y, t):
    """Small mark per project, top-right of its card."""
    a = t["accent"]
    if kind == "spec":
        return (f'<g transform="translate({x} {y})" fill="{a}"><rect width="34" height="4" rx="2"/>'
                f'<rect y="10" width="24" height="4" rx="2" opacity=".6"/><rect y="20" width="30" height="4" rx="2" opacity=".35"/>'
                f'<rect class="tick" y="30" width="14" height="4" rx="2"/></g>')
    if kind == "term":
        return (f'<g transform="translate({x} {y})"><path d="M2 4 L12 13 L2 22" fill="none" stroke="{a}" '
                f'stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>'
                f'<rect class="blink" x="18" y="19" width="16" height="4" rx="2" fill="{a}"/></g>')
    return (f'<g transform="translate({x} {y})" fill="none" stroke="{a}" stroke-width="2.6" stroke-linejoin="round">'
            f'<path d="M17 2 L32 10 L17 18 L2 10 Z"/><path d="M2 17 L17 25 L32 17" opacity=".6"/>'
            f'<path d="M2 24 L17 32 L32 24" opacity=".3"/></g>')


def project(theme, index, name, desc, tags, kind, status=None):
    t = THEMES[theme]
    W, H = 400, 300
    svg = Svg(W, H, f"{name}: {desc}")
    svg.css.append(".blink{animation:blink 1.1s steps(1) infinite}@keyframes blink{50%{opacity:0}}"
                   ".tick{animation:tick 3s ease-in-out infinite;transform-origin:0 32px}"
                   "@keyframes tick{0%,100%{transform:scaleX(.4)}50%{transform:scaleX(1.6)}}" + REDUCED)
    frame(svg, t, W, H)
    svg.text(32, 54, f"{index:02d}", 17, t["faint"], 650, 100, 1.4)
    svg.add(glyph(kind, W - 68, 30, t))
    svg.text(30, 118, name, 42, t["fg"], 820, 110, -1)
    if status:
        pill(svg, 66, 36, status, t, size=12, h=24, wght=650)
    for i, line in enumerate(wrap(desc, W - 64, 20, 430)[:3]):
        svg.text(32, 160 + i * 28, line, 20, t["muted"], 430)
    svg.add(f'<line x1="32" y1="{H - 60}" x2="{W - 32}" y2="{H - 60}" stroke="{t["line"]}"/>')
    svg.text(32, H - 26, "  ·  ".join(tags), 15, t["faint"], 580, 100, .5)
    return svg.render()


def contribution_card(theme, repos):
    t = THEMES[theme]
    W, row, top = 880, 76, 24
    H = top * 2 + max(len(repos), 1) * row + 44
    svg = Svg(W, H, "Upstream contributions: " + (", ".join(r["name"] for r in repos) or "none yet"))
    frame(svg, t, W, H, r=20)
    if not repos:
        svg.text(36, top + 46, "Nothing merged upstream yet.", 16, t["muted"], 450)
    for i, r in enumerate(repos):
        y = top + i * row
        if i:
            svg.add(f'<line x1="36" y1="{y}" x2="{W - 36}" y2="{y}" stroke="{t["line"]}"/>')
        owner, repo = r["name"].split("/", 1)
        svg.add(f'<rect x="36" y="{y + 18}" width="40" height="40" rx="11" fill="{t["pill"]}" stroke="{t["line"]}"/>')
        svg.text(56, y + 45, repo[:1].upper(), 18, t["accent"], 800, 110, anchor="middle")
        svg.text(96, y + 35, f"{owner} /", 14, t["faint"], 450)
        svg.text(100 + measure(f"{owner} /", 14, 450), y + 35, repo, 16, t["fg"], 700, 104)
        label = f"{r['count']} merged PR{'s' if r['count'] > 1 else ''}"
        pw = measure(label, 12, 600) + 24
        right = W - 36 - pw
        svg.text(96, y + 57, clip(r["description"], right - 120, 14, 420), 14, t["muted"], 420)
        svg.add(f'<rect x="{right:.1f}" y="{y + 25}" width="{pw:.1f}" height="26" rx="13" fill="none" stroke="{t["accent"]}" stroke-opacity=".55"/>')
        svg.text(right + pw / 2, y + 42.3, label, 12, t["accent"], 600, anchor="middle")
    svg.add(f'<line x1="0" y1="{H - 44}" x2="{W}" y2="{H - 44}" stroke="{t["line"]}"/>')
    svg.text(36, H - 17, "Merged pull requests on public repositories  ·  refreshed daily",
             11.5, t["faint"], 500, 100, .4)
    return svg.render()


def path_card(theme):
    t = THEMES[theme]
    W, H = 880, 200
    svg = Svg(W, H, "Career path: five years in systems and networks, a year as a freelance cloud "
                    "engineer with 11 clients, now product engineer")
    svg.css.append(".pulse{transform-origin:center;transform-box:fill-box;animation:pulse 2.4s ease-out infinite}"
                   "@keyframes pulse{0%{transform:scale(1);opacity:.5}70%,100%{transform:scale(2.8);opacity:0}}"
                   ".draw{stroke-dasharray:700;stroke-dashoffset:700;animation:draw 1.6s cubic-bezier(.4,0,.2,1) .2s forwards}"
                   "@keyframes draw{to{stroke-dashoffset:0}}" + REDUCED.replace("}}", "}.draw{stroke-dashoffset:0}}"))
    frame(svg, t, W, H, r=20)
    stops = [
        (150, "5 years", "Systems & networks", "Running production"),
        (440, "1 year", "Freelance cloud", "11 clients · 3 builds"),
        (730, "Now", "Product engineer", "Idea to production"),
    ]
    svg.add(f'<line x1="150" y1="70" x2="730" y2="70" stroke="{t["line"]}" stroke-width="2"/>')
    svg.add(f'<line class="draw" x1="150" y1="70" x2="730" y2="70" stroke="{t["accent"]}" stroke-width="2" stroke-opacity=".7"/>')
    for i, (x, when, what, note) in enumerate(stops):
        last = i == len(stops) - 1
        if last:
            svg.add(f'<circle class="pulse" cx="{x}" cy="70" r="7" fill="{t["accent"]}"/>')
        svg.add(f'<circle cx="{x}" cy="70" r="7" fill="{t["accent"] if last else t["card_a"]}" '
                f'stroke="{t["accent"]}" stroke-width="2.5"/>')
        svg.text(x, 44, when.upper(), 11.5, t["accent"] if last else t["faint"], 650, 100, 1.4, "middle")
        svg.text(x, 112, what, 19, t["fg"], 720, 106, anchor="middle")
        svg.text(x, 138, note, 14, t["muted"], 420, anchor="middle")
    return svg.render()


# ---------------------------------------------------------------- main

PROJECTS = [
    ("stipulate", 1, "Stipulate", "Spec-driven development for AI coding agents, from exploration to verified delivery.",
     ["Agent skills", "Specs", "Open source"], "spec", None),
    ("maestro", 2, "Maestro", "An AI spec-driven harness for terminal development, from idea to reviewed merge.",
     ["Terminal", "AI agents", "Open source"], "term", None),
    ("stackdeploy", 3, "StackDeploy", "Your own Kubernetes cluster, with the ease of a PaaS.",
     ["Kubernetes", "Platform", "Go"], "stack", "PAUSED"),
]


def update_readme(repos):
    alt = "Upstream contributions: " + ("; ".join(
        f"{r['name']}, {r['count']} merged" for r in repos) or "none yet")
    search = "https://github.com/search?type=pullrequests&q=" + urllib.parse.quote(
        f"is:pr is:merged author:{USER} -user:{USER}")
    block = (f'<a href="{search}">\n<picture>\n'
             f'  <source media="(prefers-color-scheme: dark)" srcset="assets/cards/contributions-dark.svg">\n'
             f'  <img src="assets/cards/contributions-light.svg" width="100%" alt="{escape(alt)}">\n'
             f'</picture>\n</a>')
    text = README.read_text(encoding="utf-8")
    new = re.sub(r"<!-- CONTRIBUTIONS:START -->.*?<!-- CONTRIBUTIONS:END -->",
                 lambda m: f"<!-- CONTRIBUTIONS:START -->\n{block}\n<!-- CONTRIBUTIONS:END -->", text, flags=re.S)
    if new != text:
        README.write_text(new, encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    version = latest_release(f"{USER}/cartouche")
    repos = contributions()
    for theme in THEMES:
        (OUT / f"cartouche-{theme}.svg").write_text(hero(theme, version))
        (OUT / f"contributions-{theme}.svg").write_text(contribution_card(theme, repos))
        (OUT / f"path-{theme}.svg").write_text(path_card(theme))
        for slug, *args in PROJECTS:
            (OUT / f"{slug}-{theme}.svg").write_text(project(theme, *args))
    update_readme(repos)
    print(f"cards built · cartouche {version} · {len(repos)} upstream repos")


if __name__ == "__main__":
    main()
