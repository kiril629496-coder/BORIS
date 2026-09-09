"""
External CRM connector contract.

Internal BORIS CRM remains available regardless of external provider.

Modes:

    boris_master
        BORIS is primary CRM.

    bidirectional
        BORIS <-> external CRM.

    external_master
        amoCRM/Bitrix24 is authoritative for mapped sales fields.

No external write is performed in Stage A.
"""

from abc import ABC, abstractmethod


class CRMConnector(ABC):

    provider = "abstract"

    @abstractmethod
    def create_contact(self, payload):
        raise NotImplementedError

    @abstractmethod
    def update_contact(self, external_id, payload):
        raise NotImplementedError

    @abstractmethod
    def create_deal(self, payload):
        raise NotImplementedError

    @abstractmethod
    def update_deal(self, external_id, payload):
        raise NotImplementedError

    @abstractmethod
    def move_deal(self, external_id, stage_external_id):
        raise NotImplementedError

    @abstractmethod
    def create_task(self, payload):
        raise NotImplementedError

    @abstractmethod
    def receive_event(self, payload):
        raise NotImplementedError


class BorisCRMConnector(CRMConnector):

    provider = "boris"

    def create_contact(self, payload):
        return payload

    def update_contact(self, external_id, payload):
        return payload

    def create_deal(self, payload):
        return payload

    def update_deal(self, external_id, payload):
        return payload

    def move_deal(self, external_id, stage_external_id):
        return {
            "id": external_id,
            "stage": stage_external_id,
        }

    def create_task(self, payload):
        return payload

    def receive_event(self, payload):
        return payload


class AmoCRMConnector(CRMConnector):
    provider = "amocrm"

    def _pending(self):
        raise RuntimeError("AMO_CONNECTOR_NOT_CONFIGURED")

    create_contact = lambda self, payload: self._pending()
    update_contact = lambda self, external_id, payload: self._pending()
    create_deal = lambda self, payload: self._pending()
    update_deal = lambda self, external_id, payload: self._pending()
    move_deal = lambda self, external_id, stage_external_id: self._pending()
    create_task = lambda self, payload: self._pending()
    receive_event = lambda self, payload: self._pending()


class Bitrix24Connector(CRMConnector):
    provider = "bitrix24"

    def _pending(self):
        raise RuntimeError("BITRIX24_CONNECTOR_NOT_CONFIGURED")

    create_contact = lambda self, payload: self._pending()
    update_contact = lambda self, external_id, payload: self._pending()
    create_deal = lambda self, payload: self._pending()
    update_deal = lambda self, external_id, payload: self._pending()
    move_deal = lambda self, external_id, stage_external_id: self._pending()
    create_task = lambda self, payload: self._pending()
    receive_event = lambda self, payload: self._pending()
