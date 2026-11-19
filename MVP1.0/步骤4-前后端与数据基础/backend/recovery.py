"""Read-only recovery snapshots. Restoring never invokes the model provider."""
import re
from .db import rows
from .errors import ApiError

VISIBLE="status NOT IN ('candidate','replaced','cancelled') AND (replaces_id IS NULL OR status='completed')"
FIELDS='id,ordinal,role,content,status,created_at,updated_at,reply_to_id,replaces_id'


class Recovery:
    def __init__(self, db, service, chat):
        self.db,self.service,self.chat=db,service,chat

    def read(self,user_id,cid,anchor=None,client_key=None,generation_key=None,session_id=None,use_id=None):
        if anchor and (not isinstance(anchor,str) or not re.fullmatch('[a-f0-9]{32}',anchor)):
            raise ApiError(422,'invalid_anchor','阅读位置不正确')
        for key in (client_key,generation_key):
            if key is not None and (not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{8,80}',key)):
                raise ApiError(422,'invalid_request_id','原请求标识不正确')
        with self.db.connect() as c:
            c.execute('BEGIN')
            conversation=self.service.own(c,cid,user_id)
            position=c.execute('SELECT ordinal FROM messages WHERE id=? AND conversation_id=? AND '+VISIBLE,(anchor,cid)).fetchone() if anchor else None
            bound=min(position['ordinal']+60,2**63-1) if position else 2**63-1
            messages=rows(c,'SELECT '+FIELDS+' FROM messages WHERE conversation_id=? AND ordinal<=? AND '+VISIBLE+' ORDER BY ordinal DESC LIMIT 101',(cid,bound))
            more=len(messages)>100
            messages=list(reversed(messages[:100]))
            self.service.annotate_message_sources(c,cid,messages)
            self.service.annotate_voice_blocked(c,cid,messages,user_id)
            self.service.annotate_deleted(c,cid,messages)
            newer=bool(messages and c.execute('SELECT 1 FROM messages WHERE conversation_id=? AND ordinal>? AND '+VISIBLE,(cid,messages[-1]['ordinal'])).fetchone())
            last_user=c.execute("SELECT "+FIELDS+" FROM messages WHERE conversation_id=? AND role='user' AND status='saved' AND id NOT IN (SELECT message_id FROM message_deletions WHERE conversation_id=?) ORDER BY ordinal DESC LIMIT 1",(cid,cid)).fetchone()
            current_request=c.execute('SELECT id FROM generation_requests WHERE conversation_id=? AND user_message_id=? ORDER BY rowid DESC LIMIT 1',(cid,last_user['id'])).fetchone() if last_user else None
            generation=self.chat.snapshot(user_id,cid,current_request['id'],connection=c,session_id=session_id,use_id=use_id) if current_request else {'request':None}
            root=c.execute("SELECT id FROM generation_requests WHERE conversation_id=? AND user_message_id=? AND kind='reply'",(cid,last_user['id'])).fetchone() if last_user else None
            submitted=c.execute("SELECT "+FIELDS+" FROM messages WHERE conversation_id=? AND client_request_id=? AND role='user'",(cid,client_key)).fetchone() if client_key else None
            submitted_generation=None
            if submitted:
                original=c.execute("SELECT id FROM generation_requests WHERE conversation_id=? AND user_message_id=? AND kind='reply'",(cid,submitted['id'])).fetchone()
                if original:submitted_generation=self.chat.snapshot(user_id,cid,original['id'],connection=c,session_id=session_id,use_id=use_id)
            intent=c.execute('SELECT id FROM generation_requests WHERE conversation_id=? AND client_request_id=?',(cid,generation_key)).fetchone() if generation_key else None
            requested_generation=self.chat.snapshot(user_id,cid,intent['id'],connection=c,session_id=session_id,use_id=use_id) if intent else None
            return {'conversation_id':cid,'character_id':conversation['character_id'],
                    'items':messages,'has_more':more,'next_before':messages[0]['ordinal'] if more else None,
                    'has_newer':newer,'anchor_found':bool(position),'last_user_message':dict(last_user) if last_user else None,
                    'generation':generation,'last_user_source_available':bool(last_user and self.chat.source_available(c,cid,last_user['id'],session_id,use_id)),'submitted_source_available':bool(submitted and self.chat.source_available(c,cid,submitted['id'],session_id,use_id)),'reply_required':bool(last_user and not root and conversation['character_id'] in self.chat.capabilities()['chat_roles'] and self.chat.source_available(c,cid,last_user['id'],session_id,use_id)),
                    'submitted_message':dict(submitted) if submitted else None,'submitted_generation':submitted_generation,
                    'requested_generation':requested_generation}
