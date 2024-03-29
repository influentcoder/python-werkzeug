# -*- coding: utf-8 -*-
"""
    tests.wrappers
    ~~~~~~~~~~~~~~

    Tests for the response and request objects.

    :copyright: 2007 Pallets
    :license: BSD-3-Clause
"""
import contextlib
import json
import os
import pickle
from datetime import datetime
from datetime import timedelta
from io import BytesIO

import pytest

from . import strict_eq
from werkzeug import wrappers
from werkzeug._compat import implements_iterator
from werkzeug._compat import iteritems
from werkzeug._compat import text_type
from werkzeug.datastructures import Accept
from werkzeug.datastructures import CharsetAccept
from werkzeug.datastructures import CombinedMultiDict
from werkzeug.datastructures import Headers
from werkzeug.datastructures import ImmutableList
from werkzeug.datastructures import ImmutableOrderedMultiDict
from werkzeug.datastructures import ImmutableTypeConversionDict
from werkzeug.datastructures import LanguageAccept
from werkzeug.datastructures import MIMEAccept
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import RequestedRangeNotSatisfiable
from werkzeug.exceptions import SecurityError
from werkzeug.http import generate_etag
from werkzeug.test import Client
from werkzeug.test import create_environ
from werkzeug.test import run_wsgi_app
from werkzeug.wrappers.json import JSONMixin
from werkzeug.wsgi import LimitedStream
from werkzeug.wsgi import wrap_file


class RequestTestResponse(wrappers.BaseResponse):
    """Subclass of the normal response class we use to test response
    and base classes.  Has some methods to test if things in the
    response match.
    """

    def __init__(self, response, status, headers):
        wrappers.BaseResponse.__init__(self, response, status, headers)
        self.body_data = pickle.loads(self.get_data())

    def __getitem__(self, key):
        return self.body_data[key]


def request_demo_app(environ, start_response):
    request = wrappers.BaseRequest(environ)
    assert "werkzeug.request" in environ
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [
        pickle.dumps(
            {
                "args": request.args,
                "args_as_list": list(request.args.lists()),
                "form": request.form,
                "form_as_list": list(request.form.lists()),
                "environ": prepare_environ_pickle(request.environ),
                "data": request.get_data(),
            }
        )
    ]


def prepare_environ_pickle(environ):
    result = {}
    for key, value in iteritems(environ):
        try:
            pickle.dumps((key, value))
        except Exception:
            continue
        result[key] = value
    return result


def assert_environ(environ, method):
    strict_eq(environ["REQUEST_METHOD"], method)
    strict_eq(environ["PATH_INFO"], "/")
    strict_eq(environ["SCRIPT_NAME"], "")
    strict_eq(environ["SERVER_NAME"], "localhost")
    strict_eq(environ["wsgi.version"], (1, 0))
    strict_eq(environ["wsgi.url_scheme"], "http")


def test_base_request():
    client = Client(request_demo_app, RequestTestResponse)

    # get requests
    response = client.get("/?foo=bar&foo=hehe")
    strict_eq(response["args"], MultiDict([("foo", u"bar"), ("foo", u"hehe")]))
    strict_eq(response["args_as_list"], [("foo", [u"bar", u"hehe"])])
    strict_eq(response["form"], MultiDict())
    strict_eq(response["form_as_list"], [])
    strict_eq(response["data"], b"")
    assert_environ(response["environ"], "GET")

    # post requests with form data
    response = client.post(
        "/?blub=blah",
        data="foo=blub+hehe&blah=42",
        content_type="application/x-www-form-urlencoded",
    )
    strict_eq(response["args"], MultiDict([("blub", u"blah")]))
    strict_eq(response["args_as_list"], [("blub", [u"blah"])])
    strict_eq(response["form"], MultiDict([("foo", u"blub hehe"), ("blah", u"42")]))
    strict_eq(response["data"], b"")
    # currently we do not guarantee that the values are ordered correctly
    # for post data.
    # strict_eq(response['form_as_list'], [('foo', ['blub hehe']), ('blah', ['42'])])
    assert_environ(response["environ"], "POST")

    # patch requests with form data
    response = client.patch(
        "/?blub=blah",
        data="foo=blub+hehe&blah=42",
        content_type="application/x-www-form-urlencoded",
    )
    strict_eq(response["args"], MultiDict([("blub", u"blah")]))
    strict_eq(response["args_as_list"], [("blub", [u"blah"])])
    strict_eq(response["form"], MultiDict([("foo", u"blub hehe"), ("blah", u"42")]))
    strict_eq(response["data"], b"")
    assert_environ(response["environ"], "PATCH")

    # post requests with json data
    json = b'{"foo": "bar", "blub": "blah"}'
    response = client.post("/?a=b", data=json, content_type="application/json")
    strict_eq(response["data"], json)
    strict_eq(response["args"], MultiDict([("a", u"b")]))
    strict_eq(response["form"], MultiDict())


def test_query_string_is_bytes():
    req = wrappers.Request.from_values(u"/?foo=%2f")
    strict_eq(req.query_string, b"foo=%2f")


def test_request_repr():
    req = wrappers.Request.from_values("/foobar")
    assert "<Request 'http://localhost/foobar' [GET]>" == repr(req)
    # test with non-ascii characters
    req = wrappers.Request.from_values("/привет")
    assert "<Request 'http://localhost/привет' [GET]>" == repr(req)
    # test with unicode type for python 2
    req = wrappers.Request.from_values(u"/привет")
    assert "<Request 'http://localhost/привет' [GET]>" == repr(req)


def test_access_route():
    req = wrappers.Request.from_values(
        headers={"X-Forwarded-For": "192.168.1.2, 192.168.1.1"}
    )
    req.environ["REMOTE_ADDR"] = "192.168.1.3"
    assert req.access_route == ["192.168.1.2", "192.168.1.1"]
    strict_eq(req.remote_addr, "192.168.1.3")

    req = wrappers.Request.from_values()
    req.environ["REMOTE_ADDR"] = "192.168.1.3"
    strict_eq(list(req.access_route), ["192.168.1.3"])


