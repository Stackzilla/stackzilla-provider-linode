from stackzilla.provider.linode.volume import LinodeVolume
#from .instance import MyServer
from .token import token
LinodeVolume.token = token
"""
class MyVolume(LinodeVolume):
    def __init__(self):
        super().__init__()

        self.size = 60
        self.region = 'us-east'
        self.tags = ['foo', 'bar', 'zim']
        self.label = 'Stackzilla_Testing_2'
        self.instance = MyServer
"""
