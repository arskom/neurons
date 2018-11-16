# encoding: utf8
#
# This file is part of the Neurons project.
# Copyright (c), Arskom Ltd. (arskom.com.tr),
#                Burak Arslan <burak.arslan@arskom.com.tr>.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the Arskom Ltd., the neurons project nor the names of
#   its its contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

from __future__ import print_function, absolute_import

import logging
logger = logging.getLogger(__name__)

import os, sys
import getpass
import resource
import traceback

from os import access
from uuid import uuid1
from os.path import isfile, abspath, dirname, getsize
from argparse import Action

from spyne import ComplexModel, Boolean, ByteArray, Uuid, Unicode, \
    Array, String, Double, UnsignedInteger16, M
from spyne.protocol import ProtocolBase
from spyne.protocol.yaml import YamlDocument
from spyne.util.color import B
from spyne.util.dictdoc import yaml_loads, get_object_as_yaml

from neurons import __version__ as NEURONS_VERSION
from neurons import is_reactor_thread
from neurons.daemon.daemonize import daemonize_do
from neurons.daemon.config import LOGLEVEL_STR_MAP
from neurons.daemon.config._wdict import wdict, Twrdict
from neurons.daemon.cli import spyne_to_argparse

from neurons.daemon.config import FILE_VERSION_KEY
from neurons.daemon.config.listener import Service, Listener
from neurons.daemon.config.logutils import Logger, Trecord_as_string, \
    TDynamicallyRotatedLog, TTwistedHandler
from neurons.daemon.config.store import RelationalStore, StorageInfo


_some_prot = ProtocolBase()

_meminfo = None

def update_meminfo():
    """Call this when the process pid changes."""

    global _meminfo

    try:
        import psutil
        process = psutil.Process(os.getpid())
        try:  # psutil 2
            _meminfo = process.get_memory_info
        except AttributeError:  # psutil 3
            _meminfo = process.memory_info

        del process

    except ImportError:
        pass


update_meminfo()


