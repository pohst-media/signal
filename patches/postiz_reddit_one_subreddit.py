#!/usr/bin/env python3
"""
Pohst Signal: Reddit compliance patches (Reddit Responsible Builder Policy).
  1. One subreddit per post   - ArrayMaxSize(1) on RedditSettingsDto.subreddit
  2. No repeat for Reddit     - rejects CreatePostDto when 'inter' is set and any channel is Reddit
  3. No Reddit comments       - rejects a Reddit channel with more than one value (extra values post as comments)
Patches backend and orchestrator compiled DTOs.

Usage:
  python3 postiz_reddit_one_subreddit.py           # check only
  python3 postiz_reddit_one_subreddit.py --apply   # patch if needed (idempotent)
"""
import subprocess
import sys

CONTAINER = "postiz"
APPS = ["backend", "orchestrator"]
BASE = "/app/apps/{app}/dist/libraries/nestjs-libraries/src/dtos/posts"

# --- Patch 1: one subreddit per post ---
P1_OLD = "(0, class_validator_1.ArrayMinSize)(1),"
P1_NEW = (
    P1_OLD
    + "\n    (0, class_validator_1.ArrayMaxSize)(1, { message: "
    + "'Reddit posts can target one subreddit only. Posting the same content to multiple subreddits is not permitted.' }),"
)

# --- Patch 2: no repeat (inter) when a Reddit channel is in the post ---
P2_OLD = (
    '    (0, class_validator_1.ArrayMinSize)(1),\n'
    '    tslib_1.__metadata("design:type", Array)\n'
    '], CreatePostDto.prototype, "posts", void 0);'
)
P2_NEW = (
    '    (0, class_validator_1.ArrayMinSize)(1),\n'
    '    (0, class_validator_1.ValidateBy)({ name: "pohstNoRedditRepeat", validator: { '
    'validate: (posts, args) => { const inter = args.object && args.object.inter; if (!inter) return true; '
    'return !(Array.isArray(posts) && posts.some((p) => p && p.settings && p.settings.__type === "reddit")); }, '
    'defaultMessage: () => "Repeating posts is not available for Reddit. Remove the Reddit channel or turn off repeat." } }),\n'
    '    tslib_1.__metadata("design:type", Array)\n'
    '], CreatePostDto.prototype, "posts", void 0);'
)

# --- Patch 3: no follow-up comments for Reddit (one value per Reddit channel) ---
P3_OLD = (
    '    tslib_1.__metadata("design:type", Array)\n'
    '], CreatePostDto.prototype, "posts", void 0);'
)
P3_NEW = (
    '    (0, class_validator_1.ValidateBy)({ name: "pohstNoRedditComments", validator: { '
    'validate: (posts) => !(Array.isArray(posts) && posts.some((p) => p && p.settings && p.settings.__type === "reddit" '
    '&& Array.isArray(p.value) && p.value.length > 1)), '
    'defaultMessage: () => "Reddit posts are limited to a single post. Follow-up comments are not available for Reddit." } }),\n'
    + P3_OLD
)

PATCHES = []
for app in APPS:
    base = BASE.format(app=app)
    PATCHES.append((f"Reddit one-subreddit [{app}]", f"{base}/providers-settings/reddit.dto.js",
                    P1_OLD, P1_NEW, "class_validator_1.ArrayMaxSize)(1"))
    PATCHES.append((f"Reddit no-repeat [{app}]", f"{base}/create.post.dto.js",
                    P2_OLD, P2_NEW, "pohstNoRedditRepeat"))
    PATCHES.append((f"Reddit no-comments [{app}]", f"{base}/create.post.dto.js",
                    P3_OLD, P3_NEW, "pohstNoRedditComments"))

APPLY = "--apply" in sys.argv


def run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def read(path):
    r = run(["docker", "exec", CONTAINER, "cat", path])
    return (r.stdout, None) if r.returncode == 0 else (None, r.stderr.strip())


errors = 0
for tag, path, old, new, marker in PATCHES:
    src, err = read(path)
    if src is None:
        print(f"ERROR {tag} cannot read file: {err}")
        errors += 1
        continue

    if marker in src:
        print(f"OK    {tag} already patched")
        continue

    if not APPLY:
        print(f"WARN  {tag} not patched (run with --apply)")
        continue

    if src.count(old) != 1:
        print(f"ERROR {tag} expected 1 anchor match, found {src.count(old)} - not patched")
        errors += 1
        continue

    b = run(["docker", "exec", CONTAINER, "cp", path, path + ".bak"])
    if b.returncode != 0:
        print(f"ERROR {tag} backup failed: {b.stderr.strip()} - not patched")
        errors += 1
        continue

    w = run(["docker", "exec", "-i", CONTAINER, "sh", "-c", f"cat > '{path}'"], stdin=src.replace(old, new))
    if w.returncode != 0:
        print(f"ERROR {tag} write failed: {w.stderr.strip()}")
        errors += 1
        continue

    check, err = read(path)
    if check is None or marker not in check:
        print(f"ERROR {tag} write not verified - restore from {path}.bak")
        errors += 1
        continue

    print(f"OK    {tag} Written and verified (backup: {path}.bak)")

sys.exit(1 if errors else 0)
