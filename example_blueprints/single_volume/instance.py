from stackzilla.provider.linode.instance import LinodeInstance

from .token import token
LinodeInstance.token = token

class MyServer(LinodeInstance):
    def __init__(self):
        super().__init__()
        self.region = 'us-east'
        self.type = 'g6-nanode-1'
        self.image = 'linode/alpine3.12'
        self.label = 'Stackzilla_Test-Linode.1'
        self.tags = ['testing']
        self.private_ip = False
