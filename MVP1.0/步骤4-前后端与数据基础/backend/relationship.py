"""Authoritative relationship consent, revocation and use-scoped continuation. No embedding calls."""
import hashlib,json,re
from .errors import ApiError
from .service import uid,now
from .memory import explicit_storage,TEMPORARY,supported_assertion


TYPE_LABEL={'unspecified':'未约定','friends':'朋友','romantic':'恋爱','new':'初识','familiar':'熟悉','close':'亲近'}
# Shared authority vocabulary for extraction, recent history and recalled facts.
AUTHORITY=re.compile(r'恋爱|交往|男朋友|女朋友|分手|情侣|亲吻|亲密授权|在一起|我们是朋友|做朋友|退回朋友|成为朋友')
RELATED=re.compile(AUTHORITY.pattern+r'|亲密|亲我|接吻|宝贝|老公|老婆|爱你|喜欢你|牵手|抱我|亲近|慢一点|不愿意|不想聊|不要叫|暂停')
AMBIGUOUS=re.compile(r'假如|假设|如果|扮演|引用|他说|她说|朋友说|开玩笑|[“「\"]')

def command(text):
    original=text
    request=re.fullmatch(r'(?:请|请帮我|帮我|麻烦你帮我)?(?:记住|保存)[，,：: ]*(.+)',text.strip().rstrip('。！!'))
    if request:
        payload=request.group(1);intent=command(payload)
        if intent and explicit_storage(payload,original):return (*intent[:2],True)
        return None
    text=text.strip().rstrip('。！!')
    if AMBIGUOUS.search(text):return None
    # Refusing to store an ending event does not cancel withdrawal of the old consent.
    # Only strip a narrowly defined storage-refusal clause, never arbitrary extra text
    # or a clause attached to an affirmative relationship request.
    clauses=re.split(r'[，,；;。]\s*',text)
    if len(clauses)==2 and re.fullmatch(r'(?:不|不要|不用|无需|不必)(?:保存|记住)(?:这件事|这段记录|这个记录|这条记录|结束事件|分手事件|结束记录|分手记录|新关系|新的朋友关系|事件|记录)?',clauses[1]):
        withdrawal=command(clauses[0])
        if withdrawal and withdrawal[0]=='revoke':return withdrawal
    if re.fullmatch(r'(?:我(?:要|想|决定)?|我们)?(?:结束恋爱|分手|不再恋爱|撤回恋爱同意)(?:吧|了)?',text):return ('revoke','unspecified')
    if re.fullmatch(r'(?:我(?:想|要)?|我们)?(?:退回|回到|只做|还是做)朋友(?:关系)?(?:吧|了)?',text):return ('revoke','friends')
    if re.fullmatch(r'(?:我(?:想|要)?|我们)?(?:开始恋爱|同意和你恋爱|愿意和你恋爱|正式交往)(?:吧|了)?',text):return ('relation','romantic')
    if re.fullmatch(r'(?:我(?:想|要)?|我们)?(?:做朋友|成为朋友)(?:吧|了)?',text):return ('relation','friends')
    if re.search(r'不要亲|别亲|暂停亲密|不要亲密|停止亲密|不愿意.{0,6}(?:亲密|亲吻|接吻)',text):return ('pause',None)
    if re.search(r'不愿意|不接受这个提议|拒绝这个提议',text):return ('pace','拒绝当前提议，不推定否定整段关系')
    if re.fullmatch(r'(?:我)?(?:暂时|现在|今天)?不想聊.{1,30}',text):return ('pace',text)
    if re.fullmatch(r'(?:我)?(?:想|希望)?慢一点',text) or text in ('慢一点','先别聊这个','我不想聊这个','暂停这个话题'):return ('pace',text)
    return None

