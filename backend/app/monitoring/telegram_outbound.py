from __future__ import annotations
import os
from app.crypto_utils import decrypt_secret
from app.monitoring.telegram import build_client

def verify_personal_message(client, entity, message_id: int | None, expected_text: str | None = None) -> bool:
    """Read back a just-sent message from the exact Telegram peer.

    This does not decide whether a send happened; it only confirms that the
    returned message_id is visible in the intended peer and is outbound.
    """
    try:
        mid=int(message_id or 0)
        if not mid:
            return False
        got=client.get_messages(entity,ids=mid)
        if not got or not bool(getattr(got,'out',False)):
            return False
        if expected_text is not None:
            actual=' '.join(str(getattr(got,'message',None) or '').split())
            expected=' '.join(str(expected_text or '').split())
            if actual != expected:
                return False
        return True
    except Exception:
        return False

def resolve_personal_entity(client, peer, dialog_limit: int = 2500):
    try:
        return client.get_entity(peer)
    except ValueError:
        if not isinstance(peer, int):
            raise
        for dialog in client.iter_dialogs(limit=dialog_limit):
            entity=getattr(dialog,'entity',None)
            if entity is not None and int(getattr(entity,'id',0) or 0)==peer:
                return entity
        raise

def send_personal_peer(peer, text: str, recipient_label: str | None = None, access_hash: int | None = None) -> dict:
    if peer in (None,'') or not str(text or '').strip():
        return {'ok':False,'error':'invalid_recipient_or_text'}
    client=build_client(
        decrypt_secret(os.environ['MONITOR_TG_SESSION_ENC']),
        int(os.environ['MONITOR_TG_API_ID']),
        os.environ['MONITOR_TG_API_HASH'],
    )
    label=recipient_label or str(peer)
    try:
        if access_hash is not None and isinstance(peer,int):
            from telethon.tl.types import InputPeerUser
            entity=InputPeerUser(peer,int(access_hash))
        else:
            entity=resolve_personal_entity(client,peer)
        body=str(text).strip()
        msg=client.send_message(entity, body, link_preview=False)
        mid=getattr(msg,'id',None)
        verified=verify_personal_message(client,entity,mid,body)
        return {'ok':True,'message_id':mid,'recipient':label,'peer_id':getattr(entity,'id',None),'verified':verified}
    except Exception as e:
        return {'ok':False,'error':f'{type(e).__name__}: {e}'[:500],'recipient':label}
    finally:
        client.disconnect()

def send_personal_message(username: str, text: str) -> dict:
    username=(username or '').strip().lstrip('@')
    if not username:
        return {'ok':False,'error':'invalid_recipient_or_text'}
    return send_personal_peer(username,text,recipient_label='@'+username)


def send_personal_from_message(user_id: int, text: str, *, source_external_id: str, source_access_hash: int, source_message_id: int, recipient_label: str | None = None) -> dict:
    """Resolve a Telegram min-user through the exact source message and send safely."""
    if not user_id or not source_external_id or not source_access_hash or not source_message_id or not str(text or '').strip():
        return {'ok':False,'error':'invalid_from_message_context'}
    client=build_client(
        decrypt_secret(os.environ['MONITOR_TG_SESSION_ENC']),
        int(os.environ['MONITOR_TG_API_ID']),
        os.environ['MONITOR_TG_API_HASH'],
    )
    label=recipient_label or f'peer:{user_id}'
    try:
        from telethon.tl.types import InputPeerChannel, InputPeerUserFromMessage
        src=str(source_external_id)
        if not src.startswith('-100'):
            return {'ok':False,'error':'unsupported_source_peer','recipient':label}
        source_peer=InputPeerChannel(int(src[4:]),int(source_access_hash))
        contextual=InputPeerUserFromMessage(source_peer,int(source_message_id),int(user_id))
        full=client.get_entity(contextual)
        if bool(getattr(full,'bot',False)) or bool(getattr(full,'deleted',False)):
            return {'ok':False,'error':'resolved_recipient_not_user','recipient':label}
        entity=client.get_input_entity(full)
        body=str(text).strip()
        msg=client.send_message(entity,body,link_preview=False)
        mid=getattr(msg,'id',None)
        verified=verify_personal_message(client,entity,mid,body)
        return {'ok':True,'message_id':mid,'recipient':label,
                'peer_id':int(getattr(full,'id',user_id) or user_id),
                'username':getattr(full,'username',None),'access_hash':getattr(full,'access_hash',None),
                'resolved_from_message':True,'verified':verified}
    except Exception as e:
        return {'ok':False,'error':f'{type(e).__name__}: {e}'[:500],'recipient':label}
    finally:
        client.disconnect()
