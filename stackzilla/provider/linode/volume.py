"""Linode Volume resource definition for Stackzilla."""
from time import sleep
from typing import Any, List, Optional

from linode_api4 import LinodeClient
from linode_api4.errors import ApiError
from linode_api4.objects.volume import Volume
from stackzilla.attribute import StackzillaAttribute
from stackzilla.events import StackzillaEvent
from stackzilla.logger.provider import ProviderLogger
from stackzilla.resource.base import ResourceVersion, StackzillaResource
from stackzilla.resource.exceptions import (ResourceCreateFailure,
                                            ResourceVerifyError)
from stackzilla.utils.numbers import StackzillaRange
from stackzilla.utils.ssh import CmdResult

from .instance import LinodeInstance
from .utils import LINODE_REGIONS


class LinodeVolume(StackzillaResource):
    """Resource definition for a Linode volume."""

    # Dynamic parameters (determined at create)
    volume_id = StackzillaAttribute(dynamic=True)

    # User-defined parameters
    label = StackzillaAttribute(required=False, modify_rebuild=False)
    region = StackzillaAttribute(required=True, modify_rebuild=True, choices=LINODE_REGIONS)
    size = StackzillaAttribute(required=True, modify_rebuild=False, number_range=StackzillaRange(min=10, max=10240))
    filesystem_path = StackzillaAttribute(dynamic=True)
    hardware_type = StackzillaAttribute(dynamic=True)
    tags = StackzillaAttribute(required=False, modify_rebuild=False)
    instance = StackzillaAttribute(required=False, types=[LinodeInstance])
    mount_point = StackzillaAttribute(required=False, types=[str])
    file_system_type = StackzillaAttribute(required=False, default=None, choices=['ext4', None])

    # Class variables
    token = None

    # Events
    size_changed_event = StackzillaEvent()

    def __init__(self):
        """Setup the logger and Linode API."""
        super().__init__()
        self._logger = ProviderLogger(provider_name='linode.volume', resource_name=self.path())

        # Make sure the user declared a token to use when authenticating with Linode
        if self.token is None:
            err = ResourceVerifyError(resource_name=self.path())
            err.add_attribute_error(name='token', error='not declared')
            raise err

        self.api = LinodeClient(self.token)

    def create(self) -> None:
        """Called when the resource is created."""
        create_data = {
            'region': self.region,
            'size': self.size,
        }

        if self.label:
            create_data['label'] = self.label

        if self.tags:
            create_data['tags'] = self.tags

            self._logger.debug(message=f'Starting volume creation {self.label}')

            try:
                volume: Volume = self.api.volume_create(**create_data)
            except ApiError as err:
                self._logger.critical(f'Volume creation failed: {err}')
                raise ResourceCreateFailure(reason=str(err), resource_name=self.path()) from err

        # Persist this resource to the database
        super().create()

        # Wait for the volume to become active
        time_left = 120
        while time_left > 0:
            volume.invalidate()
            if volume.status == 'active':
                break
            sleep(1)
            time_left -= 1

        if time_left == 0:
            raise ResourceCreateFailure(reason=f'Volume never reached active state: {volume.status}',
                                        resource_name=self.path())

        # Save the new volume ID
        self.volume_id = volume.id
        self._logger.log(message=f'Volume creation complete: {volume.id}')

        # Save the filesystem path
        self.filesystem_path = volume.filesystem_path

        # Save the hardware type
        self.hardware_type = volume.hardware_type

        # Update the database with the new information
        super().update()

        if self.instance:
            linode: LinodeInstance = self.instance.from_db()
            ssh_client = linode.ssh_connect()

            self._logger.log(f'Attaching volume ({volume.id}) to instance ({linode.instance_id})')
            volume.attach(to_linode=linode.instance_id)

            # Wait for the device attachment point to show up on the instance (attachment is done)
            time_left = 120
            while time_left > 0:
                result: CmdResult = ssh_client.run_command(command=f'stat {self.filesystem_path}')
                if result.exit_code == 0:
                    # Wait one more second. If we return right away, the API will yell at us!
                    sleep(1)
                    break

                sleep(1)
                time_left -= 1

            if time_left == 0:
                raise ResourceCreateFailure(reason='Volume never attached to instance',
                                            resource_name=self.path())

            self._logger.log('Attachment complete')

            # Mount the volume
            if self.mount_point:


                # Before mounting, format the file system (if one doesn't already exist)
                if self.file_system_type:
                    # Use 'blkid' to see if a file system already exists
                    result: CmdResult = ssh_client.run_command(command=f'blkid {self.filesystem_path}', sudo=True)
                    if result.exit_code == 0:
                        self._logger.log(f'File system already exists at {self.filesystem_path}. Skipping volume format.')
                    else:
                        # Format the file system
                        format_cmd = f'mkfs.{self.file_system_type} {self.filesystem_path}'
                        self._logger.log(f'Formatting: {format_cmd}')
                        result: CmdResult = ssh_client.run_command(command=format_cmd, sudo=True)
                        if result.exit_code:
                            raise ResourceCreateFailure(reason=f'Failed to format volume: {result.stderr}', resource_name=self.path())

                # Create the directory to mount the volume to
                mkdir_cmd = f'mkdir -p {self.mount_point}'
                self._logger.log(f'Creating mount point: {mkdir_cmd}')
                result: CmdResult = ssh_client.run_command(command=mkdir_cmd, sudo=True, use_pty=True)
                if result.exit_code:
                    raise ResourceCreateFailure(reason=f'Failed to create a mount point directory: {result.stdout}', resource_name=self.path())

                # OK...NOW we will mount the volume!
                result: CmdResult = ssh_client.run_command(command=f'mount {self.filesystem_path} {self.mount_point}', sudo=True)
                if result.exit_code:
                    raise ResourceCreateFailure(reason=f'Failed to mount the volume: {result.stdout}', resource_name=self.path())

                # TODO: Add the volume and mount point to fstab


    def delete(self) -> None:
        """Delete a previously created volume."""
        self._logger.debug(message=f'Deleting {self.label} | {self.volume_id}')

        volume = Volume(client=self.api, id=self.volume_id)

        # Detach the volume
        if self.instance:
            linode: LinodeInstance = self.instance.from_db()
            ssh_client = linode.ssh_connect()
            self._logger.debug('Unmounting volume')
            result: CmdResult = ssh_client.run_command(command=f'umount -f {self.mount_point}')
            if result.exit_code != 0:
                self._logger.warning(f'Unable to unmount volume: {result.stderr}')

            # Fire off the initial detachment request (retries may occur in the wait loop below)
            self._logger.debug('Detaching volume')
            volume.detach()

            # Wait for the detach operation to complete
            time_left = 120
            while time_left > 0:
                volume.invalidate()
                if not volume.linode_id:
                    # Wait one more second - this will fail if we bail immediately
                    sleep(1)
                    break

                # Wait a second before trying again
                sleep(1)
                time_left -= 1

                # !!!!HACK!!!!
                # The API does NOT let us know if the detachment operation failed.
                # To work around this, every 5 seconds, we'll re-issue the detachment command
                if time_left > 0 and time_left % 5 == 0:
                    self._logger.debug('Resending detach request...')
                    volume.detach()

            self._logger.debug('Detach complete')

        self._logger.debug('Deleting volume')
        volume.delete()
        self._logger.debug('Deletion complete')

        super().delete()

    def depends_on(self) -> List['StackzillaResource']:
        """Required to be overridden."""
        result = []
        if self.instance:
            result.append(self.instance)

        return result

    def label_modified(self, previous_value: Any, new_value: Any) -> None:
        """Called when the label value needs modification.

        Args:
            previous_value (Any): Previous label
            new_value (Any): New label
        """
        volume = Volume(client=self.api, id=self.volume_id)
        self._logger.log(f'Updating volume label from {previous_value} to {new_value}')
        volume.label = new_value
        volume.save()

    def linode_modified(self, previous_value: Optional[LinodeInstance], new_value: Optional[LinodeInstance]) -> None:
        """Handle when the specified Linode is modified.

        Args:
            previous_value (Optional[LinodeInstance]): The Linode that the volume was previously attached to.
            new_value (Optional[LinodeInstance]): The new Linode to attach the volume to.
        """
        volume = Volume(client=self.api, id=self.volume_id)

        if previous_value:
            self._logger.log(f'Detaching volume from {previous_value}')
            volume.detach()

        if new_value:
            loaded_obj = new_value.from_db()
            self._logger.log(f'Attaching volume to {loaded_obj.path()}')
            volume.attach(to_linode=loaded_obj.instance_id)

    def tags_modified(self, previous_value: Any, new_value: Any) -> None:
        """Called when the tags parameter is modified in the blueprint.

        Args:
            previous_value (Any): Previous list of tags
            new_value (Any): New list of tags
        """
        volume = Volume(client=self.api, id=self.volume_id)

        # Short circuit out
        if volume.tags == new_value:
            return

        self._logger.log(f'Updating volume tag from {previous_value} to {new_value}')
        volume.tags = new_value

        if volume.save() is False:
            # TODO: Raise a failure here
            self._logger.critical(message='Volume save failed')

    def size_modified(self, previous_value: Any, new_value: Any) -> None:
        """Handler for when the size attribute is modified.

        Args:
            previous_value (Any): The previous size of the volume
            new_value (Any): The new desired size of the volume
        """
        volume = Volume(client=self.api, id=self.volume_id)
        self._logger.log(f'Updating volume size from {previous_value} to {new_value}')
        volume.resize(new_value)

        # If the volume is attached to a Linode, there is some addition work that needs to be performed
        # TODO: If the volume is attached to an Instance, reboot the instance and perform the following commands:
        # resize2fs <disk file location>

        # Let any event handlers know that something changed
        self.size_changed_event.invoke(sender=self)

    def verify(self):
        """Custom verifications for the Volume resource."""
        super().verify()

        # User must specify mount_point if file_system_type is specifed
        if self.file_system_type and self.mount_point is None:
            raise ResourceVerifyError('mount_point must be specified if file_system_type is declared')

    @classmethod
    def version(cls) -> ResourceVersion:
        """Fetch the version of the resource provider."""
        return ResourceVersion(major=0, minor=1, build=0, name='alpha')
