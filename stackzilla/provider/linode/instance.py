"""Linode Instance resource definition for Stackzilla."""
import re
from typing import Any, List

from linode_api4 import LinodeClient
from linode_api4.errors import ApiError
from linode_api4.objects.linode import Instance

from stackzilla.attribute import StackzillaAttribute
from stackzilla.logger.provider import ProviderLogger
from stackzilla.resource.base import ResourceVersion, StackzillaResource
from stackzilla.resource.compute import StackzillaCompute, SSHAddress, SSHCredentials
from stackzilla.resource.compute.exceptions import SSHConnectError
from stackzilla.resource.exceptions import ResourceVerifyError, ResourceCreateFailure


from .utils import LINODE_REGIONS, LINODE_INSTANCE_TYPES, LINODE_IMAGE_TYPES

class LinodeInstance(StackzillaCompute):
    """Stackzilla provider for Linode Instances."""
    # Dynamic attributes
    instance_id = StackzillaAttribute(dynamic=True)
    root_password = StackzillaAttribute(dynamic=True, secret=True)
    ipv4 = StackzillaAttribute(dynamic=True)
    ipv6 = StackzillaAttribute(dynamic=True)

    # Configurable attributes
    type = StackzillaAttribute(required=True, choices=LINODE_INSTANCE_TYPES)
    region = StackzillaAttribute(required=True, choices=LINODE_REGIONS, modify_rebuild=True)
    image = StackzillaAttribute(required=True, choices=LINODE_IMAGE_TYPES, modify_rebuild=True)
    label = StackzillaAttribute()
    group = StackzillaAttribute()
    tags = StackzillaAttribute()
    private_ip = StackzillaAttribute(default=False, choices=[True, False])

    token = None

    def __init__(self):
        super().__init__()
        self._logger = ProviderLogger(provider_name='linode.instance', resource_name=self.path())
        self.api = LinodeClient(self.token)

    def create(self) -> None:
        """Called when the resource is created."""

        self._logger.debug(message=f'Starting instance creation {self.label}')

        create_args = {
            'ltype': self.type,
            'region': self.region,
            'image': self.image,
            'private_ip': self.private_ip
        }

        if self.tags:
            create_args['tags'] = self.tags

        if self.label:
            create_args['label'] = self.label

        if self.group:
            create_args['group'] = self.group

        # Create the instance
        try:
            instance, password = self.api.linode.instance_create(**create_args)
        except ApiError as err:
            self._logger.critical(f'Instance creation failed: {err}')
            raise ResourceCreateFailure(reason=str(err), resource_name=self.path()) from err

        self.root_password = password
        self.instance_id = instance.id
        self.ipv4 = instance.ipv4
        self.ipv6 = instance.ipv6

        # Wait up to two minutes for the server to come online
        try:
            self._logger.debug(message=f'Waiting for SSH to become available on {self.ipv4}')
            self.wait_for_ssh(retry_count=24, retry_delay=5)
        except SSHConnectError as exc:
            self._logger.critical(f'Instance creation failed: {str(exc)}')
            raise ResourceCreateFailure(reason='Unable to establish SSH connection.', resource_name=self.path()) from exc

        self._logger.debug(message=f'Instance creation complete {self.instance_id}: {self.ipv4 =} | {self.ipv6 =}')

        # Persist this resource to the database
        return super().create()

    def delete(self) -> None:
        """Delete a previously created volume."""
        self._logger.debug(message=f'Deleting {self.label}')

        instance = Instance(client=self.api, id=self.instance_id)
        instance.delete()

        # Delete the resource from the database
        super().delete()

        self._logger.debug(message='Deletion complete')

    def depends_on(self) -> List['StackzillaResource']:
        """Required to be overridden."""
        return []

    def ssh_address(self) -> SSHAddress:
        return SSHAddress(host=self.ipv4[0], port=22)

    def ssh_credentials(self) -> SSHCredentials:
        return SSHCredentials(username='root', password=self.root_password, key=None)

    def verify(self) -> None:

        # Make sure the user declared a token to use when authenticating with Linode
        if self.token is None:
            err = ResourceVerifyError(resource_name=self.path())
            err.add_attribute_error(name='token', error='not declared')
            raise err

        # Make sure that the label only contains numbers, letters, underscores, dashes, and periods
        if self.label:
            if not re.match(r'[\w\-\.]*$', self.label):
                err = ResourceVerifyError(resource_name=self.path())
                err.add_attribute_error(name='label', error='Must use only letters, numbers, underscores, dashes and periods')
                raise err

        return super().verify()

    ##############################################################
    # Modification Methods
    ##############################################################
    def type_modified(self, previous_value: Any, new_value: Any) -> None:

        self._logger.debug(message=f'Resizing instance from {previous_value} to {new_value}')
        instance = Instance(client=self.api, id=self.instance_id)

        # TODO: Handle the ApiError exception case
        # NOTE: The resize operation is async. No resizes can occur until the current resize is complete.
        instance.resize(new_value)

    def label_modified(self, previous_value: Any, new_value: Any) -> None:
        self._logger.debug(message=f'Updating label from {previous_value} to {new_value}')
        instance = Instance(client=self.api, id=self.instance_id)
        instance.label = new_value
        instance.save()

    def group_modified(self, previous_value: Any, new_value: Any) -> None:
        self._logger.debug(message=f'Updating group from {previous_value} to {new_value}')

        instance = Instance(client=self.api, id=self.instance_id)
        instance.group = new_value
        instance.save()

    def tags_modified(self, previous_value: Any, new_value: Any) -> None:
        self._logger.debug(message=f'Updating tags from {previous_value} to {new_value}')

        instance = Instance(client=self.api, id=self.instance_id)

        # Short circuit out
        if new_value == instance.tags:
            return

        instance.tags = new_value
        instance.save()

    @classmethod
    def version(cls) -> ResourceVersion:
        """Fetch the version of the resource provider."""
        return ResourceVersion(major=0, minor=1, build=0, name='alpha')
