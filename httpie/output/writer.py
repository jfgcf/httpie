import argparse
import errno
from typing import IO, TextIO, Tuple, Type, Union

import requests

from httpie.context import Environment
from httpie.models import HTTPRequest, HTTPResponse, HTTPRequestJson, HTTPResponseJson, HTTPMessage
from httpie.output.processing import Conversion, Formatting
from httpie.output.streams import (
    BaseStreamJson, BaseStream, BufferedPrettyStream, EncodedStream, PrettyStream, RawStream, 
)

from httpie.output.formatters.colors import (
    AUTO_STYLE, DEFAULT_STYLE, SOLARIZED_STYLE
)


MESSAGE_SEPARATOR = '\n\n'
MESSAGE_SEPARATOR_BYTES = MESSAGE_SEPARATOR.encode()


def write_message(
    requests_message: Union[requests.PreparedRequest, requests.Response],
    env: Environment,
    args: argparse.Namespace,
    with_headers=False,
    with_body=False,
):
    if not (with_body or with_headers):
        return
    write_stream_kwargs = {
        'stream': build_output_stream_for_message(
            args=args,
            env=env,
            requests_message=requests_message,
            with_body=with_body,
            with_headers=with_headers,
        ),
        # NOTE: `env.stdout` will in fact be `stderr` with `--download`
        'outfile': env.stdout,
        'flush': env.stdout_isatty or args.stream
    }
    try:
        if env.is_windows and 'colors' in args.prettify:
            write_stream_with_colors_win_py3(**write_stream_kwargs)
        else:
            write_stream(**write_stream_kwargs)
    except IOError as e:
        show_traceback = args.debug or args.traceback
        if not show_traceback and e.errno == errno.EPIPE:
            # Ignore broken pipes unless --traceback.
            env.stderr.write('\n')
        else:
            raise

def write_message_json(
    requests_message: [requests.PreparedRequest, requests.Response],
    env: Environment,
    args: argparse.Namespace,
    with_headers_req=False,
    with_body_req=False,
    with_headers_res=False,
    with_body_res=False,
):
    if not (with_body_req or with_headers_req or with_body_res or with_headers_res):
        return
    write_stream_kwargs = {
        'stream': build_output_stream_for_message_json(
            args=args,
            env=env,
            requests_message=requests_message,
            with_headers_req=with_headers_req,
            with_body_req=with_body_req,
            with_headers_res=with_headers_res,
            with_body_res=with_body_res,
        ),
        # NOTE: `env.stdout` will in fact be `stderr` with `--download`
        'outfile': env.stdout,
        'flush': env.stdout_isatty or args.stream
    }
    try:
        write_stream_json(**write_stream_kwargs)
    except IOError as e:
        show_traceback = args.debug or args.traceback
        if not show_traceback and e.errno == errno.EPIPE:
            # Ignore broken pipes unless --traceback.
            env.stderr.write('\n')
        else:
            raise


def write_stream(
    stream: BaseStream,
    outfile: Union[IO, TextIO],
    flush: bool
):
    """Write the output stream."""
    try:
        # Writing bytes so we use the buffer interface (Python 3).
        buf = outfile.buffer
    except AttributeError:
        buf = outfile

    for chunk in stream:
        buf.write(chunk)
        if flush:
            outfile.flush()

def write_stream_json(
    stream: BaseStreamJson,
    outfile: Union[IO, TextIO],
    flush: bool
):
    """Write the output stream."""
    try:
        # Writing bytes so we use the buffer interface (Python 3).
        buf = outfile.buffer
    except AttributeError:
        buf = outfile

    for chunk in stream:
        buf.write(chunk)
        if flush:
            outfile.flush()


def write_stream_with_colors_win_py3(
    stream: 'BaseStream',
    outfile: TextIO,
    flush: bool
):
    """Like `write`, but colorized chunks are written as text
    directly to `outfile` to ensure it gets processed by colorama.
    Applies only to Windows with Python 3 and colorized terminal output.

    """
    color = b'\x1b['
    encoding = outfile.encoding
    for chunk in stream:
        if color in chunk:
            outfile.write(chunk.decode(encoding))
        else:
            outfile.buffer.write(chunk)
        if flush:
            outfile.flush()


def build_output_stream_for_message(
    args: argparse.Namespace,
    env: Environment,
    requests_message: Union[requests.PreparedRequest, requests.Response],
    with_headers: bool,
    with_body: bool,
):
    stream_class, stream_kwargs = get_stream_type_and_kwargs(
        env=env,
        args=args,
    )
    message_class = {
        requests.PreparedRequest: HTTPRequest,
        requests.Response: HTTPResponse,
    }[type(requests_message)]
    yield from stream_class(
        msg=message_class(requests_message),
        with_headers=with_headers,
        with_body=with_body,
        **stream_kwargs,
    )
    if (env.stdout_isatty and with_body
            and not getattr(requests_message, 'is_body_upload_chunk', False)):
        # Ensure a blank line after the response body.
        # For terminal output only.
        yield MESSAGE_SEPARATOR_BYTES


def build_output_stream_for_message_json(
    args: argparse.Namespace,
    env: Environment,
    requests_message: [requests.PreparedRequest, requests.Response],
    with_headers_req: bool,
    with_body_req: bool,
    with_headers_res: bool,
    with_body_res: bool,
):
    stream_class, stream_kwargs = get_stream_type_and_kwargs(
        env=env,
        args=args,
    )
    message_class = {
        requests.PreparedRequest: HTTPRequestJson,
        requests.Response: HTTPResponseJson
    }[type(requests_message[0])]
    message_class2 = {
        requests.PreparedRequest: HTTPRequestJson,
        requests.Response: HTTPResponseJson
    }[type(requests_message[1])]

    yield from stream_class(
        msgReq=message_class(requests_message[0]),
        msgRes=message_class2(requests_message[1]),
        with_headers_req=with_headers_req,
        with_body_req=with_body_req,
        with_headers_res=with_headers_res,
        with_body_res=with_body_res,
        **stream_kwargs,
    )

def get_stream_type_and_kwargs(
    env: Environment,
    args: argparse.Namespace
) -> Tuple[Type['BaseStream'], dict]:
    """Pick the right stream type and kwargs for it based on `env` and `args`.

    """
    if not env.stdout_isatty and not args.prettify:
        stream_class = RawStream
        stream_kwargs = {
            'chunk_size': (
                RawStream.CHUNK_SIZE_BY_LINE
                if args.stream
                else RawStream.CHUNK_SIZE
            )
        }
    elif args.output_format_form=="JSON":
        stream_class = BaseStreamJson
        stream_kwargs = {
            'on_body_chunk_downloaded': None
        }
    elif args.prettify:
        stream_class = PrettyStream if args.stream else BufferedPrettyStream
        stream_kwargs = {
            'env': env,
            'conversion': Conversion(),
            'formatting': Formatting(
                env=env,
                groups=args.prettify,
                color_scheme=args.style,
                explicit_json=args.json,
                format_options=args.format_options,
            )
        }
    else:
        stream_class = EncodedStream
        stream_kwargs = {
            'env': env
        }

    return stream_class, stream_kwargs