def test_url_request_descriptors():
    req = wrappers.Request.from_values("/bar?foo=baz", "http://example.com/test")
    strict_eq(req.path, u"/bar")
    strict_eq(req.full_path, u"/bar?foo=baz")
    strict_eq(req.script_root, u"/test")
    strict_eq(req.url, u"http://example.com/test/bar?foo=baz")
    strict_eq(req.base_url, u"http://example.com/test/bar")
    strict_eq(req.url_root, u"http://example.com/test/")
    strict_eq(req.host_url, u"http://example.com/")
    strict_eq(req.host, "example.com")
    strict_eq(req.scheme, "http")

    req = wrappers.Request.from_values("/bar?foo=baz", "https://example.com/test")
    strict_eq(req.scheme, "https")


def test_url_request_descriptors_query_quoting():
    next = "http%3A%2F%2Fwww.example.com%2F%3Fnext%3D%2Fbaz%23my%3Dhash"
    req = wrappers.Request.from_values("/bar?next=" + next, "http://example.com/")
    assert req.path == u"/bar"
    strict_eq(req.full_path, u"/bar?next=" + next)
    strict_eq(req.url, u"http://example.com/bar?next=" + next)


def test_url_request_descriptors_hosts():
    req = wrappers.Request.from_values("/bar?foo=baz", "http://example.com/test")
    req.trusted_hosts = ["example.com"]
    strict_eq(req.path, u"/bar")
    strict_eq(req.full_path, u"/bar?foo=baz")
    strict_eq(req.script_root, u"/test")
    strict_eq(req.url, u"http://example.com/test/bar?foo=baz")
    strict_eq(req.base_url, u"http://example.com/test/bar")
    strict_eq(req.url_root, u"http://example.com/test/")
    strict_eq(req.host_url, u"http://example.com/")
    strict_eq(req.host, "example.com")
    strict_eq(req.scheme, "http")

    req = wrappers.Request.from_values("/bar?foo=baz", "https://example.com/test")
    strict_eq(req.scheme, "https")

    req = wrappers.Request.from_values("/bar?foo=baz", "http://example.com/test")
    req.trusted_hosts = ["example.org"]
    pytest.raises(SecurityError, lambda: req.url)
    pytest.raises(SecurityError, lambda: req.base_url)
    pytest.raises(SecurityError, lambda: req.url_root)
    pytest.raises(SecurityError, lambda: req.host_url)
    pytest.raises(SecurityError, lambda: req.host)


def test_authorization_mixin():
    request = wrappers.Request.from_values(
        headers={"Authorization": "Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="}
    )
    a = request.authorization
    strict_eq(a.type, "basic")
    strict_eq(a.username, u"Aladdin")
    strict_eq(a.password, u"open sesame")


def test_authorization_with_unicode():
    request = wrappers.Request.from_values(
        headers={"Authorization": "Basic 0YDRg9GB0YHQutC40IE60JHRg9C60LLRiw=="}
    )
    a = request.authorization
    strict_eq(a.type, "basic")
    strict_eq(a.username, u"русскиЁ")
    strict_eq(a.password, u"Буквы")


def test_stream_only_mixing():
    request = wrappers.PlainRequest.from_values(
        data=b"foo=blub+hehe", content_type="application/x-www-form-urlencoded"
    )
    assert list(request.files.items()) == []
    assert list(request.form.items()) == []
    pytest.raises(AttributeError, lambda: request.data)
    strict_eq(request.stream.read(), b"foo=blub+hehe")


def test_request_application():
    @wrappers.Request.application
    def application(request):
        return wrappers.Response("Hello World!")

    @wrappers.Request.application
    def failing_application(request):
        raise BadRequest()

    resp = wrappers.Response.from_app(application, create_environ())
    assert resp.data == b"Hello World!"
    assert resp.status_code == 200

    resp = wrappers.Response.from_app(failing_application, create_environ())
    assert b"Bad Request" in resp.data
    assert resp.status_code == 400


def test_base_response():
    # unicode
    response = wrappers.BaseResponse(u"öäü")
    strict_eq(response.get_data(), u"öäü".encode("utf-8"))

    # writing
    response = wrappers.Response("foo")
    response.stream.write("bar")
    strict_eq(response.get_data(), b"foobar")

    # set cookie
    response = wrappers.BaseResponse()
    response.set_cookie(
        "foo",
        value="bar",
        max_age=60,
        expires=0,
        path="/blub",
        domain="example.org",
        samesite="Strict",
    )
    strict_eq(
        response.headers.to_wsgi_list(),
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            (
                "Set-Cookie",
                "foo=bar; Domain=example.org; Expires=Thu, "
                "01-Jan-1970 00:00:00 GMT; Max-Age=60; Path=/blub; "
                "SameSite=Strict",
            ),
        ],
    )

    # delete cookie
    response = wrappers.BaseResponse()
    response.delete_cookie("foo")
    strict_eq(
        response.headers.to_wsgi_list(),
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            (
                "Set-Cookie",
                "foo=; Expires=Thu, 01-Jan-1970 00:00:00 GMT; Max-Age=0; Path=/",
            ),
        ],
    )

    # close call forwarding
    closed = []

    @implements_iterator
    class Iterable(object):
        def __next__(self):
            raise StopIteration()

        def __iter__(self):
            return self

        def close(self):
            closed.append(True)

    response = wrappers.BaseResponse(Iterable())
    response.call_on_close(lambda: closed.append(True))
    app_iter, status, headers = run_wsgi_app(response, create_environ(), buffered=True)
    strict_eq(status, "200 OK")
    strict_eq("".join(app_iter), "")
    strict_eq(len(closed), 2)

    # with statement
    del closed[:]
    response = wrappers.BaseResponse(Iterable())
    with response:
        pass
    assert len(closed) == 1


def test_response_status_codes():
    response = wrappers.BaseResponse()
    response.status_code = 404
    strict_eq(response.status, "404 NOT FOUND")
    response.status = "200 OK"
    strict_eq(response.status_code, 200)
    response.status = "999 WTF"
    strict_eq(response.status_code, 999)
    response.status_code = 588
    strict_eq(response.status_code, 588)
    strict_eq(response.status, "588 UNKNOWN")
    response.status = "wtf"
    strict_eq(response.status_code, 0)
    strict_eq(response.status, "0 wtf")

    # invalid status codes
    with pytest.raises(ValueError) as info:
        wrappers.BaseResponse(None, "")

    assert "Empty status argument" in str(info.value)

    with pytest.raises(TypeError) as info:
        wrappers.BaseResponse(None, tuple())

    assert "Invalid status argument" in str(info.value)


