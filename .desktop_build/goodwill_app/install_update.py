#!/usr/bin/env python3
"""Safely install Goodwill Item Type -> Category update; no DB connections."""
from __future__ import annotations
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

PYTHON_FILES = ['configuration_service.py', 'configuration_page.py', 'guidelines.py', 'guideline_page.py', 'item_search.py', 'item_search_page.py']
OTHER_FILES = ['migrations/004_preflight_read_only.sql', 'migrations/004_category_item_type.sql', 'migrations/005_seed_item_types_categories.sql', 'tests/test_category_item_type.py']
KNOWN_HASHES = {'configuration_service.py': ['11c3b553c3639b0a3d1188eca776206fb483f896f6e4ab6e6bbd14adacc8e862', 'f3cb0aea17df50bc8ac351937c7a205a2927b6861b2feabf731c997fc52c3871'], 'configuration_page.py': ['955ad1cbbd0a90b1d3d82090d68143fb65a88c4bfa33dedee8d5742f313143a8', '80c05e257a9969011337a12bba45806fc3c3ba9de6633a92ba4e0bf0330eb8b8'], 'guidelines.py': ['3eb792f8e79505ea4746209cca80c9287551ce0dece1facc217886556fa19654', '1798dda78461e569d483593e72e1a1f307a5ea493b6666d756c5866a88e3666d', '37a2b38efc008923a6cce44b8517ac738423722a5d4c8dcfdbbf4cf1f1864261'], 'guideline_page.py': ['cb6a0c00fe7488f33d0b4e1b3437131574f3ae645bc120f7a29d3e638b46fb81', '35bb585c41d4fa578c0d523829c05d05d6abd937a188ae4385f8b81f4ba8f524'], 'item_search.py': ['99977e451632480626d8302dee0c570a8703ad6806c48b05becdc1b589bd1b91', '43a493bc2b4f2d437e2394d0a2d9ab48d5ef0228be5328c6e236bb8aad9c4023'], 'item_search_page.py': ['5a43dde2fcec94f9f6139bc1d5011563007286cfea2a89c599f81182c054f7c0', 'a7b2d217aa5d2a5560988e9441953423c189d90e68a12ec21f64877d7eeb0377']}

def h(path):
    return sha256(path.read_bytes()).hexdigest()

def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path, help='Existing goodwillpricing directory')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    source = Path(__file__).resolve().parent
    if not project.is_dir() or not (project/'app.py').is_file() or not (project/'users_service.py').is_file():
        parser.error('Target does not look like the Goodwill project root.')
    invalid=[]; changed=[]; skipped=[]
    # Preflight every file before modifying anything
    for relative in PYTHON_FILES + OTHER_FILES:
        incoming=source/relative
        target=project/relative
        if not incoming.is_file():
            invalid.append(f'Missing package content: {relative}')
            continue
        if not target.exists():
            if relative in PYTHON_FILES:
                invalid.append(f'Expected original Python file not found: {relative}')
            else:
                changed.append(relative)
            continue
        if h(target)==h(incoming):
            skipped.append(relative)
        elif relative in PYTHON_FILES and h(target) in KNOWN_HASHES[relative]:
            changed.append(relative)
        else:
            invalid.append(f'Existing local version differs; not overwriting: {relative}')
    if invalid:
        print('ABORTED. Nothing changed:')
        for msg in invalid: print(' -',msg)
        print('Please share the current files for a merge rather than force-overwriting them.')
        return 2
    print('Files to add/update:')
    for f in changed: print(' -',f)
    print('Already current / matching:')
    for f in skipped: print(' -',f)
    if args.dry_run:
        print('Dry run complete; no files modified, no SQL executed.')
        return 0
    backup_dir=project/('backup_category_v2_1_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    for relative in changed:
        target=project/relative
        incoming=source/relative
        if target.exists():
            bak=backup_dir/relative
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target,bak)
    for relative in changed:
        target=project/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/relative,target)
    print('Python/SQL files updated. Backups:', backup_dir if backup_dir.exists() else '(no prior files)')
    print('Database NOT migrated automatically. Run migration SQL manually in Workbench, then tests.')
    return 0

if __name__=='__main__':
    sys.exit(run())
