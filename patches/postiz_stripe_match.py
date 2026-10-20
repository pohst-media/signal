#!/usr/bin/env python3
"""Pohst Signal: match Stripe products by metadata (billing + period) instead of product name.
Dry run by default; --apply to write. Backups go to /path/to/backups/<timestamp>/."""
import subprocess, sys, os, datetime, tempfile

CONTAINER = 'postiz'
TARGET = '/app/apps/backend/dist/libraries/nestjs-libraries/src/services/stripe.service.js'
OLD = "allProducts.data.find((product) => product.name.toUpperCase() === body.billing.toUpperCase())"
NEW = ("(allProducts.data.find((product) => product.metadata?.billing === body.billing && "
       "(!product.metadata?.period || product.metadata.period === body.period)) || "
       "allProducts.data.find((product) => product.name.toUpperCase() === body.billing.toUpperCase()))")
MARK = "product.metadata?.billing === body.billing"

def run(args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ERROR " + " ".join(args) + "\n" + r.stderr)
    return r

def main():
    apply = '--apply' in sys.argv
    tmp = tempfile.mkdtemp()
    local = os.path.join(tmp, 'stripe.service.js')
    run(['docker', 'cp', CONTAINER + ':' + TARGET, local])
    s = open(local).read()
    if MARK in s:
        print("OK    Stripe metadata matching already patched - nothing to do")
        return
    n = s.count(OLD)
    if n == 0:
        sys.exit("ERROR Stripe product lookup not found - upstream code changed, check stripe.service.js")
    print("INFO  Stripe product lookup by metadata x{}".format(n))
    if not apply:
        print("DRY RUN - nothing written (use --apply)")
        return
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    bdir = os.path.join('/path/to/backups', ts)
    os.makedirs(bdir, exist_ok=True)
    open(os.path.join(bdir, 'stripe.service.js'), 'w').write(s)
    open(local, 'w').write(s.replace(OLD, NEW))
    run(['docker', 'cp', local, CONTAINER + ':' + TARGET])
    run(['docker', 'cp', CONTAINER + ':' + TARGET, local])
    if open(local).read().count(MARK) != n:
        sys.exit("ERROR verification failed - restore from " + bdir)
    print("OK    Written and verified")

if __name__ == '__main__':
    main()