def test_type_forcing():
    def wsgi_application(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/html")])
        return ["Hello World!"]

    base_response = wrappers.BaseResponse("Hello World!", content_type="text/html")

    class SpecialResponse(wrappers.Response):
        def foo(self):
            return 42

    # good enough for this simple application, but don't ever use that in
    # real world examples!
    fake_env = {}

    for orig_resp in wsgi_application, base_response:
        response = SpecialResponse.force_type(orig_resp, fake_env)
        assert response.__class__ is SpecialResponse
        strict_eq(response.foo(), 42)
        strict_eq(response.get_data(), b"Hello World!")
        assert response.content_type == "text/html"

    # without env, no arbitrary conversion
    pytest.raises(TypeError, SpecialResponse.force_type, wsgi_application)


def test_accept_mixin():
    request = wrappers.Request(
        {
            "HTTP_ACCEPT": "text/xml,application/xml,application/xhtml+xml,"
            "text/html;q=0.9,text/plain;q=0.8,image/png,*/*;q=0.5",
            "HTTP_ACCEPT_CHARSET": "ISO-8859-1,utf-8;q=0.7,*;q=0.7",
            "HTTP_ACCEPT_ENCODING": "gzip,deflate",
            "HTTP_ACCEPT_LANGUAGE": "en-us,en;q=0.5",
        }
    )
    assert request.accept_mimetypes == MIMEAccept(
        [
            ("text/xml", 1),
            ("image/png", 1),
            ("application/xml", 1),
            ("application/xhtml+xml", 1),
            ("text/html", 0.9),
            ("text/plain", 0.8),
            ("*/*", 0.5),
        ]
    )
    strict_eq(
        request.accept_charsets,
        CharsetAccept([("ISO-8859-1", 1), ("utf-8", 0.7), ("*", 0.7)]),
    )
    strict_eq(request.accept_encodings, Accept([("gzip", 1), ("deflate", 1)]))
    strict_eq(request.accept_languages, LanguageAccept([("en-us", 1), ("en", 0.5)]))

    request = wrappers.Request({"HTTP_ACCEPT": ""})
    strict_eq(request.accept_mimetypes, MIMEAccept())


def test_etag_request_mixin():
    request = wrappers.Request(
        {
            "HTTP_CACHE_CONTROL": "no-store, no-cache",
            "HTTP_IF_MATCH": 'W/"foo", bar, "baz"',
            "HTTP_IF_NONE_MATCH": 'W/"foo", bar, "baz"',
            "HTTP_IF_MODIFIED_SINCE": "Tue, 22 Jan 2008 11:18:44 GMT",
            "HTTP_IF_UNMODIFIED_SINCE": "Tue, 22 Jan 2008 11:18:44 GMT",
        }
    )
    assert request.cache_control.no_store
    assert request.cache_control.no_cache

    for etags in request.if_match, request.if_none_match:
        assert etags("bar")
        assert etags.contains_raw('W/"foo"')
        assert etags.contains_weak("foo")
        assert not etags.contains("foo")

    assert request.if_modified_since == datetime(2008, 1, 22, 11, 18, 44)
    assert request.if_unmodified_since == datetime(2008, 1, 22, 11, 18, 44)


def test_user_agent_mixin():
    user_agents = [
        (
            "Mozilla/5.0 (Macintosh; U; Intel Mac OS X; en-US; rv:1.8.1.11) "
            "Gecko/20071127 Firefox/2.0.0.11",
            "firefox",
            "macos",
            "2.0.0.11",
            "en-US",
        ),
        (
            "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1; de-DE) Opera 8.54",
            "opera",
            "windows",
            "8.54",
            "de-DE",
        ),
        (
            "Mozilla/5.0 (iPhone; U; CPU like Mac OS X; en) AppleWebKit/420 "
            "(KHTML, like Gecko) Version/3.0 Mobile/1A543a Safari/419.3",
            "safari",
            "iphone",
            "3.0",
            "en",
        ),
        (
            "Bot Googlebot/2.1 ( http://www.googlebot.com/bot.html)",
            "google",
            None,
            "2.1",
            None,
        ),
        (
            "Mozilla/5.0 (X11; CrOS armv7l 3701.81.0) AppleWebKit/537.31 "
            "(KHTML, like Gecko) Chrome/26.0.1410.57 Safari/537.31",
            "chrome",
            "chromeos",
            "26.0.1410.57",
            None,
        ),
        (
            "Mozilla/5.0 (Windows NT 6.3; Trident/7.0; .NET4.0E; rv:11.0) like Gecko",
            "msie",
            "windows",
            "11.0",
            None,
        ),
        (
            "Mozilla/5.0 (SymbianOS/9.3; Series60/3.2 NokiaE5-00/101.003; "
            "Profile/MIDP-2.1 Configuration/CLDC-1.1 ) AppleWebKit/533.4 "
            "(KHTML, like Gecko) NokiaBrowser/7.3.1.35 Mobile Safari/533.4 3gpp-gba",
            "safari",
            "symbian",
            "533.4",
            None,
        ),
        (
            "Mozilla/5.0 (X11; OpenBSD amd64; rv:45.0) Gecko/20100101 Firefox/45.0",
            "firefox",
            "openbsd",
            "45.0",
            None,
        ),
        (
            "Mozilla/5.0 (X11; NetBSD amd64; rv:45.0) Gecko/20100101 Firefox/45.0",
            "firefox",
            "netbsd",
            "45.0",
            None,
        ),
        (
            "Mozilla/5.0 (X11; FreeBSD amd64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/48.0.2564.103 Safari/537.36",
            "chrome",
            "freebsd",
            "48.0.2564.103",
            None,
        ),
        (
            "Mozilla/5.0 (X11; FreeBSD amd64; rv:45.0) Gecko/20100101 Firefox/45.0",
            "firefox",
            "freebsd",
            "45.0",
            None,
        ),
        (
            "Mozilla/5.0 (X11; U; NetBSD amd64; en-US; rv:) Gecko/20150921 "
            "SeaMonkey/1.1.18",
            "seamonkey",
            "netbsd",
            "1.1.18",
            "en-US",
        ),
        (
            "Mozilla/5.0 (Windows; U; Windows NT 6.2; WOW64; rv:1.8.0.7) "
            "Gecko/20110321 MultiZilla/4.33.2.6a SeaMonkey/8.6.55",
            "seamonkey",
            "windows",
            "8.6.55",
            None,
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:12.0) Gecko/20120427 Firefox/12.0 "
            "SeaMonkey/2.9",
            "seamonkey",
            "linux",
            "2.9",
            None,
        ),
        (
            "Mozilla/5.0 (compatible; Baiduspider/2.0; "
            "+http://www.baidu.com/search/spider.html)",
            "baidu",
            None,
            "2.0",
            None,
        ),
        (
            "Mozilla/5.0 (X11; SunOS i86pc; rv:38.0) Gecko/20100101 Firefox/38.0",
            "firefox",
            "solaris",
            "38.0",
            None,
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:38.0) Gecko/20100101 Firefox/38.0 "
            "Iceweasel/38.7.1",
            "firefox",
            "linux",
            "38.0",
            None,
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/50.0.2661.75 Safari/537.36",
            "chrome",
            "windows",
            "50.0.2661.75",
            None,
        ),
        (
            "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
            "bing",
            None,
            "2.0",
            None,
        ),
        (
            "Mozilla/5.0 (X11; DragonFly x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/47.0.2526.106 Safari/537.36",
            "chrome",
            "dragonflybsd",
            "47.0.2526.106",
            None,
        ),
        (
            "Mozilla/5.0 (X11; U; DragonFly i386; de; rv:1.9.1) "
            "Gecko/20090720 Firefox/3.5.1",
            "firefox",
            "dragonflybsd",
            "3.5.1",
            "de",
        ),
    ]
    for ua, browser, platform, version, lang in user_agents:
        request = wrappers.Request({"HTTP_USER_AGENT": ua})
        strict_eq(request.user_agent.browser, browser)
        strict_eq(request.user_agent.platform, platform)
        strict_eq(request.user_agent.version, version)
        strict_eq(request.user_agent.language, lang)
        assert bool(request.user_agent)
        strict_eq(request.user_agent.to_header(), ua)
        strict_eq(str(request.user_agent), ua)

    request = wrappers.Request({"HTTP_USER_AGENT": "foo"})
    assert not request.user_agent


