import math
import os
import json
import re
import time
import argparse
import requests
from datetime import datetime, timedelta, timezone

TOKEN = os.environ["GH_TOKEN"]
GRAPHQL_URL = "https://api.github.com/graphql"
REST_ISSUES = "https://api.github.com/search/issues"

## ENVIRONMENT ###
SEEN_CACHE_MAX = int(os.environ.get("SEEN_CACHE_MAX", 5000))
MAX_ALLOWED_DAYS = int(os.environ.get("MAX_ALLOWED_DAYS", 5000))


HEADERS = {
    "Authorization": f"Bearer {TOKEN}"
}

# Skip already seen entries and avoid repetitions
SEEN_CACHE_FILE = os.environ.get(
    "GH_SEEN_ISSUES_FILE",
    os.path.expanduser("~/.gh_devops_scan_seen.json")
)

# Repository stars cache, avoids calling GraphQL every time for already known repos
STARS_CACHE_FILE = os.environ.get(
    "GH_STARS_CACHE_FILE",
    os.path.expanduser("~/.gh_devops_scan_stars.json")
)
STARS_CACHE_TTL_DAYS = 3

# GitHub allows max 5 AND/OR/NOT operators per query -> max 6 grouped terms
KEYWORDS_PER_QUERY = 6

# -----------------------------
# INPUT MODE (multi-keyword, grouped with OR to reduce calls)
# -----------------------------
parser = argparse.ArgumentParser(description="GitHub DevOps issue miner")
parser.add_argument("keywords", nargs="*", help="keywords to search for, e.g. docker helm terraform")
parser.add_argument(
    "--no-cache",
    action="store_true",
    help="ignore the 'already seen' cache and also show issues found in previous runs"
)
args = parser.parse_args()

keywords = [k.lower() for k in args.keywords]
use_seen_cache = not args.no_cache

if args.no_cache:
    print("'already seen' cache DISABLED: issues from previous runs will also be shown\n")

cutoff_date = (datetime.now(timezone.utc) - timedelta(days=MAX_ALLOWED_DAYS)).strftime("%Y-%m-%d")

if len(keywords) > KEYWORDS_PER_QUERY:
    parser.error(f"Maximum {KEYWORDS_PER_QUERY} keywords allowed")

queries = []

#is open: filters only issues without linked pr's
base_query = f"is:issue is:open -linked:pr created:>{cutoff_date}"

if keywords:
    term = f"({' OR '.join(keywords)})" if len(keywords) > 1 else keywords[0]
    queries.append(f"{base_query} {term}")
    print(f"Looking for issues with keywords: {', '.join(keywords)}")
else:
    queries.append(base_query)
    print("Global search with no keywords")

print(f"Only issues created after {cutoff_date}\n")


# -----------------------------
# SEEN CACHE (issues already shown)
# -----------------------------
def load_seen_cache():
    if not os.path.exists(SEEN_CACHE_FILE):
        return set()
    try:
        with open(SEEN_CACHE_FILE, "r") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen_cache(seen_set):
    trimmed = list(seen_set)[-SEEN_CACHE_MAX:]
    with open(SEEN_CACHE_FILE, "w") as f:
        json.dump(trimmed, f)


# -----------------------------
# STARS CACHE (avoids repeated GraphQL calls for already known and fresh repos)
# -----------------------------
def load_stars_cache():
    if not os.path.exists(STARS_CACHE_FILE):
        return {}
    try:
        with open(STARS_CACHE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_stars_cache(cache):
    with open(STARS_CACHE_FILE, "w") as f:
        json.dump(cache, f)


def is_fresh(entry):
    try:
        ts = datetime.fromisoformat(entry["ts"])
    except (KeyError, ValueError):
        return False
    return (datetime.now(timezone.utc) - ts) < timedelta(days=STARS_CACHE_TTL_DAYS)


# -----------------------------
# GRAPHQL: BATCH REPO STARS
# -----------------------------
def fetch_repo_stars(repo_list):
    if not repo_list:
        return {}

    nodes = "\n".join(
        f"""
        r{i}: repository(owner: "{r.split('/')[0]}", name: "{r.split('/')[1]}") {{
            stargazerCount
            nameWithOwner
        }}
        """
        for i, r in enumerate(repo_list)
        if "/" in r
    )

    query = f"""
    query {{
        {nodes}
    }}
    """

    r = requests.post(
        GRAPHQL_URL,
        headers=HEADERS,
        json={"query": query},
        timeout=30
    )

    data = r.json().get("data", {})

    out = {}
    for k, v in data.items():
        if v:
            out[v["nameWithOwner"]] = v["stargazerCount"]

    return out


# -----------------------------
# SCORING
# -----------------------------

def repo_score(stars):
    return min(40, int(math.log10(stars + 1) * 10))

def label_score(labels):
    score = 0
    matched_labels = []

    label_patterns = [
        (r"^good[\s_-]+first[\s_-]+issue$", 50, "good first issue"),
        (r"^first[\s_-]+timers?[\s_-]+only$", 60, "first timers only"),
        (r"^beginner$", 40, "beginner"),
        (r"^starter$", 40, "starter"),
        (r"^easy$", 35, "easy"),
        (r"^low[\s_-]+hanging[\s_-]+fruit$", 35, "low hanging fruit"),
        (r"^help[\s_-]+wanted$", 30, "help wanted"),
        (r"^contributions?[\s_-]+welcome$", 25, "contributions welcome"),
        (r"^up[\s_-]+for[\s_-]+grabs$", 25, "up for grabs"),
    ]

    for raw_label in labels:
        label = raw_label.lower().strip()

        for pattern, points, canonical_name in label_patterns:
            if re.search(pattern, label):
                score += points
                matched_labels.append(canonical_name)
                break

    return score, matched_labels

def freshness_score(updated_at):
    updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - updated).days

    if age_days <= 7:
        return 30
    if age_days <= 30:
        return 20
    if age_days <= 90:
        return 10
    return 0


