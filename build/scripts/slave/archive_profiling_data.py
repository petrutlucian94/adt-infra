#!/usr/bin/env python
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A tool to archive Chrome profiling data generated by perf buildbots.

Pushes generated profiling data to Google Storage.

For a list of command-line options, call this script with '--help'.
"""

import optparse
import os
import re
import sys

from slave import build_directory
from slave import slave_utils

FILENAME = 'task_profile.json'
GOOGLE_STORAGE_BUCKET = 'chromium-browser-profiling-data'


def CopyToGoogleStorage(src, dst):
  """Copies a file to the given Google Storage destination url.

  Args:
    src: path to file to be copied
    dst: Google Storage destination url (i.e., gs://...)
  Returns:
    whether the copy was successful
  """
  if not os.path.exists(src):
    print 'No such file', src
    return False
  return slave_utils.GSUtilCopy(src, dst, None, 'public-read')


def Archive(revision, build_dir, builder_name, test_name):
  """Archive the profiling data to Google Storage.

  Args:
    revision: the unique identifier of this run
    build_dir: the path to the build directory
    builder_name: builder name
    test_name: test name
  Returns:
    whether profiling data correctly uploaded or not
  """
  if not os.path.exists(build_dir):
    print 'No build directory'
    return True

  # Profiling data is in /b/build/slave/SLAVE_NAME/build/src/chrome/test/data
  # and |build_dir| is /b/build/slave/SLAVE_NAME/build/src/build.
  profiling_data_dir = os.path.join(build_dir, '..', 'chrome', 'test', 'data')
  if not os.path.exists(profiling_data_dir):
    print 'No profiling_data_dir: ', profiling_data_dir
    return True

  src_profiling_file = os.path.join(profiling_data_dir, FILENAME)
  if not os.path.exists(src_profiling_file):
    print 'No profiling_file: ', src_profiling_file
    return True

  revision = re.sub(r'\W+', '_', revision)
  builder_name = re.sub(r'\W+', '_', builder_name)
  test_name = re.sub(r'\W+', '_', test_name)

  view_url = 'https://commondatastorage.googleapis.com/' + GOOGLE_STORAGE_BUCKET
  print 'See %s for this run\'s test results' % view_url
  run_url = 'gs://%s/runs/%s/' % (GOOGLE_STORAGE_BUCKET, builder_name)
  print 'Pushing results to %s...' % run_url

  dest_profiling_file = run_url + test_name + '_' + revision + '_' + FILENAME
  if not CopyToGoogleStorage(src_profiling_file, dest_profiling_file):
    return False
  return True


def main():
  option_parser = optparse.OptionParser()
  option_parser.add_option('', '--revision', default=None,
                           help='unique id for this run')
  option_parser.add_option('', '--build-dir', help='ignored')
  option_parser.add_option('', '--builder-name', default=None,
                           help='name of the build machine')
  option_parser.add_option('', '--test-name', default=None,
                           help='name of the test')
  options = option_parser.parse_args()[0]
  options.build_dir = build_directory.GetBuildOutputDirectory()

  if (options.revision is None or options.build_dir is None or
      options.builder_name is None or options.test_name is None):
    print 'All command options are required. Use --help.'
    return 1

  if Archive(options.revision, options.build_dir, options.builder_name,
             options.test_name):
    return 0
  return 2


if '__main__' == __name__:
  sys.exit(main())