class Relationships:
    def __init__(self,db,service,memory,chat):self.db,self.service,self.memory,self.chat=db,service,memory,chat
    def use(self,c,cid,session,use_id,required=False):
        if use_id is not None and (not isinstance(use_id,str) or not re.fullmatch('[a-f0-9]{32}',use_id)):raise ApiError(422,'invalid_use_id','使用标识不正确')
        row=c.execute('SELECT * FROM conversation_uses WHERE id=? AND conversation_id=? AND auth_session_id=? AND closed_at IS NULL',(use_id,cid,session)).fetchone() if use_id else None
        if required and not row:raise ApiError(409,'use_expired','本次使用已结束，请重新进入聊天')
        return row
    def tag(self,c,cid,mid,use_id,kind):c.execute('INSERT OR REPLACE INTO relationship_message_tags VALUES(?,?,?,?)',(mid,cid,use_id,kind))
    def valid_event(self,c,e):
        if e['status'] not in ('accepted','completed') or (e['valid_until'] is not None and e['valid_until']<=now()):return False
        sources=c.execute('SELECT m.* FROM messages m WHERE m.id IN (SELECT message_id FROM relationship_event_sources WHERE event_id=? UNION SELECT primary_source_id FROM relationship_events WHERE id=? UNION SELECT consent_message_id FROM relationship_events WHERE id=? UNION SELECT storage_consent_message_id FROM relationship_events WHERE id=?)',(e['id'],e['id'],e['id'],e['id'])).fetchall()
        if not sources:return False
        if any(m['status'] not in ('saved','completed') or c.execute('SELECT 1 FROM memory_context_blocks WHERE conversation_id=? AND message_id=?',(e['conversation_id'],m['id'])).fetchone() for m in sources):return False
        if e['source_memory_id'] and not c.execute("SELECT 1 FROM memories WHERE id=? AND conversation_id=? AND status='confirmed' AND content=? AND (valid_until IS NULL OR valid_until>?)",(e['source_memory_id'],e['conversation_id'],e['content'],now())).fetchone():return False
        return True
    def reconcile(self,c,cid):
        changed=False
        for e in c.execute("SELECT * FROM relationship_events WHERE conversation_id=? AND status IN ('accepted','completed')",(cid,)).fetchall():
            if not self.valid_event(c,e):
                c.execute("UPDATE relationship_events SET status='invalidated',revision=revision+1,updated_at=? WHERE id=?",(now(),e['id']));self.block_event(c,cid,e,'source_invalidated');changed=True
        state=c.execute('SELECT * FROM relationship_states WHERE conversation_id=?',(cid,)).fetchone()
        if state['basis_event_id']:
            e=c.execute('SELECT * FROM relationship_events WHERE id=?',(state['basis_event_id'],)).fetchone()
            if not e or not e['long_term'] or not self.valid_event(c,e):
                c.execute("UPDATE relationship_states SET relationship_type='unspecified',basis_event_id=NULL WHERE conversation_id=?",(cid,));changed=True
        if state['familiarity_basis_id']:
            e=c.execute('SELECT * FROM relationship_events WHERE id=?',(state['familiarity_basis_id'],)).fetchone()
            if not e or not e['long_term'] or not self.valid_event(c,e):
                c.execute("UPDATE relationship_states SET familiarity='new',familiarity_basis_id=NULL WHERE conversation_id=?",(cid,));changed=True
        if state['pace_basis_id']:
            e=c.execute('SELECT * FROM relationship_events WHERE id=?',(state['pace_basis_id'],)).fetchone()
            if not e or not self.valid_event(c,e):c.execute("UPDATE relationship_states SET pace='',pace_basis_id=NULL WHERE conversation_id=?",(cid,));changed=True
        for use in c.execute('SELECT * FROM conversation_uses WHERE conversation_id=? AND closed_at IS NULL',(cid,)).fetchall():
            for field in ('relation','familiarity','pace'):
                event_id=use['temporary_'+field+'_event_id']
                if event_id:
                    e=c.execute('SELECT * FROM relationship_events WHERE id=?',(event_id,)).fetchone()
                    if not e or not self.valid_event(c,e):c.execute('UPDATE conversation_uses SET temporary_'+field+'=NULL,temporary_'+field+'_event_id=NULL WHERE id=?',(use['id'],));changed=True
        if changed:self.bump(c,cid)
    def bump(self,c,cid):
        c.execute('UPDATE relationship_states SET revocation_revision=revocation_revision+1,updated_at=? WHERE conversation_id=?',(now(),cid))
        c.execute('UPDATE conversation_uses SET based_on_revocation_revision=(SELECT revocation_revision FROM relationship_states WHERE conversation_id=?) WHERE conversation_id=?',(cid,cid))
        self.memory.bump(c,cid);self.memory.cancel_active(c,cid)
    def effective(self,c,cid,use=None):
        self.reconcile(c,cid);s=dict(c.execute('SELECT * FROM relationship_states WHERE conversation_id=?',(cid,)).fetchone())
        relation=s['relationship_type'];familiar=s['familiarity'];pace=s['pace']
        if use:
            use=c.execute('SELECT * FROM conversation_uses WHERE id=? AND closed_at IS NULL',(use['id'],)).fetchone()
            if use:
                # Revocation clears every previous use in its transaction; ordinary accepted changes don't revoke other uses.
                if use['temporary_relation']:relation=use['temporary_relation']
                if use['temporary_familiarity']:familiar=use['temporary_familiarity']
                if use['temporary_pace']:pace=use['temporary_pace']
        return {'relationship_type':relation,'familiarity':familiar,'pace':pace,'intimacy_paused':bool(s['intimacy_paused']),'revision':s['revocation_revision'],'persistent_type':s['relationship_type']}
    def event(self,c,cid,mid,kind,content,use_id,owner,status='accepted',target=None,familiar=None,store=False,sources=None,memory_id=None):
        eid=uid();t=now()
        c.execute('''INSERT INTO relationship_events(id,conversation_id,kind,content,status,primary_source_id,consent_message_id,storage_consent_message_id,confirmed_by,long_term,occurred_at,created_at,updated_at,use_id,target_type,target_familiarity,source_memory_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(eid,cid,kind,content,status,mid,mid if status in ('accepted','completed') else None,mid if store else None,owner if store else None,int(store),t,t,t,use_id,target,familiar,memory_id))
        for source in set((sources or [])+[mid]):c.execute('INSERT INTO relationship_event_sources VALUES(?,?,?)',(eid,cid,source))
        return eid
    def ingest(self,c,owner,cid,mid,text,session=None,use_id=None):
        use=self.use(c,cid,session,use_id);intent=command(text)
        if use:self.tag(c,cid,mid,use_id,'user_message')
        if RELATED.search(text):self.tag(c,cid,mid,use_id,'user_relation')
        if not intent:return
        action,target=intent[:2];store=len(intent)>2 and intent[2]
        if action=='relation' and target=='friends' and (c.execute('SELECT relationship_type FROM relationship_states WHERE conversation_id=?',(cid,)).fetchone()[0]=='romantic' or (use and use['temporary_relation']=='romantic')):action='revoke'
        if action=='revoke':
            s=c.execute('SELECT * FROM relationship_states WHERE conversation_id=?',(cid,)).fetchone();revision=s['revocation_revision']+1
            c.execute("UPDATE relationship_events SET status='invalidated',revision=revision+1 WHERE conversation_id=? AND kind='relationship' AND target_type IN ('romantic','friends') AND status='accepted'",(cid,))
            c.execute("UPDATE relationship_states SET relationship_type='unspecified',basis_event_id=NULL,intimacy_paused=1 WHERE conversation_id=?",(cid,))
            c.execute('UPDATE conversation_uses SET temporary_relation=NULL,temporary_relation_event_id=NULL WHERE conversation_id=?',(cid,))
            c.execute('INSERT INTO relationship_revocations VALUES(?,?,?,?,?)',(uid(),cid,revision,target,now()))
            if use and target=='friends':
                basis=self.event(c,cid,mid,'relationship','用户明确退回朋友，仅本次',use['id'],owner,target='friends')
                c.execute("UPDATE conversation_uses SET temporary_relation='friends',temporary_relation_event_id=? WHERE id=?",(basis,use['id']))
            if store:
                self.event(c,cid,mid,'revocation',text,use['id'] if use else None,owner,store=True,target=target)
                if target=='friends':
                    eid=self.event(c,cid,mid,'relationship','明确退回朋友并保存',use['id'] if use else None,owner,store=True,target='friends')
                    c.execute("UPDATE relationship_states SET relationship_type='friends',basis_event_id=? WHERE conversation_id=?",(eid,cid))
            # Only minimal invalidation is durable; do not auto-save the user's reason or ending event.
        elif action=='relation':
            if not use:return
            eid=self.event(c,cid,mid,'relationship',text,use['id'],owner,target=target,store=store)
            if store:c.execute('UPDATE relationship_states SET relationship_type=?,basis_event_id=? WHERE conversation_id=?',(target,eid,cid))
            c.execute('UPDATE conversation_uses SET temporary_relation=?,temporary_relation_event_id=? WHERE id=?',(target,eid,use['id']))
        elif action=='pause':
            c.execute('UPDATE relationship_states SET intimacy_paused=1 WHERE conversation_id=?',(cid,))
        elif action=='pace' and use:
            store=store and not TEMPORARY.search(text) and '这个' not in text
            eid=self.event(c,cid,mid,'boundary',text,use['id'],owner,store=store)
            if store:c.execute('UPDATE relationship_states SET pace=?,pace_basis_id=? WHERE conversation_id=?',(text,eid,cid))
            c.execute('UPDATE conversation_uses SET temporary_pace=?,temporary_pace_event_id=? WHERE id=?',(text,eid,use['id']))
        self.bump(c,cid)
    def read(self,user,cid,use_id=None):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE');self.service.own(c,cid,user['id']);use=self.use(c,cid,user['session_id'],use_id)
            state=self.effective(c,cid,use);events=[]
            for e in c.execute('SELECT * FROM relationship_events WHERE conversation_id=? AND (long_term=1 OR use_id=?) ORDER BY occurred_at DESC,rowid DESC',(cid,use['id'] if use else '')):
                item=dict(e);item['sources']=[dict(r) for r in c.execute('SELECT m.id,m.content,m.status,m.created_at FROM messages m WHERE m.id IN (SELECT message_id FROM relationship_event_sources WHERE event_id=? UNION SELECT primary_source_id FROM relationship_events WHERE id=?)',(e['id'],e['id']))];events.append(item)
            return {'state':state,'events':events,'write_available':True,'use_id':use['id'] if use else None,'embedding_required_for_state':False}
    def control_source(self,c,cid,use_id,text):
        mid=uid();ordinal=c.execute('SELECT coalesce(max(ordinal),0)+1 FROM messages WHERE conversation_id=?',(cid,)).fetchone()[0]
        c.execute("INSERT INTO messages(id,conversation_id,ordinal,role,content,status,created_at,updated_at) VALUES(?,?,?,'system',?,'completed',?,?)",(mid,cid,ordinal,'【关系操作】'+text,now(),now()));self.tag(c,cid,mid,use_id,'explicit_control');return mid
    def familiarity_sources(self,c,cid,ids):
        if not isinstance(ids,list) or not 4<=len(ids)<=12 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ApiError(422,'relationship_evidence_needed','熟悉程度需要双方分享及后续承接的来源，不能按消息数量升级')
        messages=[]
        for mid in ids:
            if not isinstance(mid,str) or not re.fullmatch('[a-f0-9]{32}',mid):raise ApiError(422,'relationship_source_invalid','来源标识不正确')
            m=c.execute("SELECT * FROM messages WHERE id=? AND conversation_id=? AND status IN ('saved','completed') AND role IN ('user','assistant')",(mid,cid)).fetchone()
            if not m or c.execute('SELECT 1 FROM memory_context_blocks WHERE conversation_id=? AND message_id=?',(cid,mid)).fetchone():raise ApiError(422,'relationship_source_invalid','来源无效')
            messages.append(m)
        messages.sort(key=lambda m:m['ordinal'])
        # At least two actual authored turns sharing a concrete phrase; repeated greetings don't count.
        users=[m for m in messages if m['role']=='user'];assistants=[m for m in messages if m['role']=='assistant' and m['reply_to_id']]
        if len(users)<2 or len(assistants)<2 or not any(a['reply_to_id']==users[0]['id'] for a in assistants) or not any(a['reply_to_id']==users[-1]['id'] for a in assistants):raise ApiError(422,'relationship_evidence_needed','需要两次可追溯的双向交流')
        if users[0]['content']==users[-1]['content'] or not any(re.search(r'我.{2,}',a['content']) for a in assistants):raise ApiError(422,'relationship_evidence_needed','重复原话或仅单向透露不代表双向了解')
        text=users[0]['content'];grams={text[i:i+2] for i in range(len(text)-1)}-{'你好','谢谢','今天','好的','哈哈','我们','喜欢','我喜'}
        if not any(g in users[-1]['content'] and any(g in a['content'] for a in assistants) for g in grams):raise ApiError(422,'relationship_evidence_needed','来源尚未体现具体话题的双向了解和后续承接')
        return [m['id'] for m in messages]
    def mutate(self,user,cid,body):
        action=body.get('action');key=body.get('client_request_id')
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{8,80}',key):raise ApiError(422,'invalid_request_id','操作标识不正确')
        if type(body.get('revision')) is not int or body['revision']<0:raise ApiError(422,'invalid_revision','关系版本不正确')
        for field in ('event_id','memory_id'):
            if field in body and (not isinstance(body[field],str) or not re.fullmatch('[a-f0-9]{32}',body[field])):raise ApiError(422,'invalid_identifier','事件或记忆标识不正确')
        if action in ('delete_event','edit_event') and (type(body.get('event_revision')) is not int or body['event_revision']<1):raise ApiError(422,'invalid_revision','事件版本不正确')
        digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE');self.service.own(c,cid,user['id']);use=self.use(c,cid,user['session_id'],body.get('use_id'),True)
            previous=c.execute('SELECT * FROM relationship_operations WHERE conversation_id=? AND client_request_id=?',(cid,key)).fetchone()
            if previous:
                if previous['body_hash']!=digest:raise ApiError(409,'idempotency_conflict','同一操作标识不能提交不同决定')
                return json.loads(previous['result'])
            self.reconcile(c,cid);state=c.execute('SELECT * FROM relationship_states WHERE conversation_id=?',(cid,)).fetchone()
            if body.get('revision')!=state['revocation_revision']:raise ApiError(409,'relationship_changed','关系状态已更新，请刷新核对')
            store=body.get('store') is True;mid=None;eid=None
            if action in ('set_relation','revoke','pace','pause','resume_intimacy'):
                if action=='set_relation':
                    target=body.get('target')
                    if target not in ('friends','romantic'):raise ApiError(422,'invalid_relation','请选择明确关系')
                    mid=self.control_source(c,cid,use['id'],'用户明确同意'+TYPE_LABEL[target]+('并保存供下次续聊' if store else '，仅本次使用'))
                    if target=='friends' and (state['relationship_type']=='romantic' or use['temporary_relation']=='romantic'):self.ingest(c,user['id'],cid,mid,'退回朋友',user['session_id'],use['id'])
                    eid=self.event(c,cid,mid,'relationship','用户明确同意'+TYPE_LABEL[target],use['id'],user['id'],target=target,store=store)
                    c.execute('UPDATE conversation_uses SET temporary_relation=?,temporary_relation_event_id=? WHERE id=?',(target,eid,use['id']))
                    if store:c.execute('UPDATE relationship_states SET relationship_type=?,basis_event_id=? WHERE conversation_id=?',(target,eid,cid))
                elif action=='revoke':
                    target=body.get('target','unspecified')
                    if target not in ('unspecified','friends'):raise ApiError(422,'invalid_relation','结束后的状态不正确')
                    mid=self.control_source(c,cid,use['id'],'用户结束恋爱'+('并退回朋友' if target=='friends' else '，关系未约定'))
                    self.ingest(c,user['id'],cid,mid,'退回朋友' if target=='friends' else '结束恋爱',user['session_id'],use['id'])
                    if store:
                        eid=self.event(c,cid,mid,'revocation','用户结束旧关系',use['id'],user['id'],target=target,store=True)
                        if target=='friends':
                            basis=self.event(c,cid,mid,'relationship','用户明确同意退回朋友并保存',use['id'],user['id'],target='friends',store=True)
                            c.execute("UPDATE relationship_states SET relationship_type='friends',basis_event_id=? WHERE conversation_id=?",(basis,cid))
                elif action=='pace':
                    pace=body.get('pace')
                    if pace not in ('慢一点','暂停这个话题','先听，不提供建议'):raise ApiError(422,'invalid_pace','请选择明确节奏')
                    if pace=='暂停这个话题' and store:raise ApiError(422,'boundary_scope_needed','临时话题暂停仅本次；持续边界请在记忆页核对具体话题与期限')
                    mid=self.control_source(c,cid,use['id'],'用户选择节奏：'+pace+('并保存' if store else '，仅本次'))
                    eid=self.event(c,cid,mid,'boundary',pace,use['id'],user['id'],store=store);c.execute('UPDATE conversation_uses SET temporary_pace=?,temporary_pace_event_id=? WHERE id=?',(pace,eid,use['id']))
                    if store:c.execute('UPDATE relationship_states SET pace=?,pace_basis_id=? WHERE conversation_id=?',(pace,eid,cid))
                else:
                    mid=self.control_source(c,cid,use['id'],'用户暂停亲密表达' if action=='pause' else '用户明确同意恢复亲密表达，但不等于同意任何具体亲密行为')
                    c.execute('UPDATE relationship_states SET intimacy_paused=? WHERE conversation_id=?',(1 if action=='pause' else 0,cid))
            elif action=='save_event':
                e=c.execute('SELECT * FROM relationship_events WHERE id=? AND conversation_id=? AND use_id=?',(body.get('event_id'),cid,use['id'])).fetchone()
                if not e or not self.valid_event(c,e):raise ApiError(409,'event_invalid','事件来源已失效，不能保存')
                mid=self.control_source(c,cid,use['id'],'用户确认保存：'+e['content']);eid=e['id']
                c.execute('UPDATE relationship_events SET long_term=1,confirmed_by=?,storage_consent_message_id=?,revision=revision+1,updated_at=? WHERE id=?',(user['id'],mid,now(),eid))
                c.execute('INSERT INTO relationship_event_sources VALUES(?,?,?)',(eid,cid,mid))
                if e['kind']=='boundary':
                    if TEMPORARY.search(e['content']) or '这个' in e['content']:raise ApiError(422,'boundary_scope_needed','临时边界需要明确话题与期限，不能无期限保存')
                    c.execute('UPDATE relationship_states SET pace=?,pace_basis_id=? WHERE conversation_id=?',(e['content'],eid,cid))
                if e['target_type']:c.execute('UPDATE relationship_states SET relationship_type=?,basis_event_id=? WHERE conversation_id=?',(e['target_type'],eid,cid))
                if e['target_familiarity']:c.execute('UPDATE relationship_states SET familiarity=?,familiarity_basis_id=? WHERE conversation_id=?',(e['target_familiarity'],eid,cid))
            elif action=='familiarity':
                target=body.get('target')
                if target not in ('new','familiar','close'):raise ApiError(422,'invalid_familiarity','熟悉程度不正确')
                rank={'new':0,'familiar':1,'close':2};current=use['temporary_familiarity'] or state['familiarity']
                lowering=rank[target]<max(rank[current],rank[state['familiarity']])
                if target!='new' and not lowering and body.get('confirm_evidence') is not True:raise ApiError(422,'relationship_evidence_needed','请明确核对双向了解的依据')
                if target=='close' and body.get('allow_emotional_expression') is not True:raise ApiError(422,'relationship_consent_needed','亲近表达需要明确愿意，不能默认')
                sources=[] if target=='new' or lowering else self.familiarity_sources(c,cid,body.get('source_ids'))
                if lowering:
                    old_basis=use['temporary_familiarity_event_id'] or state['familiarity_basis_id']
                    if target!='new' and old_basis:sources=[r[0] for r in c.execute('SELECT message_id FROM relationship_event_sources WHERE event_id=?',(old_basis,))]
                    for old in c.execute("SELECT * FROM relationship_events WHERE conversation_id=? AND target_familiarity IS NOT NULL AND status IN ('accepted','completed')",(cid,)).fetchall():
                        if rank[old['target_familiarity']]>rank[target]:c.execute("UPDATE relationship_events SET status='invalidated',revision=revision+1 WHERE id=?",(old['id'],))
                    fallback=next((e for e in c.execute("SELECT * FROM relationship_events WHERE conversation_id=? AND long_term=1 AND target_familiarity IS NOT NULL AND status IN ('accepted','completed') ORDER BY rowid DESC",(cid,)) if rank[e['target_familiarity']]<=rank[target] and self.valid_event(c,e)),None)
                    c.execute('UPDATE relationship_states SET familiarity=?,familiarity_basis_id=?,intimacy_paused=1 WHERE conversation_id=?',(fallback['target_familiarity'] if fallback else 'new',fallback['id'] if fallback else None,cid))
                    c.execute('UPDATE conversation_uses SET temporary_familiarity=NULL,temporary_familiarity_event_id=NULL WHERE conversation_id=?',(cid,))
                mid=self.control_source(c,cid,use['id'],'用户核对双向了解为'+TYPE_LABEL[target]+('，并明确愿意增加情感表达' if target=='close' else '')+('，同意保存' if store else '，仅本次'))
                eid=self.event(c,cid,mid,'repair','用户核对双向了解：'+TYPE_LABEL[target],use['id'],user['id'],familiar=target,store=store,sources=sources)
                c.execute('UPDATE conversation_uses SET temporary_familiarity=?,temporary_familiarity_event_id=? WHERE id=?',(target,eid,use['id']))
                if store:c.execute('UPDATE relationship_states SET familiarity=?,familiarity_basis_id=? WHERE conversation_id=?',(target,eid,cid))
            elif action=='repair_event':
                ids=body.get('source_ids');content=body.get('content')
                if body.get('confirm_repair') is not True or not isinstance(ids,list) or not 2<=len(ids)<=12 or any(not isinstance(x,str) or not re.fullmatch('[a-f0-9]{32}',x) for x in ids):raise ApiError(422,'repair_evidence_needed','请明确核对双方已接受修复的来源')
                sources=[]
                for sid in set(ids):
                    row=c.execute("SELECT * FROM messages WHERE id=? AND conversation_id=? AND status IN ('saved','completed') AND role IN ('user','assistant')",(sid,cid)).fetchone()
                    if not row or c.execute('SELECT 1 FROM memory_context_blocks WHERE conversation_id=? AND message_id=?',(cid,sid)).fetchone():raise ApiError(422,'relationship_source_invalid','修复来源无效')
                    sources.append(row)
                if not isinstance(content,str) or not 2<=len(content)<=400 or not re.search(r'我接受你的道歉|误会说开了|我们和好了|我们已经说清楚',content) or re.search(r'以后|计划|明天|假如|如果|不接受',content):raise ApiError(422,'repair_evidence_needed','不能把未来计划或未接受的道歉记成已修复')
                users=[m for m in sources if m['role']=='user' and content in m['content'] and supported_assertion(content,m['content']) and not AMBIGUOUS.search(m['content'])]
                actors=[m for m in sources if m['role']=='assistant' and m['reply_to_id'] and re.search(r'对不起|抱歉|我理解|说清楚',m['content'])]
                if not users or not actors or not any(a['ordinal']<u['ordinal'] for a in actors for u in users):raise ApiError(422,'repair_evidence_needed','需要角色解释或道歉后，用户明确接受的依据')
                mid=self.control_source(c,cid,use['id'],'用户核对已经修复：'+content+('，同意保存' if store else '，仅本次'))
                eid=self.event(c,cid,mid,'repair',content,use['id'],user['id'],status='completed',store=store,sources=ids)
            elif action=='import_memory':
                m=c.execute("SELECT * FROM memories WHERE id=? AND conversation_id=? AND status='confirmed' AND kind IN ('promise','experience','boundary') AND (valid_until IS NULL OR valid_until>?)",(body.get('memory_id'),cid,now())).fetchone()
                if not m or body.get('memory_revision')!=m['revision'] or not self.memory.sources_valid(c,m['id']):raise ApiError(422,'relationship_source_invalid','只能保存有有效来源且已确认的约定、经历或边界')
                if c.execute("SELECT 1 FROM relationship_events WHERE conversation_id=? AND source_memory_id=? AND status IN ('accepted','completed')",(cid,m['id'])).fetchone():raise ApiError(409,'event_already_saved','该记忆已有有效事件，不重复保存')
                mid=self.control_source(c,cid,use['id'],'用户明确将已确认内容保存到关系记录：'+m['content'])
                sources=[r[0] for r in c.execute('SELECT message_id FROM memory_sources WHERE memory_id=?',(m['id'],))]
                eid=self.event(c,cid,mid,m['kind'],m['content'],use['id'],user['id'],status='completed' if m['kind']=='experience' else 'accepted',store=True,sources=sources,memory_id=m['id'])
                c.execute('UPDATE relationship_events SET valid_until=? WHERE id=?',(m['valid_until'],eid))
                if m['kind']=='boundary':c.execute('UPDATE relationship_states SET pace=?,pace_basis_id=? WHERE conversation_id=?',(m['content'],eid,cid))
            elif action in ('delete_event','edit_event'):
                e=c.execute('SELECT * FROM relationship_events WHERE id=? AND conversation_id=? AND (long_term=1 OR use_id=?)',(body.get('event_id'),cid,use['id'])).fetchone()
                if not e or body.get('event_revision')!=e['revision']:raise ApiError(409,'event_changed','事件已变化，请刷新')
                if action=='delete_event':
                    if e['id'] in (state['basis_event_id'],use['temporary_relation_event_id']) and body.get('result_type')!='unspecified':raise ApiError(409,'relationship_delete_impact','删除当前关系依据需要明确恢复未约定')
                    if e['id'] in (state['familiarity_basis_id'],use['temporary_familiarity_event_id']) and body.get('result_familiarity')!='new':raise ApiError(409,'relationship_delete_impact','删除熟悉依据需要明确恢复初识')
                    mid=self.control_source(c,cid,use['id'],'用户删除事件并确认依赖状态失效')
                    c.execute("UPDATE relationship_events SET status='invalidated',long_term=0,revision=revision+1 WHERE id=?",(e['id'],))
                    if e['id'] in (state['basis_event_id'],use['temporary_relation_event_id']):
                        c.execute("UPDATE relationship_states SET relationship_type='unspecified',basis_event_id=NULL,intimacy_paused=1 WHERE conversation_id=?",(cid,));c.execute('UPDATE conversation_uses SET temporary_relation=NULL,temporary_relation_event_id=NULL WHERE conversation_id=?',(cid,))
                    if e['id'] in (state['familiarity_basis_id'],use['temporary_familiarity_event_id']):
                        c.execute("UPDATE relationship_states SET familiarity='new',familiarity_basis_id=NULL WHERE conversation_id=?",(cid,));c.execute('UPDATE conversation_uses SET temporary_familiarity=NULL,temporary_familiarity_event_id=NULL WHERE conversation_id=?',(cid,))
                    self.block_event(c,cid,e)
                else:
                    if e['kind'] in ('relationship','revocation'):raise ApiError(422,'relation_edit_forbidden','关系变更须明确确认，不能通过编辑事件成立')
                    content=body.get('content')
                    if not isinstance(content,str) or not 2<=len(content)<=400:raise ApiError(422,'invalid_event_content','请输入2—400字')
                    mid=self.control_source(c,cid,use['id'],'用户请求核对事件修改：'+content)
                    c.execute("UPDATE relationship_events SET content=?,status='pending_review',revision=revision+1 WHERE id=?",(content,e['id']));self.block_event(c,cid,e)
                eid=e['id']
            else:raise ApiError(422,'invalid_relationship_action','关系操作格式不正确')
            self.bump(c,cid);self.reconcile(c,cid)
            result={'updated':True,'event_id':eid,'state':self.effective(c,cid,use)}
            c.execute('INSERT INTO relationship_operations VALUES(?,?,?,?)',(cid,key,digest,json.dumps(result)))
        self.memory.stop_controls(cid);return result
    def block_event(self,c,cid,e,reason='deleted'):
        blocked={r[0] for r in c.execute('SELECT message_id FROM relationship_event_sources WHERE event_id=?',(e['id'],))};blocked.add(e['primary_source_id'])
        changed=True
        while changed:
            changed=False
            for row in c.execute('SELECT g.result_message_id,g.context_message_ids,g.user_message_id,i.effective_state FROM generation_requests g LEFT JOIN relationship_inputs i ON i.generation_id=g.id WHERE g.conversation_id=?',(cid,)):
                state=json.loads(row['effective_state'] or '{}');dependencies=set(json.loads(row['context_message_ids'] or '[]'))
                if (e['id'] in state.get('event_ids',[]) or dependencies&blocked or row['user_message_id'] in blocked) and row['result_message_id'] not in blocked:
                    blocked.add(row['result_message_id']);changed=True
        for sid in blocked:
            self.tag(c,cid,sid,e['use_id'],'invalid_event');c.execute('INSERT OR IGNORE INTO memory_context_blocks VALUES(?,?,?)',(cid,sid,reason))
            c.execute('INSERT OR IGNORE INTO memory_suppressions VALUES(?,?,?,?,?)',(cid,sid,'*',reason,now()))
            if getattr(self.memory,'summaries',None):self.memory.summaries.on_blocked(c,cid,{sid})
        linked=[r[0] for r in c.execute("SELECT id FROM memories WHERE conversation_id=? AND status IN ('confirmed','candidate','conflicted') AND (primary_source_id IN (SELECT message_id FROM memory_context_blocks WHERE conversation_id=?) OR id IN (SELECT memory_id FROM memory_sources WHERE message_id IN (SELECT message_id FROM memory_context_blocks WHERE conversation_id=?)))",(cid,cid,cid))]
        for mid in linked:
            self.memory.block_sources(c,cid,mid,reason);c.execute("UPDATE memories SET status='invalidated',revision=revision+1 WHERE id=?",(mid,));c.execute('DELETE FROM memory_vectors WHERE memory_id=?',(mid,))
    def extraction_sources(self,c,cid,sources):
        controls={r[0] for r in c.execute("SELECT message_id FROM relationship_message_tags WHERE conversation_id=? AND kind IN ('explicit_control','invalid_event')",(cid,))}
        return [m for m in sources if m['id'] not in controls and not AUTHORITY.search(m['content'])]
    def filter_recall(self,items):
        # Relationship authority comes from effective state/events, not a user_fact label.
        return [m for m in items if m.get('kind')!='relationship' and not AUTHORITY.search(m['content'])]
    def authority_valid(self,c,cid,record,old_state,state,use,basis,dimension):
        key=dimension+'_basis_id'
        if key in old_state:
            if not old_state[key] or old_state[key]!=basis:return False
            event=c.execute('SELECT * FROM relationship_events WHERE id=? AND conversation_id=?',(basis,cid)).fetchone()
            return bool(event and self.valid_event(c,event) and
                        (event['long_term'] or (use and record['use_id']==use['id']==event['use_id'])))
        # Pre-fix audit rows have no basis IDs. Only trust the unchanged revision and
        # original valid use, or the same still-persistent authority across uses.
        same_use=use and record['use_id']==use['id']
        if dimension=='relation':
            durable=old_state.get('persistent_type')==state['persistent_type']=='romantic'
        else:
            event=c.execute('SELECT long_term FROM relationship_events WHERE id=?',(basis,)).fetchone() if basis else None
            durable=bool(event and event['long_term'])
        return bool(record['revision']==state['revision'] and basis and (same_use or durable))
    def source_available(self,c,cid,mid,session,use_id):
        source=c.execute("SELECT * FROM messages WHERE id=? AND conversation_id=? AND role='user' AND status='saved'",(mid,cid)).fetchone()
        if not source or not source['content'].strip():return False
        tag=c.execute('SELECT * FROM relationship_message_tags WHERE message_id=?',(mid,)).fetchone()
        previous=c.execute('SELECT content FROM messages WHERE conversation_id=? AND ordinal<? ORDER BY ordinal DESC LIMIT 1',(cid,source['ordinal'])).fetchone()
        related=(bool(tag and tag['kind']!='user_message') or bool(RELATED.search(source['content'])) or
                 bool(previous and RELATED.search(previous['content']) and re.fullmatch(r'(?:可以|好|好的|行|嗯|哈哈|随便|你觉得呢)[。！？!?]*',source['content'])))
        if not related:return True
        use=self.use(c,cid,session,use_id)
        if not use:return False
        # A durable, still-effective source can be the explicit question of this turn;
        # an unsaved or revoked source must never be reinserted to make a user input.
        state=c.execute('SELECT * FROM relationship_states WHERE conversation_id=?',(cid,)).fetchone()
        bases=[use['temporary_'+field+'_event_id'] if use['temporary_'+field] else state[key]
               for field,key in (('relation','basis_event_id'),('familiarity','familiarity_basis_id'),('pace','pace_basis_id'))]
        known=c.execute("SELECT 1 FROM relationship_events WHERE conversation_id=? AND kind='relationship' AND (primary_source_id=? OR consent_message_id=?)",(cid,mid,mid)).fetchone()
        if not known and tag and tag['use_id']==use['id']:return True
        for basis in bases:
            event=c.execute('SELECT * FROM relationship_events WHERE id=? AND conversation_id=?',(basis,cid)).fetchone() if basis else None
            if not event or not self.valid_event(c,event) or not (event['long_term'] or event['use_id']==use['id']):continue
            if mid in (event['primary_source_id'],event['consent_message_id'],event['storage_consent_message_id']) or c.execute('SELECT 1 FROM relationship_event_sources WHERE event_id=? AND message_id=?',(basis,mid)).fetchone():return True
        return False
    def context(self,c,r,history):
        cid=r['conversation_id'];use=self.use(c,cid,r['auth_session_id'],r.get('use_id'));state=self.effective(c,cid,use)
        current=c.execute('SELECT * FROM relationship_states WHERE conversation_id=?',(cid,)).fetchone()
        if use:use=self.use(c,cid,r['auth_session_id'],use['id'])
        relation_basis=use['temporary_relation_event_id'] if use and use['temporary_relation'] else current['basis_event_id']
        familiarity_basis=use['temporary_familiarity_event_id'] if use and use['temporary_familiarity'] else current['familiarity_basis_id']
        tagged={x[0] for x in c.execute("SELECT message_id FROM relationship_message_tags WHERE conversation_id=? AND kind!='user_message'",(cid,))}
        # Include old persisted transcripts predating step8 and generic replies to a relationship proposal.
        previous_related=False
        for m in c.execute('SELECT id,content,role FROM messages WHERE conversation_id=? ORDER BY ordinal',(cid,)):
            relation=bool(RELATED.search(m['content']))
            if relation or (previous_related and m['role']=='user' and re.fullmatch(r'(?:可以|好|好的|行|嗯|哈哈|随便|你觉得呢)[。！？!?]*',m['content'])):tagged.add(m['id'])
            previous_related=relation
        for record in c.execute('SELECT g.result_message_id,i.effective_state,i.use_id,i.revision FROM relationship_inputs i JOIN generation_requests g ON g.id=i.generation_id WHERE i.conversation_id=?',(cid,)):
            old_state=json.loads(record['effective_state'])
            lost_romance=(old_state['relationship_type']=='romantic' and
                          (state['relationship_type']!='romantic' or state['intimacy_paused'] or
                           not self.authority_valid(c,cid,record,old_state,state,use,relation_basis,'relation')))
            lost_closeness=(old_state['familiarity']=='close' and
                           (state['familiarity']!='close' or state['intimacy_paused'] or
                            not self.authority_valid(c,cid,record,old_state,state,use,familiarity_basis,'familiarity')))
            if lost_romance or lost_closeness:tagged.add(record['result_message_id'])
        filtered=[]
        for m in history:
            if m['id'] not in tagged:filtered.append(m)
            elif m['id']==r['user_message_id'] and self.source_available(c,cid,m['id'],r['auth_session_id'],r.get('use_id')):
                item=dict(m);item['content']='[当前用户表达；关系效力以服务端有效状态为准，模糊或引用不构成同意]\n'+m['content'];filtered.append(item)
        history=filtered
        events=[{'id':e['id'],'kind':e['kind'],'content':e['content'],'status':e['status']} for e in c.execute("SELECT * FROM relationship_events WHERE conversation_id=? AND long_term=1 AND status IN ('accepted','completed') ORDER BY occurred_at DESC,rowid DESC",(cid,)) if self.valid_event(c,e)][:12]
        c.execute('INSERT OR REPLACE INTO relationship_inputs VALUES(?,?,?,?,?)',(r['id'],cid,use['id'] if use else None,state['revision'],json.dumps({**state,'event_ids':[e['id'] for e in events],'relation_basis_id':relation_basis,'familiarity_basis_id':familiarity_basis},ensure_ascii=False)))
        return history,{'state':state,'events':events,'rules':'仅以上当前状态构成关系依据；旧历史、语气、未保存同意和消息数量不授权。朋友不等于熟悉。未约定时保守表达，不用恋爱称呼。暂停亲密时不发亲密提议；任何具体亲密行为仍需当次明确同意。拒绝与慢节奏立即遵循，不挽留施压，不编造离开期间经历。'}
