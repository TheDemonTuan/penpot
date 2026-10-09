import importlib.util
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / 'docker/images/files/nginx-safe-run.py'
SPEC = importlib.util.spec_from_file_location('nginx_safe_run', WRAPPER)
SAFE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SAFE)


class NginxSafeLogging(unittest.TestCase):
    def test_queries_and_encoded_tails_never_replace_error_cause(self):
        for encode in (lambda value: value, urllib.parse.quote,
                       lambda value: urllib.parse.quote(urllib.parse.quote(value, safe=''), safe='')):
            target = encode('/mcp/ws?userToken=PRIVATE%20TAIL#fragment')
            raw = ('[error] upstream refused, request: "GET ' + target +
                   ' HTTP/1.1", upstream: "http://backend/mcp/ws?token=PRIVATE%22TAIL"\n').encode()
            with self.subTest(target=target):
                cleaned = SAFE.sanitize(raw)
                self.assertNotIn(b'PRIVATE', cleaned)
                self.assertNotIn(b'TAIL', cleaned)
                self.assertNotIn(b'fragment', cleaned)
                self.assertIn(b'[error] upstream refused', cleaned)
                self.assertIn(b'/mcp/ws HTTP/1.1', cleaned)

    def test_diagnostic_credentials_are_redacted_without_hiding_severity(self):
        raw = b'[warn] status=401 userToken=private token="private value" access_token=secret authorization="Bearer secret"\n'
        cleaned = SAFE.sanitize(raw)
        self.assertNotIn(b'private', cleaned)
        self.assertNotIn(b'secret', cleaned)
        self.assertIn(b'[warn] status=401', cleaned)
        self.assertEqual(cleaned.count(b'[redacted]'), 4)

    def test_streaming_long_unterminated_line_and_exit_status(self):
        program = '''import os, sys
os.write(1, b'access status=502\\n')
os.write(2, b'[error] upstream timeout ' + b'x' * 100000)
os.write(2, b' request: "GET /mcp/stream?userToken=PRIVATE HTTP/1.1"')
sys.exit(7)
'''
        result = subprocess.run([sys.executable, str(WRAPPER), sys.executable, '-c', program],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b'access status=502\n')
        self.assertNotIn(b'PRIVATE', result.stderr)
        self.assertIn(b'[error] upstream timeout', result.stderr)
        self.assertTrue(result.stderr.endswith(b'/mcp/stream HTTP/1.1"'))

    def test_oversized_line_stops_and_reaps_child_without_raw_passthrough(self):
        program = 'import os, time; os.write(2, b"PRIVATE" * 180000); time.sleep(60)'
        result = subprocess.run([sys.executable, str(WRAPPER), sys.executable, '-c', program],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b'NGINX_LOG_SANITIZER_FAILED: child stopped\n')

    def test_term_int_and_quit_reach_child_and_preserve_its_exit_code(self):
        program = '''import signal, sys, time
for number in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
    signal.signal(number, lambda number, frame: sys.exit(23))
print('ready', flush=True)
while True:
    time.sleep(1)
'''
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
            with self.subTest(number=number):
                child = subprocess.Popen([sys.executable, str(WRAPPER), sys.executable, '-c', program],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(child.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(timeout=5), 'child did not become ready')
                    self.assertEqual(child.stdout.readline(), b'ready\n')
                    os.kill(child.pid, number)
                    stdout, stderr = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, 23)
                    self.assertEqual(stdout, b'')
                    self.assertEqual(stderr, b'')
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
