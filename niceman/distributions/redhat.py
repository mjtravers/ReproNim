# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the niceman package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Support for Redhat(-based) distribution(s)."""
import attr

from six.moves import map

import logging

from niceman.distributions.base import DistributionTracer

lgr = logging.getLogger('niceman.distributions.redhat')

from .base import SpecObject
from .base import Package
from .base import Distribution
from .base import TypedList
from .base import _register_with_representer
from ..support.exceptions import CommandError
from ..utils import attrib


@attr.s(cmp=True)
class RPMSource(SpecObject):
    """RPM origin information"""
    id = attrib(default=attr.NOTHING)
    name = attrib()
    revision = attrib()
    updated = attrib()
    pkgs = attrib()
    size = attrib()
    mirrors = attrib()
    metalink = attrib()
    baseurl = attrib()
    expire = attrib()
    filename = attrib()

_register_with_representer(RPMSource)


@attr.s(slots=True, frozen=True, cmp=False, hash=True)
class RPMPackage(Package):
    """Redhat package information"""
    name = attrib(default=attr.NOTHING)
    pkgid = attrib()
    version = attrib()
    release = attrib()
    architecture = attrib()
    install_date = attrib()
    group = attrib()
    size = attrib()
    license = attrib()
    signature = attrib()
    source_rpm = attrib()
    build_date = attrib()
    build_host = attrib()
    packager = attrib()
    vendor = attrib()
    url = attrib()
    files = attrib(default=attr.Factory(list), hash=False)

    def satisfies(self, other):
        """return True if this package (self) satisfies the requirements of 
        the passed package (other)"""
        if not isinstance(other, Package):
            raise TypeError('satisfies() requires a package argument')
        if not isinstance(other, RPMPackage):
            return False
        if self.name != other.name:
            return False
        if other.version is not None and self.version != other.version:
            return False
        if other.architecture is not None \
                and self.architecture != other.architecture:
            return False
        return True

_register_with_representer(RPMPackage)


@attr.s
class RedhatDistribution(Distribution):
    """
    Class to provide Redhat-based shell commands.
    """

    sources = TypedList(RPMSource)
    packages = TypedList(RPMPackage)
    version = attrib()  # version as depicted by /etc/redhat_version

    def initiate(self, session):
        """
        Perform any initialization commands needed in the environment environment.

        Parameters
        ----------
        environment : object
            The Environment sub-class object.
        """
        lgr.debug("Adding Redhat update to environment command list.")
        self._init_repo_sources(session)

    def _init_repo_sources(self, session):
        """
        Install package repos listed in the spec but missing in the resource.

        Parameters
        ----------
        session : Session object
        """

        # TODO: Expand the listing of 3rd party repos as popular ones in the
        # neuroscience community become apparent.
        third_party_repos = {
            'epel/x86_64': 'epel-release'
        }

        # Get a list of the enabled repos on the system.
        out, _ = session.execute_command(['yum', 'repolist'])
        system_repos = []
        skip = True
        for line in out.splitlines():
            if line.startswith('repo id'):
                skip = False
                continue
            if skip:
                continue
            system_repos.append(line.split()[0])

        # Try to install any repos listed in the spec that are missing on the system.
        for source in self.sources:
            if source.id not in system_repos:
                if source.id in third_party_repos:
                    session.execute_command(['yum', 'install', '-y',
                        third_party_repos[source.id]])
                else:
                    lgr.error("Unable to install source repo \"%s\" found in "
                        "spec but not in resource environment.", source.id)

    def install_packages(self, session, use_version=True):
        """
        Install the packages associated to this distribution by the provenance
        into the environment.

        Parameters
        ----------
        session : object
        use_version : bool, optional
          Use version information if provided.
        """
        package_specs = []

        for package in self.packages:
            if use_version:
                package_spec = package.pkgid
            else:
                package_spec = package.name
            package_specs.append(package_spec)

        # Doing in one shot to fail early if any of the versioned specs
        # couldn't be satisfied
        lgr.debug("Installing %s", ', '.join(package_specs))
        session.execute_command(
            # TODO: Pull env out of provenance for this command.
            ['yum', 'install', '-y'] + package_specs,
            # env={'DEBIAN_FRONTEND': 'noninteractive'}
        )

    def satisfies_package(self, package):
        """return True if this distribution (self) satisfies the requirements 
        of the passed package"""
        if not isinstance(package, Package):
            raise TypeError('satisfies_package() requires a package argument')
        if not isinstance(package, RPMPackage):
            return False
        return any([p.satisfies(package) for p in self.packages])

    def satisfies(self, other):
        """return True if this distribution (self) satisfies the requirements 
        of the other distribution (other)"""
        if not isinstance(other, Distribution):
            raise TypeError('satisfies() requires a distribution argument')
        if not isinstance(other, RedhatDistribution):
            return False
        return all(map(self.satisfies_package, other.packages))

    def __sub__(self, other):
        # the semantics of distribution subtraction are, for d1 - d2:
        #     what is specified in d1 that is not specified in d2
        #     or how does d2 fall short of d1
        #     or what is in d1 that isn't satisfied by d2
        return [p for p in self.packages if not other.satisfies_package(p)]

