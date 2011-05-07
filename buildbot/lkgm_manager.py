#!/usr/bin/python
# Copyright (c) 2011 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
A library to generate and store the manifests for cros builders to use.
"""

import logging
import os
import re
import time

from chromite.buildbot import manifest_version
from chromite.lib import cros_build_lib as cros_lib


def _SyncGitRepo(local_dir):
  """"Clone Given git repo
  Args:
    local_dir: location with repo that should be synced.
  """
  cros_lib.RunCommand(['git', 'remote', 'update'], cwd=local_dir)
  cros_lib.RunCommand(['git', 'rebase', 'origin/master'], cwd=local_dir)


class _LKGMCandidateInfo(manifest_version.VersionInfo):
  """Class to encapsualte the chrome os lkgm candidate info

  You can instantiate this class in two ways.
  1)using a version file, specifically chromeos_version.sh,
  which contains the version information.
  2) just passing in the 4 version components (major, minor, sp, patch and
    revision number),
  Args:
    version_string: Optional version string to parse rather than from a file
    ver_maj: major version
    ver_min: minor version
    ver_sp:  sp version
    ver_patch: patch version
    ver_revision: version revision
    version_file: version file location.
  """
  LKGM_RE = '(\d+\.\d+\.\d+\.\d+)(?:-rc(\d+))?'

  def __init__(self, version_string=None, version_file=None):
    self.ver_revision = None
    if version_string:
      match = re.search(self.LKGM_RE, version_string)
      assert match, 'LKGM did not re %s' % self.LKGM_RE
      super(_LKGMCandidateInfo, self).__init__(match.group(1),
                                               incr_type='patch')
      self.ver_revision = match.group(int(2))
    else:
      super(_LKGMCandidateInfo, self).__init__(version_file=version_file,
                                              incr_type='patch')
    if not self.ver_revision:
      self.ver_revision = 1

  def VersionString(self):
    """returns the full version string of the lkgm candidate"""
    return '%s.%s.%s.%s-rc%s' % (self.ver_maj, self.ver_min, self.ver_sp,
                                 self.ver_patch, self.ver_revision)

  @classmethod
  def VersionCompare(cls, version_string):
    """Useful method to return a comparable version of a LKGM string."""
    lkgm = cls(version_string)
    return map(int, [lkgm.ver_maj, lkgm.ver_min, lkgm.ver_sp, lkgm.ver_patch,
                     lkgm.ver_revision])

  def IncrementVersion(self, message, dry_run):
    """Increments the version by incrementing the revision #."""
    self.version_revision += 1
    return self.VersionString()


