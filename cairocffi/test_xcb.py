"""

    cairocffi.test_xcb
    ~~~~~~~~~~~~~~~~~~

    Test suite for cairocffi.xcb.

    :copyright: Copyright 2014-2019 by Simon Sapin
    :license: BSD, see LICENSE for details.

"""

import os
import time

import pytest

xcffib = pytest.importorskip('xcffib')  # noqa
import xcffib.xproto
from xcffib.xproto import ConfigWindow, CW, EventMask, GC

from . import Context, XCBSurface, cairo_version


@pytest.fixture
def xcb_conn():
    """
    Fixture that will setup and take down a xcffib.Connection object running on
    a display spawned by xvfb
    """
    display = os.environ.get('DISPLAY')
    if display is None:  # pragma: no cover
        pytest.skip('DISPLAY environment variable not set')

    conn = xcffib.connect(display)
    yield conn
    conn.disconnect()


def find_root_visual(conn):
    """Find the xcffib.xproto.VISUALTYPE corresponding to the root visual"""
    default_screen = conn.setup.roots[conn.pref_screen]
    for i in default_screen.allowed_depths:
        for v in i.visuals:
            if v.visual_id == default_screen.root_visual:
                return v


def create_window(conn, width, height):
    """Creates a window of the given dimensions and returns the XID"""
    wid = conn.generate_id()
    default_screen = conn.setup.roots[conn.pref_screen]

    conn.core.CreateWindow(
        default_screen.root_depth,  # depth
        wid,                        # id
        default_screen.root,        # parent
        0, 0, width, height, 0,     # x, y, w, h, border width
        xcffib.xproto.WindowClass.InputOutput,  # window class
        default_screen.root_visual,             # visual
        CW.BackPixel | CW.EventMask,            # value mask
        [                                       # value list
            default_screen.black_pixel,
            EventMask.Exposure | EventMask.StructureNotify
        ]
    )

    return wid


def create_pixmap(conn, wid, width, height):
    """Creates a window of the given dimensions and returns the XID"""
    pixmap = conn.generate_id()
    default_screen = conn.setup.roots[conn.pref_screen]

    conn.core.CreatePixmap(
        default_screen.root_depth,  # depth
        pixmap, wid,                # pixmap id, drawable id (window)
        width, height
    )

    return pixmap


def create_gc(conn):
    """Creates a simple graphics context"""
    gc = conn.generate_id()
    default_screen = conn.setup.roots[conn.pref_screen]

    conn.core.CreateGC(
        gc, default_screen.root,        # gc id, drawable
        GC.Foreground | GC.Background,  # value mask
        [                               # value list
            default_screen.black_pixel,
            default_screen.white_pixel
        ]
    )

    return gc


@pytest.mark.xfail(cairo_version() < 11200,
                   reason="Cairo version too low")
def test_xcb_pixmap(xcb_conn):
    width = 10
    height = 10

    # create a new window
    wid = create_window(xcb_conn, width, height)
    # create the pixmap used to draw with cairo
    pixmap = create_pixmap(xcb_conn, wid, width, height)
    # create graphics context to copy pixmap on window
    gc = create_gc(xcb_conn)

    # create XCB surface on pixmap
    root_visual = find_root_visual(xcb_conn)
    surface = XCBSurface(xcb_conn, pixmap, root_visual, width, height)
    assert surface

    # use xcb surface to create context, draw white
    ctx = Context(surface)
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()

    # map the window and wait for it to appear
    xcb_conn.core.MapWindow(wid)
    xcb_conn.flush()

    start = time.time()
    while time.time() < start + 10:
        event = xcb_conn.wait_for_event()
        if isinstance(event, xcffib.xproto.ExposeEvent):
            break
    else:
        pytest.fail("Never received ExposeEvent")

    # copy the pixmap to the window
    xcb_conn.core.CopyArea(
        pixmap,  # source
        wid,     # dest
        gc,      # gc
        0, 0,    # source x, source y
        0, 0,    # dest x, dest y
        width, height
    )

    ctx = None
    surface = None
    xcb_conn.core.FreeGC(gc)
    xcb_conn.core.FreePixmap(pixmap)

    # flush the connection, make sure no errors were thrown
    xcb_conn.flush()
    while event:
        event = xcb_conn.poll_for_event()


@pytest.mark.xfail(cairo_version() < 11200,
                   reason="Cairo version too low")
def test_xcb_window(xcb_conn):
    width = 10
    height = 10

    # create a new window used to draw with cairo
    wid = create_window(xcb_conn, width, height)

    # map the window and wait for it to appear
    xcb_conn.core.MapWindow(wid)
    xcb_conn.flush()

    start = time.time()
    while time.time() < start + 10:
        event = xcb_conn.wait_for_event()
        if isinstance(event, xcffib.xproto.ExposeEvent):
            break
    else:
        pytest.fail("Never received ExposeEvent")

    # create XCB surface on window
    root_visual = find_root_visual(xcb_conn)
    surface = XCBSurface(xcb_conn, wid, root_visual, width, height)
    assert surface

    # use xcb surface to create context, draw white
    ctx = Context(surface)
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()

    xcb_conn.flush()

    # now move the window and change its size
    xcb_conn.core.ConfigureWindow(
        wid,
        (ConfigWindow.X | ConfigWindow.Y
            | ConfigWindow.Width | ConfigWindow.Height),
        [
            5, 5,                   # x, y
            width * 2, height * 2   # width, height
        ]
    )
    xcb_conn.flush()

    # wait for the notification of the size change
    start = time.time()
    while time.time() < start + 10:
        event = xcb_conn.wait_for_event()

        if isinstance(event, xcffib.xproto.ConfigureNotifyEvent):
            assert event.width == 2*width
            assert event.height == 2*height
            width = event.width
            height = event.height
            break
    else:
        pytest.fail("Never received ConfigureNotifyEvent")

    # re-size and re-draw the surface
    surface.set_size(width, height)
    ctx = Context(surface)
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()

    # flush the connection, make sure no errors were thrown
    xcb_conn.flush()
    while event:
        event = xcb_conn.poll_for_event()
