#
# Project Ginger Base
#
# Copyright IBM Corp, 2015-2017
#
# Code derived from Project Kimchi
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA

import fcntl
import os
import signal
import subprocess
import threading
import time
from configobj import ConfigObj, ConfigObjError
from psutil import pid_exists, process_iter

from wok.basemodel import Singleton
from wok.exception import NotFoundError, OperationFailed
from wok.utils import run_command, wok_log

from wok.plugins.gingerbase.yumparser import get_dnf_package_deps
from wok.plugins.gingerbase.yumparser import get_yum_package_deps
from wok.plugins.gingerbase.yumparser import get_yum_package_info
from wok.plugins.gingerbase.yumparser import get_yum_packages_list_update
from wok.plugins.gingerbase import portageparser

swupdateLock = threading.RLock()


class SoftwareUpdate(object):
    __metaclass__ = Singleton

    """
    Class to represent and operate with OS software update.
    """
    def __init__(self):
        # Get the distro of host machine and creates an object related to
        # correct package management system
        self._pkg_mnger = None
        for module, cls in [('dnf', DnfUpdate), ('yum', YumUpdate),
                            ('apt', AptUpdate), ('portage', PortageUpdate)]:
            try:
                __import__(module)
                wok_log.info("Logging %s features." % cls.__name__)
                self._pkg_mnger = cls()
                break
            except ImportError:
                continue
        zypper_help = ["zypper", "--help"]
        (stdout, stderr, returncode) = run_command(zypper_help)
        if returncode == 0:
            wok_log.info("Loading ZypperUpdate features.")
            self._pkg_mnger = ZypperUpdate()
        if self._pkg_mnger is None:
            raise Exception("There is no compatible package "
                            "manager for this system.")

    def getUpdates(self):
        """
        Return a list of packages eligible to be updated in the system.
        """
        swupdateLock.acquire()
        try:
            pkgs = [pkg for pkg in self._pkg_mnger.getPackagesList()]
            return pkgs
        except:
            raise
        finally:
            swupdateLock.release()

    def getUpdate(self, name):
        """
        Return a dictionary with all info from a given package name.
        """
        swupdateLock.acquire()
        try:
            package = self._pkg_mnger.getPackageInfo(name)
            if not package:
                raise NotFoundError('GGBPKGUPD0002E', {'name': name})
            return package
        except:
            raise
        finally:
            swupdateLock.release()

    def getPackageDeps(self, name):
        """
        """
        self.getUpdate(name)

        swupdateLock.acquire()
        try:
            return self._pkg_mnger.getPackageDeps(name)
        except:
            raise
        finally:
            swupdateLock.release()

    def getNumOfUpdates(self):
        """
        Return the number of packages to be updated.
        """
        swupdateLock.acquire()
        try:
            return len(self.getUpdates())
        except:
            raise
        finally:
            swupdateLock.release()

    def preUpdate(self):
        """
        Make adjustments before executing the command in
        a child process.
        """
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def tailUpdateLogs(self, cb, params):
        """
        When the package manager is already running (started outside gingerbase
        or if wokd is restarted) we can only know what's happening by reading
        the logfiles. This method acts like a 'tail -f' on the default package
        manager logfile. If the logfile is not found, a simple '*' is
        displayed to track progress. This will be until the process finishes.
        """
        if not self._pkg_mnger.isRunning():
            return

        fd = None
        try:
            fd = os.open(self._pkg_mnger.logfile, os.O_RDONLY)

        # cannot open logfile, print something to let users know that the
        # system is being upgrading until the package manager finishes its
        # job
        except (TypeError, OSError):
            msgs = []
            while self._pkg_mnger.isRunning():
                msgs.append('*')
                cb(''.join(msgs))
                time.sleep(1)
            msgs.append('\n')
            cb(''.join(msgs), True)
            return

        # go to the end of logfile and starts reading, if nothing is read or
        # a pattern is not found in the message just wait and retry until
        # the package manager finishes
        os.lseek(fd, 0, os.SEEK_END)
        msgs = []
        progress = []
        while True:
            read = os.read(fd, 1024)
            if not read:
                if not self._pkg_mnger.isRunning():
                    break

                if not msgs:
                    progress.append('*')
                    cb(''.join(progress))

                time.sleep(1)
                continue

            msgs.append(read)
            cb(''.join(msgs))

        os.close(fd)
        return cb(''.join(msgs), True)

    def doUpdate(self, cb, params):
        """
        Execute the update
        """
        swupdateLock.acquire()
        wok_log.info('doUpdate - swupdate lock acquired')
        # reset messages
        cb('')

        if params is not None:
            cmd = self._pkg_mnger.update_cmd['specific'] + params
        else:
            cmd = self._pkg_mnger.update_cmd['all']

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                preexec_fn=self.preUpdate)
        msgs = []
        while proc.poll() is None:
            msgs.append(proc.stdout.readline())
            cb(''.join(msgs))
            time.sleep(0.5)

        # read the final output lines
        msgs.extend(proc.stdout.readlines())

        retcode = proc.poll()

        swupdateLock.release()
        wok_log.info('doUpdate - swupdate lock released')
        if retcode == 0:
            return cb(''.join(msgs), True)

        msgs.extend(proc.stderr.readlines())
        return cb(''.join(msgs), False)