def test_stream_wrapping():
    class LowercasingStream(object):
        def __init__(self, stream):
            self._stream = stream

        def read(self, size=-1):
            return self._stream.read(size).lower()

        def readline(self, size=-1):
            return self._stream.readline(size).lower()

    data = b"foo=Hello+World"
    req = wrappers.Request.from_values(
        "/", method="POST", data=data, content_type="application/x-www-form-urlencoded"
    )
    req.stream = LowercasingStream(req.stream)
    assert req.form["foo"] == "hello world"


def test_data_descriptor_triggers_parsing():
    data = b"foo=Hello+World"
    req = wrappers.Request.from_values(
        "/", method="POST", data=data, content_type="application/x-www-form-urlencoded"
    )

    assert req.data == b""
    assert req.form["foo"] == u"Hello World"


def test_get_data_method_parsing_caching_behavior():
    data = b"foo=Hello+World"
    req = wrappers.Request.from_values(
        "/", method="POST", data=data, content_type="application/x-www-form-urlencoded"
    )

    # get_data() caches, so form stays available
    assert req.get_data() == data
    assert req.form["foo"] == u"Hello World"
    assert req.get_data() == data

    # here we access the form data first, caching is bypassed
    req = wrappers.Request.from_values(
        "/", method="POST", data=data, content_type="application/x-www-form-urlencoded"
    )
    assert req.form["foo"] == u"Hello World"
    assert req.get_data() == b""

    # Another case is uncached get data which trashes everything
    req = wrappers.Request.from_values(
        "/", method="POST", data=data, content_type="application/x-www-form-urlencoded"
    )
    assert req.get_data(cache=False) == data
    assert req.get_data(cache=False) == b""
    assert req.form == {}

    # Or we can implicitly start the form parser which is similar to
    # the old .data behavior
    req = wrappers.Request.from_values(
        "/", method="POST", data=data, content_type="application/x-www-form-urlencoded"
    )
    assert req.get_data(parse_form_data=True) == b""
    assert req.form["foo"] == u"Hello World"


def test_etag_response_mixin():
    response = wrappers.Response("Hello World")
    assert response.get_etag() == (None, None)
    response.add_etag()
    assert response.get_etag() == ("b10a8db164e0754105b7a99be72e3fe5", False)
    assert not response.cache_control
    response.cache_control.must_revalidate = True
    response.cache_control.max_age = 60
    response.headers["Content-Length"] = len(response.get_data())
    assert response.headers["Cache-Control"] in (
        "must-revalidate, max-age=60",
        "max-age=60, must-revalidate",
    )

    assert "date" not in response.headers
    env = create_environ()
    env.update({"REQUEST_METHOD": "GET", "HTTP_IF_NONE_MATCH": response.get_etag()[0]})
    response.make_conditional(env)
    assert "date" in response.headers

    # after the thing is invoked by the server as wsgi application
    # (we're emulating this here), there must not be any entity
    # headers left and the status code would have to be 304
    resp = wrappers.Response.from_app(response, env)
    assert resp.status_code == 304
    assert "content-length" not in resp.headers

    # make sure date is not overriden
    response = wrappers.Response("Hello World")
    response.date = 1337
    d = response.date
    response.make_conditional(env)
    assert response.date == d

    # make sure content length is only set if missing
    response = wrappers.Response("Hello World")
    response.content_length = 999
    response.make_conditional(env)
    assert response.content_length == 999