def comments_score(comments):
    if comments == 0:
        return 20
    if comments <= 5:
        return 15
    if comments <= 15:
        return 5
    return -10

def score(stars, labels, updated_at, comments):
    score = 0
    reasons = []

    repo_points = repo_score(stars)
    score += repo_points
    reasons.append(f"repo-stars:{repo_points}")

    label_points, matched_labels = label_score(labels)
    score += label_points

    for label in matched_labels:
        reasons.append(f"label:{label}")

    fresh_points = freshness_score(updated_at)
    score += fresh_points
    reasons.append(f"freshness:{fresh_points}")

    comment_points = comments_score(comments)
    score += comment_points
    reasons.append(f"comments:{comment_points}")

    return score, reasons

# -----------------------------
# FETCH ISSUES (one query per keyword group)
# -----------------------------
results = []
fetched_urls = set()
repos_to_fetch = set()

print("Scanning GitHub issues...\n")

for q in queries:
    for page in range(1, 6):
        r = requests.get(
            REST_ISSUES,
            headers=HEADERS,
            params={"q": q, "per_page": 100, "page": page},
            timeout=30
        )

        if r.status_code != 200:
            print(f"Error {r.status_code} on query '{q}' page {page}: {r.text[:200]}")
            break

        items = r.json().get("items", [])
        if not items:
            break

        for it in items:
            url = it["html_url"]
            if url in fetched_urls:
                continue
            fetched_urls.add(url)

            repo = it["repository_url"].replace(
                "https://api.github.com/repos/", ""
            )

            repos_to_fetch.add(repo)

            results.append({
                "url": url,
                "title": it["title"],
                "body": it.get("body") or "",
                "labels": [l["name"] for l in it.get("labels", [])],
                "repo": repo,
                "updated_at": it["updated_at"],
                "comments": it["comments"]
            })

        if len(items) < 100:
            break

        time.sleep(1)


# -----------------------------
# STARS: use fresh cache, call GraphQL only for missing/expired repos
# -----------------------------
stars_cache = load_stars_cache()

repos_missing = [
    repo for repo in repos_to_fetch
    if repo not in stars_cache or not is_fresh(stars_cache[repo])
]

print(f"Total repos: {len(repos_to_fetch)}. To request via GraphQL: {len(repos_missing)}. Rest from cache.\n")

for i in range(0, len(repos_missing), 100):
    batch = repos_missing[i:i + 100]
    fetched = fetch_repo_stars(batch)
    now_iso = datetime.now(timezone.utc).isoformat()
    for repo, stars in fetched.items():
        stars_cache[repo] = {"stars": stars, "ts": now_iso}

save_stars_cache(stars_cache)

repo_stars = {repo: entry["stars"] for repo, entry in stars_cache.items()}


# -----------------------------
# FINAL SCORING + ALREADY-SEEN ISSUE FILTER
# -----------------------------
seen_cache = load_seen_cache()

final = []
below_threshold = 0
already_seen = 0

for r in results:

    stars = repo_stars.get(r["repo"], 0)

    issue_score, reasons = score(
        stars=stars,
        labels=r["labels"],
        updated_at=r["updated_at"],
        comments=r["comments"]
    )

    if issue_score < 80:
        below_threshold += 1
        continue

    is_seen = r["url"] in seen_cache
    if is_seen:
        already_seen += 1

    if use_seen_cache and is_seen:
        continue

    final.append((
        issue_score,
        stars,
        r["url"],
        r["title"],
        r["repo"],
        reasons
    ))

cache_note = "excludes" if use_seen_cache else "does NOT exclude (--no-cache)"
print(
    f"Fetched: {len(results)}. Below score threshold: {below_threshold}. "
    f"Already seen in cache: {already_seen}. Cache mode: {cache_note}. Final results: {len(final)}"
)

final.sort(key=lambda x: x[0], reverse=True)

# -----------------------------
# OUTPUT
# -----------------------------
top = final[:100]

print(f"\nTOP NEW ISSUES - {len(top)} found (cache: {SEEN_CACHE_FILE})\n")

for i, (issue_score, stars, url, title, repo, reasons) in enumerate(top, 1):
    print(f"{i:03d}. score:{issue_score} stars:{stars}")
    print(f"     url: {url}")
    print(f"     title: {title}")
    print(f"     repo: {repo}")
    print(f"     reasons: {', '.join(reasons)}\n")

seen_cache.update(item[2] for item in top)
save_seen_cache(seen_cache)