class GenericUpdate(object):
    def getPackagesList(self):
        return

    def getPackageInfo(self, pkg_name):
        return

    def getPackageDeps(self, pkg_name):
        return

    def isRunning(self):
        return False

    def wait_pkg_manager_available(self):
        while self.isRunning():
            time.sleep(1)


class YumUpdate(GenericUpdate):
    """
    Class to represent and operate with YUM software update system.
    It's loaded only on those systems listed at YUM_DISTROS and loads necessary
    modules in runtime.
    """
    def __init__(self):
        self.update_cmd = dict.fromkeys(['all', 'specific'],
                                        ["yum", "-y", "update"])
        self.logfile = self._get_output_log()

    def _get_output_log(self):
        """
        Return the logfile path
        """
        yumcfg = None
        try:
            yumcfg = ConfigObj('/etc/yum.conf')

        except ConfigObjError:
            return None

        if 'main' in yumcfg and 'logfile' in yumcfg['main']:
            return yumcfg['main']['logfile']

        return None

    def getPackagesList(self):
        """
        Return a list of packages eligible to be updated by Yum.
        """
        self.wait_pkg_manager_available()
        try:
            return get_yum_packages_list_update()
        except Exception, e:
            raise OperationFailed('GGBPKGUPD0003E', {'err': str(e)})

    def getPackageInfo(self, pkg_name):
        """
        Get package information. The return is a dictionary containg the
        information about a package, in the format:

        package = {'package_name': <string>,
                   'version': <string>,
                   'arch': <string>,
                   'repository': <string>
                  }
        """
        self.wait_pkg_manager_available()
        try:
            return get_yum_package_info(pkg_name)
        except Exception, e:
            raise NotFoundError('GGBPKGUPD0003E', {'err': str(e)})

    def getPackageDeps(self, pkg_name):
        try:
            return get_yum_package_deps(pkg_name)
        except Exception, e:
            raise NotFoundError('GGBPKGUPD0003E', {'err': str(e)})

    def isRunning(self):
        """
        Return True whether the YUM package manager is already running or
        False otherwise.
        """
        try:
            with open('/var/run/yum.pid', 'r') as pidfile:
                pid = int(pidfile.read().rstrip('\n'))

        # cannot find pidfile, assumes yum is not running
        except (IOError, ValueError):
            return False

        # the pidfile exists and it lives in process table
        if pid_exists(pid):
            return True
        return False


class DnfUpdate(YumUpdate):
    """
    Class to represent and operate with DNF software update system.
    It's loaded only on those systems listed at DNF_DISTROS and loads necessary
    modules in runtime.
    """
    def __init__(self):
        self._pkgs = {}
        self.update_cmd = dict.fromkeys(['all', 'specific'],
                                        ["dnf", "-y", "update"])
        self.logfile = '/var/log/dnf.log'

    def getPackageDeps(self, pkg_name):
        self.wait_pkg_manager_available()
        try:
            return get_dnf_package_deps(pkg_name)
        except Exception, e:
            raise NotFoundError('GGBPKGUPD0003E', {'err': str(e)})

    def isRunning(self):
        """
        Return True whether the YUM package manager is already running or
        False otherwise.
        """
        pid = None
        try:
            for dnf_proc in process_iter():
                if 'dnf' in dnf_proc.name():
                    pid = dnf_proc.pid
                    break
        except:
            return False

        # the pidfile exists and it lives in process table
        return pid_exists(pid)


