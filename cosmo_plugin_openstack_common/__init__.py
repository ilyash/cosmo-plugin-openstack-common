# vim: ts=4 sw=4 et

import logging
import random
import string
import unittest

import keystoneclient.v2_0.client as keystone_client
import neutronclient.v2_0.client as neutron_client
import novaclient.v1_1.client as nova_client

import cosmo_plugin_common as cpc

PREFIX_RANDOM_CHARS = 3

# Configs

class KeystoneConfig(cpc.Config):
    which = 'keystone'


class NeutronConfig(cpc.Config):
    which = 'neutron'

class TestsConfig(cpc.Config):
    which = 'os_tests'

class OpenStackClient(object):
    def get(self, *args, **kw):
        cfg = self.__class__.config().get()
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
        ks = KeystoneClient().get()
        # ret = neutron_client.Client('2.0', endpoint_url=cfg['url'], token=ks.auth_token)
        ret = NeutronClientWithSugar(endpoint_url=cfg['url'], token=ks.auth_token)
        # print(ret.cosmo_list_routers())
        ret.format = 'json'
        return ret


# Sugar for clients


class NeutronClientWithSugar(neutron_client.Client):

    def __init__(self, *args, **kw):
        ret = neutron_client.Client.__init__(self, *args, **kw)
        # UNFINISHED
        # for obj_type_single in 'router', 'network', 'subnet':
        #   def f(obj_type_single):
        #       return self.cosmo_list_objects_of_type(obj_type_single)
        #   obj_type_plural = self.cosmo_plural(obj_type_single)
        #   setattr(self, 'cosmo_list_' + obj_type_plural, f)
        return ret

    def cosmo_plural(self, obj_type_single):
        return obj_type_single + 's'

    def cosmo_list_objects_of_type_with_name(self, obj_type_single, name):
        """ Sugar for list_XXXs()['XXXs'] """
        obj_type_plural = self.cosmo_plural(obj_type_single)
        for obj in getattr(self, 'list_' + obj_type_plural)(name=name)[obj_type_plural]:
            yield obj

    def cosmo_get_object_of_type_with_name(self, obj_type_single, name):
        ls = list(self.cosmo_list_objects_of_type_with_name(obj_type_single, name))
        if len(ls) != 1:
            raise RuntimeError(
                "Expected exactly one object of type {0} "
                "with name {1} but there are {2}".format(
                    obj_type_single, name, len(ls)))
        return ls[0]


    def cosmo_list_objects_of_type(self, obj_type_single):
        """ Sugar for list_XXXs()['XXXs'] """
        obj_type_plural = self.cosmo_plural(obj_type_single)
        for obj in getattr(self, 'list_' + obj_type_plural)()[obj_type_plural]:
            yield obj

    def cosmo_list_prefixed_objects_of_type(self, obj_type_single, name_prefix):
        for obj in self.cosmo_list_objects_of_type(obj_type_single):
            if obj['name'].startswith(name_prefix):
                yield obj

    def cosmo_delete_prefixed_objects(self, name_prefix):
        # Cleanup all neutron.list_XXX() objects with names starting with self.name_prefix
        for obj_type_single in 'router', 'network', 'subnet':
            for obj in self.cosmo_list_prefixed_objects_of_type(obj_type_single, name_prefix):
                # self.logger.info("Deleting {0} {1}".format(obj_type_single, obj.get('name', obj['id'])))
                getattr(self, 'delete_' + obj_type_single)(obj['id'])

class TestCase(unittest.TestCase):
    
    def get_nova_client(self):
        r = NovaClient().get()
        self.get_nova_client = lambda: r
        return self.get_nova_client()

    def get_neutron_client(self):
        r = NeutronClient().get()
        self.get_neutron_client = lambda: r
        return self.get_neutron_client()


    def setUp(self):
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.level = logging.DEBUG
        self.logger.debug("Cosmo test setUp() called")
        chars = string.ascii_uppercase + string.digits
        self.name_prefix = 'cosmo_test_{0}_'\
            .format(''.join(random.choice(chars) for x in range(PREFIX_RANDOM_CHARS)))
        self.timeout = 120
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
        NeutronClient().get().cosmo_delete_prefixed_objects(self.name_prefix)
        self.logger.debug("Cosmo test tearDown() done")

