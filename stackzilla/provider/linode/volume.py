"""Linode Volume resource definition for Stackzilla."""
from typing import Any, List, Optional

from linode_api4 import LinodeClient
from linode_api4.errors import ApiError
from linode_api4.objects.volume import Volume
from stackzilla.attribute import StackzillaAttribute
from stackzilla.logger.provider import ProviderLogger
from stackzilla.resource.base import ResourceVersion, StackzillaResource
from stackzilla.resource.exceptions import (ResourceCreateFailure,
                                            ResourceVerifyError)
from stackzilla.utils.numbers import StackzillaRange

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
    tags = StackzillaAttribute(required=False, modify_rebuild=False)
    instance = StackzillaAttribute(required=False, types=[LinodeInstance])
    token = None

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

        if self.instance:
            linode: LinodeInstance = self.instance()
            linode.load_from_db()

            create_data['linode '] = linode.instance_id

        # Save the new volume ID
        self.volume_id = volume.id
        self._logger.log(message=f'Volume creation complete: {volume.id}')

        # Persist this resource to the database
        return super().create()

    def delete(self) -> None:
        """Delete a previously created volume."""
        self._logger.debug(message=f'Deleting {self.label}')

        # TODO: if the volume is attached, detach it!
        volume = Volume(client=self.api, id=self.volume_id)
        volume.delete()
        super().delete()

        self._logger.debug(message='Deletion complete')

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
            loaded_obj = new_value()
            loaded_obj.load_from_db()

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


    @classmethod
    def version(cls) -> ResourceVersion:
        """Fetch the version of the resource provider."""
        return ResourceVersion(major=0, minor=1, build=0, name='alpha')
