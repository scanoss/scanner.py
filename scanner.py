#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
#   Copyright (C) 2018-2020 SCANOSS LTD
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import argparse
from binaryornot.check import is_binary
from crc32c import crc32c
import hashlib
import json
from json.decoder import JSONDecodeError
import os
from pathlib import Path
import requests
import sys
import uuid
from zipfile import ZipFile



# 64k Max post size
MAX_POST_SIZE = 64 * 1024

MAX_LONG_LINE_CHARS = 1000

RESULT_FILE = None

WFP_FILE_START = "file="

# List of extensions that are ignored
FILTERED_EXT = ["", "png", "html", "xml", "svg", "yaml", "yml", "txt", "json", "gif", "md", "test", "cfg", "pdf",
                "properties", "jpg", "vim", "sql", "result", "template", 'tiff', 'bmp', 'DS_Store', 'eot', 'otf', 'ttf', 'woff', 'rgb', 'conf', "whl", "o", "ico", "wfp"]

FILTERED_DIRS = ["/.git/", "/.svn/", "/.eggs/",
                 "__pycache__", "/node_modules", "/vendor"]

GITHUB_ROOT_URL = "https://github.com"
GITEE_ROOT_URL = "https://gitee.com"
GITHUB_MASTER_ZIP = "/archive/master.zip"
GITEE_MASTER_ZIP = "/repository/archive/master.zip"

DEFAULT_URL = "https://osskb.org/api/scan/direct"
SCANOSS_SCAN_URL = os.environ.get("SCANOSS_SCAN_URL") if os.environ.get(
    "SCANOSS_SCAN_URL") else DEFAULT_URL
SCANOSS_KEY_FILE = ".scanoss-key"

SCAN_TYPES = ['ignore', 'identify', 'blacklist']


def print_stderr(*args, **kwargs):
  print(*args, file=sys.stderr, **kwargs)


def log_result(str):
  """
  Logs result to either file or STDOUT
  """
  if RESULT_FILE:
    with open(RESULT_FILE, "a") as rf:
      rf.write(str+'\n')
  else:
    print(str)


def main():
  global RESULT_FILE
  api_key = None
  parser = argparse.ArgumentParser(
      description='Simple scanning agains SCANOSS API.')

  parser.add_argument('scan_dir', metavar='DIR', type=str, nargs='?',
                      help='A folder to scan')
  parser.add_argument('--url', type=str, help="Scan a URL. It supports urls containing zip files of projects, and it can download master.zip of open projects from GitHub and Gitee")
  parser.add_argument('--wfp',  type=str,
                      help='Scan a WFP File')
  parser.add_argument('--ignore',  type=str,
                      help='Scan and ignore components in SBOM file')
  parser.add_argument('--identify', nargs=1, type=str,
                      help='Scan and identify components in SBOM file')
  parser.add_argument('--blacklist', nargs=1, type=str,
                      help='Scan and blacklist components in SBOM file')
  parser.add_argument('--output', '-o', nargs=1, type=str,
                      help='Optional name for the result file.')
  parser.add_argument('--format', '-f', nargs=1, type=str, choices=[
                      'plain', 'spdx', 'spdx_xml', 'cyclonedx'], help='Optional format of the scan result')

  args = parser.parse_args()
  # Check for SCANOSS Key
  home = Path.home()
  scanoss_keyfile = str(home.joinpath(SCANOSS_KEY_FILE))
  if os.path.isfile(scanoss_keyfile):
    # Read key from file
    with open(scanoss_keyfile) as f:
      api_key = f.readline().strip()

  # Check if scan type has been declared

  scantype = ""

  sbom_path = ""
  if args.ignore:
    scantype = 'ignore'
    sbom_path = args.ignore[0]
  elif args.identify:
    scantype = 'identify'
    sbom_path = args.identify[0]
  elif args.blacklist:
    scantype = 'blacklist'
    sbom_path = args.blacklist[0]

  if args.output:
    RESULT_FILE = args.output[0]
    # Clear contents of file
    open(RESULT_FILE, 'w').close()
  
  format = args.format[0] if args.format else ''


  # Perform the scan
  if args.url:
    scan_dir = download_project(args.url)
    if not scan_dir:
      print_stderr("Invalid URL: %s", args.url)
      exit(1)
    scan_folder(scan_dir, api_key, scantype, sbom_path, format)
  elif args.scan_dir:
    if not os.path.isdir(args.scan_dir):
      print_stderr("Invalid directory: %s" % args.scan_dir)
      parser.print_help()
      exit(1)
    scan_folder(args.scan_dir, api_key, scantype, sbom_path, format)
  elif args.wfp:
    scan_wfp(args.wfp, api_key, scantype, sbom_path, format=format)


def valid_folder(folder):
  for excluded in FILTERED_DIRS:
    if excluded in folder:
      return False
  return True