def test_etag_response_412():
    response = wrappers.Response("Hello World")
    assert response.get_etag() == (None, None)
    response.add_etag()
    assert response.get_etag() == ("b10a8db164e0754105b7a99be72e3fe5", False)
    assert not response.cache_control
    response.cache_control.must_revalidate = True
    response.cache_control.max_age = 60
    response.headers["Content-Length"] = len(response.get_data())
    assert response.headers["Cache-Control"] in (
        "must-revalidate, max-age=60",
        "max-age=60, must-revalidate",
    )

    assert "date" not in response.headers
    env = create_environ()
    env.update(
        {"REQUEST_METHOD": "GET", "HTTP_IF_MATCH": response.get_etag()[0] + "xyz"}
    )
    response.make_conditional(env)
    assert "date" in response.headers

    # after the thing is invoked by the server as wsgi application
    # (we're emulating this here), there must not be any entity
    # headers left and the status code would have to be 412
    resp = wrappers.Response.from_app(response, env)
    assert resp.status_code == 412
    # Make sure there is a body still
    assert resp.data != b""

    # make sure date is not overriden
    response = wrappers.Response("Hello World")
    response.date = 1337
    d = response.date
    response.make_conditional(env)
    assert response.date == d

    # make sure content length is only set if missing
    response = wrappers.Response("Hello World")
    response.content_length = 999
    response.make_conditional(env)
    assert response.content_length == 999


def test_range_request_basic():
    env = create_environ()
    response = wrappers.Response("Hello World")
    env["HTTP_RANGE"] = "bytes=0-4"
    response.make_conditional(env, accept_ranges=True, complete_length=11)
    assert response.status_code == 206
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["Content-Range"] == "bytes 0-4/11"
    assert response.headers["Content-Length"] == "5"
    assert response.data == b"Hello"


def test_range_request_out_of_bound():
    env = create_environ()
    response = wrappers.Response("Hello World")
    env["HTTP_RANGE"] = "bytes=6-666"
    response.make_conditional(env, accept_ranges=True, complete_length=11)
    assert response.status_code == 206
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["Content-Range"] == "bytes 6-10/11"
    assert response.headers["Content-Length"] == "5"
    assert response.data == b"World"


def test_range_request_with_file():
    env = create_environ()
    resources = os.path.join(os.path.dirname(__file__), "res")
    fname = os.path.join(resources, "test.txt")
    with open(fname, "rb") as f:
        fcontent = f.read()
    with open(fname, "rb") as f:
        response = wrappers.Response(wrap_file(env, f))
        env["HTTP_RANGE"] = "bytes=0-0"
        response.make_conditional(
            env, accept_ranges=True, complete_length=len(fcontent)
        )
        assert response.status_code == 206
        assert response.headers["Accept-Ranges"] == "bytes"
        assert response.headers["Content-Range"] == "bytes 0-0/%d" % len(fcontent)
        assert response.headers["Content-Length"] == "1"
        assert response.data == fcontent[:1]


def test_range_request_with_complete_file():
    env = create_environ()
    resources = os.path.join(os.path.dirname(__file__), "res")
    fname = os.path.join(resources, "test.txt")
    with open(fname, "rb") as f:
        fcontent = f.read()
    with open(fname, "rb") as f:
        fsize = os.path.getsize(fname)
        response = wrappers.Response(wrap_file(env, f))
        env["HTTP_RANGE"] = "bytes=0-%d" % (fsize - 1)
        response.make_conditional(env, accept_ranges=True, complete_length=fsize)
        assert response.status_code == 200
        assert response.headers["Accept-Ranges"] == "bytes"
        assert "Content-Range" not in response.headers
        assert response.headers["Content-Length"] == str(fsize)
        assert response.data == fcontent


def test_range_request_without_complete_length():
    env = create_environ()
    response = wrappers.Response("Hello World")
    env["HTTP_RANGE"] = "bytes=-"
    response.make_conditional(env, accept_ranges=True, complete_length=None)
    assert response.status_code == 200
    assert response.data == b"Hello World"


def test_invalid_range_request():
    env = create_environ()
    response = wrappers.Response("Hello World")
    env["HTTP_RANGE"] = "bytes=-"
    with pytest.raises(RequestedRangeNotSatisfiable):
        response.make_conditional(env, accept_ranges=True, complete_length=11)


def test_etag_response_mixin_freezing():
    class WithFreeze(wrappers.ETagResponseMixin, wrappers.BaseResponse):
        pass

    class WithoutFreeze(wrappers.BaseResponse, wrappers.ETagResponseMixin):
        pass

    response = WithFreeze("Hello World")
    response.freeze()
    strict_eq(response.get_etag(), (text_type(generate_etag(b"Hello World")), False))
    response = WithoutFreeze("Hello World")
    response.freeze()
    assert response.get_etag() == (None, None)
    response = wrappers.Response("Hello World")
    response.freeze()
    assert response.get_etag() == (None, None)


def test_authenticate_mixin():
    resp = wrappers.Response()
    resp.www_authenticate.type = "basic"
    resp.www_authenticate.realm = "Testing"
    strict_eq(resp.headers["WWW-Authenticate"], u'Basic realm="Testing"')
    resp.www_authenticate.realm = None
    resp.www_authenticate.type = None
    assert "WWW-Authenticate" not in resp.headers


def test_authenticate_mixin_quoted_qop():
    # Example taken from https://github.com/pallets/werkzeug/issues/633
    resp = wrappers.Response()
    resp.www_authenticate.set_digest("REALM", "NONCE", qop=("auth", "auth-int"))

    actual = set((resp.headers["WWW-Authenticate"] + ",").split())
    expected = set('Digest nonce="NONCE", realm="REALM", qop="auth, auth-int",'.split())
    assert actual == expected

    resp.www_authenticate.set_digest("REALM", "NONCE", qop=("auth",))

    actual = set((resp.headers["WWW-Authenticate"] + ",").split())
    expected = set('Digest nonce="NONCE", realm="REALM", qop="auth",'.split())
    assert actual == expected


def test_response_stream_mixin():
    response = wrappers.Response()
    response.stream.write("Hello ")
    response.stream.write("World!")
    assert response.response == ["Hello ", "World!"]
    assert response.get_data() == b"Hello World!"


