from pathlib import Path
import tempfile
import unittest
from app.server import Application, password_hash


class PasswordTest(unittest.TestCase):
    def test_change_persists_and_revokes_all_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'password.hash'
            old = password_hash('old-password')
            app = Application('unused', old, path)
            token, _ = app.login('old-password', 'test')
            self.assertEqual(401, app.change_password('wrong', 'new-password', 'test')[0])
            self.assertFalse(path.exists())
            self.assertEqual(400, app.change_password('old-password', 'short', 'test')[0])
            self.assertEqual(200, app.change_password('old-password', 'new-password', 'test')[0])
            self.assertFalse(app.authenticated('stock2027=' + token))
            self.assertNotIn('new-password', path.read_text())
            restarted = Application('unused', old, path)
            self.assertEqual(401, restarted.login('old-password', 'test')[1])
            self.assertEqual(200, restarted.login('new-password', 'test')[1])

    def test_failure_keeps_old_password(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application('unused', password_hash('old-password'), Path(directory) / 'missing' / 'password.hash')
            self.assertEqual(503, app.change_password('old-password', 'new-password', 'test')[0])
            self.assertEqual(200, app.login('old-password', 'test')[1])

    def test_wrong_current_password_is_rate_limited(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application('unused', password_hash('old-password'), Path(directory) / 'password.hash')
            for _ in range(10):
                self.assertEqual(401, app.change_password('wrong', 'new-password', 'test')[0])
            self.assertEqual(429, app.change_password('wrong', 'new-password', 'test')[0])
    def test_master_login_and_reset_survive_regular_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'password.hash'
            original = password_hash('ordinary-test-password')
            master = password_hash('recovery-test-password')
            app = Application('unused', original, path, master)
            token, status = app.login('recovery-test-password', 'test')
            self.assertEqual(200, status)
            self.assertEqual(200, app.change_password('recovery-test-password', 'replacement-test-password', 'test')[0])
            self.assertFalse(app.authenticated('stock2027=' + token))
            restarted = Application('unused', original, path, master)
            self.assertEqual(200, restarted.login('recovery-test-password', 'test')[1])
            self.assertEqual(200, restarted.login('replacement-test-password', 'test')[1])
            self.assertEqual(401, restarted.login('ordinary-test-password', 'test')[1])
    def test_six_character_password_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application('unused', password_hash('old-password'), Path(directory) / 'password.hash')
            self.assertEqual(200, app.change_password('old-password', 'abc123', 'test')[0])
            self.assertEqual(200, app.login('abc123', 'test')[1])