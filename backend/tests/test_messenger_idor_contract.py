from pathlib import Path

ROOT=Path('/root/BORIS')
INTERNAL=ROOT/'backend/app/api/internal_messenger.py'
CRM=ROOT/'backend/app/api/crm.py'
TEAM=ROOT/'backend/app/api/team.py'

def _block(source,start,end):
    left=source.index(start); right=source.index(end,left); return source[left:right]

def test_conversation_and_message_routes_recheck_membership():
    source=INTERNAL.read_text(encoding='utf-8')
    for start,end in [
      ('def list_messages(', '@router.post("/conversations/{conversation_id}/messages")'),
      ('def send_message(', '@router.post("/conversations/{conversation_id}/read")'),
      ('def edit_message(', '@router.delete("/messages/{message_id}")'),
      ('def delete_message(', '@router.post("/messages/{message_id}/reactions")'),
    ]:
        block=_block(source,start,end)
        assert '_require_member' in block

def test_attachment_download_is_membership_scoped():
    source=INTERNAL.read_text(encoding='utf-8')
    block=source[source.index('def download_attachment('):]
    assert '_require_member' in block
    assert '_attachment_storage_path' in block

def test_crm_tasks_are_account_scoped_server_side():
    source=CRM.read_text(encoding='utf-8')
    # Read routes use _own directly. Mutation routes use the stricter _write
    # helper, and _write itself must first enforce _own before can_reply.
    assert source.count('_own(db, _cur,') >= 2
    assert source.count('_write(db, _cur,') >= 4
    write_block=_block(source,'def _write(', 'def _bad(')
    assert '_own(db, user, account_id)' in write_block
    assert 'can_reply=TRUE' in write_block
    assert 'raise HTTPException(status_code=403, detail="Аккаунт недоступен")' in source

def test_team_membership_mutations_recheck_manage_permission():
    source=TEAM.read_text(encoding='utf-8')
    assert source.count('_require_team_manage') >= 5
