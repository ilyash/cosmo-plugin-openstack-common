import collections
from functools import wraps
import logging
import random
import string
import time
import unittest

import keystoneclient.v2_0.client as keystone_client
import neutronclient.v2_0.client as neutron_client
import neutronclient.common.exceptions as neutron_exceptions
import novaclient.v1_1.client as nova_client

import cosmo.events

import cosmo_plugin_common as cpc

import cloudify.manager
import cloudify.decorators
with_logger = cloudify.decorators.with_logger

PREFIX_RANDOM_CHARS = 3
CLEANUP_RETRIES = 10
CLEANUP_RETRY_SLEEP = 2

# Configs


class KeystoneConfig(cpc.Config):
    which = 'keystone'


class NeutronConfig(cpc.Config):
    which = 'neutron'


class TestsConfig(cpc.Config):
    which = 'os_tests'


class OpenStackClient(object):
    def get(self, config=None, *args, **kw):
        static_config = self.__class__.config().get()
        cfg = {}
        cfg.update(static_config)
        if config:
            cfg.update(config)
        ret = self.connect(cfg, *args, **kw)
        ret.format = 'json'
        return ret


# Clients acquireres


class KeystoneClient(OpenStackClient):

    config = KeystoneConfig

    def connect(self, cfg):
        args = {field: cfg[field] for field in ('username', 'password', 'tenant_name', 'auth_url')}
        return keystone_client.Client(**args)


class NovaClient(OpenStackClient):

    config = KeystoneConfig

    def connect(self, cfg, region=None):
        return nova_client.Client(username=cfg['username'],
                             api_key=cfg['password'],
                             project_id=cfg['tenant_name'],
                             auth_url=cfg['auth_url'],
                             region_name=region or cfg['region'],
                             http_log_debug=False)


class NeutronClient(OpenStackClient):

    config = NeutronConfig

    def connect(self, cfg):
        ks = KeystoneClient().get(config = cfg.get('keystone_config'))
        # ret = neutron_client.Client('2.0', endpoint_url=cfg['url'], token=ks.auth_token)
        ret = NeutronClientWithSugar(endpoint_url=cfg['url'], token=ks.auth_token)
        # print(ret.cosmo_list_routers())
        ret.format = 'json'
        return ret


# Decorators

def _find_instanceof_in_kw(cls, kw):
    ret = [v for v in kw.values() if isinstance(v, cls)]
    if len(ret) != 1:
        raise RuntimeError(
            "Expected to find exactly one instance of {0} in "
            "kwargs but found {1}".format(cls, len(ret)))
    return ret[0]

def _find_context_in_kw(kw):
    return _find_instanceof_in_kw(cloudify.context.CloudifyContext, kw)

def with_neutron_client(f):
    @wraps(f)
    def wrapper(*args, **kw):
        ctx = _find_context_in_kw(kw)
        if ctx:
            config = ctx.properties.get('neutron_config')
        neutron_client = NeutronClient().get(config=config)
        kw['neutron_client'] = neutron_client
        return f(*args, **kw)
    return wrapper

def with_nova_client(f):
    @wraps(f)
    def wrapper(*args, **kw):
        ctx = _find_context_in_kw(kw)
        if ctx:
            config = ctx.properties.get('nova_config', {})
        nova_client = NovaClient().get(config=config)
        kw['nova_client'] = nova_client
        return f(*args, **kw)
    return wrapper

# Sugar for clients


class NeutronClientWithSugar(neutron_client.Client):

    def __init__(self, *args, **kw):
        return neutron_client.Client.__init__(self, *args, **kw)

    def cosmo_plural(self, obj_type_single):
        return obj_type_single + 's'

    def cosmo_get_named(self, obj_type_single, name, **kw):
        return self.cosmo_get(obj_type_single, name=name, **kw)

    def cosmo_get(self, obj_type_single, **kw):
        ls = list(self.cosmo_list(obj_type_single, **kw))
        if len(ls) != 1:
            raise RuntimeError(
                "Expected exactly one object of type {0} "
                "with match {1} but there are {2}".format(
                    obj_type_single, kw, len(ls)))
        return ls[0]


    def cosmo_list(self, obj_type_single, **kw):
        """ Sugar for list_XXXs()['XXXs'] """
        obj_type_plural = self.cosmo_plural(obj_type_single)
        for obj in getattr(self, 'list_' + obj_type_plural)(**kw)[obj_type_plural]:
            yield obj

    def cosmo_list_prefixed(self, obj_type_single, name_prefix):
        for obj in self.cosmo_list(obj_type_single):
            if obj['name'].startswith(name_prefix):
                yield obj

    def cosmo_delete_prefixed(self, name_prefix):
        # Cleanup all neutron.list_XXX() objects with names starting with self.name_prefix
        for obj_type_single in 'port', 'router', 'network', 'subnet':
            for obj in self.cosmo_list_prefixed(obj_type_single, name_prefix):
                # self.logger.info("Deleting {0} {1}".format(obj_type_single, obj.get('name', obj['id'])))
                getattr(self, 'delete_' + obj_type_single)(obj['id'])

