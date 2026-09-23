import sqlite3
import uuid
import analyzer
import unittest
from unittest.mock import patch, Mock
from analyzer import telegram_demo as demo


class DemoTests(unittest.TestCase):
    def setUp(self):
        uri = 'file:demo-' + uuid.uuid4().hex + '?mode=memory&cache=shared'
        keeper = sqlite3.connect(uri, uri=True)
        self.addCleanup(keeper.close)
        def conn():
            c = sqlite3.connect(uri, uri=True)
            c.row_factory = sqlite3.Row
            return c
        self.db = patch.object(analyzer, 'db_store', Mock(get_conn=conn), create=True)
        self.db.start()
        self.send = patch('analyzer.telegram._send_message', return_value={'ok': True}).start()
        self.answer = patch('analyzer.telegram.answer_callback_query', return_value={'ok': True}).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.db.stop)

    def update(self, token, who=123, action='approve'):
        return {'callback_query': {'id': 'callback', 'from': {'id': who},
                'message': {'chat': {'id': who, 'type': 'private'}},
                'data': f'demo:{action}:{token}'}}

    def test_send_receive_and_duplicate_are_demo_only(self):
        ticket = demo.send(1, 'fake', '123')['demo']
        self.assertEqual(ticket['status'], 'pending')
        result = demo.receive(1, self.update(ticket['token']), 'fake', '123')
        self.assertTrue(result['received'])
        self.assertTrue(result['demo_only'])
        self.assertEqual(demo.latest(1)['status'], 'approved')
        self.assertFalse(demo.receive(1, self.update(ticket['token']), 'fake', '123')['received'])
        self.assertEqual(self.send.call_count, 2)  # proposal plus one receipt only

    def test_foreign_chat_and_expired_click_cannot_approve(self):
        ticket = demo.send(1, 'fake', '123')['demo']
        self.assertFalse(demo.receive(1, self.update(ticket['token'], who=999), 'fake', '123')['ok'])
        with patch.object(demo.time, 'time', return_value=ticket['expires_at'] + 1):
            self.assertFalse(demo.receive(1, self.update(ticket['token']), 'fake', '123')['received'])
            self.assertEqual(demo.latest(1)['status'], 'expired')

    def test_reject_is_persisted_and_normal_callbacks_untouched(self):
        ticket = demo.send(1, 'fake', '123')['demo']
        demo.receive(1, self.update(ticket['token'], action='reject'), 'fake', '123')
        self.assertEqual(demo.latest(1)['status'], 'rejected')
        self.assertIsNone(demo.receive(1, {'message': {'text': '/confirm real'}}, 'fake', '123'))


if __name__ == '__main__':
    unittest.main()
