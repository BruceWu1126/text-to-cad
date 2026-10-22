"""The viewer is a short-lived launcher; one engine owns every page session."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from tests.python.support import engine_daemon


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dist = self.root / 'dist'
        self.dist.mkdir()
        (self.dist / 'index.html').write_text('<html>viewer</html>', encoding='utf-8')
        self.env = engine_daemon.watched({**os.environ, 'CADGEN_ENGINE_DIR': str(self.root / 'engine'),
                   'CADGEN_STATE_DIR': str(self.root / 'state'), 'CADGEN_ENGINE_MESH': 'inline'}, self.addCleanup)

    def launch(self, *args, cwd=None, ok=True):
        done = subprocess.run([sys.executable, '-m', 'cadgen.viewer', '--json', '--dist', str(self.dist), *args],
                              env=self.env, cwd=cwd or self.root, capture_output=True, text=True, timeout=60)
        if not ok:
            self.assertNotEqual(done.returncode, 0)
            return done.stderr
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def get(self, launched, route, *, token=True, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', launched['port'], timeout=20)
        try:
            conn.request('GET', route, headers={**({'Authorization': 'Bearer '+launched['token']} if token else {}),
                                               **(headers or {})})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_launches_reuse_one_engine_and_keep_per_session_roots(self):
        first = self.launch()
        again = self.launch()
        fresh = self.launch('--new')
        other = self.root / 'other'
        other.mkdir()
        elsewhere = self.launch(cwd=other)
        self.assertEqual({r['pid'] for r in (first, again, fresh, elsewhere)}, {first['pid']})
        self.assertEqual({r['port'] for r in (first, again, fresh, elsewhere)}, {first['port']})
        self.assertEqual(first['session'], again['session'])
        self.assertNotEqual(first['session'], fresh['session'])
        for launch, root in ((first, self.root), (elsewhere, other)):
            status, _, data = self.get(launch, urlsplit(launch['apiUrl']).path + '/__cad/server')
            self.assertEqual(status, 200)
            self.assertEqual(os.path.realpath(json.loads(data)['start']), os.path.realpath(root))
            self.assertEqual(json.loads(data)['pid'], first['pid'])
        # Both launchers have exited; the shared server still serves their pages.
        self.assertEqual(self.get(first, urlsplit(first['apiUrl']).path + '/')[0], 200)
        self.assertIn('already serves', self.launch('--port', str(first['port']+1), ok=False))

    def test_every_route_requires_token_and_keeps_host_and_post_gates(self):
        launch = self.launch()
        for route in ('/', '/__cad/server', '/api/docs', '/__cad/api/docs', '/mcp'):
            self.assertEqual(self.get(launch, route, token=False)[0], 403, route)
            self.assertEqual(self.get(launch, route, headers={'Host': 'evil.example'})[0], 403, route)
        status, headers, _ = self.get(launch, urlsplit(launch['url']).path+'?token='+launch['token'], token=False)
        self.assertEqual(status, 302)
        self.assertIn('HttpOnly', headers['set-cookie'])
        self.assertNotIn(launch['token'], headers['location'])
        conn = http.client.HTTPConnection('127.0.0.1', launch['port'], timeout=10)
        try:
            conn.request('POST', '/__cad/recents', body=b'{}', headers={'Authorization': 'Bearer '+launch['token']})
            reply = conn.getresponse()
            self.assertEqual(reply.status, 403)
            reply.read()
        finally:
            conn.close()

    def test_api_only_needs_no_built_client(self):
        done = subprocess.run([sys.executable, '-m', 'cadgen.viewer', '--api-only', '--json'],
                              env=self.env, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        launch = json.loads(done.stdout)
        self.assertEqual(self.get(launch, urlsplit(launch['apiUrl']).path+'/__cad/server')[0], 200)


if __name__ == '__main__':
    unittest.main()