# maybe move to plugins common - start

class MockNodeStatesStorage(collections.defaultdict):
    def __init__(self, logger):
        self.logger = logger
        return collections.defaultdict.__init__(self)

    def __missing__(self, k):
        self[k] = MockNodeState(self.logger, k)
        return self[k]

class MockNodeState(cloudify.manager.NodeState):

    def __init__(self, logger, __cloudify_id):
        self.logger = logger
        cloudify.manager.NodeState.__init__(self, __cloudify_id)

    def get(self, k):
        self.logger.debug("MockNodeState<{0}>.get('{1}')".format(self.id, k))
        return cloudify.manager.NodeState.get(self, k)

    def put(self, k, v):
        self.logger.debug("MockNodeState<{0}>.put('{1}', '{2}')".format(self.id, k, v))
        return cloudify.manager.NodeState.put(self, k, v)

    # Ensure no saves
    def get_updated_properties(self):
        return {}

# maybe move to plugins common - end


class TestCase(unittest.TestCase):

    def get_nova_client(self):
        r = NovaClient().get()
        self.get_nova_client = lambda: r
        return self.get_nova_client()

    def get_neutron_client(self):
        r = NeutronClient().get()
        self.get_neutron_client = lambda: r
        return self.get_neutron_client()


    def _mock_send_event(self, *args, **kw):
        self.logger.debug("_mock_send_event(args={0}, kw={1})".format(args, kw))

    def _mock_get_node_state(self, __cloudify_id, *args, **kw):
        self.logger.debug("_mock_get_node_state(__cloudify_id={0} args={1}, kw={2})".format(__cloudify_id, args, kw))
        return self.nodes_data[__cloudify_id]

    @with_logger
    def setUp(self, logger):
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.logger = logger
        self.logger.level = logging.DEBUG
        self.logger.debug("Cosmo test setUp() called")
        chars = string.ascii_uppercase + string.digits
        self.name_prefix = 'cosmo_test_{0}_'\
            .format(''.join(random.choice(chars) for x in range(PREFIX_RANDOM_CHARS)))
        self.timeout = 120
        cosmo.events._send_event = self._mock_send_event # WARNING: using implementation detail (cosmo.events._send_event)
        cloudify.decorators.get_node_state = self._mock_get_node_state
        cloudify.manager.get_node_state = self._mock_get_node_state
        self.nodes_data = MockNodeStatesStorage(self.logger)


        self.logger.debug("Cosmo test setUp() done")

    def tearDown(self):
        self.logger.debug("Cosmo test tearDown() called")
        servers_list = self.get_nova_client().servers.list()
        for server in servers_list:
            if server.name.startswith(self.name_prefix):
                self.logger.info("Deleting server with name " + server.name)
                try:
                    server.delete()
                except BaseException:
                    self.logger.warning("Failed to delete server with name "
                                        + server.name)
            else:
                self.logger.info("NOT deleting server with name "
                                 + server.name)
        for i in range(1, CLEANUP_RETRIES+1):
            try:
                self.logger.debug(
                    "Neutron resources cleanup attempt {0}/{1}"
                    .format(i, CLEANUP_RETRIES)
                )
                NeutronClient().get().cosmo_delete_prefixed(self.name_prefix)
                break
            except neutron_exceptions.NetworkInUseClient:
                pass
            time.sleep(CLEANUP_RETRY_SLEEP)
        self.logger.debug("Cosmo test tearDown() done")

    @with_neutron_client
    def create_network(self, name_suffix, neutron_client):
        return neutron_client.create_network({'network': {
            'name': self.name_prefix + name_suffix, 'admin_state_up': True
        }})['network']

    @with_neutron_client
    def create_subnet(self, name_suffix, cidr, neutron_client, network=None):
        if not network:
            network = self.create_network(name_suffix)
        return neutron_client.create_subnet({
            'subnet': {
                'name': self.name_prefix + name_suffix,
                'ip_version': 4,
                'cidr': cidr,
                'network_id': network['id']
            }
        })['subnet']

    @with_neutron_client
    def assertThereIsOneAndGet(self, obj_type_single, neutron_client, **kw):
        objs = list(neutron_client.cosmo_list(obj_type_single, **kw))
        self.assertEquals(1, len(objs))
        return objs[0]

    assertThereIsOne = assertThereIsOneAndGet

    @with_neutron_client
    def assertThereIsNo(self, obj_type_single, neutron_client, **kw):
        objs = list(neutron_client.cosmo_list(obj_type_single, **kw))
        self.assertEquals(0, len(objs))