def test_common_response_descriptors_mixin():
    response = wrappers.Response()
    response.mimetype = "text/html"
    assert response.mimetype == "text/html"
    assert response.content_type == "text/html; charset=utf-8"
    assert response.mimetype_params == {"charset": "utf-8"}
    response.mimetype_params["x-foo"] = "yep"
    del response.mimetype_params["charset"]
    assert response.content_type == "text/html; x-foo=yep"

    now = datetime.utcnow().replace(microsecond=0)

    assert response.content_length is None
    response.content_length = "42"
    assert response.content_length == 42

    for attr in "date", "expires":
        assert getattr(response, attr) is None
        setattr(response, attr, now)
        assert getattr(response, attr) == now

    assert response.age is None
    age_td = timedelta(days=1, minutes=3, seconds=5)
    response.age = age_td
    assert response.age == age_td
    response.age = 42
    assert response.age == timedelta(seconds=42)

    assert response.retry_after is None
    response.retry_after = now
    assert response.retry_after == now

    assert not response.vary
    response.vary.add("Cookie")
    response.vary.add("Content-Language")
    assert "cookie" in response.vary
    assert response.vary.to_header() == "Cookie, Content-Language"
    response.headers["Vary"] = "Content-Encoding"
    assert response.vary.as_set() == {"content-encoding"}

    response.allow.update(["GET", "POST"])
    assert response.headers["Allow"] == "GET, POST"

    response.content_language.add("en-US")
    response.content_language.add("fr")
    assert response.headers["Content-Language"] == "en-US, fr"


def test_common_request_descriptors_mixin():
    request = wrappers.Request.from_values(
        content_type="text/html; charset=utf-8",
        content_length="23",
        headers={
            "Referer": "http://www.example.com/",
            "Date": "Sat, 28 Feb 2009 19:04:35 GMT",
            "Max-Forwards": "10",
            "Pragma": "no-cache",
            "Content-Encoding": "gzip",
            "Content-MD5": "9a3bc6dbc47a70db25b84c6e5867a072",
        },
    )

    assert request.content_type == "text/html; charset=utf-8"
    assert request.mimetype == "text/html"
    assert request.mimetype_params == {"charset": "utf-8"}
    assert request.content_length == 23
    assert request.referrer == "http://www.example.com/"
    assert request.date == datetime(2009, 2, 28, 19, 4, 35)
    assert request.max_forwards == 10
    assert "no-cache" in request.pragma
    assert request.content_encoding == "gzip"
    assert request.content_md5 == "9a3bc6dbc47a70db25b84c6e5867a072"


def test_request_mimetype_always_lowercase():
    request = wrappers.Request.from_values(content_type="APPLICATION/JSON")
    assert request.mimetype == "application/json"


def test_shallow_mode():
    request = wrappers.Request({"QUERY_STRING": "foo=bar"}, shallow=True)
    assert request.args["foo"] == "bar"
    pytest.raises(RuntimeError, lambda: request.form["foo"])


def test_form_parsing_failed():
    data = b"--blah\r\n"
    request = wrappers.Request.from_values(
        input_stream=BytesIO(data),
        content_length=len(data),
        content_type="multipart/form-data; boundary=foo",
        method="POST",
    )
    assert not request.files
    assert not request.form

    # Bad Content-Type
    data = b"test"
    request = wrappers.Request.from_values(
        input_stream=BytesIO(data),
        content_length=len(data),
        content_type=", ",
        method="POST",
    )
    assert not request.form


def test_file_closing():
    data = (
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="foo"; filename="foo.txt"\r\n'
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"file contents, just the contents\r\n"
        b"--foo--"
    )
    req = wrappers.Request.from_values(
        input_stream=BytesIO(data),
        content_length=len(data),
        content_type="multipart/form-data; boundary=foo",
        method="POST",
    )
    foo = req.files["foo"]
    assert foo.mimetype == "text/plain"
    assert foo.filename == "foo.txt"

    assert foo.closed is False
    req.close()
    assert foo.closed is True


def test_file_closing_with():
    data = (
        b"--foo\r\n"
        b'Content-Disposition: form-data; name="foo"; filename="foo.txt"\r\n'
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"file contents, just the contents\r\n"
        b"--foo--"
    )
    req = wrappers.Request.from_values(
        input_stream=BytesIO(data),
        content_length=len(data),
        content_type="multipart/form-data; boundary=foo",
        method="POST",
    )
    with req:
        foo = req.files["foo"]
        assert foo.mimetype == "text/plain"
        assert foo.filename == "foo.txt"

    assert foo.closed is True


def test_url_charset_reflection():
    req = wrappers.Request.from_values()
    req.charset = "utf-7"
    assert req.url_charset == "utf-7"


def test_response_streamed():
    r = wrappers.Response()
    assert not r.is_streamed
    r = wrappers.Response("Hello World")
    assert not r.is_streamed
    r = wrappers.Response(["foo", "bar"])
    assert not r.is_streamed

    def gen():
        if 0:
            yield None

    r = wrappers.Response(gen())
    assert r.is_streamed


def test_response_iter_wrapping():
    def uppercasing(iterator):
        for item in iterator:
            yield item.upper()

    def generator():
        yield "foo"
        yield "bar"

    req = wrappers.Request.from_values()
    resp = wrappers.Response(generator())
    del resp.headers["Content-Length"]
    resp.response = uppercasing(resp.iter_encoded())
    actual_resp = wrappers.Response.from_app(resp, req.environ, buffered=True)
    assert actual_resp.get_data() == b"FOOBAR"


def test_response_freeze():
    def generate():
        yield "foo"
        yield "bar"

    resp = wrappers.Response(generate())
    resp.freeze()
    assert resp.response == [b"foo", b"bar"]
    assert resp.headers["content-length"] == "6"


def test_response_content_length_uses_encode():
    r = wrappers.Response(u"你好")
    assert r.calculate_content_length() == 6


def test_other_method_payload():
    data = b"Hello World"
    req = wrappers.Request.from_values(
        input_stream=BytesIO(data),
        content_length=len(data),
        content_type="text/plain",
        method="WHAT_THE_FUCK",
    )
    assert req.get_data() == data
    assert isinstance(req.stream, LimitedStream)


def test_urlfication():
    resp = wrappers.Response()
    resp.headers["Location"] = u"http://üser:pässword@☃.net/påth"
    resp.headers["Content-Location"] = u"http://☃.net/"
    headers = resp.get_wsgi_headers(create_environ())
    assert headers["location"] == "http://%C3%BCser:p%C3%A4ssword@xn--n3h.net/p%C3%A5th"
    assert headers["content-location"] == "http://xn--n3h.net/"


