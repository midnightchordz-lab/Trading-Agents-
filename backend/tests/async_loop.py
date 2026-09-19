"""One event loop shared by every test module that awaits backend code.

Motor hands each operation to whichever loop is current at call time, and
`asyncio.run` closes its loop on the way out — so a second `asyncio.run`
anywhere in the same worker process finds a dead loop and the call fails with
"Event loop is closed". Importing this module's `run_async` everywhere keeps
one open loop for the whole process.
"""
import asyncio

LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def run_async(coro):
    return LOOP.run_until_complete(coro)
