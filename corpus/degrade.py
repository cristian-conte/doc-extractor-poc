"""Image degradations that simulate how documents actually arrive.

Each transform takes a clean rendered page and a severity in {1, 2, 3} and
returns a degraded PIL image. The transforms are deliberately physical --
resolution loss, sensor noise, skew, lossy compression, thresholding -- rather
than anything model-specific, so that they degrade any reader equally.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

# Severity-indexed parameters. Index 0 is unused so severity reads naturally.
_SCAN = {
    1: dict(scale=0.80, skew=0.4, noise=5, blur=0.3, quality=85),
    2: dict(scale=0.62, skew=1.5, noise=11, blur=0.5, quality=55),
    3: dict(scale=0.45, skew=2.6, noise=19, blur=0.9, quality=32),
}
_PHOTO = {
    1: dict(scale=0.85, warp=0.010, shadow=0.18, blur=0.4, quality=80, cast=(1.02, 1.00, 0.96)),
    2: dict(scale=0.68, warp=0.022, shadow=0.34, blur=0.7, quality=58, cast=(1.05, 1.00, 0.92)),
    3: dict(scale=0.52, warp=0.038, shadow=0.50, blur=1.1, quality=38, cast=(1.09, 0.99, 0.88)),
}
_FAX = {
    1: dict(scale=0.55, threshold=170, streaks=1, speckle=0.0015),
    2: dict(scale=0.44, threshold=160, streaks=3, speckle=0.0045),
    3: dict(scale=0.34, threshold=150, streaks=6, speckle=0.0110),
}


def _rng(seed):
    return np.random.default_rng(seed)


def _add_noise(img, sigma, seed):
    arr = np.asarray(img).astype(np.float32)
    arr += _rng(seed).normal(0, sigma, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _perspective(img, strength, seed):
    """Warp the page as if photographed off-axis."""
    w, h = img.size
    r = _rng(seed)
    dx, dy = w * strength, h * strength
    # Destination corners jittered; solve for the coefficients PIL wants.
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    dst = [
        (r.uniform(0, dx), r.uniform(0, dy)),
        (w - r.uniform(0, dx), r.uniform(0, dy)),
        (w - r.uniform(0, dx), h - r.uniform(0, dy)),
        (r.uniform(0, dx), h - r.uniform(0, dy)),
    ]
    matrix = []
    for (sx, sy), (ox, oy) in zip(src, dst):
        matrix.append([ox, oy, 1, 0, 0, 0, -sx * ox, -sx * oy])
        matrix.append([0, 0, 0, ox, oy, 1, -sy * ox, -sy * oy])
    coeffs = np.linalg.lstsq(np.array(matrix, float), np.array(src, float).reshape(8), rcond=None)[0]
    return img.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(235, 233, 228))


def _shadow(img, strength, seed):
    """Uneven illumination: a soft gradient across the page."""
    w, h = img.size
    r = _rng(seed)
    xs = np.linspace(0, 1, w)[None, :]
    ys = np.linspace(0, 1, h)[:, None]
    ang = r.uniform(0, 2 * np.pi)
    grad = np.cos(ang) * xs + np.sin(ang) * ys
    grad = (grad - grad.min()) / (np.ptp(grad) + 1e-6)
    mask = 1.0 - strength * grad
    arr = np.asarray(img).astype(np.float32)
    arr *= mask[:, :, None] if arr.ndim == 3 else mask
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _resave_jpeg(img, quality, tmp_path):
    img.save(tmp_path, "JPEG", quality=quality)
    return Image.open(tmp_path).copy()


def to_scan(img, severity, seed, tmp_path):
    """Flatbed scanner: greyscale, skewed, noisy, mid resolution, JPEG'd."""
    p = _SCAN[severity]
    img = img.convert("L")
    img = img.rotate(_rng(seed).uniform(-p["skew"], p["skew"]), expand=True,
                     fillcolor=255, resample=Image.BICUBIC)
    img = img.resize((max(1, int(img.width * p["scale"])), max(1, int(img.height * p["scale"]))),
                     Image.LANCZOS)
    img = _add_noise(img, p["noise"], seed + 1)
    img = img.filter(ImageFilter.GaussianBlur(p["blur"]))
    return _resave_jpeg(img.convert("RGB"), p["quality"], tmp_path)


def to_photo(img, severity, seed, tmp_path):
    """Phone camera: off-axis, uneven light, colour cast, motion blur."""
    p = _PHOTO[severity]
    img = img.convert("RGB")
    img = _perspective(img, p["warp"], seed)
    img = _shadow(img, p["shadow"], seed + 1)
    r, g, b = p["cast"]
    arr = np.asarray(img).astype(np.float32) * np.array([r, g, b])
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    img = img.resize((max(1, int(img.width * p["scale"])), max(1, int(img.height * p["scale"]))),
                     Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(p["blur"]))
    img = ImageEnhance.Contrast(img).enhance(0.92)
    img = _add_noise(img, 4, seed + 2)
    return _resave_jpeg(img, p["quality"], tmp_path)


def to_fax(img, severity, seed, tmp_path):
    """Fax: low resolution, hard 1-bit threshold, dropout streaks, speckle."""
    p = _FAX[severity]
    img = img.convert("L")
    img = img.resize((max(1, int(img.width * p["scale"])), max(1, int(img.height * p["scale"]))),
                     Image.LANCZOS)
    arr = np.asarray(img).astype(np.uint8)
    r = _rng(seed)
    # Horizontal transmission dropouts.
    for _ in range(p["streaks"]):
        y = r.integers(0, arr.shape[0])
        thick = int(r.integers(1, 3))
        arr[y:y + thick, :] = 255 if r.random() < 0.5 else 0
    # Salt and pepper.
    noise = r.random(arr.shape)
    arr = np.where(noise < p["speckle"] / 2, 0, arr)
    arr = np.where(noise > 1 - p["speckle"] / 2, 255, arr)
    img = Image.fromarray(arr).convert("1", dither=Image.FLOYDSTEINBERG)
    return img.convert("RGB")


def rotate_180(img):
    return img.rotate(180, expand=True)


def crop_bottom(img, fraction):
    """Cut the bottom off the page -- the framing error people make constantly."""
    w, h = img.size
    return img.crop((0, 0, w, int(h * (1 - fraction))))


TRANSFORMS = {"scan": to_scan, "photo": to_photo, "fax": to_fax}


def apply(condition, img, severity, seed, tmp_path):
    """Dispatch to a named degradation; 'digital' passes the page through."""
    if condition == "digital":
        return img
    return TRANSFORMS[condition](img, severity, seed, tmp_path)
