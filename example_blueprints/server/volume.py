"""Example volume blueprint resource for a Linode volume."""
import os
from stackzilla.provider.linode.volume import LinodeVolume
from .instance import MyServer
LinodeVolume.token = os.getenv('STACKZILLA_LINODE_TOKEN')

class MyVolume(LinodeVolume):
    def __init__(self):
        super().__init__()

        self.size = 120
        self.region = 'us-east'
        self.tags = ['foo', 'bar', 'zim']
        self.label = 'Stackzilla_Testing_2'
        self.instance = MyServer
        self.mount_point = '/mnt/zim_vol'
        self.file_system_type = 'ext4'
