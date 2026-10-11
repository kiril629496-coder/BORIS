"""Transactional receipts for internal Messenger resend after lost responses."""
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import text


def begin(db, conversation_id, user_id, body):
    key = getattr(body, 'client_message_id', None)
    if key is None:
        return None, None
    payload = body.model_dump(mode='json')
    payload.pop('client_message_id', None)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()
    values = {'c': conversation_id, 'u': user_id, 'k': str(key), 'h': digest}
    # Held until message and receipt commit together; never release on an HTTP timeout.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
               {'key': f'messenger-outbox:{conversation_id}:{user_id}:{key}'})
    row = db.execute(text('SELECT request_hash,response_json FROM internal_message_receipts '
                          'WHERE conversation_id=:c AND user_id=:u AND client_message_id=:k'), values).mappings().first()
    if row and row['request_hash'] != digest:
        raise HTTPException(409, 'Ключ отправки уже использован для другого сообщения')
    return values, dict(row['response_json']) if row else None


def finish(db, receipt, response):
    if receipt is None:
        return
    db.execute(text('INSERT INTO internal_message_receipts(conversation_id,user_id,client_message_id,request_hash,response_json) '
                    'VALUES (:c,:u,:k,:h,CAST(:response AS jsonb))'),
               {**receipt, 'response': json.dumps(response, ensure_ascii=False)})