class _SetStaticPathAction(Action):
    def __init__(self, option_strings, dest, const=None, help=None):
        super(_SetStaticPathAction, self).__init__(nargs=1, const=const,
                            option_strings=option_strings, dest=dest, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        self.const.path = abspath(values[0])


def _apply_custom_attributes(cls):
    fti = cls.get_flat_type_info(cls)
    for k, v in sorted(fti.items(), key=lambda i: i[0]):
        attrs = _some_prot.get_cls_attrs(v)
        if attrs.no_file == True:
            v.Attributes.prot_attrs = {YamlDocument: dict(exc=True)}



class Limits(ComplexModel):
    _type_info = [
        ('max_mem_mb', Double(rlimit=resource.RLIMIT_AS)),
    ]


class LimitsChoice(ComplexModel):
    _type_info = [
        ('soft', Limits),
        ('hard', Limits),
    ]

class AlertDestination(ComplexModel):
    type = Unicode(default='email', values=['email'])


EmailAddress = Unicode(pattern=r"[^@\s]+@[^@\s]+")


class EmailAlert(AlertDestination):
    host = Unicode(default='localhost')
    port = UnsignedInteger16(default=25)
    user = Unicode
    sender = Unicode(default='Joe Developer <somepoorhuman@spyne.io>')
    envelope_from = EmailAddress
    password = Unicode
    recipients = M(Array(M(EmailAddress)))


class Daemon(ComplexModel):
    """This is a custom daemon with only pid files, forking, logging.
    No setuid/setgid support is implemented.
    """

    LOGGING_DEBUG_FORMAT = \
                     u"%(l)s %(r)s | %(module)12s:%(lineno)-4d | %(message)s"
    LOGGING_DEVEL_FORMAT = u"%(l)s | %(module)12s:%(lineno)-4d | %(message)s"
    LOGGING_PROD_FORMAT = \
               u"%(l)s %(asctime)s | %(module)12s:%(lineno)-4d | %(message)s"

    _type_info = [
        ('name', Unicode(no_cli=True, help="Daemon Name")),
        ('help', Boolean(no_config=True, short='h',
                                       help="Show this help message and exit")),
        ('file_version', Unicode(no_cli=True, sub_name=FILE_VERSION_KEY,
                                                      default=NEURONS_VERSION)),
        ('uuid', Uuid(
            no_cli=True,
            help=u"Daemon uuid. Regenerated every time a new config file is "
                 u"written. It could come in handy.")),

        ('secret', ByteArray(
            no_cli=True,
            help=u"Secret key for signing cookies and other stuff.")),

        ('daemonize', Boolean(
            default=False,
            help=u"Daemonizes before everything else.")),

        ('workdir', Unicode(
            help=u"The daemon workdir. The daemon won't boot if this doesn't "
                 u"exist and can't be " u"created.")),
        ('uid', Unicode(
            help=u"The daemon user. You need to start the server as a "
                 u"privileged user for this to work.")),
        ('gid', Unicode(
            help=u"The daemon group. You need to start the server as a "
                 u"privileged user for this to work.")),

        ('gids', Array(Unicode,
            help=u"Additional groups for the daemon. Use an empty to drop"
                 u"all additional groups.")),

        ('limits', LimitsChoice.customize(help=u"Process limits.")),

        ('pid_file', String(
            help=u"The path to a text file that contains the pid of the "
                 u"daemonized process.")),

        ('logger_dest', String(
            help=u"The path to the log file. The server won't daemonize "
                 u"without this. Converted to an absolute path if not.")),

        ('logger_dest_rotation_period', Unicode(
            values=['DAILY', 'WEEKLY', 'MONTHLY'],
            help="Logs rotation period")),

        ('logger_dest_rotation_compression', Unicode(
            values=['gzip'],
            help="Logs rotation compression")),

        ('version', Boolean(help=u"Show version", no_file=True)),

        ('bootstrap', Boolean(
            no_file=True,
            help=u"Bootstrap the application. Create schema, insert initial "
                 u"data, etc.")),

        ('log_rss', Boolean(default=False,
            help=u"Prepend resident set size to all logging messages. "
                 u"Requires psutil")),

        ('log_protocol', Boolean(default=False, help=u"Log protocol operations.")),
        ('log_model', Boolean(default=False, help=u"Log model operations.")),
        ('log_cloth', Boolean(default=False, help=u"Log cloth generation.")),
        ('log_interface', Boolean(default=False,
                                         help=u"Log interface build process.")),

        ('write_config', Boolean(no_file=True,
                                   help=u"Write configuration file and exit.")),

        ('alert_dests', Array(AlertDestination, default=[])),

        ('shell', Boolean(
            no_file=True, default=False,
            help=u"Drop to IPython shell. Useful for trying ORM stuff")),
        ('ikernel', Boolean(
            no_file=True, default=False,
            help=u"Start IPython kernel.")),

        ('autoreload', Boolean(
            default=False, help=u"Auto-relaunch daemon process when one of "
                                u"the source files change.")),

        ('dry_run', Boolean(
            default=False, help=u"Do everything up until the reactor start and "
                                u"exit instead of starting the reactor.")),

        ('debug', Boolean(default=False)),
        ('debug_reactor', Boolean(default=False)),

        ('_services', Array(Service, sub_name='services')),
        ('_loggers', Array(Logger, sub_name='loggers')),
    ]

    # FIXME: We need all this hacky magic with custom constructor and properties
    # because Spyne doesn't support custom containers
    def __init__(self, *args, **kwargs):
        super(Daemon, self).__init__(*args, **kwargs)

        services = kwargs.get('services', None)
        if services is not None:
            self.services = services
        if self.services is None:
            self.services = Twrdict(self, 'name')()
        self._set_parent(self.services)

        loggers = kwargs.get('loggers', None)
        if loggers is not None:
            self.loggers = loggers
        if self.loggers is None:
            self.loggers = wdict()
        self._set_parent(self.loggers)

    def _set_parent(self, wrd):
        for v in wrd.values():
            v.set_parent(self)

    @property
    def _services(self):
        if self.services is not None:
            for k, v in self.services.items():
                v.name = k

            return self.services.values()

        self.services = Twrdict(self, 'name')()
        return []

    @_services.setter
    def _services(self, what):
        self.services = what
        if what is not None:
            self.services = Twrdict(self, 'name')([(s.name, s) for s in what])

    @property
    def _loggers(self):
        if self.loggers is not None:
            for k, v in self.loggers.items():
                v.name = k

            return self.loggers.values()

        self.loggers = wdict()
        return []

    @_loggers.setter
    def _loggers(self, what):
        self.loggers = what
        if what is not None:
            self.loggers = wdict([(s.path, s) for s in what])

    @classmethod
    def gen_secret(cls):
        return [os.urandom(64)]

    @classmethod
    def gen_uuid(cls):
        return uuid1()

    @classmethod
    def get_default(cls, daemon_name):
        return cls(
            debug=True,
            uuid=cls.gen_uuid(),
            secret=cls.gen_secret(),
            name=daemon_name,
            log_rss=True,
            workdir=os.getcwd(),
            _loggers=[
                Logger(path='.', level='DEBUG', format=cls.LOGGING_DEVEL_FORMAT),
            ],
        )

    def _clear_other_observers(self, publisher, observer):
        from twisted.logger import LimitedHistoryLogObserver, LogPublisher

        # FIXME: Remove Limited History Observer in a supported way.
        logger.debug("Looking for rogue observers in %r", publisher._observers)

        for o in publisher._observers:
            if isinstance(o, LogPublisher):
                self._clear_other_observers(o, observer)

            elif isinstance(o, LimitedHistoryLogObserver):
                publisher.removeObserver(o)
                o.replayTo(observer)
                logger.debug("Removing observer %r", o)

            else:
                logger.debug("Leaving alone observer %r", o)

    def sanitize(self):
        if self.logger_dest is not None:
            self.logger_dest = abspath(self.logger_dest)
        if self.pid_file is not None:
            self.pid_file = abspath(self.pid_file)

    def apply_logging(self):
        # We're using twisted logging only for IO.
        from twisted.logger import FileLogObserver
        from twisted.logger import Logger, globalLogPublisher

        loggers = {}

        if self.logger_dest is not None:
            DynamicallyRotatedLog = TDynamicallyRotatedLog(self,
                                          self.logger_dest_rotation_compression)

            self.logger_dest = abspath(self.logger_dest)
            if access(dirname(self.logger_dest), os.R_OK | os.W_OK):
                log_dest = DynamicallyRotatedLog.fromFullPath(self.logger_dest)

            else:
                Logger().warn("%r is not accessible. We need rwx on it to "
                               "rotate logs." % dirname(self.logger_dest))
                log_dest = open(self.logger_dest, 'w+')

            if self.debug:
                formatter = logging.Formatter(self.LOGGING_DEBUG_FORMAT)
            else:
                formatter = logging.Formatter(self.LOGGING_PROD_FORMAT)

        else:
            if self.debug:
                formatter = logging.Formatter(self.LOGGING_DEBUG_FORMAT)
            else:
                formatter = logging.Formatter(self.LOGGING_DEVEL_FORMAT)
            log_dest = sys.stdout

            try:
                import colorama
                colorama.init()
                logger.debug("colorama loaded.")

            except Exception as e:
                logger.debug("coloarama not loaded: %r" % e)

        record_as_string = Trecord_as_string(formatter)
        observer = FileLogObserver(log_dest, record_as_string)
        globalLogPublisher.addObserver(observer)
        self._clear_other_observers(globalLogPublisher, observer)

        TwistedHandler = TTwistedHandler(self, loggers, _meminfo)

        handler = TwistedHandler()
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)

        self.pre_logging_apply()

        for l in self.loggers.values() or []:
            l.apply()

        return self

    def boot_message(self):
        import spyne
        import neurons
        import twisted
        import sqlalchemy

        logging.info("Booting daemon '%s'. We have spyne-%s, neurons-%s, "
                     "sqlalchemy-%s and twisted-%s.",
                        self.name, B(spyne.__version__), B(neurons.__version__),
                          B(sqlalchemy.__version__), B(twisted.version.short()))

    @staticmethod
    def hello_darkness_my_old_friend():
        logger.info("Quis custodiet ipsos custodes?")

    def pre_logging_apply(self):
        logging.info("Root logger level = %s",
                                    LOGLEVEL_STR_MAP[logging.getLogger().level])

        self.hello_darkness_my_old_friend()
        self.boot_message()

    def pre_limits_apply(self):
        pass

    @classmethod
    def apply_limits_impl(cls, limtype, limits):
        if limits is None:
            return

        if limits.max_mem_mb is not None:
            curr_rlimit = Limits._type_info['max_mem_mb'].Attributes.rlimit

            oldlim = resource.getrlimit(curr_rlimit)

            newlim = list(oldlim)
            newlim[limtype] = int(limits.max_mem_mb * 1024 ** 2)
            newlim = tuple(newlim)

            resource.setrlimit(curr_rlimit, newlim)
            logger.debug("RLIMIT_AS: was: %r now: %r", oldlim, newlim)

    def apply_limits(self):
        # In case a resource-intensive task needs to be done prior to daemon
        # boot. It's a bad idea but hey, who are we to tell you what to do?..
        self.pre_limits_apply()

        if self.limits is None:
            return

        if self.limits.soft is None and self.limits.hard is None:
            return

        # Remember:
        #     soft, hard = resource.getrlimit(whatever)
        SOFT = 0
        HARD = 1

        self.apply_limits_impl(SOFT, self.limits.soft)
        self.apply_limits_impl(HARD, self.limits.hard)

    def apply_listeners(self):
        dl = []

        for s in self._services:
            if isinstance(s, Listener):
                if not s.disabled:
                    dl.append(s.listen())

        return dl

    def apply_uidgid(self):
        if self.uid is not None:
            os.setuid(self.uid)

        if self.gid is not None:
            os.setgid(self.gid)

        if self.gids is not None:
            os.setgroups(self.gids)

    def apply(self, daemonize=True):
        """Daemonizes the process if requested, then sets up logging and pid
        files.
        """

        # Daemonization won't work if twisted is imported before fork().
        # It's best to know this in advance or you'll have to deal with daemons
        # that work perfectly well in development environments but won't boot
        # in production ones, solely because daemonization involves closing of
        # every previously open file descriptors.
        if daemonize and ('twisted' in sys.modules):
            import twisted
            raise Exception(
                 "Twisted is already imported from {}".format(twisted.__file__))

        self.sanitize()
        if daemonize and self.daemonize:
            assert self.logger_dest, "Refusing to start without any log output."

            workdir = self.workdir
            if workdir is None:
                workdir = '/'
            daemonize_do(workdir=workdir)

            update_meminfo()

        else:
            if self.workdir is not None:
                os.chdir(self.workdir)
                logger.debug("Change working directory to: %s", self.workdir)

        self.apply_limits()
        self.apply_logging()
        self.apply_listeners()
        self.apply_uidgid()

        if daemonize and self.pid_file is not None:
            pid = os.getpid()
            with open(self.pid_file, 'w') as f:
                f.write(str(pid))
                logger.debug("Pid file is at: %r", self.pid_file)

        return self

    def add_reactor_checks(self):
        """Logs warnings when stuff that could be better off in a dedicated
        thread is found running inside the reactor thread."""

        from sqlalchemy import event

        def before_cursor_execute(conn, cursor, statement, parameters, context,
                                                                   executemany):
            if is_reactor_thread():
                logger.warning("SQL query found inside reactor thread. "
                                    "Statement: %s Traceback: %s",
                                   statement, ''.join(traceback.format_stack()))

        for store in self._stores:
            if not isinstance(store, RelationalStore):
                continue

            engine = store.itself.engine
            event.listen(engine, "before_cursor_execute", before_cursor_execute)

            logger.info("Installed reactor warning hook for engine %s.", engine)

    @classmethod
    def parse_config(cls, daemon_name, argv=None):
        _apply_custom_attributes(cls)
        retval = cls.get_default(daemon_name)
        file_name = abspath('%s.yaml' % daemon_name)

        argv_parser = spyne_to_argparse(cls, ignore_defaults=True)
        cli = {}
        if argv is not None and len(argv) > 1:
            cli = dict(argv_parser.parse_args(argv[1:]).__dict__.items())
            if cli['config_file'] is not None:
                file_name = abspath(cli['config_file'])
                del cli['config_file']

        exists = isfile(file_name) and os.access(file_name, os.R_OK)
        if exists and getsize(file_name) > 0:
            s = open(file_name, 'rb').read()
            s = retval._migrate_impl(s)
            retval = yaml_loads(s, cls, validator='soft', polymorphic=True)

        else:
            if not access(dirname(file_name), os.R_OK | os.W_OK):
                raise Exception("File %r can't be created in %r" %
                                                (file_name, dirname(file_name)))

        for k, v in cli.items():
            if not v in (None, False):
                setattr(retval, k, v)

        retval.config_file = file_name

        import neurons.daemon
        neurons.daemon.config_data = retval

        return retval

    def do_write_config(self):
        open(self.config_file, 'wb').write(get_object_as_yaml(self,
                                              self.__class__, polymorphic=True))

    def get_email_alert_addresses(self):
        retval = []

        if self.alert_dests is not None:
            for dest in self.alert_dests:
                if isinstance(dest, EmailAlert):
                    retval.extend(dest.recipients)

        return retval

    def _migrate_impl(self, s):
        """Migrates old config files to new ones. Takes old config file as
        string and returns the new one.

        This is not supposed to be overridden. Override migrate_dict if you
        want add migration
        """

        import yaml
        config_dict = yaml.load(s)
        key, = config_dict.keys()

        config_root = config_dict[key]
        config_version = config_root.get(FILE_VERSION_KEY, None)

        if config_version is None:
            # Perform relational store migration
            try:
                z = config_root['stores'][0]['Relational']

            except KeyError:
                pass

            else:
                del config_root['stores'][0]['Relational']
                config_root['stores'][0]['RelationalStore'] = z

        else:
            config_version = [int(ver_tuple)
                                     for ver_tuple in config_version.split(".")]
            while len(config_version) < 3:
                config_version.append(0)
            config_version = tuple(config_version)

            self.migrate(config_dict, config_version)

        config_root[FILE_VERSION_KEY] = NEURONS_VERSION
        return yaml.dump(config_dict, indent=4, default_flow_style=False)

    def migrate(self, config_dict, config_version):
        """Makes in-place changes in the config_dict before it's parsed and
        applied by the configuration engine."""

        # example
        if config_version < (0, 6, 0):
            key, = config_dict.keys()
            _ = config_dict[key]
            # do something with the dict

        pass


