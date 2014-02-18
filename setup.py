import setuptools

# TODO: test_requires

setuptools.setup(
    zip_safe=True,
    name='cosmo-plugin-openstack-common',
    version='0.1',
    author='Ilya Sher',
    author_email='ilya.sher@coding-knight.com',
    packages=['cosmo_plugin_openstack_common'],
    license='LICENSE',
    description='Common code for Cosmo OpenStack plugins',
    install_requires=[
        "cosmo-plugin-common",
        "python-keystoneclient",
        "python-neutronclient",
        "python-novaclient",
    ],
    dependency_links=[
        "https://github.com/Fewbytes/cosmo-plugin-common/tarball/" \
        "master#egg=cosmo-plugin-common-0.1"
    ]
)
