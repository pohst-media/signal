#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pohst Signal - Post Update Patch (container-safe)

Patches files INSIDE the running Postiz container via docker cp:
  1. Sets posts_per_month per tier in ALL pricing.js files (backend + orchestrator)
  2. Rebrands interface text (all languages, static + SSR chunks):
       a. Rewrites billing headline + FAQ per language (signal_text_overrides.json)
       b. Agent Name placeholder MyPostizAgent -> MyPohstAgent
       c. "Postiz" -> "Pohst" in human-readable strings (case-sensitive, whole
          word). Billing/FAQ overrides keep "Pohst Signal" (product-specific).
       d. App display name ternary ?"Postiz":"Gitroom" -> ?"Pohst":"Gitroom"
          (used for e.g. "Logout from ...")
       e. Migrates earlier "Pohst Signal" interface text to "Pohst"

UI hiding (sidebar items, login buttons, Design Media, extension icon) is done
by nginx + pohst-brand.css, not by this script.

Usage:
  python3 postiz_update.py           # dry run
  python3 postiz_update.py --apply   # make changes
Normally run via ./signal-up.sh
"""

import os
import re
import sys
import json
import shutil
import datetime
import subprocess
import tempfile

CONTAINER = 'postiz'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OVERRIDES_FILE = os.path.join(SCRIPT_DIR, 'signal_text_overrides.json')
FRONTEND_DIRS = [
    '/app/apps/frontend/.next/static/chunks',
    '/app/apps/frontend/.next/server/chunks/ssr',
]
PRICING_SEARCH_ROOT = '/app/apps'
BACKUP_ROOT = '/path/to/backups'

# Pohst Signal posts_per_month limits per tier - change here if needed
POSTS_LIMITS = {
    'STANDARD': 100,
    'TEAM':     300,
    'PRO':      600,
    'ULTIMATE': 2000,
}

# Pohst Signal plan values per tier (prices USD, channels, AI images, features)
TIER_VALUES = {
    'STANDARD': {'month_price': 9,  'year_price': 84,  'channel': 3,   'image_generation_count': 20,  'image_generator': True, 'autoPost': True},
    'TEAM':     {'month_price': 15, 'year_price': 144, 'channel': 15,  'image_generation_count': 50,  'image_generator': True, 'autoPost': True},
    'PRO':      {'month_price': 39, 'year_price': 372, 'channel': 45,  'image_generation_count': 100, 'image_generator': True, 'autoPost': True},
    'ULTIMATE': {'month_price': 79, 'year_price': 756, 'channel': 150, 'image_generation_count': 200, 'image_generator': True, 'autoPost': True},
}

BRAND_FROM = 'Postiz'
BRAND_TO = 'Pohst'
OLD_BRAND = 'Pohst Signal'        # earlier rebrand, migrated to BRAND_TO
PROTECT_MARKER = 'AGPL-3.0'   # strings that intentionally keep the word "Postiz"

GREEN, RED, YELLOW, BLUE, BOLD, RESET = (
    '\033[92m', '\033[91m', '\033[93m', '\033[94m', '\033[1m', '\033[0m')


def log(msg, color=RESET):
    print("{}{}{}".format(color, msg, RESET))


def success(msg):
    log("OK    {}".format(msg), GREEN)


def warn(msg):
    log("WARN  {}".format(msg), YELLOW)


def info(msg):
    log("INFO  {}".format(msg), BLUE)


def error(msg):
    log("ERROR {}".format(msg), RED)
    sys.exit(1)


# ------------------------------------------------------------
# Docker helpers
# ------------------------------------------------------------

def run(args):
    return subprocess.run(args, capture_output=True, text=True)


def container_running():
    r = run(['docker', 'inspect', '-f', '{{.State.Running}}', CONTAINER])
    return r.returncode == 0 and r.stdout.strip() == 'true'


def grep_files(pattern, dirs):
    found = set()
    for d in dirs:
        r = run(['docker', 'exec', CONTAINER, 'grep', '-rlF', pattern, d])
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and not line.endswith('.map'):
                found.add(line)
    return found


def find_pricing_files():
    r = run(['docker', 'exec', CONTAINER, 'find', PRICING_SEARCH_ROOT,
             '-name', 'pricing.js', '-path', '*subscriptions*'])
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def copy_out(container_path, local_path):
    r = run(['docker', 'cp', '{}:{}'.format(CONTAINER, container_path), local_path])
    if r.returncode != 0:
        error("docker cp out failed for {}: {}".format(container_path, r.stderr.strip()))


def copy_in(local_path, container_path):
    r = run(['docker', 'cp', local_path, '{}:{}'.format(CONTAINER, container_path)])
    if r.returncode != 0:
        error("docker cp in failed for {}: {}".format(container_path, r.stderr.strip()))


def read_text(path):
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def write_text(path, content):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)


def backup_name(container_path):
    return container_path.strip('/').replace('/', '__')


# ------------------------------------------------------------
# Patch 1 - Pricing limits
# ------------------------------------------------------------

TIER_KEY = r'\b{}\s*:\s*\{{'
POSTS_RE = re.compile(r'posts_per_month:\s*(\d+)')
ANY_TIER_KEY = re.compile(r'\b[A-Z_]{3,}\s*:\s*\{')


def locate_tier_value(content, tier):
    m = re.search(TIER_KEY.format(tier), content)
    if not m:
        raise ValueError("tier {} not found".format(tier))
    pm = POSTS_RE.search(content, m.end())
    if not pm:
        raise ValueError("posts_per_month not found after tier {}".format(tier))
    if ANY_TIER_KEY.search(content[m.end():pm.start()]):
        raise ValueError("posts_per_month for {} is not inside its own block".format(tier))
    return pm


def patch_pricing(content):
    changes = []
    for tier, limit in POSTS_LIMITS.items():
        pm = locate_tier_value(content, tier)
        current = int(pm.group(1))
        if current != limit:
            content = content[:pm.start()] + 'posts_per_month: {}'.format(limit) + content[pm.end():]
            changes.append("{}: {} -> {}".format(tier, current, limit))
    for tier, limit in POSTS_LIMITS.items():
        if int(locate_tier_value(content, tier).group(1)) != limit:
            raise ValueError("verification failed for {}".format(tier))

    # Pohst: prices, channels, AI images and RSS - each value changed only inside its own tier block
    def _fmt(v):
        return ('true' if v else 'false') if isinstance(v, bool) else str(v)
    for _verify in (False, True):
        for tier, fields in TIER_VALUES.items():
            tm = re.search(TIER_KEY.format(tier), content)
            if not tm:
                raise ValueError("tier {} not found".format(tier))
            nxt = ANY_TIER_KEY.search(content, tm.end())
            end = nxt.start() if nxt else len(content)
            block = content[tm.end():end]
            for field, value in fields.items():
                want = _fmt(value)
                fm = re.search(r'\b{}\s*:\s*(true|false|\d+)'.format(field), block)
                if not fm:
                    raise ValueError("{} not found in tier {}".format(field, tier))
                if fm.group(1) != want:
                    if _verify:
                        raise ValueError("verification failed for {} {}".format(tier, field))
                    changes.append("{} {}: {} -> {}".format(tier, field, fm.group(1), want))
                    block = block[:fm.start(1)] + want + block[fm.end(1):]
            content = content[:tm.end()] + block + content[end:]
    return content, changes


# ------------------------------------------------------------
# Patch 2 - Interface text rebrand
# ------------------------------------------------------------

def load_overrides():
    if not os.path.exists(OVERRIDES_FILE):
        error("Missing {} - place it next to this script".format(OVERRIDES_FILE))
    data = json.load(open(OVERRIDES_FILE, encoding='utf-8'))
    pairs = []
    for lang, entries in data['overrides'].items():
        for key, v in entries.items():
            pairs.append((lang, key, v['from'], v['to']))
    return pairs


OVERRIDES = []

STRING_LIT = re.compile(r'"(?:[^"\\\n]|\\.)*"')
BRAND_WORD = re.compile(r'(?<![A-Za-z0-9_$]){}(?![A-Za-z0-9_$])'.format(BRAND_FROM))


def encodings(text):
    """Ways a JSON string value can appear inside compiled JS."""
    raw = json.dumps(text, ensure_ascii=False)[1:-1]
    asc = json.dumps(text, ensure_ascii=True)[1:-1]
    out = [raw, asc]
    for e in (raw, asc):   # inside JSON.parse('...') the backslashes are doubled
        out.append(e.replace('\\', '\\\\').replace("'", "\\'"))
    return list(dict.fromkeys(out))


def apply_overrides(content):
    changed = []
    for lang, key, frm, to in OVERRIDES:
        for enc in encodings(frm):
            pat = re.compile('"' + re.escape(key) + r'"(\s*[:,]\s*)"' + re.escape(enc) + '"')
            content, n = pat.subn(lambda m: '"' + key + '"' + m.group(1) + '"' + to + '"', content)
            if n:
                changed.append("rewrote {} [{}] x{}".format(key, lang, n))
    return content, changed


OVERRIDE_VALUE = None


def check_overrides(content):
    """Every override key present must now hold one of our new values."""
    global OVERRIDE_VALUE
    if OVERRIDE_VALUE is None:
        OVERRIDE_VALUE = {}
        for lang, key, frm, to in OVERRIDES:
            OVERRIDE_VALUE.setdefault(key, set()).add(to)
    for key, allowed in OVERRIDE_VALUE.items():
        pat = re.compile('"' + re.escape(key) + r'"\s*[:,]\s*"((?:[^"\\\n]|\\.)*)"')
        for m in pat.finditer(content):
            if m.group(1) not in allowed:
                raise ValueError("override for '{}' did not match this file's text: {}".format(
                    key, m.group(1)[:70]))


KEEP_LITERALS = None


def keep_literals():
    """Encoded forms of override values - these intentionally say 'Pohst Signal'."""
    global KEEP_LITERALS
    if KEEP_LITERALS is None:
        KEEP_LITERALS = set()
        for lang, key, frm, to in OVERRIDES:
            KEEP_LITERALS.update(encodings(to))
    return KEEP_LITERALS


def brand_swap(content):
    samples = []
    count = [0]
    keep = keep_literals()

    def fix_literal(m):
        lit = m.group(0)
        inner = lit[1:-1]
        if BRAND_FROM not in inner and OLD_BRAND not in inner:
            return lit
        if inner.strip() == BRAND_FROM or PROTECT_MARKER in inner or inner in keep:
            return lit
        new_inner, n1 = BRAND_WORD.subn(BRAND_TO, inner)
        new_inner = new_inner.replace(OLD_BRAND, BRAND_TO)
        n2 = inner.count(OLD_BRAND)
        if not (n1 or n2):
            return lit
        count[0] += n1 + n2
        if len(samples) < 4:
            samples.append(new_inner[:90])
        return '"' + new_inner + '"'

    content = STRING_LIT.sub(fix_literal, content)
    return content, count[0], samples


def remaining_brand(content):
    keep = keep_literals()
    n = 0
    for m in STRING_LIT.finditer(content):
        inner = m.group(0)[1:-1]
        if inner.strip() == BRAND_FROM or PROTECT_MARKER in inner or inner in keep:
            continue
        n += len(BRAND_WORD.findall(inner)) + inner.count(OLD_BRAND)
    return n


def patch_text(content):
    changes = []

    content, over = apply_overrides(content)
    changes += over
    check_overrides(content)

    old_ph = 'placeholder:"MyPostizAgent"'
    n = content.count(old_ph)
    if n:
        content = content.replace(old_ph, 'placeholder:"MyPohstAgent"')
        changes.append("placeholder MyPostizAgent -> MyPohstAgent x{}".format(n))

    n = content.count('?"Postiz":"Gitroom"')
    if n:
        content = content.replace('?"Postiz":"Gitroom"', '?"Pohst":"Gitroom"')
        changes.append("display name ?\"Postiz\" -> ?\"Pohst\" x{}".format(n))

    # v2.25+: display name has a leading space inside the quotes
    n = content.count('?" Postiz":" Gitroom"')
    if n:
        content = content.replace('?" Postiz":" Gitroom"', '?" Pohst":" Gitroom"')
        changes.append("display name ?\" Postiz\" -> ?\" Pohst\" x{}".format(n))

    # v2.25+: MCP client config snippets use "postiz" as the server label
    import re as _re
    for _pat, _rep in [
        (r':\{postiz:\{', ':{pohst:{'),
        (r'\(\{postiz:\{', '({pohst:{'),
        (r'((?:mcp add(?: --transport http)?|mcp login|--name) )postiz\b', r'\1pohst'),
        (r'mcp_servers\.postiz\]', 'mcp_servers.pohst]'),
        (r'(mcp_servers:(?:\\n|\n)[ ]*)postiz:', r'\1pohst:'),
    ]:
        content, n = _re.subn(_pat, _rep, content)
        if n:
            changes.append("MCP label {} x{}".format(_pat, n))

    # Pohst: hide the "Official connector" tab (Postiz Cloud relay); "Sign in with Pohst" becomes default
    _old = '["official","oauth","apikey"]'
    n = content.count(_old)
    if n:
        content = content.replace(_old, '["oauth","apikey"]')
        changes.append("hide Official connector tab x{}".format(n))

    # Pohst: hide the Integrations (HeyGen/Reel.Farm) and Plugs tabs in Settings
    _old = 'e.push({tab:"integrations",label:S("integrations","Integrations")}),e.push({tab:"plugs",label:S("plugs","Plugs")}),'
    n = content.count(_old)
    if n:
        content = content.replace(_old, '')
        changes.append("hide Settings tabs Integrations + Plugs x{}".format(n))

    # Pohst: OAuth MCP URL must be /mcp-oauth-dynamic (no /api) to match the advertised OAuth resource
    content, n = re.subn(r'(\w+)=>`\$\{\1\}/mcp-oauth-dynamic`',
                         r'\1=>`${\1.replace(/\\/api$/,"")}/mcp-oauth-dynamic`', content)
    if n:
        changes.append("MCP OAuth URL without /api x{}".format(n))

    # Pohst: CLI "Locally" step 2 - Postiz Cloud login does not work for Signal; use API URL + key
    _old = '{label:"Run: postiz auth:login",code:"postiz auth:login"}'
    _new = '{label:"Connect to Pohst Signal (copy your API key from above)",code:"export POSTIZ_API_URL=https://signal.pohst.app/api\\nexport POSTIZ_API_KEY=your-api-key"}'
    n = content.count(_old)
    if n:
        content = content.replace(_old, _new)
        changes.append("CLI step 2 -> API URL + key x{}".format(n))
    # Pohst: CLI "CI / Remote servers" - add the Signal API URL before the key
    _old = '{label:"Set your API key as an environment variable",code:\'export POSTIZ_API_KEY="{API_KEY}"\'}'
    _new = '{label:"Set the Pohst Signal address and your API key",code:\'export POSTIZ_API_URL=https://signal.pohst.app/api\\nexport POSTIZ_API_KEY="{API_KEY}"\'}'
    n = content.count(_old)
    if n:
        content = content.replace(_old, _new)
        changes.append("CLI CI step -> API URL + key x{}".format(n))

    content, n, samples = brand_swap(content)
    if n:
        changes.append("interface text -> {} in {} place(s)".format(BRAND_TO, n))
        for s in samples:
            changes.append("   e.g. {}".format(s))

    left = remaining_brand(content)
    if left:
        raise ValueError("verification failed - {} brand occurrence(s) remain".format(left))
    return content, changes


# ------------------------------------------------------------
# File processing
# ------------------------------------------------------------

def process(container_path, patch_fn, label, apply, workdir, backup_dir, quiet_ok=False):
    local = os.path.join(workdir, backup_name(container_path))
    copy_out(container_path, local)
    original = read_text(local)

    try:
        new_content, changes = patch_fn(original)
    except ValueError as e:
        log("\n" + "-" * 60)
        log("{}: {}".format(label, container_path), BOLD)
        warn("Skipped - {}".format(e))
        return False

    if not changes:
        if not quiet_ok:
            log("\n" + "-" * 60)
            log("{}: {}".format(label, container_path), BOLD)
            success("Already patched - nothing to do")
        return True

    log("\n" + "-" * 60)
    log("{}: {}".format(label, container_path), BOLD)
    for c in changes:
        info(c)

    if not apply:
        warn("Dry run - not written")
        return True

    shutil.copy2(local, os.path.join(backup_dir, backup_name(container_path)))
    write_text(local, new_content)
    copy_in(local, container_path)

    check = local + '.verify'
    copy_out(container_path, check)
    if read_text(check) != new_content:
        error("Post-write verification failed for {}".format(container_path))

    success("Written and verified")
    return True


def main():
    global OVERRIDES
    apply = '--apply' in sys.argv

    log("\n" + "=" * 60)
    log("  Pohst Signal - Post Update Patch ({})".format("APPLY" if apply else "DRY RUN"), BOLD)
    log("=" * 60)
    log("Started: {}".format(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    if not container_running():
        error("Container '{}' is not running".format(CONTAINER))

    OVERRIDES = load_overrides()
    info("Loaded {} text overrides".format(len(OVERRIDES)))
    for tier, limit in POSTS_LIMITS.items():
        info("Target {} = {}".format(tier, limit))

    pricing_files = find_pricing_files()
    if not pricing_files:
        error("No pricing.js files found in container")
    text_files = sorted(grep_files(BRAND_FROM, FRONTEND_DIRS) | grep_files(OLD_BRAND, FRONTEND_DIRS) | grep_files('e.push({tab:"plugs"', FRONTEND_DIRS) | grep_files('/mcp-oauth-dynamic`', FRONTEND_DIRS))

    log("\nPricing files: {}".format(len(pricing_files)), BOLD)
    log("Text files containing '{}': {}".format(BRAND_FROM, len(text_files)), BOLD)

    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = os.path.join(BACKUP_ROOT, stamp)
    if apply:
        os.makedirs(backup_dir, exist_ok=True)
        info("Backups: {}".format(backup_dir))

    results = []
    with tempfile.TemporaryDirectory() as workdir:
        for f in pricing_files:
            results.append(process(f, patch_pricing, "Pricing", apply, workdir, backup_dir))
        text_ok = 0
        for f in text_files:
            ok = process(f, patch_text, "Text", apply, workdir, backup_dir, quiet_ok=True)
            results.append(ok)
            text_ok += 1 if ok else 0
        log("\n" + "-" * 60)
        success("Text: {} of {} files OK (unchanged files not listed)".format(text_ok, len(text_files)))

    log("\n" + "=" * 60)
    if all(results):
        success("All files OK")
    else:
        warn("Some files were skipped - check output above")

    if apply:
        log("\nNext: restart Postiz and WAIT ~2 minutes (signal-up.sh does this)", YELLOW)
        log("Rollback: docker cp files from {} back into the container".format(backup_dir), YELLOW)
    else:
        log("\nDry run complete. Re-run with --apply to make changes.", YELLOW)
    log("=" * 60 + "\n")


if __name__ == '__main__':
    main()