def download_project(url: str):
  global GITEE_ROOT_URL, GITHUB_ROOT_URL
  if (GITEE_ROOT_URL in url or GITHUB_ROOT_URL in url) and not url.endswith(".zip"):
    if GITEE_ROOT_URL in url:
      url += GITEE_MASTER_ZIP
    elif GITHUB_ROOT_URL in url:
      url+= GITHUB_MASTER_ZIP
  
  if url.endswith(".zip"):
    zipfile = url.replace("https://","").replace("/","_").replace(".","_")
    r = requests.get(url, headers={'Accept': 'application/zip', 'User-Agent': 'curl/7.64.1'})
    if r.status_code != 200:
      print_stderr("ERROR: HTTP Status %d getting content from URL: %s, " % (r.status_code, url))
    with open(zipfile, 'wb') as f:
      f.write(r.content)
    folder = zipfile.replace("_zip", "")
    with ZipFile(zipfile, 'r') as zipObject:
      zipObject.extractall(folder)
    os.remove(zipfile)
    return folder
  return None
    


def scan_folder(dir: str, api_key: str, scantype: str, sbom_path: str, format: str):
  """ Performs a scan of the folder given

  Parameters
  ----------
  dir : str
    The folder containing the files to be scanned
  api_key : str
    A valid SCANOSS API key
  scantype: str
    A valid scan type (ignore, identify, blacklist)
  sbom_path: str
    A path to a valid CycloneDX or SPDX 2.2 JSON document.
  """

  wfp = ''
  # This is a dictionary that is used to perform a lookup of a file name using the corresponding file index
  files_conversion = {} if not format else None
  # We assign a number to each of the files. This avoids sending the file names to SCANOSS API,
  # thus hiding the names and the structure of the project from SCANOSS API.
  files_index = 0
  for root, sub, files in os.walk(dir):
    if valid_folder(root):
      for file in [f for f in files if os.path.splitext(f)[1][1:] not in FILTERED_EXT]:
        files_index += 1
        path = os.path.join(root, file)
        if files_conversion:
          files_conversion[str(files_index)] = path
          wfp += wfp_for_file(files_index, path)
        else:
          wfp += wfp_for_file(file, path)
  with open('scan.wfp', 'w') as f:
    f.write(wfp)
  scan_wfp('scan.wfp', api_key, scantype,
           sbom_path, files_conversion, format)


def scan_wfp(wfp_file: str, api_key: str, scantype: str, sbom_path: str, files_conversion=None, format=None):
  global WFP_FILE_START
  file_count = count_files_in_wfp_file(wfp_file)
  cur_files = 0
  cur_size = 0
  wfp = ""
  if 'xml' in format:
    with open(wfp_file) as f:
      wfp = f.read()
    scan_resp = do_scan(wfp, api_key, scantype, sbom_path, format)
    log_result(scan_resp)
  else:
    log_result("{")
    with open(wfp_file) as f:
      for line in f:
        wfp += "\n" + line
        cur_size += len(line.encode('utf-8'))
        if WFP_FILE_START in line:
          cur_files += 1
          if cur_size >= MAX_POST_SIZE:

            # Scan current WFP and store
            scan_resp = do_scan(wfp, api_key, scantype, sbom_path, format)

            for key, value in scan_resp.items():
              file_key = files_conversion[key] if files_conversion else key
              log_result("\"%s\":%s,\n" %
                         (file_key, json.dumps(value, indent=4)))
            cur_size = 0
            wfp = ""
    if wfp:
      scan_resp = do_scan(wfp, api_key, scantype, sbom_path, format)
      first = True

      for key, value in scan_resp.items():
        file_key = files_conversion[key] if files_conversion else key
        if first:
          log_result("\"%s\":%s" % (file_key, json.dumps(value, indent=4)))
          first = False
        else:
          log_result(",\"%s\":%s" % (file_key, json.dumps(value, indent=4)))
      log_result("}")


def count_files_in_wfp_file(wfp_file: str):
  count = 0
  with open(wfp_file) as f:
    for line in f:
      if "file=" in line:
        count += 1
  return count


def do_scan(wfp: str, api_key: str, scantype: str, sbom_path: str, format: str):
  form_data = {}
  if scantype:
    with open(sbom_path) as f:
      sbom = f.read()
    form_data = {'type': scantype, 'assets': sbom}
  if format:
    form_data['format'] = format
  headers = {}
  if api_key:
    headers['X-Session'] = api_key
  scan_files = {
      'file': ("%s.wfp" % uuid.uuid1().hex, wfp)}

  r = requests.post(SCANOSS_SCAN_URL, files=scan_files, data=form_data,
                    headers=headers)
  if r.status_code >= 400:
    print("ERROR: The SCANOSS API returned the following error: HTTP %d, %s" %
          (r.status_code, r.text))
    exit(1)
  try:
    if 'xml' in format:
      return r.text
    json_resp = r.json()
    return json_resp
  except JSONDecodeError:
    print("The SCANOSS API returned an invalid JSON")
    with open('bad_json.txt', 'w') as f:
      f.write(r.text)
    exit(1)
  # Decode file names


