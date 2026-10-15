#!/usr/bin/env python3
"""
Auto-update a GitHub profile README with live data:
  * latest projects (repos)        * open pull requests
  * recently merged pull requests  * open contributions to other people's repos
  * PRs with merge conflicts       * open issues
  * profile stats + "last updated" timestamp
  * (optional) the GitHub profile bio

Usage:
  python update_readme.py --init       # one time: insert the marker sections into README.md
  python update_readme.py              # refresh the sections
  python update_readme.py --dry-run    # print what would change, write nothing

Env vars:
  GH_USERNAME   GitHub username              (default: samiulaudi1712)
  GH_TOKEN      token (falls back to GITHUB_TOKEN)
  README_PATH   path to README               (default: README.md)
  UPDATE_BIO    "1" to also update profile bio (needs a classic PAT with `user` scope)
  BIO_BASE      static part of the bio
"""
import argparse
import datetime as dt
import os
import re
import sys
import time

import requests

USERNAME = os.getenv("GH_USERNAME", "samiulaudi1712")
TOKEN = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
README_PATH = os.getenv("README_PATH", "README.md")
BIO_BASE = os.getenv("BIO_BASE", "CSE @ BRACU | Frontend & Robotics | Mongol Tori")

API = "https://api.github.com"
MAX_ITEMS = 6          # items shown per section
MAX_PR_DETAILS = 20    # PR detail lookups (for conflict detection)
EXCLUDE_REPOS = {USERNAME.lower(), f"{USERNAME}.github.io".lower()}  # profile repos

session = requests.Session()
session.headers.update({
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": f"{USERNAME}-readme-updater",
})
if TOKEN:
    session.headers["Authorization"] = f"Bearer {TOKEN}"


