import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import action
import list as list_module
import read as read_module


class RichTextSafetyTests(unittest.TestCase):
    def test_plain_text_cannot_inject_qt_rich_text(self):
        rendered = read_module.text_to_rich_html(
            '<img src="file:///etc/passwd">\n'
            '<script>danger()</script>\n'
            '[local](file:///etc/passwd)\n'
            '[web](https://example.com/?a=1&b=2)'
        )

        self.assertNotIn('<img ', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('href="file:', rendered)
        self.assertIn('&lt;img', rendered)
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', rendered)

    def test_html_sanitizer_keeps_only_safe_link_schemes(self):
        rendered = read_module.sanitize_and_enrich_html(
            '<img src="file:///etc/passwd">'
            '<a href="file:///etc/passwd">local</a>'
            '<a href="https://example.com/?a=1&b=2">web</a>'
        )

        self.assertNotIn('<img ', rendered)
        self.assertNotIn('href="file:', rendered)
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', rendered)


class BoundedProcessTests(unittest.TestCase):
    def test_process_output_is_rejected_at_the_limit(self):
        secure_io = importlib.import_module('secure_io')
        out, err, code = secure_io.run_bounded(
            [sys.executable, '-c', 'import sys; sys.stdout.write("x" * 10000)'],
            timeout=2,
            max_output_bytes=1024,
        )

        self.assertNotEqual(0, code)
        self.assertEqual('', out)
        self.assertIn('output limit', err.lower())


class SecureCacheTests(unittest.TestCase):
    def test_atomic_write_does_not_follow_destination_symlink(self):
        secure_io = importlib.import_module('secure_io')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            victim = root / 'victim.txt'
            victim.write_text('do not overwrite', encoding='utf-8')
            cache = root / 'cache.json'
            cache.symlink_to(victim)

            secure_io.atomic_write_json(cache, {'mail': 'private'})

            self.assertEqual('do not overwrite', victim.read_text(encoding='utf-8'))
            self.assertFalse(cache.is_symlink())
            self.assertEqual({'mail': 'private'}, json.loads(cache.read_text(encoding='utf-8')))
            self.assertEqual(0o600, cache.stat().st_mode & 0o777)

    def test_secure_json_read_rejects_symlinks(self):
        secure_io = importlib.import_module('secure_io')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            victim = root / 'victim.json'
            victim.write_text('{"secret": true}', encoding='utf-8')
            cache = root / 'cache.json'
            cache.symlink_to(victim)

            self.assertEqual({}, secure_io.read_json(cache, default={}))

    def test_secure_read_repairs_private_file_permissions(self):
        secure_io = importlib.import_module('secure_io')
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / 'cache.json'
            cache.write_text('{"mail": true}', encoding='utf-8')
            cache.chmod(0o644)

            self.assertEqual({'mail': True}, secure_io.read_json(cache, default={}))
            self.assertEqual(0o600, cache.stat().st_mode & 0o777)


class CredentialTests(unittest.TestCase):
    def test_imap_password_command_is_supported(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / 'config.toml'
            command = json.dumps([sys.executable, '-c', 'print("from-keyring")'])
            config.write_text(
                '[accounts.personal]\n'
                'imap.server = "imap.example.com:993"\n'
                'imap.sasl.plain.username = "me@example.com"\n'
                f'imap.sasl.plain.password.command = {command}\n',
                encoding='utf-8',
            )
            with mock.patch.object(list_module, 'HIMALAYA_CONFIG', str(config)):
                creds = list_module.load_imap_credentials()

        self.assertEqual(('imap.example.com:993', 'me@example.com', 'from-keyring', 'plain'), creds)

    def test_intentional_config_symlink_is_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / 'dotfile.toml'
            target.write_text(
                '[accounts.personal]\n'
                'imap.server = "imap.example.com:993"\n'
                'imap.sasl.plain.username = "me@example.com"\n'
                'imap.sasl.plain.password.raw = "app-password"\n',
                encoding='utf-8',
            )
            config = root / 'config.toml'
            config.symlink_to(target)
            with mock.patch.object(list_module, 'HIMALAYA_CONFIG', str(config)):
                creds = list_module.load_imap_credentials()

        self.assertEqual(('imap.example.com:993', 'me@example.com', 'app-password', 'plain'), creds)


class ExclusionIdentifierTests(unittest.TestCase):
    def test_invalid_imap_search_terms_are_discarded(self):
        self.assertEqual(
            ['category:promotions'],
            list_module._normalize_excluded_terms(['category:promotions', 'bad\r\nUID SEARCH ALL']),
        )

    def test_gmail_search_maps_uid_and_gmail_message_id(self):
        gmail_message_id = 1278455344230334865

        class FakeConnection:
            def uid(self, command, *args):
                if command == 'SEARCH':
                    return 'OK', [b'42']
                if command == 'FETCH':
                    return 'OK', [f'42 (X-GM-MSGID {gmail_message_id})'.encode()]
                raise AssertionError(command)

            def logout(self):
                return 'BYE', []

        with mock.patch.object(list_module, 'load_imap_credentials', return_value=('server', 'user', 'pw', 'plain')), \
             mock.patch.object(list_module, '_imap_connect', return_value=FakeConnection()):
            identifiers = list_module._resolve_excluded_imap(['category:promotions'])

        self.assertIn('42', identifiers)
        self.assertIn(format(gmail_message_id, 'x'), identifiers)


class MailboxTests(unittest.TestCase):
    def test_trash_uses_a_separate_page_cache(self):
        inbox = list_module.get_page_cache_path(30, 1, 'inbox')
        trash = list_module.get_page_cache_path(30, 1, 'trash')
        self.assertNotEqual(inbox, trash)
        self.assertIn('trash_', Path(trash).name)

    def test_restore_moves_from_trash_to_inbox(self):
        with mock.patch.object(action, 'run_himalaya_safe', return_value=('', '', 0)) as run:
            ok, error = action.restore_message('abc123')

        self.assertTrue(ok)
        self.assertEqual('', error)
        run.assert_called_once_with(
            ['himalaya', 'message', 'move', '--from', 'trash', '--to', 'inbox', '--', 'abc123'],
            timeout=20.0,
        )


class PanelCompatibilityTests(unittest.TestCase):
    def test_close_hides_before_optional_hover_suppression(self):
        panel = (ROOT / 'Panel.qml').read_text(encoding='utf-8')
        close_body = panel.split('function close() {', 1)[1].split('}', 1)[0]
        self.assertLess(close_body.index('root.controller.hide()'), close_body.index('setCenterHoverRevealSuppressed(false)'))
        self.assertIn('"centerHoverRevealSuppressed" in root.bar', panel)
        self.assertIn('try {', panel)

    def test_empty_successful_refresh_clears_stale_messages(self):
        panel = (ROOT / 'Panel.qml').read_text(encoding='utf-8')
        self.assertIn('if (!result.error && result.envelopes)', panel)

    def test_actions_are_queued_instead_of_killing_inflight_helpers(self):
        panel = (ROOT / 'Panel.qml').read_text(encoding='utf-8')
        self.assertIn('function processNextFlag()', panel)
        self.assertIn('function processNextMove()', panel)
        self.assertNotIn('flagProc.running = false', panel)
        self.assertNotIn('moveProc.running = false', panel)

    def test_manifest_refresh_and_page_settings_are_used(self):
        panel = (ROOT / 'Panel.qml').read_text(encoding='utf-8')
        self.assertIn('settings.pageSize', panel)
        self.assertIn('settings.refreshIntervalSec', panel)
        self.assertIn('interval: root.refreshIntervalMs', panel)

    def test_lazy_panel_actions_survive_loader_startup(self):
        widget = (ROOT / 'BarWidget.qml').read_text(encoding='utf-8')
        self.assertIn('function queuePanelAction(', widget)
        self.assertIn('root.runPendingPanelAction()', widget)
        self.assertIn('function openMessage(id)', widget)

    def test_panel_has_inbox_trash_tabs_and_restore_action(self):
        panel = (ROOT / 'Panel.qml').read_text(encoding='utf-8')
        inbox = (ROOT / 'InboxView.qml').read_text(encoding='utf-8')
        self.assertIn('property string mailboxMode: "inbox"', panel)
        self.assertIn('function selectMailbox(', panel)
        self.assertIn('function restoreMessage(', panel)
        self.assertIn('{ label: "Trash", mailbox: "trash"', inbox)
        self.assertIn('p.selectMailbox(mailboxTab.modelData.mailbox)', inbox)

    def test_removed_rows_are_backfilled_from_the_next_page(self):
        panel = (ROOT / 'Panel.qml').read_text(encoding='utf-8')
        self.assertIn('function fillFromNextPage()', panel)
        self.assertIn('root.fillFromNextPage()', panel)
        self.assertIn('root.allEnvelopes.concat(additions)', panel)

    def test_open_during_fetch_requests_a_followup_refresh(self):
        panel = (ROOT / 'Panel.qml').read_text(encoding='utf-8')
        self.assertIn('property bool refreshPending: false', panel)
        self.assertIn('refreshPending = true', panel)
        self.assertIn('root.runPendingRefresh()', panel)


if __name__ == '__main__':
    unittest.main()