class ServiceDaemon(Daemon):
    """This is a daemon with data stores."""

    _type_info = [
        ('write_wsdl', Unicode(
            help=u"Write Wsdl document(s) to the given directory and exit. "
                 u"It is created if missing", no_file=True, metavar='WSDL_DIR')),

        ('write_xsd', Unicode(
            help=u"Write Xml Schema document(s) to given directory and exit. "
                 u"It is created if missing", no_file=True, metavar='XSD_DIR')),

        ('log_orm', Boolean(default=False,
                                       help=u"Log SQLAlchemy ORM operations.")),
        ('log_queries', Boolean(default=False, help=u"Log SQL queries.")),
        ('log_results', Boolean(default=False,
                        help=u"Log SQL query results in addition to queries.")),
        ('log_dbconn', Boolean(default=False,
            help=u"Prepend number of open database connections to all "
                                                         u"logging messages.")),
        ('log_sqlalchemy', Boolean(default=False,
                               help=u"Log SQLAlchemy messages in debug level")),

        ('drop_all_tables', Boolean(help="Drops all tables in the database.",
                                                                 no_file=True)),

        ('main_store', Unicode(help=u"The name of the store for binding "
                                         u"neurons.TableModel's metadata to.")),

        ('gen_data', Boolean(help=u"Generates random data", no_file=True)),
        ('_stores', Array(StorageInfo, sub_name='stores')),
    ]

    # FIXME: We need all this hacky magic with custom constructor and properties
    # because Spyne doesn't support custom containers
    def __init__(self, *args, **kwargs):
        super(Daemon, self).__init__(*args, **kwargs)

        stores = kwargs.get('stores', None)
        if stores is not None:
            self.stores = stores
        if not hasattr(self, 'stores') or self.stores is None:
            self.stores = wdict()
        self._set_parent(self.stores)

    @property
    def _stores(self):
        if self.stores is not None:
            for k, v in self.stores.items():
                v.name = k

            return self.stores.values()

        self.stores = wdict()
        return self.stores.values()

    @_stores.setter
    def _stores(self, what):
        self.stores = what
        if what is not None:
            self.stores = wdict([(s.name, s) for s in what])

    @classmethod
    def get_default(cls, daemon_name):
        return cls(
            debug=True,
            uuid=cls.gen_uuid(),
            secret=cls.gen_secret(),
            name=daemon_name,
            _stores=[
                RelationalStore(
                    name=u"sql_main",
                    backend=u"sqlalchemy",
                    pool_size=10,
                    pool_recycle=3600,
                    pool_timeout=30,
                    max_overflow=3,
                    conn_str=u'postgresql://{user}:@/{daemon}_{user}'.format(
                                    daemon=daemon_name, user=getpass.getuser()),
                    sync_pool=True,
                    async_pool=True,
                ),
            ],
            main_store=u'sql_main',
            logger_dest_rotation_period='WEEKLY',
            logger_dest_rotation_compression='gzip',
            _loggers=[
                Logger(
                    path=u'.',
                    level=u'DEBUG',
                    # TODO: implement this
                    # format=cls.LOGGING_DEVEL_FORMAT
                ),
            ],
        )

    def apply_storage(self):
        for store in self.stores.values():
            try:
                store.apply()
            except Exception as e:
                logger.exception(e)
                raise

            if self.main_store == store.name:
                engine = store.itself.engine

                import neurons
                neurons.TableModel.Attributes.sqla_metadata.bind = engine

        if self.log_dbconn:
            handler = logging.getLogger().handlers[0]
            _mr = handler._modify_record
            pool = self.get_main_store().engine.pool
            def _modify_record(record):
                _mr(record)
                record.msg = "[%d]%s" % (pool.checkedout(), record.msg)
            handler._modify_record = _modify_record

        return self

    def apply(self, daemonize=True):
        """Daemonizes the process if requested, then sets up logging and pid
        files plus data stores.
        """

        # FIXME: apply_storage could return a deferred due to txpool init.

        super(ServiceDaemon, self).apply(daemonize=daemonize)

        self.apply_storage()

        return self

    @staticmethod
    def _set_log_level(ns, level):
        logging.getLogger(ns).setLevel(level)
        logging.info("Sublogger level initialized for '%s' as %s", ns,
                                                        LOGLEVEL_STR_MAP[level])

    def pre_logging_apply(self):
        if self.log_protocol or self.log_queries or self.log_results or \
                    self.log_model or self.log_interface or self.log_protocol or \
                                                                self.log_cloth:
            logging.getLogger().setLevel(logging.DEBUG)

        if self.log_model:
            self._set_log_level('spyne.model', logging.DEBUG)

        else:
            self._set_log_level('spyne.model', logging.INFO)

        if self.log_interface:
            self._set_log_level('spyne.interface', logging.DEBUG)
        else:
            self._set_log_level('spyne.interface', logging.INFO)

        rpc_loggers = (
            'spyne.protocol',
            'spyne.protocol.xml',
            'spyne.protocol.dictdoc',
            'spyne.protocol.cloth.to_parent',
            'spyne.protocol.cloth.to_cloth.serializer',
            'neurons.form',
            'neurons.polymer.protocol',
        )

        if self.log_protocol:
            for ns in rpc_loggers:
                self._set_log_level(ns, logging.DEBUG)

        else:
            for ns in rpc_loggers:
                self._set_log_level(ns, logging.INFO)

        if self.log_cloth:
            self._set_log_level('spyne.protocol.cloth.to_cloth.cloth',
                                                                  logging.DEBUG)
        else:
            self._set_log_level('spyne.protocol.cloth.to_cloth.cloth',
                                                                   logging.INFO)

        if self.log_sqlalchemy:
            self._set_log_level('sqlalchemy', logging.DEBUG)

        else:
            if self.log_orm:
                self._set_log_level('sqlalchemy.orm', logging.DEBUG)

            if self.log_queries or self.log_results:
                if self.log_queries:
                    self._set_log_level('sqlalchemy.engine', logging.INFO)

                if self.log_results:
                    self._set_log_level('sqlalchemy.engine', logging.DEBUG)

    def get_main_store(self):
        return self.stores[self.main_store].itself

    def get_main_store_config(self):
        return self.stores[self.main_store]
