"""
    cairocffi.pixbuf
    ~~~~~~~~~~~~~~~~

    Loading various image formats with GDK-PixBuf

    :copyright: Copyright 2013-2019 by Simon Sapin
    :license: BSD, see LICENSE for details.

"""

import sys
from array import array
from functools import partial
from io import BytesIO

from . import Context, ImageSurface, constants, dlopen
from ._generated.ffi_pixbuf import ffi

__all__ = ['decode_to_image_surface']

gdk_pixbuf = dlopen(ffi, 'gdk_pixbuf-2.0', 'gdk_pixbuf-2.0-0')
gobject = dlopen(ffi, 'gobject-2.0', 'gobject-2.0-0')
glib = dlopen(ffi, 'glib-2.0', 'glib-2.0-0')
try:
    gdk = dlopen(ffi, 'gdk-3', 'gdk-x11-2.0', 'gdk-win32-2.0-0')
except OSError:
    gdk = None

gobject.g_type_init()


class ImageLoadingError(ValueError):
    """PixBuf returned an error when loading an image.

    The image data is probably corrupted.

    """


def handle_g_error(error, return_value):
    """Convert a :c:type:`GError**` to a Python :exception:`ImageLoadingError`,
    and raise it.

    """
    error = error[0]
    assert bool(return_value) == (error == ffi.NULL)
    if error != ffi.NULL:
        if error.message != ffi.NULL:
            message = ('Pixbuf error: ' +
                       ffi.string(error.message).decode('utf8', 'replace'))
        else:  # pragma: no cover
            message = 'Pixbuf error'
        glib.g_error_free(error)
        raise ImageLoadingError(message)


class Pixbuf(object):
    """Wrap a :c:type:`GdkPixbuf` pointer and simulate methods."""
    def __init__(self, pointer):
        gobject.g_object_ref(pointer)
        self._pointer = ffi.gc(pointer, gobject.g_object_unref)

    def __getattr__(self, name):
        function = getattr(gdk_pixbuf, 'gdk_pixbuf_' + name)
        return partial(function, self._pointer)


def decode_to_pixbuf(image_data, width=None, height=None):
    """Decode an image from memory with GDK-PixBuf.
    The file format is detected automatically.

    :param image_data: A byte string
    :param width: Integer width in pixels or None
    :param height: Integer height in pixels or None
    :returns:
        A tuple of a new :class:`PixBuf` object
        and the name of the detected image format.
    :raises:
        :exc:`ImageLoadingError` if the image data is invalid
        or in an unsupported format.

    """
    loader = ffi.gc(
        gdk_pixbuf.gdk_pixbuf_loader_new(), gobject.g_object_unref)
    error = ffi.new('GError **')
    if width and height:
        gdk_pixbuf.gdk_pixbuf_loader_set_size(loader, width, height)
    handle_g_error(error, gdk_pixbuf.gdk_pixbuf_loader_write(
        loader, ffi.new('guchar[]', image_data), len(image_data), error))
    handle_g_error(error, gdk_pixbuf.gdk_pixbuf_loader_close(loader, error))

    format_ = gdk_pixbuf.gdk_pixbuf_loader_get_format(loader)
    format_name = (
        ffi.string(gdk_pixbuf.gdk_pixbuf_format_get_name(format_))
        .decode('ascii')
        if format_ != ffi.NULL else None)

    pixbuf = gdk_pixbuf.gdk_pixbuf_loader_get_pixbuf(loader)
    if pixbuf == ffi.NULL:  # pragma: no cover
        raise ImageLoadingError('Not enough image data (got a NULL pixbuf.)')
    return Pixbuf(pixbuf), format_name


def decode_to_image_surface(image_data, width=None, height=None):
    """Decode an image from memory into a cairo surface.
    The file format is detected automatically.

    :param image_data: A byte string
    :param width: Integer width in pixels or None
    :param height: Integer height in pixels or None
    :returns:
        A tuple of a new :class:`~cairocffi.ImageSurface` object
        and the name of the detected image format.
    :raises:
        :exc:`ImageLoadingError` if the image data is invalid
        or in an unsupported format.

    """
    pixbuf, format_name = decode_to_pixbuf(image_data, width, height)
    surface = (
        pixbuf_to_cairo_gdk(pixbuf) if gdk is not None
        else pixbuf_to_cairo_slices(pixbuf) if not pixbuf.get_has_alpha()
        else pixbuf_to_cairo_png(pixbuf))
    return surface, format_name


def pixbuf_to_cairo_gdk(pixbuf):
    """Convert from PixBuf to ImageSurface, using GDK.

    This method is fastest but GDK is not always available.

    """
    dummy_context = Context(ImageSurface(constants.FORMAT_ARGB32, 1, 1))
    gdk.gdk_cairo_set_source_pixbuf(
        dummy_context._pointer, pixbuf._pointer, 0, 0)
    return dummy_context.get_source().get_surface()


def pixbuf_to_cairo_slices(pixbuf):
    """Convert from PixBuf to ImageSurface, using slice-based byte swapping.

    This method is 2~5x slower than GDK but does not support an alpha channel.
    (cairo uses pre-multiplied alpha, but not Pixbuf.)

    """
    assert pixbuf.get_colorspace() == gdk_pixbuf.GDK_COLORSPACE_RGB
    assert pixbuf.get_n_channels() == 3
    assert pixbuf.get_bits_per_sample() == 8
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    rowstride = pixbuf.get_rowstride()
    pixels = ffi.buffer(pixbuf.get_pixels(), pixbuf.get_byte_length())
    # TODO: remove this when cffi buffers support slicing with a stride.
    pixels = pixels[:]

    # Convert GdkPixbuf’s big-endian RGBA to cairo’s native-endian ARGB
    cairo_stride = ImageSurface.format_stride_for_width(
        constants.FORMAT_RGB24, width)
    data = bytearray(cairo_stride * height)
    big_endian = sys.byteorder == 'big'
    pixbuf_row_length = width * 3  # stride == row_length + padding
    cairo_row_length = width * 4  # stride == row_length + padding
    alpha = b'\xff' * width  # opaque
    for y in range(height):
        offset = rowstride * y
        end = offset + pixbuf_row_length
        red = pixels[offset:end:3]
        green = pixels[offset + 1:end:3]
        blue = pixels[offset + 2:end:3]

        offset = cairo_stride * y
        end = offset + cairo_row_length
        if big_endian:  # pragma: no cover
            data[offset:end:4] = alpha
            data[offset + 1:end:4] = red
            data[offset + 2:end:4] = green
            data[offset + 3:end:4] = blue
        else:
            data[offset + 3:end:4] = alpha
            data[offset + 2:end:4] = red
            data[offset + 1:end:4] = green
            data[offset:end:4] = blue

    data = array('B', data)
    return ImageSurface(constants.FORMAT_RGB24,
                        width, height, data, cairo_stride)


def pixbuf_to_cairo_png(pixbuf):
    """Convert from PixBuf to ImageSurface, by going through the PNG format.

    This method is 10~30x slower than GDK but always works.

    """
    buffer_pointer = ffi.new('gchar **')
    buffer_size = ffi.new('gsize *')
    error = ffi.new('GError **')
    handle_g_error(error, pixbuf.save_to_buffer(
        buffer_pointer, buffer_size, ffi.new('char[]', b'png'), error,
        ffi.new('char[]', b'compression'), ffi.new('char[]', b'0'),
        ffi.NULL))
    png_bytes = ffi.buffer(buffer_pointer[0], buffer_size[0])
    return ImageSurface.create_from_png(BytesIO(png_bytes))