# ----------------------------------------------------------------- helpers
def gh(path, **params):
    r = session.get(path if path.startswith("http") else API + path, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def ago(iso):
    if not iso:
        return "n/a"
    then = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    s = int((dt.datetime.now(dt.timezone.utc) - then).total_seconds())
    for unit, n in (("y", 31536000), ("mo", 2592000), ("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


def esc(text):
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def repo_of(item):
    return item["repository_url"].replace(f"{API}/repos/", "")


def is_external(full_name):
    return full_name.split("/")[0].lower() != USERNAME.lower()


_pr_cache = {}


def pr_detail(full_name, number):
    key = (full_name, number)
    if key not in _pr_cache:
        data = gh(f"/repos/{full_name}/pulls/{number}")
        if data.get("mergeable") is None:       # GitHub is still computing it
            time.sleep(2)
            data = gh(f"/repos/{full_name}/pulls/{number}")
        _pr_cache[key] = data
    return _pr_cache[key]


def pr_status(detail):
    if detail is None:
        return "🟡 Open"
    if detail.get("draft"):
        return "📝 Draft"
    return {
        "dirty": "⚠️ Conflict",
        "behind": "🔄 Behind base",
        "blocked": "⏳ Awaiting review",
        "unstable": "🔴 Checks failing",
        "clean": "✅ Ready to merge",
        "has_hooks": "✅ Ready to merge",
    }.get(detail.get("mergeable_state"), "🟡 Open")


def search(query, n=30):
    return gh("/search/issues", q=query, sort="updated", order="desc", per_page=n)["items"]


def open_prs():
    """All open PRs authored by the user, with merge state attached."""
    items = search(f"is:pr is:open author:{USERNAME}")
    for i, it in enumerate(items):
        it["_detail"] = None
        if i < MAX_PR_DETAILS:
            try:
                it["_detail"] = pr_detail(repo_of(it), it["number"])
            except requests.RequestException:
                pass
    return items


def pr_line(it, with_status=True):
    status = f"{pr_status(it.get('_detail'))} · " if with_status else ""
    return (f"- {status}[**{esc(it['title'])}**]({it['html_url']}) — "
            f"`{repo_of(it)}` #{it['number']} · updated {ago(it['updated_at'])}")


def empty(msg):
    return f"_{msg}_"


# ----------------------------------------------------------------- sections
def sec_stats(ctx):
    u = ctx["user"]
    stars = sum(r["stargazers_count"] for r in ctx["repos"])
    prs = ctx["open_prs"]
    conflicts = sum(1 for p in prs if (p.get("_detail") or {}).get("mergeable_state") == "dirty")
    merged = gh("/search/issues", q=f"is:pr is:merged author:{USERNAME}", per_page=1)["total_count"]
    return (
        f"| 📦 Public Repos | ⭐ Stars Earned | 👥 Followers | 🔀 PRs Merged | 📬 Open PRs | ⚠️ Conflicts |\n"
        f"|:-:|:-:|:-:|:-:|:-:|:-:|\n"
        f"| **{u['public_repos']}** | **{stars}** | **{u['followers']}** | **{merged}** "
        f"| **{len(prs)}** | **{conflicts}** |"
    )


def sec_projects(ctx):
    repos = ctx["repos"][:MAX_ITEMS]
    if not repos:
        return empty("No repositories found.")
    rows = ["| Repo | Description | Lang | ⭐ | Updated |", "|:--|:--|:-:|:-:|:-:|"]
    for r in repos:
        rows.append(
            f"| [**{r['name']}**]({r['html_url']}) | {esc(r['description']) or '—'} "
            f"| {r['language'] or '—'} | {r['stargazers_count']} | {ago(r['pushed_at'])} |"
        )
    return "\n".join(rows)


def sec_open_prs(ctx):
    prs = ctx["open_prs"][:MAX_ITEMS]
    return "\n".join(pr_line(p) for p in prs) if prs else empty("No open pull requests 🎉")


def sec_merged(ctx):
    items = search(f"is:pr is:merged author:{USERNAME}", MAX_ITEMS)
    if not items:
        return empty("No merged pull requests yet.")
    out = []
    for it in items:
        merged_at = (it.get("pull_request") or {}).get("merged_at") or it["closed_at"]
        out.append(f"- 🟣 [**{esc(it['title'])}**]({it['html_url']}) — `{repo_of(it)}` "
                   f"#{it['number']} · merged {ago(merged_at)}")
    return "\n".join(out)


def sec_contributions(ctx):
    ext = [p for p in ctx["open_prs"] if is_external(repo_of(p))][:MAX_ITEMS]
    return ("\n".join(pr_line(p) for p in ext) if ext
            else empty("No open contributions to other projects right now."))


def sec_conflicts(ctx):
    """Conflicts in my own PRs AND in PRs other people opened against my repos."""
    found = {}
    for p in ctx["open_prs"]:
        if (p.get("_detail") or {}).get("mergeable_state") == "dirty":
            found[p["html_url"]] = (p, "my PR")
    try:
        for it in search(f"is:pr is:open user:{USERNAME} -author:{USERNAME}", 20)[:MAX_PR_DETAILS]:
            d = pr_detail(repo_of(it), it["number"])
            if d.get("mergeable_state") == "dirty":
                it["_detail"] = d
                found[it["html_url"]] = (it, "incoming PR")
    except requests.RequestException:
        pass
    if not found:
        return empty("No merge conflicts 🎯")
    return "\n".join(
        f"- ⚠️ [**{esc(p['title'])}**]({p['html_url']}) — `{repo_of(p)}` #{p['number']} "
        f"· {kind} · updated {ago(p['updated_at'])}"
        for p, kind in list(found.values())[:MAX_ITEMS]
    )


def sec_issues(ctx):
    seen = {}
    for q in (f"is:issue is:open author:{USERNAME}", f"is:issue is:open assignee:{USERNAME}"):
        for it in search(q, 15):
            seen[it["html_url"]] = it
    items = sorted(seen.values(), key=lambda i: i["updated_at"], reverse=True)[:MAX_ITEMS]
    if not items:
        return empty("No open issues 🙌")
    return "\n".join(
        f"- 🐞 [**{esc(i['title'])}**]({i['html_url']}) — `{repo_of(i)}` #{i['number']} "
        f"· updated {ago(i['updated_at'])}" for i in items)


SECTIONS = [  # (marker name, heading, builder)
    ("stats", "📈 Live Stats", sec_stats),
    ("projects", "🆕 Latest Projects", sec_projects),
    ("open_prs", "📬 Open Pull Requests", sec_open_prs),
    ("merged_prs", "🟣 Recently Merged", sec_merged),
    ("contributions", "🤝 Open Contributions (other repos)", sec_contributions),
    ("conflicts", "⚠️ Merge Conflicts To Resolve", sec_conflicts),
    ("issues", "🐞 Open Issues", sec_issues),
]


# ----------------------------------------------------------------- README editing
def marker(name):
    return f"<!--START_SECTION:{name}-->", f"<!--END_SECTION:{name}-->"


def replace_section(text, name, body):
    start, end = marker(name)
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        print(f"  ! marker '{name}' not found (run with --init)", file=sys.stderr)
        return text
    return pattern.sub(lambda _m: f"{start}\n{body}\n{end}", text, count=1)


def build_dashboard_block():
    parts = ["## ⚡ Live Activity\n", "<sub>🤖 Auto-updated by "
             "[`update_readme.py`](./update_readme.py) via GitHub Actions</sub>\n"]
    for name, heading, _ in SECTIONS:
        s, e = marker(name)
        parts.append(f"### {heading}\n\n{s}\n{e}\n")
    s, e = marker("timestamp")
    parts.append(f"<div align=\"center\"><sub>{s}{e}</sub></div>\n\n---\n")
    return "\n".join(parts)


def init_readme(text):
    if "<!--START_SECTION:stats-->" in text:
        print("Markers already present, nothing to do.")
        return text
    block = build_dashboard_block() + "\n"
    anchor = "## 📊 GitHub Stats"
    if anchor in text:
        return text.replace(anchor, block + anchor, 1)
    return text.rstrip() + "\n\n" + block


# ----------------------------------------------------------------- main
def update_bio(ctx):
    bio = f"{BIO_BASE} | {ctx['user']['public_repos']} repos"[:160]
    r = session.patch(f"{API}/user", json={"bio": bio}, timeout=30)
    print("  bio updated" if r.ok else f"  ! bio update failed: {r.status_code} {r.text[:120]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="insert marker sections into README")
    ap.add_argument("--dry-run", action="store_true", help="print result instead of writing")
    args = ap.parse_args()

    with open(README_PATH, encoding="utf-8") as f:
        original = f.read()

    if args.init:
        new = init_readme(original)
        if not args.dry_run and new != original:
            with open(README_PATH, "w", encoding="utf-8") as f:
                f.write(new)
            print("Markers inserted. Now run the script without --init.")
        return

    print(f"Fetching data for @{USERNAME} ...")
    ctx = {"user": gh(f"/users/{USERNAME}")}
    ctx["repos"] = [
        r for r in gh(f"/users/{USERNAME}/repos", type="owner", sort="pushed", per_page=100)
        if not r["fork"] and not r["archived"] and r["name"].lower() not in EXCLUDE_REPOS
    ]
    try:
        ctx["open_prs"] = open_prs()
    except requests.RequestException as e:
        print(f"  ! could not load PRs: {e}", file=sys.stderr)
        ctx["open_prs"] = []

    updated = original
    for name, _, builder in SECTIONS:
        try:
            updated = replace_section(updated, name, builder(ctx))
            print(f"  ✓ {name}")
        except requests.RequestException as e:
            print(f"  ✗ {name}: {e}", file=sys.stderr)   # keep the old content for this section

    # Only bump the timestamp when something real changed -> no noisy commits.
    if updated != original:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("Last updated: %Y-%m-%d %H:%M UTC")
        updated = replace_section(updated, "timestamp", stamp)

    if args.dry_run:
        print("\n" + "=" * 60 + "\n" + updated)
    elif updated != original:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(updated)
        print("README.md updated.")
    else:
        print("No changes.")

    if os.getenv("UPDATE_BIO") == "1" and not args.dry_run:
        update_bio(ctx)


if __name__ == "__main__":
    main()
