"""The import handler must read the upload only after auth and rate limits.

With an `UploadFile = File(...)` parameter FastAPI reads the whole body before
any dependency runs, so an anonymous caller could stream unlimited data before
the 401. Status-code tests can't see that, so the shape is pinned here."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes import portfolio as route  # noqa: E402


def test_handler_takes_the_raw_request_not_a_file_param():
    params = list(inspect.signature(route.portfolio_import_file).parameters)
    assert params == ["request"], params


def test_body_is_byte_capped_and_parsed_off_the_event_loop():
    src = inspect.getsource(route.portfolio_import_file)
    assert "_byte_capped(request.receive" in src
    assert "form(max_files=1, max_fields=1)" in src
    assert "asyncio.to_thread(pimport.parse_holdings_file" in src
