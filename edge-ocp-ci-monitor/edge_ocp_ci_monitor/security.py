"""Keep MCP credentials out of diagnostic output and non-authentication data."""

from __future__ import annotations

import functools
import html
import inspect
import json
import logging
import re
import threading
import traceback
from contextlib import contextmanager
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from urllib.parse import quote, quote_plus

_REDACTED = '[REDACTED]'
_lock = threading.RLock()
_active_tokens: dict[str, int] = {}
_previous_factory = None
_redacting_factory = None
_previous_make_record = None
_redacting_make_record = None


def redact_token(text: str, token: str) -> str:
    """Redact raw and common encoded representations without truncating data."""
    if not token:
        return text
    variants = {token, quote(token, safe=''), quote_plus(token, safe=''),
                html.escape(token, quote=True), json.dumps(token)[1:-1]}
    for variant in sorted(variants, key=len, reverse=True):
        text = text.replace(variant, _REDACTED)
        # Percent escapes are case insensitive; the token itself is not.
        if '%' in variant:
            pattern = re.sub(r'%[0-9A-Fa-f]{2}', lambda m: '%' + ''.join(
                f'[{c.lower()}{c.upper()}]' if c.isalpha() else c for c in m[0][1:]
            ), re.escape(variant))
            text = re.sub(pattern, _REDACTED, text)
    return text


def redact_data(value, token: str):
    """Return a sanitized copy suitable for prompts, reports and exports."""
    if not token:
        return value
    if isinstance(value, Enum):
        return value
    if isinstance(value, str):
        clean = redact_token(value, token)
        return value if clean == value else clean
    if is_dataclass(value) and not isinstance(value, type):
        changes = {}
        for f in fields(value):
            if f.init:
                original = getattr(value, f.name)
                clean = redact_data(original, token)
                if clean is not original:
                    changes[f.name] = clean
        return replace(value, **changes) if changes else value
    if isinstance(value, dict):
        pairs = [(redact_data(k, token), redact_data(v, token)) for k, v in value.items()]
        return (value if all(k is old_k and v is old_v for (k, v), (old_k, old_v)
                             in zip(pairs, value.items(), strict=True)) else dict(pairs))
    if isinstance(value, (tuple, list)):
        clean = [redact_data(item, token) for item in value]
        return value if all(a is b for a, b in zip(clean, value, strict=True)) else type(value)(clean)
    return value


def _redact_exception(error: BaseException, token: str, seen=None) -> BaseException:
    """Keep unexpected failures visible without exposing credentials in tracebacks."""
    seen = set() if seen is None else seen
    if id(error) in seen:
        return error
    seen.add(id(error))
    if isinstance(error, BaseExceptionGroup):
        clean = BaseExceptionGroup(redact_token(error.message, token), [
            _redact_exception(child, token, seen) for child in error.exceptions
        ]).with_traceback(error.__traceback__)
    else:
        clean = error
        clean.args = redact_data(clean.args, token)
        for name in ('message', 'filename', 'filename2', 'strerror', 'reason'):
            value = getattr(clean, name, None)
            if isinstance(value, str):
                try:
                    setattr(clean, name, redact_token(value, token))
                except AttributeError:
                    pass  # Some exception attributes are read-only views of args.
    if error.__cause__:
        clean.__cause__ = _redact_exception(error.__cause__, token, seen)
    if error.__context__:
        clean.__context__ = _redact_exception(error.__context__, token, seen)
    clean.__suppress_context__ = error.__suppress_context__
    if hasattr(error, '__notes__'):
        clean.__notes__ = redact_data(error.__notes__, token)
    return clean


@contextmanager
def redact_logs(token: str):
    """Protect all logger namespaces, including concurrent requests and tracebacks."""
    global _previous_factory, _redacting_factory, _previous_make_record, _redacting_make_record
    if not token:
        yield
        return
    with _lock:
        if not _active_tokens:
            previous = logging.getLogRecordFactory()
            _previous_factory = previous

            def factory(*args, **kwargs):
                record = previous(*args, **kwargs)
                with _lock:
                    tokens = sorted(_active_tokens, key=len, reverse=True)
                message = record.getMessage()
                exception = ''.join(traceback.format_exception(*record.exc_info)) if record.exc_info else record.exc_text
                for secret in tokens:
                    message = redact_token(message, secret)
                    if exception:
                        exception = redact_token(exception, secret)
                    if record.stack_info:
                        record.stack_info = redact_token(record.stack_info, secret)
                record.msg, record.args = message, ()
                record.exc_info, record.exc_text = None, exception
                return record

            _redacting_factory = factory
            logging.setLogRecordFactory(factory)
            previous_make_record = logging.Logger.makeRecord
            _previous_make_record = previous_make_record

            def make_record(logger, *args, **kwargs):
                record = previous_make_record(logger, *args, **kwargs)
                # Logger.makeRecord attaches structured "extra" fields after
                # the factory runs; scrub them before any handler sees them.
                with _lock:
                    tokens = sorted(_active_tokens, key=len, reverse=True)
                for secret in tokens:
                    for name, value in tuple(record.__dict__.items()):
                        record.__dict__[name] = redact_data(value, secret)
                return record

            _redacting_make_record = make_record
            logging.Logger.makeRecord = make_record
        _active_tokens[token] = _active_tokens.get(token, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _active_tokens[token] -= 1
            if not _active_tokens[token]:
                del _active_tokens[token]
            if not _active_tokens and logging.getLogRecordFactory() is _redacting_factory:
                logging.setLogRecordFactory(_previous_factory)
            if not _active_tokens and logging.Logger.makeRecord is _redacting_make_record:
                logging.Logger.makeRecord = _previous_make_record


def protect_logs(token_from_args):
    """Scope credential redaction to a synchronous or asynchronous entry point."""
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def wrapped(*args, **kwargs):
                token = token_from_args(*args, **kwargs)
                with redact_logs(token):
                    try:
                        return await function(*args, **kwargs)
                    except BaseException as error:
                        clean = _redact_exception(error, token)
                        if clean is error:
                            raise
                        raise clean from clean.__cause__
        else:
            @functools.wraps(function)
            def wrapped(*args, **kwargs):
                token = token_from_args(*args, **kwargs)
                with redact_logs(token):
                    try:
                        return function(*args, **kwargs)
                    except BaseException as error:
                        clean = _redact_exception(error, token)
                        if clean is error:
                            raise
                        raise clean from clean.__cause__
        return wrapped
    return decorate
