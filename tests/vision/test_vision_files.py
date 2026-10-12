"""Template loading (cache, errors, alpha masks) and saving captures as PNG."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from j5_macro.backends.base import Rect  # noqa: E402
from j5_macro.vision.api import Frame  # noqa: E402
from j5_macro.vision.drawing import HIGHLIGHT_COLOR, save_png  # noqa: E402
from j5_macro.vision.templates import TemplateCache, read_template  # noqa: E402


def noise(height: int, width: int, channels: int = 3, seed: int = 0):  # type: ignore[no-untyped-def]
    shape = (height, width) if channels == 1 else (height, width, channels)
    return np.random.default_rng(seed).integers(0, 256, shape, dtype=np.uint8)


def write_image(path: Path, pixels) -> Path:  # type: ignore[no-untyped-def]
    ok, data = cv2.imencode(path.suffix, pixels)
    assert ok
    path.write_bytes(data.tobytes())
    return path


def bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


@pytest.mark.parametrize("suffix", [".png", ".bmp"])
def test_lossless_formats_load_as_bgr(tmp_path: Path, suffix: str) -> None:
    pixels = noise(12, 20)
    template = read_template(write_image(tmp_path / f"t{suffix}", pixels))
    assert template.size == (20, 12)
    assert template.mask is None
    assert np.array_equal(template.pixels, pixels)
    assert template.path == tmp_path / f"t{suffix}"


def test_jpeg_and_grayscale_become_bgr(tmp_path: Path) -> None:
    jpeg = read_template(write_image(tmp_path / "t.jpg", noise(16, 16)))
    assert jpeg.pixels.shape == (16, 16, 3)
    gray = read_template(write_image(tmp_path / "g.png", noise(8, 9, channels=1)))
    assert gray.pixels.shape == (8, 9, 3)


def test_alpha_channel_becomes_a_binary_mask(tmp_path: Path) -> None:
    bgra = noise(10, 10, channels=4)
    bgra[:, :, 3] = 255
    bgra[0, 0, 3] = 0
    bgra[0, 1, 3] = 127
    bgra[0, 2, 3] = 128
    template = read_template(write_image(tmp_path / "a.png", bgra))
    assert template.pixels.shape == (10, 10, 3)
    assert np.array_equal(template.pixels, bgra[:, :, :3])
    assert template.mask.dtype == np.uint8
    assert template.mask.shape == (10, 10)
    assert list(template.mask[0, :4]) == [0, 0, 255, 255]
    assert int((template.mask == 0).sum()) == 2


def test_opaque_alpha_needs_no_mask_and_transparent_is_rejected(tmp_path: Path) -> None:
    bgra = noise(5, 5, channels=4)
    bgra[:, :, 3] = 255
    assert read_template(write_image(tmp_path / "o.png", bgra)).mask is None
    bgra[:, :, 3] = 0
    with pytest.raises(ValueError, match="transparent"):
        read_template(write_image(tmp_path / "t.png", bgra))


def test_loaded_pixels_are_read_only(tmp_path: Path) -> None:
    template = read_template(write_image(tmp_path / "t.png", noise(4, 4)))
    with pytest.raises(ValueError):
        template.pixels[0, 0] = 0


def test_missing_and_unreadable_files(tmp_path: Path) -> None:
    cache = TemplateCache()
    with pytest.raises(FileNotFoundError):
        cache(tmp_path / "missing.png")
    with pytest.raises(FileNotFoundError):
        read_template(tmp_path / "missing.png")
    (tmp_path / "bad.png").write_bytes(b"this is not an image")
    with pytest.raises(ValueError):
        cache(tmp_path / "bad.png")
    (tmp_path / "empty.png").write_bytes(b"")
    with pytest.raises(ValueError):
        cache(tmp_path / "empty.png")
    with pytest.raises(ValueError):
        cache(tmp_path)


def test_unicode_filename(tmp_path: Path) -> None:
    folder = tmp_path / "imágenes 画像"
    folder.mkdir()
    pixels = noise(6, 7)
    path = folder / "botón_保存.png"
    save_png(Frame(pixels), path)
    assert np.array_equal(TemplateCache()(path).pixels, pixels)


def test_cache_hit_and_mtime_invalidation(tmp_path: Path) -> None:
    path = write_image(tmp_path / "t.png", noise(5, 5, seed=1))
    cache = TemplateCache()
    first = cache(path)
    assert cache(path) is first
    (tmp_path / "sub").mkdir()
    assert cache(Path(f"{tmp_path}/sub/../t.png")) is first  # same file, other spelling
    assert (cache.hits, cache.misses) == (2, 1)
    write_image(path, noise(5, 5, seed=2))
    bump_mtime(path)
    second = cache(path)
    assert second is not first
    assert not np.array_equal(second.pixels, first.pixels)
    assert cache.misses == 2


def test_cache_evicts_least_recently_used(tmp_path: Path) -> None:
    paths = [write_image(tmp_path / f"{i}.png", noise(3, 3, seed=i)) for i in range(3)]
    cache = TemplateCache(maxsize=2)
    a = cache(paths[0])
    cache(paths[1])
    cache(paths[0])  # paths[1] is now the least recently used
    cache(paths[2])
    assert len(cache) == 2
    assert cache(paths[0]) is a
    misses = cache.misses
    cache(paths[1])
    assert cache.misses == misses + 1


def test_deleted_file_is_not_served_from_cache(tmp_path: Path) -> None:
    path = write_image(tmp_path / "t.png", noise(3, 3))
    cache = TemplateCache()
    cache(path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        cache(path)


def test_save_png_round_trip_creates_directories(tmp_path: Path) -> None:
    pixels = noise(30, 40)
    path = tmp_path / "runs" / "r1" / "capture.png"
    save_png(Frame(pixels, (100, 200)), path)
    loaded = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    assert np.array_equal(loaded, pixels)


def test_save_png_highlights_in_frame_coordinates(tmp_path: Path) -> None:
    pixels = np.zeros((50, 60, 3), np.uint8)
    frame = Frame(pixels, (-100, 300))
    path = tmp_path / "h.png"
    save_png(frame, path, Rect(-90, 320, 20, 10))  # frame x 10..29, y 20..29
    loaded = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    red = np.all(loaded == HIGHLIGHT_COLOR, axis=2)
    expected = np.zeros((50, 60), bool)
    expected[20:30, 10:30] = True
    expected[22:28, 12:28] = False  # a 2 px outline inside the rectangle
    assert np.array_equal(red, expected)
    assert not pixels.any()  # the frame itself is untouched


def test_save_png_highlight_partly_outside_the_frame(tmp_path: Path) -> None:
    path = tmp_path / "h.png"
    save_png(Frame(np.zeros((20, 20, 3), np.uint8)), path, Rect(10, 10, 30, 30))
    loaded = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    red = np.all(loaded == HIGHLIGHT_COLOR, axis=2)
    assert red[10, 10:].all() and red[11, 10:].all() and red[10:, 10].all()
    assert int(red.sum()) == 2 * 10 + 2 * 10 - 4
