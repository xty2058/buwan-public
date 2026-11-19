"""API boundary checks for the integrated MVP1.1 routes; isolated fixtures only."""
import json
import unittest
import test_chat


class MVP11APITest(unittest.TestCase):
    setUpClass = classmethod(test_chat.ChatTest.setUpClass.__func__)
    setUp = test_chat.ChatTest.setUp
    tearDown = test_chat.ChatTest.tearDown
    req = test_chat.ChatTest.req

    def test_capabilities_and_no_secret_exposure(self):
        response, data = self.req('GET', '/api/health')
        self.assertEqual(response.status, 200)
        self.assertTrue(data['capabilities']['activities'])
        self.assertTrue(data['capabilities']['relationship_archive'])
        self.assertIn('tts_configured', data['capabilities'])
        self.assertNotIn('key_file', json.dumps(data))
        self.assertNotIn('access_key', json.dumps(data))
        self.assertIn("media-src 'self'", response.headers['Content-Security-Policy'])

    def test_new_private_endpoints_require_authentication(self):
        paths = [f'/api/conversations/{self.cid}/activities',
                 f'/api/conversations/{self.cid}/archive',
                 f'/api/conversations/{self.cid}/messages/'+('a'*32)+'/audio/content']
        for path in paths:
            response = self.app.handle('GET', path, {'Host':'test.local'})
            self.assertEqual(response.status, 401, path)

    def test_all_new_writes_keep_csrf_boundary(self):
        paths = [f'/api/conversations/{self.cid}/activities',
                 f'/api/conversations/{self.cid}/archive',
                 f'/api/conversations/{self.cid}/messages/'+('a'*32)+'/audio']
        token, _ = self.sessions['a']
        for path in paths:
            response = self.app.handle('POST', path, {'Host':'test.local',
                'Origin':'http://test.local', 'Content-Type':'application/json',
                'Cookie':'weiwan_session='+token}, b'{}')
            self.assertEqual(response.status, 403, path)

    def test_private_archive_and_activity_are_owner_scoped(self):
        for suffix in ('activities','archive'):
            response, _ = self.req('GET', f'/api/conversations/{self.cid}/{suffix}', user='b')
            self.assertEqual(response.status, 404)
            response, data = self.req('GET', f'/api/conversations/{self.cid}/{suffix}')
            self.assertEqual(response.status, 200, data)

    def test_logs_use_route_templates_without_private_ids(self):
        cid, mid = self.cid, 'a'*32
        for suffix, expected in [('activities','/api/conversations/:id/activities'),
                                 ('archive','/api/conversations/:id/archive'),
                                 (f'messages/{mid}/audio/content','/api/conversations/:id/messages/:id/audio')]:
            route = self.app.route_label('GET', f'/api/conversations/{cid}/{suffix}')
            self.assertEqual(route, expected)

    def test_activity_progress_message_exposes_source_link(self):
        base = f'/api/conversations/{self.cid}'
        response, activity = self.req('POST', base+'/activities', {'client_request_id':'source-start-001'})
        self.assertEqual(response.status, 200, activity)
        response, result = self.req('POST', base+'/activities/'+activity['id'],
            {'client_request_id':'source-pause-001','action':'pause','revision':activity['revision']})
        self.assertEqual(response.status, 200, result)
        response, data = self.req('GET', base+'/messages')
        self.assertEqual(response.status, 200, data)
        records = [m for m in data['items'] if m.get('source_type') == 'activity']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['activity_id'], activity['id'])


if __name__ == '__main__':
    unittest.main()
