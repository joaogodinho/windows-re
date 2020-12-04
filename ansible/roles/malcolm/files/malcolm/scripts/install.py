#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2020 Battelle Energy Alliance, LLC.  All rights reserved.

from __future__ import print_function

import argparse
import datetime
import fileinput
import getpass
import glob
import json
import os
import platform
import pprint
import math
import re
import shutil
import sys
import tarfile
import tempfile
import time

try:
  from pwd import getpwuid
except ImportError:
  getpwuid = None
from collections import defaultdict, namedtuple

from malcolm_common import *

###################################################################################################
DOCKER_COMPOSE_INSTALL_VERSION="1.27.4"

DEB_GPG_KEY_FINGERPRINT = '0EBFCD88' # used to verify GPG key for Docker Debian repository

MAC_BREW_DOCKER_PACKAGE = 'docker-edge'
MAC_BREW_DOCKER_SETTINGS = '/Users/{}/Library/Group Containers/group.com.docker/settings.json'

###################################################################################################
ScriptName = os.path.basename(__file__)
origPath = os.getcwd()

###################################################################################################
args = None
PY3 = (sys.version_info.major >= 3)

###################################################################################################
try:
  FileNotFoundError
except NameError:
  FileNotFoundError = IOError

###################################################################################################
# get interactive user response to Y/N question
def InstallerYesOrNo(question, default=None, forceInteraction=False):
  global args
  return YesOrNo(question, default=default, forceInteraction=forceInteraction, acceptDefault=args.acceptDefaults)

###################################################################################################
# get interactive user response
def InstallerAskForString(question, default=None, forceInteraction=False):
  global args
  return AskForString(question, default=default, forceInteraction=forceInteraction, acceptDefault=args.acceptDefaults)

