"""Template matching on synthetic images."""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from j5_macro.backends.base import Rect  # noqa: E402
from j5_macro.vision.api import Frame, Template  # noqa: E402
from j5_macro.vision.matching import locate, locate_all  # noqa: E402

ORIGIN = (-1920, 40)  # a frame from a monitor left of the primary one


def noise(height: int, width: int, seed: int = 0):  # type: ignore[no-untyped-def]
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


def blocky(height: int, width: int, seed: int = 0, block: int = 8):  # type: ignore[no-untyped-def]
    """Noise made of solid blocks: survives resizing, unlike per-pixel noise."""
    small = noise(-(-height // block), -(-width // block), seed)
    big = np.repeat(np.repeat(small, block, axis=0), block, axis=1)
    return np.ascontiguousarray(big[:height, :width])


def template(pixels, mask=None) -> Template:  # type: ignore[no-untyped-def]
    return Template(path=Path("t.png"), pixels=pixels, mask=mask)


def test_exact_match_scores_one_at_the_desktop_bbox() -> None:
    pixels = noise(200, 300)
    match = locate(template(pixels[50:90, 120:180].copy()), Frame(pixels, ORIGIN), 0.9)
    assert match.found
    assert match.score == pytest.approx(1.0, abs=1e-4)
    assert match.bbox == Rect(ORIGIN[0] + 120, ORIGIN[1] + 50, 60, 40)
    assert match.elapsed_ms > 0


def test_threshold_boundary_and_best_score_when_not_found() -> None:
    pixels = noise(120, 160)
    patch = pixels[30:70, 40:100].copy()
    noisy = np.clip(patch.astype(np.int16) + noise(40, 60, 7).astype(np.int16) // 4 - 32,
                    0, 255).astype(np.uint8)
    frame = Frame(pixels, (0, 0))
    score = locate(template(noisy), frame, 0.0).score
    assert 0.5 < score < 0.99
    assert locate(template(noisy), frame, score).found  # found means score >= threshold
    missed = locate(template(noisy), frame, min(1.0, score + 1e-3))
    assert not missed.found
    assert missed.score == pytest.approx(score)
    assert missed.bbox == Rect(40, 30, 60, 40)  # the best location is still reported


def test_unrelated_template_reports_a_low_best_score() -> None:
    match = locate(template(noise(30, 30, 1)), Frame(noise(200, 200, 2)), 0.9)
    assert not match.found
    assert 0.0 <= match.score < 0.5
    assert match.bbox is not None


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_scale_matches_a_resized_copy(scale: float) -> None:
    frame_pixels = blocky(400, 400, seed=3)
    original = blocky(64, 96, seed=4)
    size = (round(96 * scale), round(64 * scale))
    on_screen = cv2.resize(original, size, interpolation=cv2.INTER_NEAREST)
    frame_pixels[100:100 + size[1], 150:150 + size[0]] = on_screen
    frame = Frame(frame_pixels, (10, 20))
    match = locate(template(original), frame, 0.9, scale)
    assert match.found, match
    assert match.bbox == Rect(160, 120, size[0], size[1])
    assert locate(template(original), frame, 0.9, 1.0).score < 0.9


def test_invalid_scale_is_rejected() -> None:
    with pytest.raises(ValueError, match="scale"):
        locate(template(noise(10, 10)), Frame(noise(50, 50)), 0.9, 0.0)


def test_masked_template_ignores_transparent_pixels() -> None:
    icon = noise(40, 40, 5)
    mask = np.zeros((40, 40), np.uint8)
    cv2.circle(mask, (20, 20), 16, 255, -1)
    tmpl_pixels = icon.copy()
    tmpl_pixels[mask == 0] = (255, 0, 255)  # whatever the template had behind the icon
    screen = np.full((240, 320, 3), (30, 160, 60), np.uint8)  # another background
    screen[60:100, 200:240][mask > 0] = icon[mask > 0]
    frame = Frame(screen, (100, 100))
    masked = locate(template(tmpl_pixels, mask), frame, 0.9)
    assert masked.found
    assert masked.score == pytest.approx(1.0, abs=1e-3)
    assert masked.bbox == Rect(300, 160, 40, 40)
    assert locate(template(tmpl_pixels), frame, 0.9).score < 0.9  # unmasked fails


def test_masked_template_with_scale() -> None:
    icon = blocky(48, 48, seed=6)
    mask = np.zeros((48, 48), np.uint8)
    mask[8:40, 8:40] = 255
    screen = np.full((200, 200, 3), 200, np.uint8)
    big = cv2.resize(icon, (96, 96), interpolation=cv2.INTER_NEAREST)
    big_mask = cv2.resize(mask, (96, 96), interpolation=cv2.INTER_NEAREST)
    screen[50:146, 60:156][big_mask > 0] = big[big_mask > 0]
    match = locate(template(icon, mask), Frame(screen), 0.9, 2.0)
    assert match.found, match
    assert match.bbox == Rect(60, 50, 96, 96)


def test_masked_score_is_not_fooled_by_plain_bright_areas() -> None:
    icon = noise(30, 30, 8)
    mask = np.full((30, 30), 255, np.uint8)
    mask[:5] = 0
    screen = np.full((100, 100, 3), 220, np.uint8)
    screen[50:, :] = noise(50, 100, 9)
    match = locate(template(icon, mask), Frame(screen), 0.9)
    assert not match.found
    assert match.score < 0.5


def test_flat_template_scores_zero_not_nan() -> None:
    flat = np.full((20, 20, 3), 128, np.uint8)
    frame = Frame(np.full((100, 100, 3), 128, np.uint8))
    match = locate(template(flat), frame, 0.9)
    assert match.score == 0.0
    assert not match.found
    mask = np.full((20, 20), 255, np.uint8)
    mask[0, 0] = 0
    assert locate(template(flat, mask), frame, 0.9).score == 0.0


@pytest.mark.parametrize("masked", [False, True])
def test_flat_region_scores_zero_not_nan(masked: bool) -> None:
    mask = None
    if masked:
        mask = np.full((20, 20), 255, np.uint8)
        mask[:3] = 0
    match = locate(template(noise(20, 20), mask), Frame(np.full((80, 80, 3), 40, np.uint8)), 0.5)
    assert match.score == 0.0
    assert not match.found
    assert match.bbox is not None


def test_template_bigger_than_frame() -> None:
    match = locate(template(noise(50, 50)), Frame(noise(40, 100)), 0.9)
    assert match == type(match)(found=False, score=0.0, bbox=None, elapsed_ms=match.elapsed_ms)
    assert locate(template(noise(20, 20)), Frame(noise(40, 40)), 0.9, 3.0).bbox is None


def test_grayscale_and_bgra_frames_are_accepted() -> None:
    gray = noise(60, 60)[:, :, 0].copy()
    tmpl = cv2.cvtColor(gray[10:30, 20:40], cv2.COLOR_GRAY2BGR)
    assert locate(template(tmpl), Frame(gray), 0.99).bbox == Rect(20, 10, 20, 20)
    bgra = cv2.cvtColor(noise(60, 60), cv2.COLOR_BGR2BGRA)
    assert locate(template(bgra[5:25, 5:25, :3].copy()), Frame(bgra), 0.99).found


def test_locate_all_returns_non_overlapping_matches_best_first() -> None:
    icon = noise(20, 30, 10)
    screen = noise(200, 300, 11) // 4
    spots = [(10, 10), (100, 40), (150, 200), (25, 20)]  # the last overlaps the first
    for i, (y, x) in enumerate(spots):
        screen[y:y + 20, x:x + 30] = np.clip(icon.astype(np.int16) - i * 30, 0, 255)
    matches = locate_all(template(icon), Frame(screen, (5, 5)), 0.8)
    boxes = [m.bbox for m in matches]
    assert all(m.found and m.score >= 0.8 for m in matches)
    assert [m.score for m in matches] == sorted((m.score for m in matches), reverse=True)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a is not None and b is not None
            assert (a.x + a.width <= b.x or b.x + b.width <= a.x
                    or a.y + a.height <= b.y or b.y + b.height <= a.y)
    assert Rect(45, 105, 30, 20) in boxes
    assert Rect(205, 155, 30, 20) in boxes
    assert len(locate_all(template(icon), Frame(screen), 0.8, max_results=1)) == 1
    assert locate_all(template(noise(300, 300)), Frame(screen), 0.8) == []


def test_locate_all_min_distance() -> None:
    icon = noise(10, 10, 12)
    screen = noise(100, 100, 13) // 4
    screen[10:20, 10:20] = icon
    screen[10:20, 25:35] = icon  # 15 px to the right: no overlap
    frame = Frame(screen)
    assert len(locate_all(template(icon), frame, 0.95)) == 2
    assert len(locate_all(template(icon), frame, 0.95, min_distance=20)) == 1
