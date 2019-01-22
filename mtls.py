"""mtls (Mutual TLS) - A cli for creating short-lived client certiicates."""

import os
import sys
from configparser import ConfigParser

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import click
import gnupg
import json
import requests

__author__ = 'Danny Grove <danny@drgrovell.com>'
VERSION = 'version 0.1'
NAME = 'mtls - Mutual TLS'


class MutualTLS:
    MISSING_CONFIGURATION = """
    Configuration missing for mtls at {}/{}
    """
    MISSING_CONFIGURATION_FOR_SERVER = """
    Configuration missing for {server}:

    Please ensure that you have a configuration for you server similar to:
    [{server}]
    email=foo@example.com
    url=ca.example.com
    server_fingerprint=XXXXX

    For more details see config.ini.example
    """
    CONFIG_FOLDER_PATH = '{}/.config/mtls'.format(os.environ['HOME'])
    CONFIG_FILE = 'config.ini'
    USER_KEY = '{}.key.gpg'.format(os.environ['USER'])
    GNUPGHOME = os.environ.get('GNUPGHOME', '{}/{}'.format(os.environ['HOME'],
                                                           '.gnupg'))

    def __init__(self, server):
        self.gpg = gnupg.GPG(gnupghome=self.GNUPGHOME)
        self.gpg.encoding = 'utf-8'
        self.config = self.get_config()
        self.server = server
        self.server_in_config()

    def run(self):
        key = self.get_key_or_generate()
        csr = self.generate_csr(key)
        cert = self.encrypt_and_send_to_server(csr)

    @staticmethod
    def print_version(ctx, param, value):
        """Prints the version of the application."""
        if not value or ctx.resilient_parsing:
            return
        click.echo(NAME + ' ' + VERSION)
        ctx.exit()

    def check_for_config(self):
        """Check if the config exists, otherwise exit."""
        config_dir_exists = os.path.isdir(self.CONFIG_FOLDER_PATH)
        config_exists = os.path.isfile('{}/{}'.format(self.CONFIG_FOLDER_PATH,
                                                      self.CONFIG_FILE))

        if not config_dir_exists or not config_exists:
            msg = self.MISSING_CONFIGURATION.format(self.CONFIG_FOLDER_PATH,
                                                    self.CONFIG_FILE)
            click.echo(msg)
            sys.exit(1)

    def get_config(self):
        """Gets config from file.

        Returns:
            config
        """
        self.check_for_config()
        config = ConfigParser()
        config.read('{}/{}'.format(self.CONFIG_FOLDER_PATH, self.CONFIG_FILE))
        return config

    def server_in_config(self):
        """Determines if the set server is in the config, otherwise exit."""
        if self.server is None and len(self.config.sections()) > 1:
            click.echo('You have multiple servers configured, please ' +
                       'selection one with the --server (-s) option')
        if self.server not in self.config:
            click.echo(self.MISSING_CONFIGURATION_FOR_SERVER)
            sys.exit(1)

    def encrypt(self, data, recipient, sign=False):
        """Encrypt data using PGP to recipient."""
        if sign is True:
            click.echo('Encrypting and Signing data...')
        return self.gpg.encrypt(data, recipient, sign=sign)

    def get_key_or_generate(self):
        """Get users key from file or generate a new RSA Key.

        Returns:
            key - RSA Key
        """
        key = None
        config_folder = self.CONFIG_FOLDER_PATH
        user_key = self.USER_KEY
        key_path = f'{config_folder}/{user_key}'
        if os.path.isfile(key_path):
            click.echo('Decrypting User Key...')
            encrypted_key_file = open(key_path, 'rb')
            key_data = self.gpg.decrypt_file(encrypted_key_file)
            byte_key_data = bytes(str(key_data), 'utf-8')
            key = serialization.load_pem_private_key(byte_key_data,
                                                     password=None,
                                                     backend=default_backend())
        else:
            click.echo('Generating User Key')
            key = rsa.generate_private_key(
                    public_exponent=65537,
                    key_size=4096,
                    backend=default_backend())
            openssl_format = serialization.PrivateFormat.TraditionalOpenSSL
            no_encyption = serialization.NoEncryption()
            key_data = key.private_bytes(encoding=serialization.Encoding.PEM,
                                         format=openssl_format,
                                         encryption_algorithm=no_encyption)
            user_fingerprint = self.config.get(self.server, 'fingerprint')
            encrypted_key = self.encrypt(key_data, user_fingerprint)
            user_email = self.config.get(self.server, 'email')
            click.echo('Encrypting file to {}'.format(user_email))
            with open(key_path, 'w') as f:
                f.write(str(encrypted_key))
        return key

    def generate_csr(self, key):
        country = self.config.get(self.server, 'country')
        state = self.config.get(self.server, 'state')
        locality = self.config.get(self.server, 'locality')
        organization_name = self.config.get(self.server, 'organization_name')
        common_name = self.config.get(self.server, 'common_name')
        csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, country),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, state),
            x509.NameAttribute(NameOID.LOCALITY_NAME, locality),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization_name),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])).sign(key, hashes.SHA256(), default_backend())
        return csr

    def encrypt_and_send_to_server(self, csr):
        server_fingerprint = self.config.get(self.server, 'server_fingerprint')
        enc_csr = self.encrypt(csr.public_bytes(serialization.Encoding.PEM),
                               server_fingerprint,
                               sign=True)
        payload = {
            'csr': str(enc_csr),
            'lifetime': '18',  # Currently locked 18 hours
            'host': os.environ.get('HOST', None),
            'type': 'CREATE_CERTIFICATE'
        }
        server_url = self.config.get(self.server, 'url')
        r = requests.post(server_url, json=payload)
        response = r.json()
        print(str(response))


@click.command()
@click.option('--server', '-s')
@click.option('--version', '-v',
              is_flag=True, callback=MutualTLS.print_version,
              expose_value=False, is_eager=True)
def main(server=None):
    mtls = MutualTLS(server)
    mtls.run()


if __name__ == '__main__':
    main()