"""
Winnowing Algorithm implementation for SCANOSS.

This module implements an adaptation of the original winnowing algorithm by S. Schleimer, D. S. Wilkerson and A. Aiken
as described in their seminal article which can be found here: https://theory.stanford.edu/~aiken/publications/papers/sigmod03.pdf

The winnowing algorithm is configured using two parameters, the gram size and the window size. For SCANOSS the values need to be:
 - GRAM: 30
 - WINDOW: 64

The result of performing the Winnowing algorithm is a string called WFP (Winnowing FingerPrint). A WFP contains optionally
the name of the source component and the results of the Winnowing algorithm for each file.

EXAMPLE output: test-component.wfp
component=f9fc398cec3f9dd52aa76ce5b13e5f75,test-component.zip
file=cae3ae667a54d731ca934e2867b32aaa,948,test/test-file1.c
4=579be9fb
5=9d9eefda,58533be6,6bb11697
6=80188a22,f9bb9220
10=750988e0,b6785a0d
12=600c7ec9
13=595544cc
18=e3cb3b0f
19=e8f7133d
file=cae3ae667a54d731ca934e2867b32aaa,1843,test/test-file2.c
2=58fb3eed
3=f5f7f458
4=aba6add1
8=53762a72,0d274008,6be2454a
10=239c7dfa
12=0b2188c9
15=bd9c4b10,d5c8f9fb
16=eb7309dd,63aebec5
19=316e10eb
[...]

Where component is the MD5 hash and path of the component container (It could be a path to a compressed file or a URL).
file is the MD5 hash, file length and file path being fingerprinted, followed by
a list of WFP fingerprints with their corresponding line numbers.
"""
# Winnowing configuration. DO NOT CHANGE.
GRAM = 30
WINDOW = 64

# ASCII characters
ASCII_0 = 48
ASCII_9 = 57
ASCII_A = 65
ASCII_Z = 90
ASCII_a = 97
ASCII_z = 122
ASCII_LF = 10
ASCII_BACKSLASH = 92

MAX_CRC32 = 4294967296


def normalize(byte):
  """
  This function normalizes a given byte as an ASCII character

  Parameters
  ----------
  byte : int
    The byte to normalize
  """
  if byte < ASCII_0:
    return 0
  if byte > ASCII_z:
    return 0
  if byte <= ASCII_9:
    return byte
  if byte >= ASCII_a:
    return byte
  if ((byte >= 65) and (byte <= 90)):
    return byte + 32

  return 0


def skip_snippets(src: str, file: str) -> bool:
  if len(src) == 0:
    return True
  if src[0] == "{":
    return True
  prefix = src[0:5].lower()
  if prefix.startswith("<?xml") or prefix.startswith("<html"):
    return True
  index = src.index('\n') if '\n' in src else len(src)
  if len(src[0:index]) > MAX_LONG_LINE_CHARS:
    return True
  return False


def wfp_for_file(file: str, path: str) -> str:
  """ Returns the WFP for a file by executing the winnowing algorithm over its contents.

  Parameters
  ----------
  file: str
    The name of the file
  path : str
    The full contents of the file as a byte array.
  """
  contents = None
  binary = False

  with open(path, 'rb') as f:
    contents = f.read()

  file_md5 = hashlib.md5(
      contents).hexdigest()
  # Print file line
  wfp = 'file={0},{1},{2}\n'.format(file_md5, len(contents), file)
  # We don't process snippets for binaries.
  if is_binary(path) or skip_snippets(contents.decode('utf-8', 'ignore'), file):
    return wfp
  # Initialize variables
  gram = ""
  window = []
  normalized = 0
  line = 1
  min_hash = MAX_CRC32
  last_hash = MAX_CRC32
  last_line = 0
  output = ""

  # Otherwise recurse src_content and calculate Winnowing hashes
  for byte in contents:

    if byte == ASCII_LF:
      line += 1
      normalized = 0
    else:
      normalized = normalize(byte)

    # Is it a useful byte?
    if normalized:

      # Add byte to gram
      gram += chr(normalized)

      # Do we have a full gram?
      if len(gram) >= GRAM:
        gram_crc32 = crc32c(gram.encode('ascii'))
        window.append(gram_crc32)

        # Do we have a full window?
        if len(window) >= WINDOW:

          # Select minimum hash for the current window
          min_hash = min(window)

          # Is the minimum hash a new one?
          if min_hash != last_hash:

            # Hashing the hash will result in a better balanced resulting data set
            # as it will counter the winnowing effect which selects the "minimum"
            # hash in each window
            crc = crc32c((min_hash).to_bytes(4, byteorder='little'))
            crc_hex = '{:08x}'.format(crc)
            if last_line != line:
              if output:
                wfp += output + '\n'
              output = "%d=%s" % (line, crc_hex)
            else:
              output += ',' + crc_hex

            last_line = line
            last_hash = min_hash

          # Shift window
          window.pop(0)

        # Shift gram
        gram = gram[1:]

  if output:
    wfp += output + '\n'

  return wfp


if __name__ == "__main__":
  main()
