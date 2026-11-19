import json
import unittest
import test_chat as helpers

class GuestTest(unittest.TestCase):
    setUpClass=classmethod(helpers.ChatTest.setUpClass.__func__)
    setUp=helpers.ChatTest.setUp
    tearDown=helpers.ChatTest.tearDown
    req=helpers.ChatTest.req

    def test_guest_isolation_reuse_and_origin(self):
        headers={'Host':'test.local','Origin':'http://test.local','Content-Type':'application/json'}
        def call(extra=None):
            return self.app.handle('POST','/api/auth/guest',{**headers,**(extra or {})},b'{}','127.0.0.1')
        first=call();second=call()
        self.assertEqual(first.status,201)
        a=json.loads(first.body);b=json.loads(second.body)
        self.assertNotEqual(a['user']['id'],b['user']['id'])
        cookie=first.headers['Set-Cookie'].split(';')[0]
        again=call({'Cookie':cookie})
        self.assertEqual(again.status,200)
        self.assertEqual(json.loads(again.body)['user']['id'],a['user']['id'])
        self.assertEqual(call({'Origin':'http://evil.example'}).status,403)
        response=self.app.handle('GET','/api/conversations/'+self.cid,{**headers,'Cookie':cookie},b'','127.0.0.1')
        self.assertEqual(response.status,404)
        self.assertNotIn('password',a['user'])
