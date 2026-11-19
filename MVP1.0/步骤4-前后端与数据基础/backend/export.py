# -*- coding: utf-8 -*-
"""Export one conversation's chat, memories and relationship record (roadmap 1.10).

Only the signed-in owner's data. Never included: other users, the character's
private definition and prompts, credentials, internal ids of other records,
and the text of messages the user deleted (they appear as placeholders).
"""
import json
from datetime import datetime

from .timeline import BEIJING

VISIBLE = "status NOT IN ('candidate','replaced','cancelled','pending','streaming') AND (replaces_id IS NULL OR status='completed')"
ROLE = {'user': '我', 'system': '记录'}
KIND = {'user_fact': '关于我', 'boundary': '相处边界', 'promise': '约定', 'experience': '共同经历', 'relationship': '关系'}
STATUS = {'confirmed': '已记住', 'candidate': '待确认', 'conflicted': '待确认（与已有记忆冲突）'}
RELATION = {'unspecified': '未约定', 'friends': '朋友', 'romantic': '恋爱'}
FAMILIAR = {'new': '初识', 'familiar': '熟悉', 'close': '亲近'}


def stamp(ts):
    return datetime.fromtimestamp(ts, BEIJING).strftime('%Y-%m-%d %H:%M') if ts else ''


def collect(db, service, relationship, user, cid):
    with db.connect() as c:
        conversation = service.own(c, cid, user['id'])
        bound = c.execute('SELECT public_profile,version FROM character_versions WHERE id=?',
                          (conversation['character_version_id'],)).fetchone()
        profile = json.loads(bound['public_profile'])
        deleted = {r[0] for r in c.execute('SELECT message_id FROM message_deletions WHERE conversation_id=?', (cid,))}
        messages = []
        for m in c.execute('SELECT id,role,content,status,created_at FROM messages WHERE conversation_id=? AND ' + VISIBLE + ' ORDER BY ordinal', (cid,)):
            item = {'role': m['role'], 'time': stamp(m['created_at']), 'status': m['status']}
            if m['id'] in deleted:
                item.update(deleted=True, content='')
            else:
                item['content'] = m['content']
            messages.append(item)
        memories = [{'kind': KIND.get(m['kind'], m['kind']), 'scope': '故事内' if m['fact_scope'] == 'story' else '现实',
                     'content': m['content'], 'status': STATUS[m['status']], 'updated': stamp(m['updated_at'])}
                    for m in c.execute("SELECT * FROM memories WHERE conversation_id=? AND status IN ('confirmed','candidate','conflicted') ORDER BY kind,updated_at", (cid,))]
    rel = relationship.read(user, cid)
    state = rel['state']
    events = [{'content': e.get('content'), 'kind': e.get('kind'), 'status': e.get('status'), 'time': stamp(e.get('occurred_at'))}
              for e in rel['events']]
    return {
        'exported_at': stamp(int(datetime.now(BEIJING).timestamp())),
        'character': {'name': profile['name'], 'version': bound['version']},
        'initial_scene': conversation['initial_scene'],
        'relationship': {'type': RELATION.get(state.get('relationship_type'), '未约定'),
                         'familiarity': FAMILIAR.get(state.get('familiarity'), '初识'), 'events': events},
        'memories': memories,
        'messages': messages,
    }


def markdown(data):
    name = data['character']['name']
    lines = ['# 我与' + name + '的记录', '', '导出时间：' + data['exported_at'], '', '初始背景：' + data['initial_scene'], '',
             '## 关系', '', '- 关系：' + data['relationship']['type'], '- 熟悉程度：' + data['relationship']['familiarity']]
    for e in data['relationship']['events']:
        lines.append('- ' + (e['time'] + ' ' if e['time'] else '') + (e['content'] or ''))
    lines += ['', '## 记忆', '']
    if not data['memories']:
        lines.append('（暂无）')
    for m in data['memories']:
        lines.append('- 【' + m['kind'] + ' · ' + m['scope'] + ' · ' + m['status'] + '】' + m['content'])
    lines += ['', '## 聊天', '']
    for m in data['messages']:
        who = ROLE.get(m['role'], name)
        text = '（这条消息已删除）' if m.get('deleted') else m['content'].replace('\n', '\n> ')
        note = {'stopped': '（中断）', 'failed': '（未完成）'}.get(m['status'], '')
        lines.append('**' + who + '** · ' + m['time'] + note)
        lines.append('> ' + text)
        lines.append('')
    return '\n'.join(lines) + '\n'