def test_new_response_iterator_behavior():
    req = wrappers.Request.from_values()
    resp = wrappers.Response(u"Hello Wörld!")

    def get_content_length(resp):
        headers = resp.get_wsgi_headers(req.environ)
        return headers.get("content-length", type=int)

    def generate_items():
        yield "Hello "
        yield u"Wörld!"

    # werkzeug encodes when set to `data` now, which happens
    # if a string is passed to the response object.
    assert resp.response == [u"Hello Wörld!".encode("utf-8")]
    assert resp.get_data() == u"Hello Wörld!".encode("utf-8")
    assert get_content_length(resp) == 13
    assert not resp.is_streamed
    assert resp.is_sequence

    # try the same for manual assignment
    resp.set_data(u"Wörd")
    assert resp.response == [u"Wörd".encode("utf-8")]
    assert resp.get_data() == u"Wörd".encode("utf-8")
    assert get_content_length(resp) == 5
    assert not resp.is_streamed
    assert resp.is_sequence

    # automatic generator sequence conversion
    resp.response = generate_items()
    assert resp.is_streamed
    assert not resp.is_sequence
    assert resp.get_data() == u"Hello Wörld!".encode("utf-8")
    assert resp.response == [b"Hello ", u"Wörld!".encode("utf-8")]
    assert not resp.is_streamed
    assert resp.is_sequence

    # automatic generator sequence conversion
    resp.response = generate_items()
    resp.implicit_sequence_conversion = False
    assert resp.is_streamed
    assert not resp.is_sequence
    pytest.raises(RuntimeError, lambda: resp.get_data())
    resp.make_sequence()
    assert resp.get_data() == u"Hello Wörld!".encode("utf-8")
    assert resp.response == [b"Hello ", u"Wörld!".encode("utf-8")]
    assert not resp.is_streamed
    assert resp.is_sequence

    # stream makes it a list no matter how the conversion is set
    for val in True, False:
        resp.implicit_sequence_conversion = val
        resp.response = ("foo", "bar")
        assert resp.is_sequence
        resp.stream.write("baz")
        assert resp.response == ["foo", "bar", "baz"]


def test_form_data_ordering():
    class MyRequest(wrappers.Request):
        parameter_storage_class = ImmutableOrderedMultiDict

    req = MyRequest.from_values("/?foo=1&bar=0&foo=3")
    assert list(req.args) == ["foo", "bar"]
    assert list(req.args.items(multi=True)) == [
        ("foo", "1"),
        ("bar", "0"),
        ("foo", "3"),
    ]
    assert isinstance(req.args, ImmutableOrderedMultiDict)
    assert isinstance(req.values, CombinedMultiDict)
    assert req.values["foo"] == "1"
    assert req.values.getlist("foo") == ["1", "3"]


def test_storage_classes():
    class MyRequest(wrappers.Request):
        dict_storage_class = dict
        list_storage_class = list
        parameter_storage_class = dict

    req = MyRequest.from_values("/?foo=baz", headers={"Cookie": "foo=bar"})
    assert type(req.cookies) is dict
    assert req.cookies == {"foo": "bar"}
    assert type(req.access_route) is list

    assert type(req.args) is dict
    assert type(req.values) is CombinedMultiDict
    assert req.values["foo"] == u"baz"

    req = wrappers.Request.from_values(headers={"Cookie": "foo=bar"})
    assert type(req.cookies) is ImmutableTypeConversionDict
    assert req.cookies == {"foo": "bar"}
    assert type(req.access_route) is ImmutableList

    MyRequest.list_storage_class = tuple
    req = MyRequest.from_values()
    assert type(req.access_route) is tuple


def test_response_headers_passthrough():
    headers = Headers()
    resp = wrappers.Response(headers=headers)
    assert resp.headers is headers


def test_response_304_no_content_length():
    resp = wrappers.Response("Test", status=304)
    env = create_environ()
    assert "content-length" not in resp.get_wsgi_headers(env)


def test_ranges():
    # basic range stuff
    req = wrappers.Request.from_values()
    assert req.range is None
    req = wrappers.Request.from_values(headers={"Range": "bytes=0-499"})
    assert req.range.ranges == [(0, 500)]

    resp = wrappers.Response()
    resp.content_range = req.range.make_content_range(1000)
    assert resp.content_range.units == "bytes"
    assert resp.content_range.start == 0
    assert resp.content_range.stop == 500
    assert resp.content_range.length == 1000
    assert resp.headers["Content-Range"] == "bytes 0-499/1000"

    resp.content_range.unset()
    assert "Content-Range" not in resp.headers

    resp.headers["Content-Range"] = "bytes 0-499/1000"
    assert resp.content_range.units == "bytes"
    assert resp.content_range.start == 0
    assert resp.content_range.stop == 500
    assert resp.content_range.length == 1000


def test_auto_content_length():
    resp = wrappers.Response("Hello World!")
    assert resp.content_length == 12

    resp = wrappers.Response(["Hello World!"])
    assert resp.content_length is None
    assert resp.get_wsgi_headers({})["Content-Length"] == "12"


def test_stream_content_length():
    resp = wrappers.Response()
    resp.stream.writelines(["foo", "bar", "baz"])
    assert resp.get_wsgi_headers({})["Content-Length"] == "9"

    resp = wrappers.Response()
    resp.make_conditional({"REQUEST_METHOD": "GET"})
    resp.stream.writelines(["foo", "bar", "baz"])
    assert resp.get_wsgi_headers({})["Content-Length"] == "9"

    resp = wrappers.Response("foo")
    resp.stream.writelines(["bar", "baz"])
    assert resp.get_wsgi_headers({})["Content-Length"] == "9"


def test_disabled_auto_content_length():
    class MyResponse(wrappers.Response):
        automatically_set_content_length = False

    resp = MyResponse("Hello World!")
    assert resp.content_length is None

    resp = MyResponse(["Hello World!"])
    assert resp.content_length is None
    assert "Content-Length" not in resp.get_wsgi_headers({})

    resp = MyResponse()
    resp.make_conditional({"REQUEST_METHOD": "GET"})
    assert resp.content_length is None
    assert "Content-Length" not in resp.get_wsgi_headers({})


