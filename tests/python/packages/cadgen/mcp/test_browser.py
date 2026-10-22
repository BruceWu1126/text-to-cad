"""A text-only host opens the engine page by authenticated session URL."""
import unittest
from unittest.mock import patch
from cadgen.mcp.browser import ViewerUnavailable, viewer_url, model_link


class ViewerUrlTest(unittest.TestCase):
    def test_uses_engine_session_without_starting_another_server(self):
        with patch('cadgen.mcp.browser.request', return_value={'url': 'http://localhost:123/s/page/?token=t'}) as request:
            self.assertEqual(viewer_url(cwd='/models'), 'http://localhost:123/s/page/?token=t')
            request.assert_called_once_with({'op': 'viewer', 'start': '/models'})

    def test_engine_failure_is_reported(self):
        with patch('cadgen.mcp.browser.request', return_value={'failed': 'missing client'}):
            with self.assertRaisesRegex(ViewerUnavailable, 'missing client'):
                viewer_url()

    def test_model_link_preserves_token_and_encodes_path(self):
        self.assertEqual(model_link('http://localhost:123/s/page/?token=t', '/parts/a #1.step'),
                         'http://localhost:123/s/page/?token=t&file=/parts/a%20%231.step')


if __name__ == '__main__':
    unittest.main()
