"""Example Stackzilla resource for a Linode instance."""
import os
from stackzilla.resource.ssh_key import StackzillaSSHKey
from stackzilla.provider.linode.instance import LinodeInstance

LinodeInstance.token = os.getenv('STACKZILLA_LINODE_TOKEN')

class MyKey(StackzillaSSHKey):
    def __init__(self) -> None:
        super().__init__()
        self.key_size = 2048


class MyServer(LinodeInstance):
    def __init__(self):
        super().__init__()
        self.region = 'us-east'
        self.type = 'g6-nanode-1'
        self.image = 'linode/alpine3.13'
        self.label = 'Stackzilla_Test-Linode.1'
        self.tags = ['testing']
        self.private_ip = False
        self.ssh_key = MyKey