@pytest.mark.parametrize(
    ("auto", "location", "expect"),
    (
        (False, "/test", "/test"),
        (True, "/test", "http://localhost/test"),
        (True, "test", "http://localhost/a/b/test"),
        (True, "./test", "http://localhost/a/b/test"),
        (True, "../test", "http://localhost/a/test"),
    ),
)
def test_location_header_autocorrect(monkeypatch, auto, location, expect):
    monkeypatch.setattr(wrappers.Response, "autocorrect_location_header", auto)
    env = create_environ("/a/b/c")
    resp = wrappers.Response("Hello World!")
    resp.headers["Location"] = location
    assert resp.get_wsgi_headers(env)["Location"] == expect


def test_204_and_1XX_response_has_no_content_length():
    response = wrappers.Response(status=204)
    assert response.content_length is None

    headers = response.get_wsgi_headers(create_environ())
    assert "Content-Length" not in headers

    response = wrappers.Response(status=100)
    assert response.content_length is None

    headers = response.get_wsgi_headers(create_environ())
    assert "Content-Length" not in headers


def test_malformed_204_response_has_no_content_length():
    # flask-restful can generate a malformed response when doing `return '', 204`
    response = wrappers.Response(status=204)
    response.set_data(b"test")
    assert response.content_length == 4

    env = create_environ()
    app_iter, status, headers = response.get_wsgi_response(env)
    assert status == "204 NO CONTENT"
    assert "Content-Length" not in headers
    assert b"".join(app_iter) == b""  # ensure data will not be sent


def test_modified_url_encoding():
    class ModifiedRequest(wrappers.Request):
        url_charset = "euc-kr"

    req = ModifiedRequest.from_values(u"/?foo=정상처리".encode("euc-kr"))
    strict_eq(req.args["foo"], u"정상처리")


def test_request_method_case_sensitivity():
    req = wrappers.Request({"REQUEST_METHOD": "get"})
    assert req.method == "GET"


def test_is_xhr_warning():
    req = wrappers.Request.from_values()

    with pytest.warns(DeprecationWarning):
        req.is_xhr


def test_write_length():
    response = wrappers.Response()
    length = response.stream.write(b"bar")
    assert length == 3


def test_stream_zip():
    import zipfile

    response = wrappers.Response()
    with contextlib.closing(zipfile.ZipFile(response.stream, mode="w")) as z:
        z.writestr("foo", b"bar")

    buffer = BytesIO(response.get_data())
    with contextlib.closing(zipfile.ZipFile(buffer, mode="r")) as z:
        assert z.namelist() == ["foo"]
        assert z.read("foo") == b"bar"


class TestSetCookie(object):
    """Tests for :meth:`werkzeug.wrappers.BaseResponse.set_cookie`."""

    def test_secure(self):
        response = wrappers.BaseResponse()
        response.set_cookie(
            "foo",
            value="bar",
            max_age=60,
            expires=0,
            path="/blub",
            domain="example.org",
            secure=True,
            samesite=None,
        )
        strict_eq(
            response.headers.to_wsgi_list(),
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                (
                    "Set-Cookie",
                    "foo=bar; Domain=example.org; Expires=Thu, "
                    "01-Jan-1970 00:00:00 GMT; Max-Age=60; Secure; Path=/blub",
                ),
            ],
        )

    def test_httponly(self):
        response = wrappers.BaseResponse()
        response.set_cookie(
            "foo",
            value="bar",
            max_age=60,
            expires=0,
            path="/blub",
            domain="example.org",
            secure=False,
            httponly=True,
            samesite=None,
        )
        strict_eq(
            response.headers.to_wsgi_list(),
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                (
                    "Set-Cookie",
                    "foo=bar; Domain=example.org; Expires=Thu, "
                    "01-Jan-1970 00:00:00 GMT; Max-Age=60; HttpOnly; Path=/blub",
                ),
            ],
        )

    def test_secure_and_httponly(self):
        response = wrappers.BaseResponse()
        response.set_cookie(
            "foo",
            value="bar",
            max_age=60,
            expires=0,
            path="/blub",
            domain="example.org",
            secure=True,
            httponly=True,
            samesite=None,
        )
        strict_eq(
            response.headers.to_wsgi_list(),
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                (
                    "Set-Cookie",
                    "foo=bar; Domain=example.org; Expires=Thu, "
                    "01-Jan-1970 00:00:00 GMT; Max-Age=60; Secure; HttpOnly; "
                    "Path=/blub",
                ),
            ],
        )

    def test_samesite(self):
        response = wrappers.BaseResponse()
        response.set_cookie(
            "foo",
            value="bar",
            max_age=60,
            expires=0,
            path="/blub",
            domain="example.org",
            secure=False,
            samesite="strict",
        )
        strict_eq(
            response.headers.to_wsgi_list(),
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                (
                    "Set-Cookie",
                    "foo=bar; Domain=example.org; Expires=Thu, "
                    "01-Jan-1970 00:00:00 GMT; Max-Age=60; Path=/blub; "
                    "SameSite=Strict",
                ),
            ],
        )


class TestJSONMixin(object):
    class Request(JSONMixin, wrappers.Request):
        pass

    class Response(JSONMixin, wrappers.Response):
        pass

    def test_request(self):
        value = {u"ä": "b"}
        request = self.Request.from_values(json=value)
        assert request.json == value
        assert request.get_data()

    def test_response(self):
        value = {u"ä": "b"}
        response = self.Response(
            response=json.dumps(value), content_type="application/json"
        )
        assert response.json == value

    def test_force(self):
        value = [1, 2, 3]
        request = self.Request.from_values(json=value, content_type="text/plain")
        assert request.json is None
        assert request.get_json(force=True) == value

    def test_silent(self):
        request = self.Request.from_values(
            data=b'{"a":}', content_type="application/json"
        )
        assert request.get_json(silent=True) is None

        with pytest.raises(BadRequest):
            request.get_json()

    def test_cache_disabled(self):
        value = [1, 2, 3]
        request = self.Request.from_values(json=value)
        assert request.get_json(cache=False) == [1, 2, 3]
        assert not request.get_data()

        with pytest.raises(BadRequest):
            request.get_json()