class LKGMManager(manifest_version.BuildSpecsManager):
  """A Class to manage lkgm candidates and their states."""
  MAX_TIMEOUT_SECONDS = 300
  SLEEP_TIMEOUT = 30
  # Wait an additional 5 minutes for any other builder.

  def __init__(self, tmp_dir, source_repo, manifest_repo, branch,
               build_name, dry_run):
    super(LKGMManager, self).__init__(tmp_dir, source_repo, manifest_repo,
                                      branch, build_name, 'patch', dry_run)
    self.compare_versions_fn = lambda s: _LKGMCandidateInfo.VersionCompare(s)

  def _LoadSpecs(self, version_info):
    """Loads the specifications from the working directory.
    Args:
      version_info: Info class for version information of cros.
    """
    super(LKGMManager, self)._LoadSpecs(version_info, 'LKGM-candidates')

  def _GetLatestLKGMCandidate(self, version_info):
    """Returns the latest lkgm candidate corresponding to the version file.
    Args:
      version_info: Info class for version information of cros.
    """
    if self.all:
      matched_lkgms = filter(
          lambda ver: ver == version_info.VersionString(), self.all)
      if matched_lkgms:
        return sorted(matched_lkgms, key=self.compare_versions_fn)

    return _LKGMCandidateInfo(version_info.VersionString)

  def CreateNextLKGMCandidate(self, version_file, retries=0):
    """Gets the version number of the next build spec to build.
      Args:
        version_file: File to use in cros when checking for cros version.
        retries: Number of retries for updating the status
      Returns:
        next_build: a string of the next build number for the builder to consume
                    or None in case of no need to build.
      Raises:
        GenerateBuildSpecException in case of failure to generate a buildspec
    """
    try:
      version_info = self._GetCurrentVersionInfo(version_file)
      self._LoadSpecs(version_info)
      lkgm_info = self._GetLatestLKGMCandidate(version_info)

      self.current_version = self._CreateNewBuildSpec(lkgm_info)
      if self.current_version:
        logging.debug('Using build spec: %s', self.current_version)
        commit_message = 'Automatic: Start %s %s' % (self.build_name,
                                                     self.current_version)
        self._SetInFlight(commit_message)

      return self.current_version

    except (cros_lib.RunCommandError,
            manifest_version.GitCommandException) as e:
      err_msg = 'Failed to generate LKGM Candidate. error: %s' % e
      logging.error(err_msg)
      raise manifest_version.GenerateBuildSpecException(err_msg)

  def GetNextLKGMCandidate(self, version_file, retries=0):
    """Gets the version number of the next build spec to build.
      Args:
        version_file: File to use in cros when checking for cros version.
        retries: Number of retries for updating the status
      Returns:
        next_build: a string of the next build number for the builder to consume
                    or None in case of no need to build.
      Raises:
        GenerateBuildSpecException in case of failure to generate a buildspec
    """
    try:
      version_info = self._GetCurrentVersionInfo(version_file)
      self._LoadSpecs(version_info)
      self.current_version = self.latest_unprocessed
      if self.current_version:
        logging.debug('Using build spec: %s', self.current_version)
        commit_message = 'Automatic: Start %s %s' % (self.build_name,
                                                     self.current_version)
        self._SetInFlight(commit_message)

      return self.current_version

    except (cros_lib.RunCommandError,
            manifest_version.GitCommandException) as e:
      err_msg = 'Failed to get next LKGM Candidate. error: %s' % e
      logging.error(err_msg)
      raise manifest_version.GenerateBuildSpecException(err_msg)

  def GetOthersStatuses(self, others):
    """Returns a build-names->status dictionary of build statuses."""
    xml_name = self.current_version + '.xml'

    # Set some default location strings.
    dir_pfx = self.current_version.DirPrefix()
    specs_for_build = os.path.join(
        self.manifest_dir, 'LKGM-candidates', 'build-name', '%{build_name}s')
    pass_file = os.path.join(specs_for_build, 'pass', dir_pfx, xml_name)
    fail_file = os.path.join(specs_for_build, 'fail', dir_pfx, xml_name)
    inflight_file = os.path.join(specs_for_build, 'inflight', dir_pfx, xml_name)

    start_time = time.time()
    other_statuses = {}
    num_complete = 0

    # Monitor the repo until all builders report in or we've waited too long.
    while (time.time() - start_time) < self.MAX_TIMEOUT_SECONDS:
      _SyncGitRepo(self.manifests_dir)
      for other in others:
        if other_statuses.get(other, None) not in ['pass', 'fail']:
          other_pass = pass_file % {'build-name': other}
          other_fail = fail_file % {'build-name': other}
          other_inflight = inflight_file % {'build-name': other}
          if os.path.lexists(other_pass):
            other_statuses[other] = 'pass'
            num_complete += 1
          elif os.path.lexists(other_fail):
            other_statuses[other] = 'fail'
            num_complete += 1
          elif os.path.lexists(other_inflight):
            other_statuses[other] = 'inflight'
          else:
            other_statuses[other] = None

      if num_complete < len(others):
        logging.info('Waiting for other builds to complete')
        time.sleep(self.SLEEP_TIMEOUT)

    if num_complete != len(others):
      logging.error('Not all builds finished before MAX_TIMEOUT reached.')

    return other_statuses

  def PromoteLKGMCandidate(self):
    """Promotes the current LKGM candidate to be a real versioned LKGM."""
    # TODO(sosa): Implement
    pass