###################################################################################################
class Installer(object):

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def __init__(self, debug=False, configOnly=False):
    self.debug = debug
    self.configOnly = configOnly

    self.platform = platform.system()
    self.scriptUser = getpass.getuser()

    self.checkPackageCmds = []
    self.installPackageCmds = []
    self.requiredPackages = []

    self.pipCmd = 'pip3' if PY3 else 'pip2'
    if not Which(self.pipCmd, debug=self.debug): self.pipCmd = 'pip'

    self.tempDirName = tempfile.mkdtemp()

    self.totalMemoryGigs = 0.0
    self.totalCores = 0

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def __del__(self):
    shutil.rmtree(self.tempDirName, ignore_errors=True)

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def run_process(self, command, stdout=True, stderr=True, stdin=None, privileged=False, retry=0, retrySleepSec=5):

    # if privileged, put the sudo command at the beginning of the command
    if privileged and (len(self.sudoCmd) > 0):
      command = self.sudoCmd + command

    return run_process(command, stdout=stdout, stderr=stderr, stdin=stdin, retry=retry, retrySleepSec=retrySleepSec, debug=self.debug)

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def package_is_installed(self, package):
    result = False
    for cmd in self.checkPackageCmds:
      ecode, out = self.run_process(cmd + [package])
      if (ecode == 0):
        result = True
        break
    return result

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def install_package(self, packages):
    result = False
    pkgs = []

    for package in packages:
      if not self.package_is_installed(package):
        pkgs.append(package)

    if (len(pkgs) > 0):
      for cmd in self.installPackageCmds:
        ecode, out = self.run_process(cmd + pkgs, privileged=True)
        if (ecode == 0):
          result = True
          break
    else:
      result = True

    return result

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def install_required_packages(self):
    if (len(self.requiredPackages) > 0): eprint("Installing required packages: {}".format(self.requiredPackages))
    return self.install_package(self.requiredPackages)

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def install_docker_images(self, docker_image_file):
    result = False
    if docker_image_file and os.path.isfile(docker_image_file) and InstallerYesOrNo('Load Malcolm Docker images from {}'.format(docker_image_file), default=True, forceInteraction=True):
      ecode, out = self.run_process(['docker', 'load', '-q', '-i', docker_image_file], privileged=True)
      if (ecode == 0):
        result = True
      else:
        eprint("Loading Malcolm Docker images failed: {}".format(out))
    return result

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def install_malcolm_files(self, malcolm_install_file):
    result = False
    installPath = None
    if malcolm_install_file and os.path.isfile(malcolm_install_file) and InstallerYesOrNo('Extract Malcolm runtime files from {}'.format(malcolm_install_file), default=True, forceInteraction=True):

      # determine and create destination path for installation
      while True:
        defaultPath = os.path.join(origPath, 'malcolm')
        installPath = InstallerAskForString('Enter installation path for Malcolm [{}]'.format(defaultPath), default=defaultPath, forceInteraction=True)
        if (len(installPath) == 0): installPath = defaultPath
        if os.path.isdir(installPath):
          eprint("{} already exists, please specify a different installation path".format(installPath))
        else:
          try:
            os.makedirs(installPath)
          except:
            pass
          if os.path.isdir(installPath):
            break
          else:
            eprint("Failed to create {}, please specify a different installation path".format(installPath))

      # extract runtime files
      if installPath and os.path.isdir(installPath):
        if self.debug:
          eprint("Created {} for Malcolm runtime files".format(installPath))
        tar = tarfile.open(malcolm_install_file)
        try:
          if PY3:
            tar.extractall(path=installPath, numeric_owner=True)
          else:
            tar.extractall(path=installPath)
        finally:
          tar.close()

        # .tar.gz normally will contain an intermediate subdirectory. if so, move files back one level
        childDir = glob.glob('{}/*/'.format(installPath))
        if (len(childDir) == 1) and os.path.isdir(childDir[0]):
          if self.debug:
            eprint("{} only contains {}".format(installPath, childDir[0]))
          for f in os.listdir(childDir[0]):
            shutil.move(os.path.join(childDir[0], f), installPath)
          shutil.rmtree(childDir[0], ignore_errors=True)

        # verify the installation worked
        if os.path.isfile(os.path.join(installPath, "docker-compose.yml")):
          eprint("Malcolm runtime files extracted to {}".format(installPath))
          result = True
          with open(os.path.join(installPath, "install_source.txt"), 'w') as f:
            f.write('{} (installed {})\n'.format(os.path.basename(malcolm_install_file), str(datetime.datetime.now())))
        else:
          eprint("Malcolm install file extracted to {}, but missing runtime files?".format(installPath))

    return result, installPath

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def tweak_malcolm_runtime(self, malcolm_install_path, expose_logstash_default=False, restart_mode_default=False):
    global args

    if not args.configFile:
      # get a list of all of the docker-compose files
      composeFiles = glob.glob(os.path.join(malcolm_install_path, 'docker-compose*.yml'))

    elif os.path.isfile(args.configFile):
      # single docker-compose file explicitly specified
      composeFiles = [os.path.realpath(args.configFile)]
      malcolm_install_path = os.path.dirname(composeFiles[0])

    # figure out what UID/GID to run non-rood processes under docker as
    puid = '1000'
    pgid = '1000'
    try:
      if (self.platform == PLATFORM_LINUX):
        puid = str(os.getuid())
        pgid = str(os.getgid())
        if (puid == '0') or (pgid == '0'):
          raise Exception('it is preferrable not to run Malcolm as root, prompting for UID/GID instead')
    except:
      puid = '1000'
      pgid = '1000'

    while (not puid.isdigit()) or (not pgid.isdigit()) or (not InstallerYesOrNo('Malcolm processes will run as UID {} and GID {}. Is this OK?'.format(puid, pgid), default=True)):
      puid = InstallerAskForString('Enter user ID (UID) for running non-root Malcolm processes')
      pgid = InstallerAskForString('Enter group ID (GID) for running non-root Malcolm processes')

    # guestimate how much memory we should use based on total system memory

    if self.debug:
      eprint("{} contains {}, system memory is {} GiB".format(malcolm_install_path, composeFiles, self.totalMemoryGigs))

    if self.totalMemoryGigs >= 63.0:
      esMemory = '30g'
      lsMemory = '6g'
    elif self.totalMemoryGigs >= 31.0:
      esMemory = '21g'
      lsMemory = '3500m'
    elif self.totalMemoryGigs >= 15.0:
      esMemory = '10g'
      lsMemory = '3g'
    elif self.totalMemoryGigs >= 11.0:
      esMemory = '6g'
      lsMemory = '2500m'
    elif self.totalMemoryGigs >= 7.0:
      eprint("Detected only {} GiB of memory; performance will be suboptimal".format(self.totalMemoryGigs))
      esMemory = '4g'
      lsMemory = '2500m'
    elif self.totalMemoryGigs > 0.0:
      eprint("Detected only {} GiB of memory; performance will be suboptimal".format(self.totalMemoryGigs))
      esMemory = '3500m'
      lsMemory = '2g'
    else:
      eprint("Failed to determine system memory size, using defaults; performance may be suboptimal")
      esMemory = '8g'
      lsMemory = '3g'

    while not InstallerYesOrNo('Setting {} for Elasticsearch and {} for Logstash. Is this OK?'.format(esMemory, lsMemory), default=True):
      esMemory = InstallerAskForString('Enter memory for Elasticsearch (e.g., 16g, 9500m, etc.)')
      lsMemory = InstallerAskForString('Enter memory for LogStash (e.g., 4g, 2500m, etc.)')

    restartMode = None
    allowedRestartModes = ('no', 'on-failure', 'always', 'unless-stopped')
    if InstallerYesOrNo('Restart Malcolm upon system or Docker daemon restart?', default=restart_mode_default):
      while restartMode not in allowedRestartModes:
        restartMode = InstallerAskForString('Select Malcolm restart behavior {}'.format(allowedRestartModes), default='unless-stopped')
    else:
      restartMode = 'no'
    if (restartMode == 'no'): restartMode = '"no"'

    ldapStartTLS = False
    ldapServerType = 'winldap'
    useBasicAuth = not InstallerYesOrNo('Authenticate against Lightweight Directory Access Protocol (LDAP) server?', default=False)
    if not useBasicAuth:
      allowedLdapModes = ('winldap', 'openldap')
      ldapServerType = None
      while ldapServerType not in allowedLdapModes:
        ldapServerType = InstallerAskForString('Select LDAP server compatibility type {}'.format(allowedLdapModes), default='winldap')
      ldapStartTLS = InstallerYesOrNo('Use StartTLS for LDAP connection security?', default=True)
      try:
        with open(os.path.join(os.path.realpath(os.path.join(ScriptPath, "..")), ".ldap_config_defaults"), "w") as ldapDefaultsFile:
          print("LDAP_SERVER_TYPE='{}'".format(ldapServerType), file=ldapDefaultsFile)
          print("LDAP_PROTO='{}'".format('ldap://' if useBasicAuth or ldapStartTLS else 'ldaps://'), file=ldapDefaultsFile)
          print("LDAP_PORT='{}'".format(3268 if ldapStartTLS else 3269), file=ldapDefaultsFile)
      except:
        pass

    curatorSnapshots = InstallerYesOrNo('Create daily snapshots (backups) of Elasticsearch indices?', default=False)
    curatorSnapshotDir = './elasticsearch-backup'
    if curatorSnapshots:
      if not InstallerYesOrNo('Store snapshots locally in {}?'.format(os.path.join(malcolm_install_path, 'elasticsearch-backup')), default=True):
        while True:
          curatorSnapshotDir = InstallerAskForString('Enter Elasticsearch index snapshot directory')
          if (len(curatorSnapshotDir) > 1) and os.path.isdir(curatorSnapshotDir):
            curatorSnapshotDir = os.path.realpath(curatorSnapshotDir)
            break

    curatorCloseUnits = 'years'
    curatorCloseCount = '5'
    if InstallerYesOrNo('Periodically close old Elasticsearch indices?', default=False):
      while not InstallerYesOrNo('Indices older than {} {} will be periodically closed. Is this OK?'.format(curatorCloseCount, curatorCloseUnits), default=True):
        while True:
          curatorPeriod = InstallerAskForString('Enter index close threshold (e.g., 90 days, 2 years, etc.)').lower().split()
          if (len(curatorPeriod) == 2) and (not curatorPeriod[1].endswith('s')):
            curatorPeriod[1] += 's'
          if ((len(curatorPeriod) == 2) and
              curatorPeriod[0].isdigit() and
              (curatorPeriod[1] in ('seconds', 'minutes', 'hours', 'days', 'weeks', 'months', 'years'))):
            curatorCloseUnits = curatorPeriod[1]
            curatorCloseCount = curatorPeriod[0]
            break
    else:
      curatorCloseUnits = 'years'
      curatorCloseCount = '99'

    curatorDeleteUnits = 'years'
    curatorDeleteCount = '10'
    if InstallerYesOrNo('Periodically delete old Elasticsearch indices?', default=False):
      while not InstallerYesOrNo('Indices older than {} {} will be periodically deleted. Is this OK?'.format(curatorDeleteCount, curatorDeleteUnits), default=True):
        while True:
          curatorPeriod = InstallerAskForString('Enter index delete threshold (e.g., 90 days, 2 years, etc.)').lower().split()
          if (len(curatorPeriod) == 2) and (not curatorPeriod[1].endswith('s')):
            curatorPeriod[1] += 's'
          if ((len(curatorPeriod) == 2) and
              curatorPeriod[0].isdigit() and
              (curatorPeriod[1] in ('seconds', 'minutes', 'hours', 'days', 'weeks', 'months', 'years'))):
            curatorDeleteUnits = curatorPeriod[1]
            curatorDeleteCount = curatorPeriod[0]
            break
    else:
      curatorDeleteUnits = 'years'
      curatorDeleteCount = '99'

    curatorDeleteOverGigs = '10000'
    if InstallerYesOrNo('Periodically delete the oldest Elasticsearch indices when the database exceeds a certain size?', default=False):
      while not InstallerYesOrNo('Indices will be deleted when the database exceeds {} gigabytes. Is this OK?'.format(curatorDeleteOverGigs), default=True):
        while True:
          curatorSize = InstallerAskForString('Enter index threshold in gigabytes')
          if (len(curatorSize) > 0) and curatorSize.isdigit():
            curatorDeleteOverGigs = curatorSize
            break
    else:
      curatorDeleteOverGigs = '9000000'

    autoZeek = InstallerYesOrNo('Automatically analyze all PCAP files with Zeek?', default=True)
    reverseDns = InstallerYesOrNo('Perform reverse DNS lookup locally for source and destination IP addresses in Zeek logs?', default=False)
    autoOui = InstallerYesOrNo('Perform hardware vendor OUI lookups for MAC addresses?', default=True)
    autoFreq = InstallerYesOrNo('Perform string randomness scoring on some fields?', default=True)
    logstashOpen = InstallerYesOrNo('Expose Logstash port to external hosts?', default=expose_logstash_default)
    logstashSsl = logstashOpen and InstallerYesOrNo('Should Logstash require SSL for Zeek logs? (Note: This requires the forwarder to be similarly configured and a corresponding copy of the client SSL files.)', default=True)
    externalEsForward = InstallerYesOrNo('Forward Logstash logs to external Elasticstack instance?', default=False)
    if externalEsForward:
      externalEsHost = InstallerAskForString('Enter external Elasticstack host:port (e.g., 10.0.0.123:9200)')
      externalEsSsl = InstallerYesOrNo('Connect to "{}" using SSL?'.format(externalEsHost), default=True)
      externalEsSslVerify = externalEsSsl and InstallerYesOrNo('Require SSL certificate validation for communication with "{}"?'.format(externalEsHost), default=False)
    else:
      externalEsHost = ""
      externalEsSsl = False
      externalEsSslVerify = False

    # input file extraction parameters
    allowedFileCarveModes = ('none', 'known', 'mapped', 'all', 'interesting')
    allowedFilePreserveModes = ('quarantined', 'all', 'none')

    fileCarveModeUser = None
    fileCarveMode = None
    filePreserveMode = None
    vtotApiKey = '0'
    yaraScan = False
    capaScan = False
    clamAvScan = False
    clamAvUpdate = False

    if InstallerYesOrNo('Enable file extraction with Zeek?', default=False):
      while fileCarveMode not in allowedFileCarveModes:
        fileCarveMode = InstallerAskForString('Select file extraction behavior {}'.format(allowedFileCarveModes), default=allowedFileCarveModes[0])
      while filePreserveMode not in allowedFilePreserveModes:
        filePreserveMode = InstallerAskForString('Select file preservation behavior {}'.format(allowedFilePreserveModes), default=allowedFilePreserveModes[0])
      if fileCarveMode is not None:
        if InstallerYesOrNo('Scan extracted files with ClamAV?', default=False):
          clamAvScan = True
          clamAvUpdate = InstallerYesOrNo('Download updated ClamAV virus signatures periodically?', default=True)
        if InstallerYesOrNo('Scan extracted files with Yara?', default=False):
          yaraScan = True
        if InstallerYesOrNo('Scan extracted PE files with Capa?', default=False):
          capaScan = True
        if InstallerYesOrNo('Lookup extracted file hashes with VirusTotal?', default=False):
          while (len(vtotApiKey) <= 1):
            vtotApiKey = InstallerAskForString('Enter VirusTotal API key')

    if fileCarveMode not in allowedFileCarveModes:
      fileCarveMode = allowedFileCarveModes[0]
    if filePreserveMode not in allowedFileCarveModes:
      filePreserveMode = allowedFilePreserveModes[0]
    if (vtotApiKey is None) or (len(vtotApiKey) <= 1):
      vtotApiKey = '0'

    # input packet capture parameters
    pcapNetSniff = False
    pcapTcpDump = False
    pcapIface = 'lo'
    if InstallerYesOrNo('Should Malcolm capture network traffic to PCAP files?', default=False):
      pcapIface = ''
      while (len(pcapIface) <= 0):
        pcapIface = InstallerAskForString('Specify capture interface(s) (comma-separated)')
      pcapNetSniff = InstallerYesOrNo('Capture packets using netsniff-ng?', default=True)
      pcapTcpDump = InstallerYesOrNo('Capture packets using tcpdump?', default=(not pcapNetSniff))

    # modify specified values in-place in docker-compose files
    for composeFile in composeFiles:
      # save off owner of original files
      composeFileStat = os.stat(composeFile)
      origUid, origGuid = composeFileStat[4], composeFileStat[5]
      composeFileHandle = fileinput.FileInput(composeFile, inplace=True, backup=None)
      try:
        servicesSectionFound = False
        serviceIndent = None
        currentService = None

        for line in composeFileHandle:
          line = line.rstrip("\n")
          skipLine = False

          # it would be cleaner to use something like PyYAML to do this, but I want to have as few dependencies
          # as possible so we're going to do it janky instead

          # determine indentation for each service section (assumes YML file is consistently indented)
          if (not servicesSectionFound) and line.lower().startswith('services:'):
            servicesSectionFound = True
          elif servicesSectionFound and (serviceIndent is None):
            indentMatch = re.search(r'^(\s+)\S+\s*:\s*$', line)
            if indentMatch is not None:
              serviceIndent = indentMatch.group(1)

          # determine which service we're currently processing in the YML file
          serviceStartLine = False
          if servicesSectionFound and (serviceIndent is not None):
            serviceMatch = re.search(r'^{}(\S+)\s*:\s*$'.format(serviceIndent), line)
            if serviceMatch is not None:
              currentService = serviceMatch.group(1).lower()
              serviceStartLine = True

          if (currentService is not None) and (restartMode is not None) and re.match(r'^\s*restart\s*:.*$', line):
            # elasticsearch backup directory
            line = "{}restart: {}".format(serviceIndent * 2, restartMode)
          elif 'PUID' in line:
            # process UID
            line = re.sub(r'(PUID\s*:\s*)(\S+)', r"\g<1>{}".format(puid), line)
          elif 'PGID' in line:
            # process GID
            line = re.sub(r'(PGID\s*:\s*)(\S+)', r"\g<1>{}".format(pgid), line)
          elif 'NGINX_BASIC_AUTH' in line:
            # basic (useBasicAuth=true) vs ldap (useBasicAuth=false)
            line = re.sub(r'(NGINX_BASIC_AUTH\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if useBasicAuth else "'false'"), line)
          elif 'NGINX_LDAP_TLS_STUNNEL_PROTOCOL' in line:
            # ldap server type (windldap|openldap) for StartTLS
            line = re.sub(r'(NGINX_LDAP_TLS_STUNNEL_PROTOCOL\s*:\s*)(\S+)', r"\g<1>'{}'".format(ldapServerType), line)
          elif 'NGINX_LDAP_TLS_STUNNEL' in line:
            # StartTLS vs. ldap:// or ldaps://
            line = re.sub(r'(NGINX_LDAP_TLS_STUNNEL\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if ((not useBasicAuth) and ldapStartTLS) else "'false'"), line)
          elif 'ZEEK_EXTRACTOR_MODE' in line:
            # zeek file extraction mode
            line = re.sub(r'(ZEEK_EXTRACTOR_MODE\s*:\s*)(\S+)', r"\g<1>'{}'".format(fileCarveMode), line)
          elif 'EXTRACTED_FILE_PRESERVATION' in line:
            # zeek file preservation mode
            line = re.sub(r'(EXTRACTED_FILE_PRESERVATION\s*:\s*)(\S+)', r"\g<1>'{}'".format(filePreserveMode), line)
          elif 'VTOT_API2_KEY' in line:
            # virustotal API key
            line = re.sub(r'(VTOT_API2_KEY\s*:\s*)(\S+)', r"\g<1>'{}'".format(vtotApiKey), line)
          elif 'EXTRACTED_FILE_ENABLE_YARA' in line:
            # file scanning via yara
            line = re.sub(r'(EXTRACTED_FILE_ENABLE_YARA\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if yaraScan else "'false'"), line)
          elif 'EXTRACTED_FILE_ENABLE_CAPA' in line:
            # PE file scanning via capa
            line = re.sub(r'(EXTRACTED_FILE_ENABLE_CAPA\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if capaScan else "'false'"), line)
          elif 'EXTRACTED_FILE_ENABLE_CLAMAV' in line:
            # file scanning via clamav
            line = re.sub(r'(EXTRACTED_FILE_ENABLE_CLAMAV\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if clamAvScan else "'false'"), line)
          elif 'EXTRACTED_FILE_ENABLE_FRESHCLAM' in line:
            # clamav updates via freshclam
            line = re.sub(r'(EXTRACTED_FILE_ENABLE_FRESHCLAM\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if clamAvUpdate else "'false'"), line)
          elif 'PCAP_ENABLE_NETSNIFF' in line:
            # capture pcaps via netsniff-ng
            line = re.sub(r'(PCAP_ENABLE_NETSNIFF\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if pcapNetSniff else "'false'"), line)
          elif 'PCAP_ENABLE_TCPDUMP' in line:
            # capture pcaps via tcpdump
            line = re.sub(r'(PCAP_ENABLE_TCPDUMP\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if pcapTcpDump else "'false'"), line)
          elif 'PCAP_IFACE' in line:
            # capture interface(s)
            line = re.sub(r'(PCAP_IFACE\s*:\s*)(\S+)', r"\g<1>'{}'".format(pcapIface), line)
          elif 'ES_JAVA_OPTS' in line:
            # elasticsearch memory allowance
            line = re.sub(r'(-Xm[sx])(\w+)', r'\g<1>{}'.format(esMemory), line)
          elif 'LS_JAVA_OPTS' in line:
            # logstash memory allowance
            line = re.sub(r'(-Xm[sx])(\w+)', r'\g<1>{}'.format(lsMemory), line)
          elif 'ZEEK_AUTO_ANALYZE_PCAP_FILES' in line:
            # automatic pcap analysis with Zeek
            line = re.sub(r'(ZEEK_AUTO_ANALYZE_PCAP_FILES\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if autoZeek else "'false'"), line)
          elif 'LOGSTASH_REVERSE_DNS' in line:
            # automatic local reverse dns lookup
            line = re.sub(r'(LOGSTASH_REVERSE_DNS\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if reverseDns else "'false'"), line)
          elif 'LOGSTASH_OUI_LOOKUP' in line:
            # automatic MAC OUI lookup
            line = re.sub(r'(LOGSTASH_OUI_LOOKUP\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if autoOui else "'false'"), line)
          elif 'FREQ_LOOKUP' in line:
            # freq.py string randomness calculations
            line = re.sub(r'(FREQ_LOOKUP\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if autoFreq else "'false'"), line)
          elif 'BEATS_SSL' in line:
            # enable/disable beats SSL
            line = re.sub(r'(BEATS_SSL\s*:\s*)(\S+)', r'\g<1>{}'.format("'true'" if logstashOpen and logstashSsl else "'false'"), line)
          elif 'CURATOR_SNAPSHOT_DISABLED' in line:
            # set count for index curation snapshot enable/disable
            line = re.sub(r'(CURATOR_SNAPSHOT_DISABLED\s*:\s*)(\S+)', r'\g<1>{}'.format("'False'" if curatorSnapshots else "'True'"), line)
          elif (currentService == 'elasticsearch') and re.match(r'^\s*-.+:/opt/elasticsearch/backup(:.+)?\s*$', line) and (curatorSnapshotDir is not None) and os.path.isdir(curatorSnapshotDir):
            # elasticsearch backup directory
            volumeParts = line.strip().lstrip('-').lstrip().split(':')
            volumeParts[0] = curatorSnapshotDir
            line = "{}- {}".format(serviceIndent * 3, ':'.join(volumeParts))
          elif 'CURATOR_CLOSE_COUNT' in line:
            # set count for index curation close age
            line = re.sub(r'(CURATOR_CLOSE_COUNT\s*:\s*)(\S+)', r'\g<1>{}'.format(curatorCloseCount), line)
          elif 'CURATOR_CLOSE_UNITS' in line:
            # set units for index curation close age
            line = re.sub(r'(CURATOR_CLOSE_UNITS\s*:\s*)(\S+)', r'\g<1>{}'.format(curatorCloseUnits), line)
          elif 'CURATOR_DELETE_COUNT' in line:
            # set count for index curation delete age
            line = re.sub(r'(CURATOR_DELETE_COUNT\s*:\s*)(\S+)', r'\g<1>{}'.format(curatorDeleteCount), line)
          elif 'CURATOR_DELETE_UNITS' in line:
            # set units for index curation delete age
            line = re.sub(r'(CURATOR_DELETE_UNITS\s*:\s*)(\S+)', r'\g<1>{}'.format(curatorDeleteUnits), line)
          elif 'CURATOR_DELETE_GIGS' in line:
            # set size for index deletion threshold
            line = re.sub(r'(CURATOR_DELETE_GIGS\s*:\s*)(\S+)', r'\g<1>{}'.format(curatorDeleteOverGigs), line)
          elif 'ES_EXTERNAL_HOSTS' in line:
            # enable/disable forwarding Logstash to external Elasticsearch instance
            line = re.sub(r'(#\s*)?(ES_EXTERNAL_HOSTS\s*:\s*)(\S+)', r"\g<2>'{}'".format(externalEsHost), line)
          elif 'ES_EXTERNAL_SSL_CERTIFICATE_VERIFICATION' in line:
            # enable/disable SSL certificate verification for external Elasticsearch instance
            line = re.sub(r'(#\s*)?(ES_EXTERNAL_SSL_CERTIFICATE_VERIFICATION\s*:\s*)(\S+)', r'\g<2>{}'.format("'true'" if externalEsSsl and externalEsSslVerify else "'false'"), line)
          elif 'ES_EXTERNAL_SSL' in line:
            # enable/disable SSL certificate verification for external Elasticsearch instance
            line = re.sub(r'(#\s*)?(ES_EXTERNAL_SSL\s*:\s*)(\S+)', r'\g<2>{}'.format("'true'" if externalEsSsl else "'false'"), line)
          elif (len(externalEsHost) > 0) and re.match(r'^\s*#.+:/usr/share/logstash/config/logstash.keystore(:r[ow])?\s*$', line):
            # make sure logstash.keystore is shared (volume mapping is not commented out)
            leadingSpaces = len(line) - len(line.lstrip())
            if leadingSpaces <= 0: leadingSpaces = 6
            line = "{}{}".format(' ' * leadingSpaces, line.lstrip().lstrip('#').lstrip())
          elif logstashOpen and serviceStartLine and (currentService == 'logstash'):
            # exposing logstash port 5044 to the world
            print(line)
            line = "{}ports:".format(serviceIndent * 2)
            print(line)
            line = "{}- 0.0.0.0:5044:5044".format(serviceIndent * 3)
          elif (not serviceStartLine) and (currentService == 'logstash') and re.match(r'^({}ports:|{}-.*5044:5044)\s*$'.format(serviceIndent * 2, serviceIndent * 3), line):
            # remove previous/leftover/duplicate exposing logstash port 5044 to the world
            skipLine = True

          if not skipLine: print(line)

      finally:
        composeFileHandle.close()
        # restore ownership
        os.chown(composeFile, origUid, origGuid)

    # if the Malcolm dir is owned by root, see if they want to reassign ownership to a non-root user
    if (((self.platform == PLATFORM_LINUX) or (self.platform == PLATFORM_MAC)) and
        (self.scriptUser == "root") and (getpwuid(os.stat(malcolm_install_path).st_uid).pw_name == self.scriptUser) and
        InstallerYesOrNo('Set ownership of {} to an account other than {}?'.format(malcolm_install_path, self.scriptUser), default=True, forceInteraction=True)):
      tmpUser = ''
      while (len(tmpUser) == 0):
        tmpUser = InstallerAskForString('Enter user account').strip()
      err, out = self.run_process(['id', '-g', '-n', tmpUser], stderr=True)
      if (err == 0) and (len(out) > 0) and (len(out[0]) > 0):
        tmpUser = "{}:{}".format(tmpUser, out[0])
      err, out = self.run_process(['chown', '-R', tmpUser, malcolm_install_path], stderr=True)
      if (err == 0):
        if self.debug: eprint("Changing ownership of {} to {} succeeded".format(malcolm_install_path, tmpUser))
      else:
        eprint("Changing ownership of {} to {} failed: {}".format(malcolm_install_path, tmpUser, out))


###################################################################################################
class LinuxInstaller(Installer):

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def __init__(self, debug=False, configOnly=False):
    if PY3:
      super().__init__(debug, configOnly)
    else:
      super(LinuxInstaller, self).__init__(debug, configOnly)

    self.distro = None
    self.codename = None
    self.release = None

    # determine the distro (e.g., ubuntu) and code name (e.g., bionic) if applicable

    # check /etc/os-release values first
    if os.path.isfile('/etc/os-release'):
      osInfo = dict()

      with open("/etc/os-release", 'r') as f:
        for line in f:
          try:
            k, v = line.rstrip().split("=")
            osInfo[k] = v.strip('"')
          except:
            pass

      if ('NAME' in osInfo) and (len(osInfo['NAME']) > 0):
        distro = osInfo['NAME'].lower().split()[0]

      if ('VERSION_CODENAME' in osInfo) and (len(osInfo['VERSION_CODENAME']) > 0):
        codename = osInfo['VERSION_CODENAME'].lower().split()[0]

      if ('VERSION_ID' in osInfo) and (len(osInfo['VERSION_ID']) > 0):
        release = osInfo['VERSION_ID'].lower().split()[0]

    # try lsb_release next
    if (self.distro is None):
      err, out = self.run_process(['lsb_release', '-is'], stderr=False)
      if (err == 0) and (len(out) > 0):
        self.distro = out[0].lower()

    if (self.codename is None):
      err, out = self.run_process(['lsb_release', '-cs'], stderr=False)
      if (err == 0) and (len(out) > 0):
        self.codename = out[0].lower()

    if (self.release is None):
      err, out = self.run_process(['lsb_release', '-rs'], stderr=False)
      if (err == 0) and (len(out) > 0):
        self.release = out[0].lower()

    # try release-specific files
    if (self.distro is None):
      if os.path.isfile('/etc/centos-release'):
        distroFile = '/etc/centos-release'
      if os.path.isfile('/etc/redhat-release'):
        distroFile = '/etc/redhat-release'
      elif os.path.isfile('/etc/issue'):
        distroFile = '/etc/issue'
      else:
        distroFile = None
      if (distroFile is not None):
        with open(distroFile, 'r') as f:
          distroVals = f.read().lower().split()
          distroNums = [x for x in distroVals if x[0].isdigit()]
          self.distro = distroVals[0]
          if (self.release is None) and (len(distroNums) > 0):
            self.release = distroNums[0]

    if (self.distro is None):
      self.distro = "linux"

    if self.debug:
      eprint("distro: {}{}{}".format(self.distro,
                                     " {}".format(self.codename) if self.codename else "",
                                     " {}".format(self.release) if self.release else ""))

    if not self.codename: self.codename = self.distro

    # determine packages required by Malcolm itself (not docker, those will be done later)
    if (self.distro == PLATFORM_LINUX_UBUNTU) or (self.distro == PLATFORM_LINUX_DEBIAN):
      self.requiredPackages.extend(['apache2-utils', 'make', 'openssl'])
    elif (self.distro == PLATFORM_LINUX_FEDORA) or (self.distro == PLATFORM_LINUX_CENTOS):
      self.requiredPackages.extend(['httpd-tools', 'make', 'openssl'])

    # on Linux this script requires root, or sudo, unless we're in local configuration-only mode
    if os.getuid() == 0:
      self.scriptUser = "root"
      self.sudoCmd = []
    else:
      self.sudoCmd = ["sudo", "-n"]
      err, out = self.run_process(['whoami'], privileged=True)
      if ((err != 0) or (len(out) == 0) or (out[0] != 'root')) and (not self.configOnly):
        raise Exception('{} must be run as root, or {} must be available'.format(ScriptName, self.sudoCmd))

    # determine command to use to query if a package is installed
    if Which('dpkg', debug=self.debug):
      os.environ["DEBIAN_FRONTEND"] = "noninteractive"
      self.checkPackageCmds.append(['dpkg', '-s'])
    elif Which('rpm', debug=self.debug):
      self.checkPackageCmds.append(['rpm', '-q'])
    elif Which('dnf', debug=self.debug):
      self.checkPackageCmds.append(['dnf', 'list', 'installed'])
    elif Which('yum', debug=self.debug):
      self.checkPackageCmds.append(['yum', 'list', 'installed'])

    # determine command to install a package from the distro's repos
    if Which('apt-get', debug=self.debug):
      self.installPackageCmds.append(['apt-get', 'install', '-y', '-qq'])
    elif Which('apt', debug=self.debug):
      self.installPackageCmds.append(['apt', 'install', '-y', '-qq'])
    elif Which('dnf', debug=self.debug):
      self.installPackageCmds.append(['dnf', '-y', 'install', '--nobest'])
    elif Which('yum', debug=self.debug):
      self.installPackageCmds.append(['yum', '-y', 'install'])

    # determine total system memory
    try:
      totalMemBytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
      self.totalMemoryGigs = math.ceil(totalMemBytes/(1024.**3))
    except:
      self.totalMemoryGigs = 0.0

    # determine total system memory a different way if the first way didn't work
    if (self.totalMemoryGigs <= 0.0):
      err, out = self.run_process(['awk', '/MemTotal/ { printf "%.0f \\n", $2 }', '/proc/meminfo'])
      if (err == 0) and (len(out) > 0):
        totalMemKiloBytes = int(out[0])
        self.totalMemoryGigs = math.ceil(totalMemKiloBytes/(1024.**2))

    # determine total system CPU cores
    try:
      self.totalCores = os.sysconf('SC_NPROCESSORS_ONLN')
    except:
      self.totalCores = 0

    # determine total system CPU cores a different way if the first way didn't work
    if (self.totalCores <= 0):
      err, out = self.run_process(['grep', '-c', '^processor', '/proc/cpuinfo'])
      if (err == 0) and (len(out) > 0):
        self.totalCores = int(out[0])

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def install_docker(self):
    result = False

    # first see if docker is already installed and runnable
    err, out = self.run_process(['docker', 'info'], privileged=True)

    if (err == 0):
      result = True

    elif InstallerYesOrNo('"docker info" failed, attempt to install Docker?', default=True):

      if InstallerYesOrNo('Attempt to install Docker using official repositories?', default=True):

        # install required packages for repo-based install
        if self.distro == PLATFORM_LINUX_UBUNTU:
          requiredRepoPackages = ['apt-transport-https', 'ca-certificates', 'curl', 'gnupg-agent', 'software-properties-common']
        elif self.distro == PLATFORM_LINUX_DEBIAN:
          requiredRepoPackages = ['apt-transport-https', 'ca-certificates', 'curl', 'gnupg2', 'software-properties-common']
        elif self.distro == PLATFORM_LINUX_FEDORA:
          requiredRepoPackages = ['dnf-plugins-core']
        elif self.distro == PLATFORM_LINUX_CENTOS:
          requiredRepoPackages = ['yum-utils', 'device-mapper-persistent-data', 'lvm2']
        else:
          requiredRepoPackages = []

        if len(requiredRepoPackages) > 0:
          eprint("Installing required packages: {}".format(requiredRepoPackages))
          self.install_package(requiredRepoPackages)

        # install docker via repo if possible
        dockerPackages = []
        if ((self.distro == PLATFORM_LINUX_UBUNTU) or (self.distro == PLATFORM_LINUX_DEBIAN)) and self.codename:

          # for debian/ubuntu, add docker GPG key and check its fingerprint
          if self.debug:
            eprint("Requesting docker GPG key for package signing")
          dockerGpgKey = requests.get('https://download.docker.com/linux/{}/gpg'.format(self.distro), allow_redirects=True)
          err, out = self.run_process(['apt-key', 'add'], stdin=dockerGpgKey.content.decode(sys.getdefaultencoding()) if PY3 else dockerGpgKey.content, privileged=True, stderr=False)
          if (err == 0):
            err, out = self.run_process(['apt-key', 'fingerprint', DEB_GPG_KEY_FINGERPRINT], privileged=True, stderr=False)

          # add docker .deb repository
          if (err == 0):
            if self.debug:
              eprint("Adding docker repository")
            err, out = self.run_process(['add-apt-repository', '-y', '-r', 'deb [arch=amd64] https://download.docker.com/linux/{} {} stable'.format(self.distro, self.codename)], privileged=True)
            err, out = self.run_process(['add-apt-repository', '-y', '-u', 'deb [arch=amd64] https://download.docker.com/linux/{} {} stable'.format(self.distro, self.codename)], privileged=True)

          # docker packages to install
          if (err == 0):
            dockerPackages.extend(['docker-ce', 'docker-ce-cli', 'containerd.io'])

        elif self.distro == PLATFORM_LINUX_FEDORA:

          # add docker fedora repository
          if self.debug:
            eprint("Adding docker repository")
          err, out = self.run_process(['dnf', 'config-manager', '-y', '--add-repo', 'https://download.docker.com/linux/fedora/docker-ce.repo'], privileged=True)

          # docker packages to install
          if (err == 0):
            dockerPackages.extend(['docker-ce', 'docker-ce-cli', 'containerd.io'])

        elif self.distro == PLATFORM_LINUX_CENTOS:
          # add docker centos repository
          if self.debug:
            eprint("Adding docker repository")
          err, out = self.run_process(['yum-config-manager', '-y', '--add-repo', 'https://download.docker.com/linux/centos/docker-ce.repo'], privileged=True)

          # docker packages to install
          if (err == 0):
            dockerPackages.extend(['docker-ce', 'docker-ce-cli', 'containerd.io'])

        else:
          err, out = None, None

        if len(dockerPackages) > 0:
          eprint("Installing docker packages: {}".format(dockerPackages))
          if self.install_package(dockerPackages):
            eprint("Installation of docker packages apparently succeeded")
            result = True
          else:
            eprint("Installation of docker packages failed")

      # the user either chose not to use the official repos, the official repo installation failed, or there are not official repos available
      # see if we want to attempt using the convenience script at https://get.docker.com (see https://github.com/docker/docker-install)
      if not result and InstallerYesOrNo('Docker not installed via official repositories. Attempt to install Docker via convenience script (please read https://github.com/docker/docker-install)?', default=False):
        tempFileName = os.path.join(self.tempDirName, 'docker-install.sh')
        if DownloadToFile("https://get.docker.com/", tempFileName, debug=self.debug):
          os.chmod(tempFileName, 493) # 493 = 0o755
          err, out = self.run_process(([tempFileName]), privileged=True)
          if (err == 0):
            eprint("Installation of docker apparently succeeded")
            result = True
          else:
            eprint("Installation of docker failed: {}".format(out))
        else:
          eprint("Downloading {} to {} failed".format(dockerComposeUrl, tempFileName))

    if result and ((self.distro == PLATFORM_LINUX_FEDORA) or (self.distro == PLATFORM_LINUX_CENTOS)):
      # centos/fedora don't automatically start/enable the daemon, so do so now
      err, out = self.run_process(['systemctl', 'start', 'docker'], privileged=True)
      if (err == 0):
        err, out = self.run_process(['systemctl', 'enable', 'docker'], privileged=True)
        if (err != 0):
          eprint("Enabling docker service failed: {}".format(out))
      else:
        eprint("Starting docker service failed: {}".format(out))

    # at this point we either have installed docker successfully or we have to give up, as we've tried all we could
    err, out = self.run_process(['docker', 'info'], privileged=True, retry=6, retrySleepSec=5)
    if result and (err == 0):
      if self.debug:
        eprint('"docker info" succeeded')

      # add non-root user to docker group if required
      usersToAdd = []
      if self.scriptUser == 'root':
        while InstallerYesOrNo('Add {} non-root user to the "docker" group?'.format('a' if len(usersToAdd) == 0 else 'another')):
          tmpUser = InstallerAskForString('Enter user account')
          if (len(tmpUser) > 0): usersToAdd.append(tmpUser)
      else:
        usersToAdd.append(self.scriptUser)

      for user in usersToAdd:
        err, out = self.run_process(['usermod', '-a', '-G', 'docker', user], privileged=True)
        if (err == 0):
          if self.debug:
            eprint('Adding {} to "docker" group succeeded'.format(user))
        else:
          eprint('Adding {} to "docker" group failed'.format(user))

    elif (err != 0):
      result = False
      raise Exception('{} requires docker, please see {}'.format(ScriptName, DOCKER_INSTALL_URLS[self.distro]))

    return result

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def install_docker_compose(self):
    result = False

    dockerComposeCmd = 'docker-compose'
    if not Which(dockerComposeCmd, debug=self.debug) and os.path.isfile('/usr/local/bin/docker-compose'):
      dockerComposeCmd = '/usr/local/bin/docker-compose'

    # first see if docker-compose is already installed and runnable (try non-root and root)
    err, out = self.run_process([dockerComposeCmd, 'version'], privileged=False)
    if (err != 0):
      err, out = self.run_process([dockerComposeCmd, 'version'], privileged=True)

    if (err != 0) and InstallerYesOrNo('"docker-compose version" failed, attempt to install docker-compose?', default=True):

      if InstallerYesOrNo('Install docker-compose directly from docker github?', default=True):
        # download docker-compose from github and put it in /usr/local/bin

        # need to know some linux platform info
        unames = []
        err, out = self.run_process((['uname', '-s']))
        if (err == 0) and (len(out) > 0): unames.append(out[0])
        err, out = self.run_process((['uname', '-m']))
        if (err == 0) and (len(out) > 0): unames.append(out[0])
        if len(unames) == 2:
          # download docker-compose from github and save it to a temporary file
          tempFileName = os.path.join(self.tempDirName, dockerComposeCmd)
          dockerComposeUrl = "https://github.com/docker/compose/releases/download/{}/docker-compose-{}-{}".format(DOCKER_COMPOSE_INSTALL_VERSION, unames[0], unames[1])
          if DownloadToFile(dockerComposeUrl, tempFileName, debug=self.debug):
            os.chmod(tempFileName, 493) # 493 = 0o755, mark as executable
            # put docker-compose into /usr/local/bin
            err, out = self.run_process((['cp', '-f', tempFileName, '/usr/local/bin/docker-compose']), privileged=True)
            if (err == 0):
              eprint("Download and installation of docker-compose apparently succeeded")
              dockerComposeCmd = '/usr/local/bin/docker-compose'
            else:
              raise Exception('Error copying {} to /usr/local/bin: {}'.format(tempFileName, out))

          else:
            eprint("Downloading {} to {} failed".format(dockerComposeUrl, tempFileName))

      elif InstallerYesOrNo('Install docker-compose via pip (privileged)?', default=False):
        # install docker-compose via pip (as root)
        err, out = self.run_process([self.pipCmd, 'install', dockerComposeCmd], privileged=True)
        if (err == 0):
          eprint("Installation of docker-compose apparently succeeded")
        else:
          eprint("Install docker-compose via pip failed with {}, {}".format(err, out))

      elif InstallerYesOrNo('Install docker-compose via pip (user)?', default=True):
        # install docker-compose via pip (regular user)
        err, out = self.run_process([self.pipCmd, 'install', dockerComposeCmd], privileged=False)
        if (err == 0):
          eprint("Installation of docker-compose apparently succeeded")
        else:
          eprint("Install docker-compose via pip failed with {}, {}".format(err, out))

    # see if docker-compose is now installed and runnable (try non-root and root)
    err, out = self.run_process([dockerComposeCmd, 'version'], privileged=False)
    if (err != 0):
      err, out = self.run_process([dockerComposeCmd, 'version'], privileged=True)

    if (err == 0):
      result = True
      if self.debug:
        eprint('"docker-compose version" succeeded')

    else:
      raise Exception('{} requires docker-compose, please see {}'.format(ScriptName, DOCKER_COMPOSE_INSTALL_URLS[self.platform]))

    return result

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def tweak_system_files(self):

    # make some system configuration changes with permission

    ConfigLines = namedtuple("ConfigLines", ["distros", "filename", "prefix", "description", "lines"], rename=False)

    configLinesToAdd = [ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'fs.file-max=',
                                    'fs.file-max increases allowed maximum for file handles',
                                    ['# the maximum number of open file handles',
                                     'fs.file-max=2097152']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'fs.inotify.max_user_watches=',
                                    'fs.inotify.max_user_watches increases allowed maximum for monitored files',
                                    ['# the maximum number of user inotify watches',
                                     'fs.inotify.max_user_watches=131072']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'fs.inotify.max_queued_events=',
                                    'fs.inotify.max_queued_events increases queue size for monitored files',
                                    ['# the inotify event queue size',
                                     'fs.inotify.max_queued_events=131072']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'fs.inotify.max_user_instances=',
                                    'fs.inotify.max_user_instances increases allowed maximum monitor file watchers',
                                    ['# the maximum number of user inotify monitors',
                                     'fs.inotify.max_user_instances=512']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'vm.max_map_count=',
                                    'vm.max_map_count increases allowed maximum for memory segments',
                                    ['# the maximum number of memory map areas a process may have',
                                     'vm.max_map_count=262144']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'net.core.somaxconn=',
                                    'net.core.somaxconn increases allowed maximum for socket connections',
                                    ['# the maximum number of incoming connections',
                                     'net.core.somaxconn=65535']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'vm.swappiness=',
                                    'vm.swappiness adjusts the preference of the system to swap vs. drop runtime memory pages',
                                    ['# decrease "swappiness" (swapping out runtime memory vs. dropping pages)',
                                     'vm.swappiness=1']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'vm.dirty_background_ratio=',
                                    'vm.dirty_background_ratio defines the percentage of system memory fillable with "dirty" pages before flushing',
                                    ['# the % of system memory fillable with "dirty" pages before flushing',
                                     'vm.dirty_background_ratio=40']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'vm.dirty_background_ratio=',
                                    'vm.dirty_background_ratio defines the percentage of dirty system memory before flushing',
                                    ['# maximum % of dirty system memory before committing everything',
                                     'vm.dirty_background_ratio=40']),
                        ConfigLines([],
                                    '/etc/sysctl.conf',
                                    'vm.dirty_ratio=',
                                    'vm.dirty_ratio defines the maximum percentage of dirty system memory before committing everything',
                                    ['# maximum % of dirty system memory before committing everything',
                                     'vm.dirty_ratio=80']),
                        ConfigLines(['centos', 'core'],
                                    '/etc/systemd/system.conf.d/limits.conf',
                                    '',
                                    '/etc/systemd/system.conf.d/limits.conf increases the allowed maximums for file handles and memlocked segments',
                                    ['[Manager]',
                                     'DefaultLimitNOFILE=65535:65535',
                                     'DefaultLimitMEMLOCK=infinity']),
                        ConfigLines(['bionic', 'cosmic', 'disco', 'eoan', 'stretch', 'buster', 'sid', 'fedora'],
                                    '/etc/security/limits.d/limits.conf',
                                    '',
                                    '/etc/security/limits.d/limits.conf increases the allowed maximums for file handles and memlocked segments',
                                    ['* soft nofile 65535',
                                     '* hard nofile 65535',
                                     '* soft memlock unlimited',
                                     '* hard memlock unlimited'])]

    for config in configLinesToAdd:

      if (((len(config.distros) == 0) or (self.codename in config.distros)) and
          (os.path.isfile(config.filename) or InstallerYesOrNo('\n{}\n{} does not exist, create it?'.format(config.description, config.filename), default=True))):

        confFileLines = [line.rstrip('\n') for line in open(config.filename)] if os.path.isfile(config.filename) else []

        if ((len(confFileLines) == 0) or
            (not os.path.isfile(config.filename) and (len(config.prefix) == 0)) or
            ((len(list(filter(lambda x: x.startswith(config.prefix), confFileLines))) == 0) and
              InstallerYesOrNo('\n{}\n{} appears to be missing from {}, append it?'.format(config.description, config.prefix, config.filename), default=True))):

          err, out = self.run_process(['bash', '-c', "mkdir -p {} && echo -n -e '\\n{}\\n' >> '{}'".format(os.path.dirname(config.filename),
                                                                                                           "\\n".join(config.lines),
                                                                                                           config.filename)], privileged=True)

###################################################################################################
class MacInstaller(Installer):

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def __init__(self, debug=False, configOnly=False):
    if PY3:
      super().__init__(debug, configOnly)
    else:
      super(MacInstaller, self).__init__(debug, configOnly)

    self.sudoCmd = []

    # first see if brew is already installed and runnable
    err, out = self.run_process(['brew', 'info'])
    brewInstalled = (err == 0)

    if brewInstalled and InstallerYesOrNo('Homebrew is installed: continue with Homebrew?', default=True):
      self.useBrew = True

    else:
      self.useBrew = False
      eprint('Docker can be installed and maintained with Homebrew, or manually.')
      if (not brewInstalled) and (not InstallerYesOrNo('Homebrew is not installed: continue with manual installation?', default=False)):
        raise Exception('Follow the steps at {} to install Homebrew, then re-run {}'.format(HOMEBREW_INSTALL_URLS[self.platform], ScriptName))

    if self.useBrew:
      # make sure we have brew cask
      err, out = self.run_process(['brew', 'info', 'cask'])
      if (err != 0):
        self.install_package(['cask'])
        if (err == 0):
          if self.debug: eprint('"brew install cask" succeeded')
        else:
          eprint('"brew install cask" failed with {}, {}'.format(err, out))

      err, out = self.run_process(['brew', 'tap', 'homebrew/cask-versions'])
      if (err == 0):
        if self.debug: eprint('"brew tap homebrew/cask-versions" succeeded')
      else:
        eprint('"brew tap homebrew/cask-versions" failed with {}, {}'.format(err, out))

      self.checkPackageCmds.append(['brew', 'cask', 'ls', '--versions'])
      self.installPackageCmds.append(['brew', 'cask', 'install'])

    # determine total system memory
    try:
      totalMemBytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
      self.totalMemoryGigs = math.ceil(totalMemBytes/(1024.**3))
    except:
      self.totalMemoryGigs = 0.0

    # determine total system memory a different way if the first way didn't work
    if (self.totalMemoryGigs <= 0.0):
      err, out = self.run_process(['sysctl', '-n', 'hw.memsize'])
      if (err == 0) and (len(out) > 0):
        totalMemBytes = int(out[0])
        self.totalMemoryGigs = math.ceil(totalMemBytes/(1024.**3))

    # determine total system CPU cores
    try:
      self.totalCores = os.sysconf('SC_NPROCESSORS_ONLN')
    except:
      self.totalCores = 0

    # determine total system CPU cores a different way if the first way didn't work
    if (self.totalCores <= 0):
      err, out = self.run_process(['sysctl', '-n', 'hw.ncpu'])
      if (err == 0) and (len(out) > 0):
        self.totalCores = int(out[0])

  #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  def install_docker(self):
    result = False

    # first see if docker is already installed/runnable
    err, out = self.run_process(['docker', 'info'])

    if (err != 0) and self.useBrew and self.package_is_installed(MAC_BREW_DOCKER_PACKAGE):
      # if docker is installed via brew, but not running, prompt them to start it
      eprint('{} appears to be installed via Homebrew, but "docker info" failed'.format(MAC_BREW_DOCKER_PACKAGE))
      while True:
        response = InstallerAskForString('Starting Docker the first time may require user interaction. Please find and start Docker in the Applications folder, then return here and type YES').lower()
        if (response == 'yes'):
          break
      err, out = self.run_process(['docker', 'info'], retry=12, retrySleepSec=5)

    # did docker info work?
    if (err == 0):
      result = True

    elif InstallerYesOrNo('"docker info" failed, attempt to install Docker?', default=True):

      if self.useBrew:
        # install docker via brew cask (requires user interaction)
        dockerPackages = [MAC_BREW_DOCKER_PACKAGE]
        eprint("Installing docker packages: {}".format(dockerPackages))
        if self.install_package(dockerPackages):
          eprint("Installation of docker packages apparently succeeded")
          while True:
            response = InstallerAskForString('Starting Docker the first time may require user interaction. Please find and start Docker in the Applications folder, then return here and type YES').lower()
            if (response == 'yes'):
              break
        else:
          eprint("Installation of docker packages failed")

      else:
        # install docker via downloaded dmg file (requires user interaction)
        dlDirName = '/Users/{}/Downloads'.format(self.scriptUser)
        if os.path.isdir(dlDirName):
          tempFileName = os.path.join(dlDirName, 'Docker.dmg')
        else:
          tempFileName = os.path.join(self.tempDirName, 'Docker.dmg')
        if DownloadToFile('https://download.docker.com/mac/edge/Docker.dmg', tempFileName, debug=self.debug):
          while True:
            response = InstallerAskForString('Installing and starting Docker the first time may require user interaction. Please open Finder and install {}, start Docker from the Applications folder, then return here and type YES'.format(tempFileName)).lower()
            if (response == 'yes'):
              break

      # at this point we either have installed docker successfully or we have to give up, as we've tried all we could
      err, out = self.run_process(['docker', 'info'], retry=12, retrySleepSec=5)
      if (err == 0):
        result = True
        if self.debug:
          eprint('"docker info" succeeded')

      elif (err != 0):
        raise Exception('{} requires docker edge, please see {}'.format(ScriptName, DOCKER_INSTALL_URLS[self.platform]))

    elif (err != 0):
      raise Exception('{} requires docker edge, please see {}'.format(ScriptName, DOCKER_INSTALL_URLS[self.platform]))

    # tweak CPU/RAM usage for Docker in Mac
    settingsFile = MAC_BREW_DOCKER_SETTINGS.format(self.scriptUser)
    if result and os.path.isfile(settingsFile) and InstallerYesOrNo('Configure Docker resource usage in {}?'.format(settingsFile), default=True):

      # adjust CPU and RAM based on system resources
      if self.totalCores >= 16:
        newCpus = 12
      elif self.totalCores >= 12:
        newCpus = 8
      elif self.totalCores >= 8:
        newCpus = 6
      elif self.totalCores >= 4:
        newCpus = 4
      else:
        newCpus = 2

      if self.totalMemoryGigs >= 64.0:
        newMemoryGiB = 32
      elif self.totalMemoryGigs >= 32.0:
        newMemoryGiB = 24
      elif self.totalMemoryGigs >= 24.0:
        newMemoryGiB = 16
      elif self.totalMemoryGigs >= 16.0:
        newMemoryGiB = 12
      elif self.totalMemoryGigs >= 8.0:
        newMemoryGiB = 8
      elif self.totalMemoryGigs >= 4.0:
        newMemoryGiB = 4
      else:
        newMemoryGiB = 2

      while not InstallerYesOrNo('Setting {} for CPU cores and {} GiB for RAM. Is this OK?'.format(newCpus if newCpus else "(unchanged)", newMemoryGiB if newMemoryGiB else "(unchanged)"), default=True):
        newCpus = InstallerAskForString('Enter Docker CPU cores (e.g., 4, 8, 16)')
        newMemoryGiB = InstallerAskForString('Enter Docker RAM MiB (e.g., 8, 16, etc.)')

      if newCpus or newMemoryMiB:
        with open(settingsFile, 'r+') as f:
          data = json.load(f)
          if newCpus: data['cpus'] = int(newCpus)
          if newMemoryGiB: data['memoryMiB'] = int(newMemoryGiB)*1024
          f.seek(0)
          json.dump(data, f, indent=2)
          f.truncate()

        # at this point we need to essentially update our system memory stats because we're running inside docker
        # and don't have the whole banana at our disposal
        self.totalMemoryGigs = newMemoryGiB

        eprint("Docker resource settings adjusted, attempting restart...")

        err, out = self.run_process(['osascript', '-e', 'quit app "Docker"'])
        if (err == 0):
          time.sleep(5)
          err, out = self.run_process(['open', '-a', 'Docker'])

        if (err == 0):
          err, out = self.run_process(['docker', 'info'], retry=12, retrySleepSec=5)
          if (err == 0):
            if self.debug:
              eprint('"docker info" succeeded')

        else:
          eprint("Restarting Docker automatically failed: {}".format(out))
          while True:
            response = InstallerAskForString('Please restart Docker via the system taskbar, then return here and type YES').lower()
            if (response == 'yes'):
              break

    return result

###################################################################################################
# main
def main():
  global args

  # extract arguments from the command line
  # print (sys.argv[1:]);
  parser = argparse.ArgumentParser(description='Malcolm install script', add_help=False, usage='{} <arguments>'.format(ScriptName))
  parser.add_argument('-v', '--verbose', dest='debug', type=str2bool, nargs='?', const=True, default=False, help="Verbose output")
  parser.add_argument('-m', '--malcolm-file', required=False, dest='mfile', metavar='<STR>', type=str, default='', help='Malcolm .tar.gz file for installation')
  parser.add_argument('-i', '--image-file', required=False, dest='ifile', metavar='<STR>', type=str, default='', help='Malcolm docker images .tar.gz file for installation')
  parser.add_argument('-c', '--configure', dest='configOnly', type=str2bool, nargs='?', const=True, default=False, help="Only do configuration (not installation)")
  parser.add_argument('-f', '--configure-file', required=False, dest='configFile', metavar='<STR>', type=str, default='', help='Single docker-compose YML file to configure')
  parser.add_argument('-d', '--defaults', dest='acceptDefaults', type=str2bool, nargs='?', const=True, default=False, help="Accept defaults to prompts without user interaction")
  parser.add_argument('-l', '--logstash-expose', dest='exposeLogstash', type=str2bool, nargs='?', const=True, default=False, help="Expose Logstash port to external hosts")
  parser.add_argument('-r', '--restart-malcolm', dest='malcolmAutoRestart', type=str2bool, nargs='?', const=True, default=False, help="Restart Malcolm on system restart (unless-stopped)")

  try:
    parser.error = parser.exit
    args = parser.parse_args()
  except SystemExit:
    parser.print_help()
    exit(2)

  if args.debug:
    eprint(os.path.join(ScriptPath, ScriptName))
    eprint("Arguments: {}".format(sys.argv[1:]))
    eprint("Arguments: {}".format(args))
  else:
    sys.tracebacklimit = 0

  if not ImportRequests(debug=args.debug):
    exit(2)

  # If Malcolm and images tarballs are provided, we will use them.
  # If they are not provided, look in the pwd first, then in the script directory, to see if we
  # can locate the most recent tarballs
  malcolmFile = None
  imageFile = None

  if args.mfile and os.path.isfile(args.mfile):
    malcolmFile = args.mfile
  else:
    # find the most recent non-image tarball, first checking in the pwd then in the script path
    files = list(filter(lambda x: "_images" not in x, glob.glob(os.path.join(origPath, '*.tar.gz'))))
    if (len(files) == 0):
      files = list(filter(lambda x: "_images" not in x, glob.glob(os.path.join(ScriptPath, '*.tar.gz'))))
    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    if (len(files) > 0):
      malcolmFile = files[0]

  if args.ifile and os.path.isfile(args.ifile):
    imageFile = args.ifile

  if (malcolmFile and os.path.isfile(malcolmFile)) and (not imageFile or not os.path.isfile(imageFile)):
    # if we've figured out the malcolm tarball, the _images tarball should match it
    imageFile = malcolmFile.replace('.tar.gz', '_images.tar.gz')
    if not os.path.isfile(imageFile): imageFile = None

  if args.debug:
    if args.configOnly:
      eprint("Only doing configuration, not installation")
    else:
      eprint("Malcolm install file: {}".format(malcolmFile))
      eprint("Docker images file: {}".format(imageFile))

  installerPlatform = platform.system()
  if installerPlatform == PLATFORM_LINUX:
    installer = LinuxInstaller(debug=args.debug, configOnly=args.configOnly)
  elif installerPlatform == PLATFORM_MAC:
    installer = MacInstaller(debug=args.debug, configOnly=args.configOnly)
  elif installerPlatform == PLATFORM_WINDOWS:
    raise Exception('{} is not yet supported on {}'.format(ScriptName, installerPlatform))
    installer = WindowsInstaller(debug=args.debug, configOnly=args.configOnly)

  success = False
  installPath = None

  if (not args.configOnly):
    if hasattr(installer, 'install_required_packages'): success = installer.install_required_packages()
    if hasattr(installer, 'install_docker'): success = installer.install_docker()
    if hasattr(installer, 'install_docker_compose'): success = installer.install_docker_compose()
    if hasattr(installer, 'tweak_system_files'): success = installer.tweak_system_files()
    if hasattr(installer, 'install_docker_images'): success = installer.install_docker_images(imageFile)

  if args.configOnly or (args.configFile and os.path.isfile(args.configFile)):
    if not args.configFile:
      for testPath in [origPath, ScriptPath, os.path.realpath(os.path.join(ScriptPath, ".."))]:
        if os.path.isfile(os.path.join(testPath, "docker-compose.yml")):
          installPath = testPath
    else:
      installPath = os.path.dirname(os.path.realpath(args.configFile))
    success = (installPath is not None) and os.path.isdir(installPath)
    if args.debug:
      eprint("Malcolm installation detected at {}".format(installPath))

  elif hasattr(installer, 'install_malcolm_files'):
    success, installPath = installer.install_malcolm_files(malcolmFile)

  if (installPath is not None) and os.path.isdir(installPath) and hasattr(installer, 'tweak_malcolm_runtime'):
    installer.tweak_malcolm_runtime(installPath, expose_logstash_default=args.exposeLogstash, restart_mode_default=args.malcolmAutoRestart)
    eprint("\nMalcolm has been installed to {}. See README.md for more information.".format(installPath))
    eprint("Scripts for starting and stopping Malcolm and changing authentication-related settings can be found in {}.".format(os.path.join(installPath, "scripts")))

if __name__ == '__main__':
  main()
