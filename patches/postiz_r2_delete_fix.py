#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pohst Signal - R2 Delete Fix (container-safe)
Temporary fix for https://github.com/gitroomhq/postiz-app/issues/1217

v2.24.0 already implements CloudflareStorage.removeFile(); this only patches
MediaService.deleteMedia() so it removes the R2 object AFTER the org-scoped
soft delete succeeds (prevents cross-organisation deletion).

Usage:
  python3 postiz_r2_delete_fix.py           # dry run
  python3 postiz_r2_delete_fix.py --apply   # make the change

Lost on container recreate - re-run after every upgrade/recreate.
"""

import os
import sys
import shutil
import datetime
import subprocess
import tempfile

CONTAINER = 'postiz'
MEDIA_SERVICE = ('/app/apps/backend/dist/libraries/nestjs-libraries/src/'
                 'database/prisma/media/media.service.js')
STORAGE_FILE = ('/app/apps/backend/dist/libraries/nestjs-libraries/src/'
                'upload/cloudflare.storage.js')
BACKUP_ROOT = '/path/to/backups'

OLD = ("    async deleteMedia(org, id) {\n"
       "        return this._mediaRepository.deleteMedia(org, id);\n"
       "    }")

NEW = ("    async deleteMedia(org, id) {\n"
       "        const deleted = await this._mediaRepository.deleteMedia(org, id);\n"
       "        const target = deleted && (deleted.path || deleted.name);\n"
       "        if (target) {\n"
       "            try {\n"
       "                await this.storage.removeFile(target);\n"
       "            } catch (err) {\n"
       "                console.error('Error removing file from storage:', err);\n"
       "            }\n"
       "        }\n"
       "        return deleted;\n"
       "    }")

MARKER = "const deleted = await this._mediaRepository.deleteMedia(org, id);"

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


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


def container_running():
    r = run(['docker', 'inspect', '-f', '{{.State.Running}}', CONTAINER])
    return r.returncode == 0 and r.stdout.strip() == 'true'


def copy_out(cpath, local):
    r = run(['docker', 'cp', '{}:{}'.format(CONTAINER, cpath), local])
    if r.returncode != 0:
        error("docker cp out failed: {}".format(r.stderr.strip()))


def copy_in(local, cpath):
    r = run(['docker', 'cp', local, '{}:{}'.format(CONTAINER, cpath)])
    if r.returncode != 0:
        error("docker cp in failed: {}".format(r.stderr.strip()))


def read_text(path):
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def write_text(path, content):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)


def main():
    apply = '--apply' in sys.argv

    log("\n" + "=" * 60)
    log("  Pohst Signal - R2 Delete Fix ({})".format("APPLY" if apply else "DRY RUN"), BOLD)
    log("=" * 60)

    if not container_running():
        error("Container '{}' is not running".format(CONTAINER))

    with tempfile.TemporaryDirectory() as work:
        # Sanity check: upstream removeFile must be implemented
        storage_local = os.path.join(work, 'cloudflare.storage.js')
        copy_out(STORAGE_FILE, storage_local)
        storage = read_text(storage_local)
        if 'async removeFile(' not in storage or 'DeleteObjectCommand' not in storage:
            error("CloudflareStorage.removeFile() is not implemented in this version - "
                  "this script needs updating")
        success("CloudflareStorage.removeFile() is implemented upstream")

        local = os.path.join(work, 'media.service.js')
        copy_out(MEDIA_SERVICE, local)
        content = read_text(local)

        if MARKER in content:
            success("deleteMedia already patched - nothing to do")
            return

        count = content.count(OLD)
        if count == 0:
            warn("deleteMedia pattern not found - Postiz may have changed or fixed it.")
            warn("Check https://github.com/gitroomhq/postiz-app/issues/1217")
            return
        if count > 1:
            error("deleteMedia pattern found {} times - refusing to patch".format(count))

        new_content = content.replace(OLD, NEW, 1)
        info("deleteMedia will soft-delete first (org-scoped), then remove the R2 object")

        if not apply:
            warn("Dry run - not written. Re-run with --apply.")
            return

        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = os.path.join(BACKUP_ROOT, stamp)
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(local, os.path.join(backup_dir, 'media.service.js'))
        info("Backup: {}/media.service.js".format(backup_dir))

        write_text(local, new_content)
        copy_in(local, MEDIA_SERVICE)

        check = os.path.join(work, 'verify.js')
        copy_out(MEDIA_SERVICE, check)
        if read_text(check) != new_content:
            error("Post-write verification failed")
        success("Written and verified")

        log("\nNext: restart Postiz and WAIT ~2 minutes:", YELLOW)
        log("  cd /path/to/postiz && docker compose restart postiz", BOLD)
        log("Rollback: docker cp {}/media.service.js {}:{}".format(
            backup_dir, CONTAINER, MEDIA_SERVICE), YELLOW)


if __name__ == '__main__':
    main()