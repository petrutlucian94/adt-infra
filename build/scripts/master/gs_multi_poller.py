# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""This PollingChangeSource polls multiple Storage URL for change revisions.

Each change is submitted to change master which triggers build steps.

Notice that the gsutil configuration (.boto file) must be setup in either the
default location (home dir) or by using the environment variables
AWS_CREDENTIAL_FILE and BOTO_CONFIG.

Example:
To poll a change from buckets path1 and path2, use -
from master import gs_multi_poller
gs_bucket = "bucket_name"
gs_path_list = ['path/to/path1', 'path/to/path2']
poller = gs_multi_poller.GSMultiPoller("poller", gs_bucket, gs_path_list, pollInterval=10800)
c['change_source'] = [poller]
"""

import os
import sys

from twisted.internet import defer
from twisted.python import log

from buildbot.changes import base
import gcs_oauth2_boto_plugin
import StringIO
from boto import boto
from twisted.python import log

class GSMultiPoller(base.PollingChangeSource):
  """Poll a Google Storage URL for change number and submit to change master."""

  compare_attrs = ['changeurl', 'pollInterval']

  # pylint runs this against the wrong buildbot version.
  # In buildbot 8.4 base.PollingChangeSource has no __init__
  # pylint: disable=W0231
  def __init__(self, name, gs_bucket, gs_path_list, pollInterval=5*60,
               project=None, branch=None, name_identifier='', category=None):
    """Initialize GSMultiPoller.

    Args:
    gs_bucket: bucket name
    gs_path_list: list of GS URLs to watch for
    pollInterval: Time (in seconds) between queries for changes.
    name_identifier: If given, used to identify if a file is important
    category: Build category to trigger (optional).
    """
    #if not changeurl.startswith('gs://'):
    #  raise Exception('GSMultiPoller changeurl must start with gs://')

    self.name = name
    self.cachepath = self.name + '.cache'
    self.gs_bucket = gs_bucket
    self.gs_path_list = gs_path_list
    self.pollInterval = pollInterval
    self.category = category
    self.prev_build  = None
    self.last_change = None
    self.project = project
    self.branch = branch
    self.name_identifier = name_identifier

    if os.path.exists(self.cachepath):
      try:
        with open(self.cachepath, "r") as f:
          self.last_change = f.readline().strip()
          log.msg("%s: Setting last_change to %s" % (self.name, self.last_change))
      except:
        self.cachepath = None
        log.msg("%s: Cache file corrupt or unwriteable; skipping and not using" % self.name)
        log.err()

  def describe(self):
    return '%s: watching %s' % (self.name, self.gs_path_list)

  def poll(self):
    log.msg('%s: polling %s' % (self.name, self.gs_path_list))
    d = defer.succeed(None)
    d.addCallback(self.find_latest_build)
    d.addCallback(self._process_changes)
    d.addErrback(self._finished_failure)
    return d

  def _finished_failure(self, res):
    log.msg('%s: poll failed: %s. URL: %s' % (self.name, res, self.gs_path_list))

  # return the latest complete build
  def find_latest_build_internal(self, img_index):
    bucket = boto.storage_uri(self.gs_bucket, 'gs').get_bucket()
    build_version = None
    last_modified_file = None
    for obj in bucket.list(self.gs_path_list[img_index]):
      if self.name_identifier in obj.name:
        # file path: "builds/[builder_name]/[build_version]/[random_hash]/[binary].zip"
        build_version = max(build_version, obj.name.split('/')[2])
        last_modified_file = obj.name
    log.msg('%s: last_change %s, new_last_change %s' % (self.name, self.last_change, build_version))
    if build_version == None or build_version <= self.last_change:
      return None
    file_list = []
    for path in self.gs_path_list:
      objs = bucket.list(path + build_version + '/')
      count = len(list(objs))
      log.msg("%s: search %s, file count %d" % (self.name, path + build_version, count))
      if count == 0:
        log.msg("%s: Build incomplete, couldn't find %s" % (self.name, path + build_version))
        return None
      for obj in objs:
        if self.name_identifier in obj.name:
          file_list.append(obj.name)
    return file_list

  #some caller still passes two parameters, keep _no_use
  def find_latest_build(self, _no_use):
    log.msg('%s: last_change %s' % (self.name, self.last_change))
    for img_index in range(len(self.gs_path_list)):
      file_list = self.find_latest_build_internal(img_index)
      if file_list != None:
        return file_list;
    return None;

  def _update_last_rev(self, new_revision):
    log.msg("%s: last revision changed from %s to %s" % (self.name, self.last_change, new_revision))
    self.prev_build = self.last_change
    self.last_change = new_revision
    if self.cachepath:
      with open(self.cachepath, "w") as f:
          f.write("%s\n" % self.last_change)

  def _process_changes(self, file_list):
    if file_list is not None:
      parsed_revision = file_list[0].split('/')[2]
      self._update_last_rev(parsed_revision)
      dst_file_list = []
      for file in file_list:
        gs_full_path = 'gs://' + self.gs_bucket + '/' + file
        dst_file_list.append(gs_full_path)

      props={'file_list': ','.join(dst_file_list),
             'prev_build': self.prev_build}
      self.master.addChange(who=self.name,
                            revision=parsed_revision,
                            files=dst_file_list,
                            project=self.project,
                            branch=self.branch,
                            comments='comment',
                            properties=props,
                            category=self.category)