_register_with_representer(RedhatDistribution)


class RPMTracer(DistributionTracer):
    """.rpm-based (and using yum and rpm) systems package tracer
    """

    # The Redhat tracer is not designed to handle directories
    HANDLES_DIRS = False

    def _init(self):
        # TODO: we might want a generic helper for collections of things
        # where we could match based on the set of attrs which matter
        self._package_install_dates = {}

    def identify_distributions(self, files):
        """
        Return a distribution object containing package and repos source
        information for files found to be from CentOS distribution

        Parameters
        ----------
        files : list(string)
            List of files to check to see if they come from CentOS packages

        Returns
        -------
        dist : RedhatDistribution object
        remaining_files : list(string)
            List of files not identified to be in CentOS packages
        """
        if not files:
            return

        try:
            redhat_version = self._session.read('/etc/redhat-release').strip()
            self._session.exists('/etc/os-release')
            _, _ = self._session.execute_command(
                ['grep', '-i', '^ID.*="rhel fedora"', '/etc/os-release'])
            _, _ = self._session.execute_command('ls -ld /etc/yum')
        except CommandError as exc:
            lgr.debug("Did not detect Redhat (or derivative): %s", exc)
            return

        packages, remaining_files = self.identify_packages_from_files(files)
        # TODO: add option to report distribution even if no packages/files
        # found association
        if not packages:
            return

        dist = RedhatDistribution(
            name="redhat",
            version=redhat_version,
            packages=packages,
            sources=self._find_all_sources()
        )

        yield dist, remaining_files

    def _get_packagefields_for_files(self, files):
        """
        Query the system for detail information for each package found.

        Parameters
        ----------
        files : list(string)
            List of files to trace which packages they come from

        Returns
        -------
        dictionary : key = package id, value = dict of package details
        """

        file_to_package_dict = {}

        # Get a list of the attrs in the RPMPackage class to filter
        # out non-relavant values retrieved from the system.
        package_fields = [p.name for p in attr.fields(RPMPackage)]

        for file in files:
            try:
                # Get the package identifier that the file is a member.
                pkgid, err = self._session.execute_command(['rpm', '-qf',
                    file])
            except CommandError:
                continue
            if err:
                continue

            pkgid = pkgid.strip()

            # Get the package information from the system.
            package_info, _ = self._session.execute_command(['rpm', '-qi',
                pkgid])
            if package_info:
                # Store the relevant fields of the package in the dict.
                # The labels of the fields returned by the system are mapped
                # to the attr fields in the RPMPacakge class.
                pkg = {'pkgid': pkgid}
                for line in package_info.splitlines():
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        key = parts[0].strip().lower().replace(' ', '_')
                        if key in package_fields:
                            pkg[key] = parts[1].strip()
                lgr.debug("Identified file %r to belong to package %s",
                          pkgid, pkg)
                file_to_package_dict[file] = pkg
        return file_to_package_dict

    def _create_package(self, name, **kwargs):
        return RPMPackage(name=name, **kwargs)

    def _find_all_sources(self):
        """
        Retrieve repository source information from the system

        Returns
        -------
        sources : list(RPMSource)
        """
        sources = []
        # Get all repo info from the system and store information for each
        # enabled repo in a RPMSource object.
        out, _ = self._session.execute_command(['yum', '--verbose',
            'repolist'])
        for line in out.splitlines():
            if line.startswith('Repo-'):
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
            else:
                continue
            if key == ('Repo-id'):
                values = {'id': value}
            else:
                # Map the field labels returned by the sytem to the attr
                # fields in the RPMSource class.
                values[key.split('-')[1]] = value
            if key == 'Repo-filename':
                sources.append(RPMSource(**values))
        return sources
