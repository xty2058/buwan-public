import hashlib
import json
import random
import re
import time
import uuid
from pathlib import Path
from .auth import validate_nickname
from .db import rows
from .errors import ApiError


def uid():
    return uuid.uuid4().hex


def now():
    return int(time.time())


class Service:
    def __init__(self, db, open_characters=()):
        self.db = db
        # Deployment allow-list (APP_OPEN_CHARACTERS). Empty keeps the database's own availability.
        self.open_characters = tuple(open_characters)

    def annotate_voice_blocked(self, c, conversation_id, messages, user_id):
        voice = getattr(self, 'voice', None)
        if voice is not None:
            blocked = voice.voice_blocked_map(c, {'id': user_id, 'session_id': None}, conversation_id, messages)
            for message in messages:
                if blocked.get(message['id']):
                    message['voice_blocked'] = True
        return messages

    def annotate_deleted(self, c, conversation_id, messages):
        # Roadmap 1.14: deleted messages keep their place but not their text.
        deleted = {r[0] for r in c.execute('SELECT message_id FROM message_deletions WHERE conversation_id=?', (conversation_id,))}
        for message in messages:
            if message['id'] in deleted:
                message.update(deleted=True, content='', voice_blocked=True)
        return messages

    def annotate_message_sources(self, c, conversation_id, messages):
        sources = {r['message_id']: r['activity_id'] for r in c.execute(
            'SELECT s.message_id,s.activity_id FROM activity_memory_sources s '
            'JOIN activities a ON a.id=s.activity_id WHERE a.conversation_id=?',
            (conversation_id,))}
        promises = {r[0] for r in c.execute(
            'SELECT source_message_id FROM promise_lifecycle WHERE conversation_id=?',
            (conversation_id,))}
        for message in messages:
            if message['id'] in sources:
                message.update(source_type='activity', activity_id=sources[message['id']])
            elif message['id'] in promises:
                message.update(source_type='promise_lifecycle')
        return messages

    def publish(self, definition, seed=False):
        slug, version = definition['slug'], definition['version']
        profile, private = definition['public'], definition['private']
        if not re.fullmatch('[a-z][a-z0-9_-]{1,40}', slug) or type(version) is not int or version < 1:
            raise ValueError('Invalid slug or version')
        allowed = {'name', 'age', 'job', 'tags', 'place', 'theme', 'bio', 'initial_scene', 'opening', 'dialogue_draft'}
        # Roadmap 1.5: optional alternative openings for the default scene.
        optional = {'openings'}
        if set(profile) - optional != allowed or not isinstance(private, dict):
            raise ValueError('Public and private definitions must be separate')
        for key in ('name', 'job', 'place', 'theme', 'bio', 'initial_scene', 'opening'):
            if not isinstance(profile[key], str) or not profile[key].strip():
                raise ValueError('Invalid public field: ' + key)
        if type(profile['age']) is not int or profile['age'] < 18 or not isinstance(profile['tags'], list):
            raise ValueError('Invalid adult age or tags')
        if 'openings' in profile:
            openings = profile['openings']
            if (not isinstance(openings, list) or not 2 <= len(openings) <= 5 or
                    not all(isinstance(o, dict) and set(o) == {'style', 'text'} and isinstance(o['style'], str) and o['style'].strip()
                            and isinstance(o['text'], str) and 1 <= len(o['text'].strip()) <= 300 for o in openings)):
                raise ValueError('Invalid public field: openings')
        raw = json.dumps(private, ensure_ascii=False, sort_keys=True)
        public = json.dumps(profile, ensure_ascii=False)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            character = c.execute('SELECT * FROM characters WHERE slug=?', (slug,)).fetchone()
            if not character:
                character_id = slug
                c.execute('INSERT INTO characters(id,slug,created_at) VALUES(?,?,?)', (character_id, slug, now()))
            else:
                character_id = character['id']
                existing = c.execute('SELECT id FROM character_versions WHERE character_id=? AND version=?', (character_id, version)).fetchone()
                if existing and seed:
                    return existing['id']
                if existing:
                    raise ValueError('Version already exists; publish a new version')
                latest = c.execute('SELECT max(version) FROM character_versions WHERE character_id=?', (character_id,)).fetchone()[0]
                if latest and version <= latest:
                    raise ValueError('Version must increase')
            version_id = uid()
            c.execute('INSERT INTO character_versions VALUES(?,?,?,?,?,?,?)', (version_id, character_id, version, public, raw, hashlib.sha256(raw.encode()).hexdigest(), now()))
            c.execute('UPDATE characters SET current_version_id=? WHERE id=?', (version_id, character_id))
            return version_id

    def seed(self):
        for definition in json.loads((Path(__file__).parent/'seed_characters.json').read_text(encoding='utf-8')):
            self.publish(definition, seed=True)

    def publish_releases(self):
        """Publish released versions kept in backend/characters/ (e.g. linyu-v6.json) on a fresh or older database.
        Called by scripts/init_db.py (the deployment entrypoint), not on every Application start, so tests keep the v1 seed.

        A fresh deployment otherwise stays on the v1 seed. Versions already present are kept as they are;
        a file older than the database's latest version is skipped (versions are immutable and only increase)."""
        published = []
        files = sorted((Path(__file__).parent / 'characters').glob('*.json'))
        for definition in sorted((json.loads(f.read_text(encoding='utf-8')) for f in files), key=lambda d: (d['slug'], d['version'])):
            with self.db.connect() as c:
                latest = c.execute('SELECT max(v.version) FROM character_versions v JOIN characters c ON c.id=v.character_id WHERE c.slug=?',
                                   (definition['slug'],)).fetchone()[0]
            if latest is None or definition['version'] > latest:
                self.publish(definition)
                published.append(f"{definition['slug']}-v{definition['version']}")
        return published

    def characters(self):
        with self.db.connect() as c:
            return [{**json.loads(r['public_profile']), 'id': r['id'], 'slug': r['slug'],
                     'version': r['version'],
                     'available': bool(r['available']) and (not self.open_characters or r['id'] in self.open_characters)}
                    for r in c.execute('SELECT c.id,c.slug,c.available,v.version,v.public_profile FROM characters c JOIN character_versions v ON v.id=c.current_version_id ORDER BY c.rowid')]

    def character(self, character_id):
        found = next((r for r in self.characters() if r['id'] == character_id), None)
        if not found:
            raise ApiError(404, 'not_found', '角色不存在')
        return found

    def own(self, c, conversation_id, user_id):
        row = c.execute('SELECT * FROM conversations WHERE id=? AND user_id=?', (conversation_id, user_id)).fetchone()
        if not row:
            # Same response for unknown and someone else's identifiers.
            raise ApiError(404, 'not_found', '记录不存在或不可访问')
        return dict(row)

    def create_conversation(self, user_id, body):
        character_id = body.get('character_id')
        if not isinstance(character_id, str):
            raise ApiError(422, 'invalid_character', '请选择角色')
        custom_scene = body.get('initial_scene')
        if custom_scene is not None and (not isinstance(custom_scene, str) or not 1 <= len(custom_scene.strip()) <= 500):
            raise ApiError(422, 'invalid_scene', '初始场景需为 1—500 个字')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            previous = c.execute('SELECT id FROM conversations WHERE user_id=? AND character_id=?', (user_id, character_id)).fetchone()
            if previous:
                return previous['id'], False
            character = c.execute('SELECT c.*,v.public_profile FROM characters c JOIN character_versions v ON v.id=c.current_version_id WHERE c.id=?', (character_id,)).fetchone()
            if not character:
                raise ApiError(404, 'not_found', '角色不存在')
            if not character['available'] or (self.open_characters and character_id not in self.open_characters):
                raise ApiError(409, 'character_unavailable', '角色暂不可用，请选择其他角色')
            profile, cid, timestamp = json.loads(character['public_profile']), uid(), now()
            scene = custom_scene.strip() if custom_scene else profile['initial_scene']
            c.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?,?)', (cid, user_id, character_id, character['current_version_id'], scene, timestamp, timestamp))
            # An adjusted background must not silently reuse an incompatible scripted encounter.
            adjusted = scene != profile['initial_scene']
            # Roadmap 1.5: several openings for the default scene, one picked per new conversation.
            opening = random.choice(profile['openings'])['text'] if profile.get('openings') else profile['opening']
            content = '已采用你调整的初始背景：'+scene if adjusted else opening
            c.execute('INSERT INTO messages(id,conversation_id,ordinal,role,content,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                      (uid(), cid, 1, 'system' if adjusted else 'assistant', content, 'completed', timestamp, timestamp))
            c.execute('INSERT INTO relationship_states(conversation_id,updated_at) VALUES(?,?)', (cid, timestamp))
            return cid, True

    def conversations(self, user_id):
        with self.db.connect() as c:
            result = rows(c, '''SELECT c.id,c.character_id,c.character_version_id,c.updated_at,v.version,v.public_profile,
             (SELECT content FROM messages m WHERE m.conversation_id=c.id AND m.status NOT IN ('candidate','replaced','cancelled') AND (m.replaces_id IS NULL OR m.status='completed') ORDER BY ordinal DESC LIMIT 1) AS preview,
             (SELECT status FROM generation_requests g WHERE g.conversation_id=c.id ORDER BY rowid DESC LIMIT 1) AS generation_status
             FROM conversations c JOIN character_versions v ON v.id=c.character_version_id
             WHERE c.user_id=? ORDER BY c.updated_at DESC,c.rowid DESC''', (user_id,))
            for item in result:
                item['character'] = json.loads(item.pop('public_profile'))
            return result

    def conversation(self, user_id, conversation_id):
        with self.db.connect() as c:
            result = self.own(c, conversation_id, user_id)
            version = c.execute('SELECT version,public_profile FROM character_versions WHERE id=?', (result['character_version_id'],)).fetchone()
            current = c.execute('SELECT current_version_id FROM characters WHERE id=?', (result['character_id'],)).fetchone()
            result['character'] = json.loads(version['public_profile'])
            result['version'] = version['version']
            result['has_newer_version'] = current['current_version_id'] != result['character_version_id']
            return result

    def messages(self, user_id, conversation_id, before=None, limit=100):
        if before is not None and (type(before) is not int or not 1 <= before <= 2**63-1):
            raise ApiError(422, 'invalid_cursor', '消息游标不正确')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ApiError(422, 'invalid_limit', '每次读取 1—200 条消息')
        with self.db.connect() as c:
            self.own(c, conversation_id, user_id)
            data = rows(c, '''SELECT id,ordinal,role,content,status,created_at,updated_at,reply_to_id,replaces_id FROM messages WHERE conversation_id=? AND ordinal<?
              AND status NOT IN ('candidate','replaced','cancelled') AND (replaces_id IS NULL OR status='completed') ORDER BY ordinal DESC LIMIT ?''', (conversation_id, before or 2**63-1, limit+1))
            has_more = len(data) > limit
            data = list(reversed(data[:limit]))
            self.annotate_message_sources(c, conversation_id, data)
            self.annotate_voice_blocked(c, conversation_id, data, user_id)
            self.annotate_deleted(c, conversation_id, data)
            return {'items': data, 'has_more': has_more, 'next_before': data[0]['ordinal'] if has_more else None}

    def save_message(self, user_id, conversation_id, body, session_id=None):
        content, request_id = body.get('content'), body.get('client_request_id')
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 1000:
            raise ApiError(422, 'invalid_content', '消息需为 1—1000 个字')
        if not isinstance(request_id, str) or not re.fullmatch('[a-zA-Z0-9_-]{8,80}', request_id):
            raise ApiError(422, 'invalid_request_id', '请求标识不正确')
        content = content.strip()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self.own(c, conversation_id, user_id)
            previous = c.execute('SELECT id,content FROM messages WHERE conversation_id=? AND client_request_id=?', (conversation_id, request_id)).fetchone()
            if previous:
                if previous['content'] != content:
                    raise ApiError(409, 'idempotency_conflict', '同一请求标识不能提交不同内容')
                return {'id': previous['id'], 'status': 'saved', 'generation_available': False}, False
            from .relationship import command
            intent=command(content) if getattr(self,'relationship',None) else None
            if c.execute("SELECT 1 FROM generation_requests WHERE conversation_id=? AND status IN ('queued','generating','awaiting_choice')", (conversation_id,)).fetchone():
                if intent and intent[0] in ('revoke','pause','pace'):self.relationship.memory.cancel_active(c,conversation_id)
                else:raise ApiError(409, 'generation_pending', '先结束当前生成或决定候选回复')
            message_id, timestamp = uid(), now()
            ordinal = c.execute('SELECT coalesce(max(ordinal),0)+1 FROM messages WHERE conversation_id=?', (conversation_id,)).fetchone()[0]
            c.execute('INSERT INTO messages(id,conversation_id,ordinal,role,content,status,client_request_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                      (message_id, conversation_id, ordinal, 'user', content, 'saved', request_id, timestamp, timestamp))
            if getattr(self,'relationship',None):self.relationship.ingest(c,user_id,conversation_id,message_id,content,session_id,body.get('use_id'))
            c.execute('UPDATE conversations SET updated_at=? WHERE id=?', (timestamp, conversation_id))
            return {'id': message_id, 'status': 'saved', 'generation_available': False}, True

    def memories(self, user_id, conversation_id):
        with self.db.connect() as c:
            self.own(c, conversation_id, user_id)
            result = rows(c, "SELECT id,kind,fact_scope,story_scene,content,status,primary_source_id,revision,updated_at FROM memories WHERE conversation_id=? AND status IN ('candidate','confirmed','conflicted') ORDER BY updated_at DESC", (conversation_id,))
            return {'items': result, 'write_available': False}

    def relationships(self, user_id, conversation_id):
        with self.db.connect() as c:
            self.own(c, conversation_id, user_id)
            state = dict(c.execute('SELECT * FROM relationship_states WHERE conversation_id=?', (conversation_id,)).fetchone())
            # Unstored temporary events are scoped to conversation_uses in later steps, never restored as durable consent.
            events = rows(c, 'SELECT id,kind,content,status,primary_source_id,occurred_at,long_term FROM relationship_events WHERE conversation_id=? AND long_term=1 ORDER BY occurred_at DESC', (conversation_id,))
            return {'state': state, 'events': events, 'write_available': False}

    def profile(self, user_id, body):
        nickname = validate_nickname(body.get('nickname'))
        with self.db.connect() as c:
            c.execute('UPDATE users SET nickname=?,updated_at=? WHERE id=?', (nickname, now(), user_id))
        return {'nickname': nickname}

    def open_use(self, user, conversation_id, use_id=None):
        if use_id is not None and (not isinstance(use_id, str) or not re.fullmatch('[a-f0-9]{32}', use_id)):
            raise ApiError(422, 'invalid_use_id', '使用标识不正确')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self.own(c, conversation_id, user['id'])
            if use_id:
                use = c.execute('SELECT * FROM conversation_uses WHERE id=? AND conversation_id=? AND auth_session_id=? AND closed_at IS NULL', (use_id, conversation_id, user['session_id'])).fetchone()
                if not use:
                    raise ApiError(409, 'use_expired', '本次使用已结束，请重新进入会话')
                revision = c.execute('SELECT revocation_revision FROM relationship_states WHERE conversation_id=?', (conversation_id,)).fetchone()[0]
                if use['based_on_revocation_revision'] < revision:
                    c.execute('UPDATE conversation_uses SET temporary_relation=NULL,based_on_revocation_revision=? WHERE id=?', (revision, use_id))
                c.execute('UPDATE conversation_uses SET last_seen_at=? WHERE id=?', (now(), use_id))
                return dict(c.execute('SELECT * FROM conversation_uses WHERE id=?', (use_id,)).fetchone())
            use_id, timestamp = uid(), now()
            revision = c.execute('SELECT revocation_revision FROM relationship_states WHERE conversation_id=?', (conversation_id,)).fetchone()[0]
            c.execute('INSERT INTO conversation_uses(id,conversation_id,auth_session_id,based_on_revocation_revision,opened_at,last_seen_at) VALUES(?,?,?,?,?,?)', (use_id, conversation_id, user['session_id'], revision, timestamp, timestamp))
            return {'id': use_id, 'temporary_relation': None, 'based_on_revocation_revision': revision}

    def close_use(self, user, conversation_id, use_id):
        with self.db.connect() as c:
            self.own(c, conversation_id, user['id'])
            c.execute('UPDATE conversation_uses SET closed_at=coalesce(closed_at,?) WHERE id=? AND conversation_id=? AND auth_session_id=?', (now(), use_id, conversation_id, user['session_id']))
        return {'closed': True}
