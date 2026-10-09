#!/usr/bin/env python3
"""Keep nginx error causes without logging query strings or credentials."""
import os
import re
import signal
import subprocess
import sys
import urllib.parse

MAX_LINE = 1024 * 1024
URL = re.compile(r'(?:https?(?:://|%(?:25)*3a%(?:25)*2f%(?:25)*2f)|/|%(?:25)*2f)[^\s\"\'<>]*', re.IGNORECASE)
CREDENTIAL = re.compile(
    r'\b(userToken|token|access_token|authorization)\b(\s*[:=]\s*)'
    r'(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;]+)', re.IGNORECASE)
BEARER = re.compile(r'\bBearer\s+[^\s\"\',;]+', re.IGNORECASE)


def decode(text):
    for _ in range(8):
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            return text
        text = decoded
    raise ValueError('diagnostic encoding depth')


def sanitize(raw):
    text = raw.decode('utf-8', errors='replace')
    # Strip an entire raw URL token before decoding: encoded spaces or quotes in
    # a query must not turn its tail into an unprotected diagnostic value.
    text = URL.sub(lambda match: re.split(r'[?#]', decode(match.group(0)), maxsplit=1)[0], text)
    text = BEARER.sub('Bearer [redacted]', text)
    text = CREDENTIAL.sub(lambda match: match.group(1) + match.group(2) + '[redacted]', text)
    text = decode(text)
    text = URL.sub(lambda match: re.split(r'[?#]', match.group(0), maxsplit=1)[0], text)
    text = BEARER.sub('Bearer [redacted]', text)
    text = CREDENTIAL.sub(lambda match: match.group(1) + match.group(2) + '[redacted]', text)
    return text.encode('utf-8')


def stop(child):
    if child is None:
        return
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    else:
        child.wait()


def run(argv):
    child = None
    pending = []

    def forward(number, frame):
        if child is None:
            pending.append(number)
        elif child.poll() is None:
            child.send_signal(number)

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
        signal.signal(number, forward)
    try:
        child = subprocess.Popen(argv, stderr=subprocess.PIPE)
        for number in pending:
            if child.poll() is None:
                child.send_signal(number)
        buffer = bytearray()
        while True:
            chunk = os.read(child.stderr.fileno(), 65536)
            if not chunk:
                if buffer:
                    sys.stderr.buffer.write(sanitize(buffer))
                    sys.stderr.buffer.flush()
                break
            buffer.extend(chunk)
            while True:
                end = buffer.find(b'\n')
                if end < 0:
                    break
                if end + 1 > MAX_LINE:
                    raise ValueError('diagnostic line too long')
                sys.stderr.buffer.write(sanitize(buffer[:end + 1]))
                sys.stderr.buffer.flush()
                del buffer[:end + 1]
            if len(buffer) > MAX_LINE:
                raise ValueError('diagnostic line too long')
        return child.wait()
    except Exception:
        stop(child)
        # Never include the failed input or exception message in this diagnostic.
        sys.stderr.write('NGINX_LOG_SANITIZER_FAILED: child stopped\n')
        sys.stderr.flush()
        return 1
    finally:
        if child is not None and child.stderr is not None:
            child.stderr.close()


def main():
    if len(sys.argv) < 2:
        sys.stderr.write('NGINX_LOG_SANITIZER_COMMAND_REQUIRED\n')
        return 1
    status = run(sys.argv[1:])
    if status < 0:
        number = -status
        signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)
    return status


if __name__ == '__main__':
    sys.exit(main())
