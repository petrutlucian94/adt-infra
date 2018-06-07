#!/usr/bin/python
"""
Driver for psq_boot_test.  Recieves a command via Swarming, runs boot tests on
the images defined within PSQ_CONFIG_FILE, and returns a JSON representation to
stdout that is parsed via the SWARMING server and sent to ATP.
"""

import unittest
import os
import shutil
import sys
import subprocess
import json
import socket
from datetime import datetime

"""
Variables that are used to set various ENV variables.
This PSQ boot code is adopted from the Buildbot boot_test codebase. As such,
much of the code has a intrinsic assumption that the ENV has been set up
correctly. The variables below cover these cases for this script which is invoked
directly.
"""
ADT_INFRA_PATH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH_DEPENDENCIES = [
    os.path.join(ADT_INFRA_PATH),
    os.path.join(ADT_INFRA_PATH, 'emu_test'),
    os.path.join(ADT_INFRA_PATH, 'emu_test', 'utils')
]
ANDROID_SDK_ROOT = os.path.join(os.path.expanduser('~'), 'Android', 'android-sdk-linux_public')
ANDROID_SDK_PATHS = [
    os.path.join(ANDROID_SDK_ROOT, 'tools'),
    os.path.join(ANDROID_SDK_ROOT, 'platform-tools'),
    os.path.join(ANDROID_SDK_ROOT, 'build_tools', '23.0.2')
]
PSQ_CONFIG_FILE = os.path.join(ADT_INFRA_PATH, 'emu_test', 'config', 'psq_boot_cfg.csv')


def modify_env():
  """
  Function to modify the environment as neccessary for this script to run.
  Various environment PATHS and VARS must be set to properly run this buildbot
  based script.  This function is called at first execution and handles this.
  """
  for path in PATH_DEPENDENCIES:
    sys.path.append(path)
  for path in ANDROID_SDK_PATHS:
    os.environ['PATH'] += os.pathsep + path
  os.environ['ANDROID_SDK_ROOT'] = ANDROID_SDK_ROOT

"""
We must call modify_env() before we finish our imports.  The below files are
part of the Buildbot codebase, and have various import dependencies that require
modify_env() to be called before we can successfully import them.
"""
modify_env()

"""
Finish imports now that PYTHONPATH is properly set
"""
import psq_helper
from psq_boot_test import PsqBootTestCase
import emu_test
from emu_test.utils import emu_argparser, emu_unittest


def update_git():
  """
  Call git to update the local repository.  Note that the executing code will
  already be loaded into memory when this occurs, and therefore code updates
  will not be RUN until the subsequent PSQ run.

  :return: None.
  """
  fetch = subprocess.Popen(["git", "fetch"], cwd = ADT_INFRA_PATH)
  fetch.communicate()
  pull = subprocess.Popen(["git", "pull"], cwd = ADT_INFRA_PATH)
  pull.communicate()


def create_test_case():
  """
  Create the PsqBootTestCase classes that represent a single unittest. These
  tests are created from the information within the PSQ_CONFIG_FILE file.

  :return: None.
  """
  emu_argparser.emu_args.config_file = PSQ_CONFIG_FILE
  emu_test.utils.emu_testcase.create_test_case_from_file("boot", PsqBootTestCase,
                                                         PsqBootTestCase.run_boot_test)


def setup_build_environment(ab_buildid, target):
  """
  Function to download the emulator zip file from treehuger, and extract the
  emulator to the designated location.

  :param ab_buildid:  Android Build ID number of emulator package.
  :param target: SDK target.  Usually sdk_linux_tools.
  :return: emu_binary.  Filepath location of the emulator binary file.
  """
  zip_location = psq_helper.downloadArtifact(ab_buildid, target)
  emu_binary   = psq_helper.unzipArtifacts(zip_location)
  return emu_binary


