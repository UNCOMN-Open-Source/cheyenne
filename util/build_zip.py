#!/usr/bin/env python3
'''
Build a zip file out of a provided subdirectory
'''
import os
import sys
import zipfile

if len(sys.argv) < 2:
    print(f'Usage: {__file__} <target directory> <zip file>')
    sys.exit(1)

source_dir = sys.argv[1]
zip_path = sys.argv[2]

fd = zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED)
for root, _dirs, files in os.walk(source_dir):
    for file in files:
        inner_path = os.path.join(root, file)
        fd.write(inner_path, os.path.relpath(inner_path, source_dir))

fd.close()
