"""UAS-835 account-private customer continuity."""
from __future__ import annotations
import hashlib
from sqlalchemy import text

def _key(account_id, kind, external_id):
    raw=(str(account_id)+"\\0"+str(kind)+"\\0"+str(external_id)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def identity_from_chat(account_id, chat):
    chat=chat or {}
    chat_id=str(chat.get("id") or "").strip()
    ctx=chat.get("context") or {}
    value=(ctx.get("value") or {}) if isinstance(ctx,dict) else {}
    seller_id=str(value.get("user_id") or "").strip()
    buyer_id=""
    for user in chat.get("users") or []:
        if not isinstance(user,dict):
            continue
        uid=str(user.get("id") or "").strip()
        if uid and uid != seller_id:
            buyer_id=uid
            break
    if buyer_id:
        return {"customer_key":_key(account_id,"avito-user",buyer_id),"source":"avito_user_id","confidence":100,"cross_chat":True}
    if chat_id:
        return {"customer_key":_key(account_id,"avito-chat",chat_id),"source":"chat_fallback","confidence":30,"cross_chat":False}
    return {}

def upsert_identity(db, account_id, chat):
    ident=identity_from_chat(account_id,chat)
    chat_id=str((chat or {}).get("id") or "").strip()
    if not ident or not chat_id:
        return {}
    db.execute(text("INSERT INTO mop_customer_chat_identity(account_id,avito_chat_id,customer_key,identity_source,confidence,first_seen_at,last_seen_at) VALUES(:a,:c,:k,:s,:q,now(),now()) ON CONFLICT(account_id,avito_chat_id) DO UPDATE SET customer_key=EXCLUDED.customer_key,identity_source=EXCLUDED.identity_source,confidence=EXCLUDED.confidence,last_seen_at=now()"),{"a":str(account_id),"c":chat_id,"k":ident["customer_key"],"s":ident["source"],"q":ident["confidence"]})
    return ident

def customer_context(db, account_id, chat_id, max_chats=8, max_messages=32, max_chars=6000):
    row=db.execute(text("SELECT customer_key,identity_source,confidence FROM mop_customer_chat_identity WHERE account_id=:a AND avito_chat_id=:c"),{"a":str(account_id),"c":str(chat_id)}).mappings().first()
    if not row:
        return {"customer_key":None,"prior_chat_count":0,"prompt_context":"","role_history":""}
    customer_key=str(row["customer_key"])
    ids=db.execute(text("SELECT avito_chat_id FROM mop_customer_chat_identity WHERE account_id=:a AND customer_key=:k ORDER BY last_seen_at DESC LIMIT :n"),{"a":str(account_id),"k":customer_key,"n":max(1,min(int(max_chats),20))}).all()
    chat_ids=[str(x[0]) for x in ids]
    if str(chat_id) not in chat_ids:
        chat_ids.insert(0,str(chat_id))
    rows=db.execute(text("SELECT avito_chat_id,direction,text,item_title FROM messenger_messages WHERE account_id=:a AND avito_chat_id=ANY(:ids) AND COALESCE(msg_type,'text')<>'system' AND COALESCE(text,'')<>'' ORDER BY avito_created_at DESC NULLS LAST,id DESC LIMIT :n"),{"a":str(account_id),"ids":chat_ids,"n":max(1,min(int(max_messages),80))}).mappings().all()
    rows=list(reversed([dict(x) for x in rows]))
    prior=[x for x in rows if str(x.get("avito_chat_id"))!=str(chat_id)]
    role=[]
    items=[]
    for x in rows:
        title=" ".join(str(x.get("item_title") or "").split()).strip()
        if title and title not in items:
            items.append(title)
    for x in prior:
        body=" ".join(str(x.get("text") or "").split()).strip()[:500]
        if body:
            who="Клиент" if str(x.get("direction") or "").lower().startswith("in") else "Мы"
            role.append(who+": "+body)
    role_history="\n".join(role[-max_messages:])
    prior_count=max(0,len(set(chat_ids))-(1 if str(chat_id) in chat_ids else 0))
    prompt=""
    if prior_count:
        parts=["ПАМЯТЬ ЭТОГО ЖЕ ПОКУПАТЕЛЯ ВНУТРИ ТЕКУЩЕГО БИЗНЕС-АККАУНТА.","Это тот же человек, который уже писал компании по другим объявлениям.","Не начинай знакомство заново. Не задавай повторно вопросы, на которые он уже отвечал. Не смешивай характеристики разных объявлений."]
        if items:
            parts.append("Ранее обсуждались: "+"; ".join(items[:6]))
        if role_history:
            parts.append("Предыдущие диалоги этого покупателя:\n"+role_history)
        prompt="\n".join(parts)[:max(1200,int(max_chars))]
    return {"customer_key":customer_key,"identity_source":str(row["identity_source"]),"identity_confidence":int(row["confidence"] or 0),"prior_chat_count":prior_count,"prompt_context":prompt,"role_history":role_history}