class AptUpdate(GenericUpdate):
    """
    Class to represent and operate with APT software update system.
    It's loaded only on those systems listed at APT_DISTROS and loads necessary
    modules in runtime.
    """
    def __init__(self):
        self.update_cmd = {'all': ['apt-get', 'upgrade', '-y'],
                           'specific': ['apt-get', '-y', '--only-upgrade',
                                        'install']}
        self.logfile = '/var/log/apt/term.log'
        self._apt_cache = getattr(__import__('apt'), 'Cache')()

    def getPackagesList(self):
        """
        Return a list of packages eligible to be updated by apt-get.
        """
        self.wait_pkg_manager_available()
        try:
            self._apt_cache.open()
            self._apt_cache.update()
            self._apt_cache.upgrade()
            pkgs = self._apt_cache.get_changes()
            self._apt_cache.close()
        except Exception, e:
            raise OperationFailed('GGBPKGUPD0003E', {'err': e.message})

        return [{'package_name': pkg.shortname,
                 'version': pkg.candidate.version,
                 'arch': pkg._pkg.architecture,
                 'repository': pkg.candidate.origins[0].label} for pkg in pkgs]

    def getPackageInfo(self, pkg_name):
        """
        Get package information. The return is a dictionary containg the
        information about a package, in the format:

        package = {'package_name': <string>,
                   'version': <string>,
                   'arch': <string>,
                   'repository': <string>
                  }
        """
        self.wait_pkg_manager_available()

        package = {}
        try:
            self._apt_cache.open()
            self._apt_cache.upgrade()
            pkgs = self._apt_cache.get_changes()
            self._apt_cache.close()
        except Exception, e:
            raise OperationFailed('GGBPKGUPD0006E', {'err': e.message})

        pkg = next((x for x in pkgs if x.shortname == pkg_name), None)
        if not pkg:
            message = 'No package found'
            raise NotFoundError('GGBPKGUPD0006E', {'err': message})

        package = {'package_name': pkg.shortname,
                   'version': pkg.candidate.version,
                   'arch': pkg._pkg.architecture,
                   'repository': pkg.candidate.origins[0].label}
        return package

    def getPackageDeps(self, pkg_name):
        self.wait_pkg_manager_available()

        try:
            self._apt_cache.open()
            self._apt_cache.upgrade()
            pkgs = self._apt_cache.get_changes()
            self._apt_cache.close()
        except Exception, e:
            raise OperationFailed('GGBPKGUPD0006E', {'err': e.message})

        pkg = next((x for x in pkgs if x.shortname == pkg_name), None)
        if not pkg:
            message = 'No package found'
            raise NotFoundError('GGBPKGUPD0006E', {'err': message})

        return list(set([d[0].name for d in pkg.candidate.dependencies]))

    def isRunning(self):
        """
        Return True whether the APT package manager is already running or
        False otherwise.
        """
        try:
            with open('/var/lib/dpkg/lock', 'w') as lockfile:
                fcntl.lockf(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # cannot open dpkg lock file to write in exclusive mode means the
        # apt is currently running
        except IOError:
            return True

        return False


class ZypperUpdate(GenericUpdate):
    """
    Class to represent and operate with Zypper software update system.
    It's loaded only on those systems listed at ZYPPER_DISTROS and loads
    necessary modules in runtime.
    """
    def __init__(self):
        self.update_cmd = dict.fromkeys(['all', 'specific'],
                                        ["zypper", "--non-interactive",
                                         "update",
                                         "--auto-agree-with-licenses"])
        self.logfile = '/var/log/zypp/history'

    def getPackagesList(self):
        """
        Return a list of packages eligible to be updated by Zypper.
        """
        self.wait_pkg_manager_available()

        packages = []
        cmd = ["zypper", "list-updates"]
        (stdout, stderr, returncode) = run_command(cmd)

        if len(stderr) > 0:
            raise OperationFailed('GGBPKGUPD0003E', {'err': stderr})

        for line in stdout.split('\n'):
            if line.startswith('v |'):
                line = line.split(' | ')
                pkg = {'package_name': line[2].strip(),
                       'version': line[4].strip(), 'arch': line[5].strip(),
                       'repository': line[1].strip()}
                packages.append(pkg)
        return packages

    def getPackageInfo(self, pkg_name):
        """
        Get package information. The return is a dictionary containg the
        information about a package, in the format:

        package = {'package_name': <string>,
                   'version': <string>,
                   'arch': <string>,
                   'repository': <string>
                  }
        """
        self.wait_pkg_manager_available()

        cmd = ["zypper", "info", pkg_name]
        (stdout, stderr, returncode) = run_command(cmd)

        if len(stderr) > 0:
            raise OperationFailed('GGBPKGUPD0006E', {'err': stderr})

        # Zypper returns returncode == 0 and stderr <= 0, even if package is
        # not found in it's base. Need check the output of the command to parse
        # correctly.
        message = '\'%s\' not found' % pkg_name
        if message in stdout:
            raise NotFoundError('GGBPKGUPD0006E', {'err': message})

        package = {}
        stdout = stdout.split('\n')
        for (key, token) in (('repository', 'Repository:'),
                             ('version', 'Version:'),
                             ('arch', 'Arch:'),
                             ('package_name', 'Name:')):
            for line in stdout:
                if line.startswith(token):
                    package[key] = line.split(': ')[1].strip()
                    break

        return package

    def getPackageDeps(self, pkg_name):
        self.wait_pkg_manager_available()

        cmd = ["zypper", "--non-interactive", "update", "--dry-run", pkg_name]
        (stdout, stderr, returncode) = run_command(cmd)

        if len(stderr) > 0:
            raise OperationFailed('GGBPKGUPD0006E', {'err': stderr})

        # Zypper returns returncode == 0 and stderr <= 0, even if package is
        # not found in it's base. Need check the output of the command to parse
        # correctly.
        message = '\'%s\' not found' % pkg_name
        if message in stdout:
            raise NotFoundError('GGBPKGUPD0006E', {'err': message})

        # get the list of dependencies
        out = stdout.split('\n')
        for line in out:
            if line.startswith("The following"):
                deps_index = out.index(line) + 1
                break

        deps = out[deps_index].split()
        deps.remove(pkg_name)
        return deps

    def isRunning(self):
        """
        Return True whether the Zypper package manager is already running or
        False otherwise.
        """
        try:
            with open('/var/run/zypp.pid', 'r') as pidfile:
                pid = int(pidfile.read().rstrip('\n'))

        # cannot find pidfile, assumes yum is not running
        except (IOError, ValueError):
            return False

        # the pidfile exists and it lives in process table
        if pid_exists(pid):
            return True

        return False


class PortageUpdate(GenericUpdate):
    """
    Class to represent and operate with Portage software update system.
    It's loaded only on those systems listed at PORTAGE_DISTROS and loads
    necessary modules in runtime.
    """
    def __init__(self):
        # on purpose empty, not smart to do that over a webui in gentoo
        self.update_cmd = dict()
        # specific updates would require the usage of '=package-$version'
        # not implemented in gingerbase therefore omitted
        # self.update_cmd = dict.fromkeys(['all', ],
        #                                ["emerge", "-u", "@world"])
        self.logfile = self._get_output_log()

    def _get_output_log(self):
        """
        Return the logfile path
        """
        # TODO: find potential custom location in make.conf?
        return "/var/log/emerge.log"

    def getPackagesList(self):
        """
        Return a list of packages eligible to be updated.
        """
        self.wait_pkg_manager_available()
        try:
            return portageparser.packages_list_update()
        except Exception, e:
            raise OperationFailed('GGBPKGUPD0003E', {'err': str(e)})

    def getPackageInfo(self, pkg_name):
        """
        Get package information. The return is a dictionary containg the
        information about a package, in the format:

        package = {'package_name': <string>,
                   'version': <string>,
                   'arch': <string>,
                   'repository': <string>
                  }
        """
        self.wait_pkg_manager_available()
        try:
            return portageparser.package_info(pkg_name)
        except Exception, e:
            raise NotFoundError('GGBPKGUPD0003E', {'err': str(e)})

    def getPackageDeps(self, pkg_name):
        try:
            return portageparser.package_deps(pkg_name)
        except Exception, e:
            raise NotFoundError('GGBPKGUPD0003E', {'err': str(e)})

    def isRunning(self):
        """
        Return True whether the package manager is already running or
        False otherwise.
        """
        pid = None
        try:
            for dnf_proc in process_iter():
                if 'emerge' in dnf_proc.name():
                    pid = dnf_proc.pid
                    break
        except:
            return False

        # the pidfile exists and it lives in process table
        return pid_exists(pid)