def upload_to_gs():
  """
  Uploads the log files generated by this script to GoogleStorage.  This
  uploaded log file will be included in the report of the run sent back to the
  swarming server.

  :return: The return code of the script "ADT_INFRA_PATH/emu_test/utils/zip_upload_logs.py
  """
  build_source_root = os.path.join(ADT_INFRA_PATH, 'build')
  log_util_path = os.path.join(ADT_INFRA_PATH, 'emu_test', 'utils', 'zip_upload_logs.py')
  upload_log_args = ['--dir', emu_argparser.emu_args.session_dir,
                     '--name', '%s.zip' % os.path.basename(emu_argparser.emu_args.session_dir),
                     '--ip', '', # Unused, but required by script
                     '--user', '', # Unused, but required by script
                     '--dst', '', # Unused, but required by script
                     '--build-dir', build_source_root,
                     '--skiplog']
  zip_and_upload = subprocess.Popen(["/usr/bin/python", log_util_path] +
                                    upload_log_args,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
  out, err = zip_and_upload.communicate()
  return zip_and_upload.returncode


def create_json_output(creation_time, test_start_time, test_end_time,
                       total_tests, failed_tests, session_dir):
  """
  Swarming server scans the output of the called script and searches for a JSON
  output that is identified via the '#JSON_START#' and '#JSON_END#' flags.
  This function creates this json output given the input information.

  :param creation_time: Time the PSQ script started running.
  :param test_start_time: Time the PSQ script started running PsqBootTestCase's.
  :param test_end_time: Time the PSQ script finished running PsqBootTestCase's.
  :param total_tests: Total number of PsqBootTestCase's run.
  :param failed_tests: Total number of PsqBootTestCase's that failed.
  :param session_dir: Directory where logs have been written.
  :return: JSON formatted string.
  """
  web_base = 'https://pantheon.corp.google.com/storage/browser/emu_psq_logs/'
  output = '#JSON_START#'
  json_output = {}
  if failed_tests > 0:
    json_output['status'] = 'FAILED'
  else:
    json_output['status'] = 'COMPLETED'
  json_output['created_on'] = creation_time
  json_output['tests_started_on'] = test_start_time
  json_output['tests_ended_on'] = test_end_time
  json_output['total_test_count'] = total_tests
  json_output['failed_test_count'] = failed_tests
  json_output['logs_url'] = web_base + '%s' % os.path.basename(session_dir)
  output += json.dumps(json_output)
  output += '#JSON_END#'
  return output


def get_hostname():
  """
  Get the hostname of the current machine.  If we detect the machine is a dev
  machine (".mtv." in name) we 'fake out' the hostname to
  androidstudio-swarming-1 to allow testing.

  :return: The hostname we will use when reading from the PSQ_CONFIG_FILE.
  """
  hostname = socket.gethostname()
  if '.mtv.' in hostname:
    hostname = 'androidstudio-swarming-1'
  return hostname


def set_emu_args(emu_binary):
  """
  The main unittest code being used by this PSQ is based upon the existing
  Buildbot infrastructure for Emulator Boot Testing.  This code has
  dependenices upon certain variables within the emu_argparser.emu_args var.
  This function overrides/inserts these variables are necc for the PSQ boot test.

  :return: None.
  """
  emu_argparser.emu_args.builder_name = get_hostname()
  emu_argparser.emu_args.emulator_exec = emu_binary
  session_dir = os.path.join(os.path.dirname(__file__),
                             "emu_psq_logs-%s-%s" %
                             (emu_argparser.emu_args.build_id,
                              emu_argparser.emu_args.build_target))
  emu_argparser.emu_args.session_dir = session_dir
  emu_argparser.emu_args.test_dir = "psq_test"
  if not os.path.exists(emu_argparser.emu_args.session_dir):
    os.makedirs(emu_argparser.emu_args.session_dir)
  if not os.path.exists(os.path.join(session_dir, emu_argparser.emu_args.test_dir)):
    os.makedirs(os.path.join(session_dir, emu_argparser.emu_args.test_dir))


if __name__ == '__main__':
    os.environ["SHELL"] = "/bin/bash"
    # Update our local git repo to continually update codebase.
    update_git()

    emu_argparser.emu_args = emu_argparser.get_parser().parse_args()
    emu_argparser.emu_args.creation_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print 'Run Target: %s' % emu_argparser.emu_args.run_target
    print 'Branch: %s' % emu_argparser.emu_args.branch
    print 'Build Target: %s' % emu_argparser.emu_args.build_target
    print 'Build ID: %s' % emu_argparser.emu_args.build_id
    emu_dir = setup_build_environment(emu_argparser.emu_args.build_id,
                                      emu_argparser.emu_args.build_target)
    emu_binary = os.path.join(emu_dir, "emulator", "emulator")
    set_emu_args(emu_binary)
    create_test_case()
    sys.argv[1:] = emu_argparser.emu_args.unittest_args
    test_start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    test_suite = unittest.TestLoader().loadTestsFromTestCase(PsqBootTestCase)
    test_runner = emu_unittest.EmuTextTestRunner()
    test_result = test_runner.run(test_suite)
    test_end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # Upload logs to GCS (gs://emu_psq_logs/)
    upload_rc = upload_to_gs()
    output = create_json_output(emu_argparser.emu_args.creation_time,
                                test_start_time, test_end_time,
                                len(test_result.errors) + len(test_result.passes),
                                len(test_result.errors),
                                emu_argparser.emu_args.session_dir)
    # Print out the results to stdout.  This is what Swarming is looking for.
    print output
    # Delete the emulator folder artifact
    if os.path.exists(emu_dir):
      shutil.rmtree(emu_dir)
    # Delete the log folder artifact
    if os.path.exists(emu_argparser.emu_args.session_dir):
      shutil.rmtree(emu_argparser.emu_args.session_dir)
    sys.exit(0)
