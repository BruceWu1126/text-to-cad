"""Procedural studio HDRIs (Radiance .hdr), one canonical mapping for every renderer.

Canonical mapping = three.js's equirect (Y up): u = atan2(z, x) / 2pi + 0.5, row 0 = straight up.
A rig is a list of soft-edged rectangular softboxes in (azimuth, elevation) degrees, azimuth
measured like three.js (0 = +X, 90 = +Z, -90 = -Z), plus a background gradient.
The hero camera of every shot looks from +Z (azimuth 90) toward the origin, so a light at
azimuth 90 is behind the camera (a front light) and one at -90 is behind the subject (a rim).

usage: python make_env.py OUTDIR
"""
import sys
from pathlib import Path

import numpy as np

W, H = 2048, 1024


def dirs():
    u = (np.arange(W) + 0.5) / W
    v = (np.arange(H) + 0.5) / H
    phi = (u - 0.5) * 2 * np.pi            # azimuth, atan2(z, x)
    lat = np.pi / 2 - v * np.pi            # row 0 = +90 deg
    return np.meshgrid(np.degrees(phi), np.degrees(lat))


def angdiff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def unit(az, el):
    a, e = np.radians(az), np.radians(el)
    return np.stack([np.cos(e) * np.cos(a), np.sin(e) * np.ones_like(a), np.cos(e) * np.sin(a)], -1)


def softbox(az, el, center_az, center_el, w, h, edge=1.5):
    """A planar rectangle facing the origin (gnomonic projection), w/h its angular size in
    degrees across its centre; soft edges and a slight centre hot spot, as a diffuser has."""
    if w >= 360:  # a band all round the horizon, h tall
        de = np.abs(el - center_el)
        f = np.clip((h / 2 - de) / edge, 0, 1)
        return f * f * (3 - 2 * f)
    d = unit(az, el)
    c = unit(np.float64(center_az), np.float64(center_el))
    world_up = np.array([0.0, 1.0, 0.0]) if abs(center_el) < 80 else np.array([-np.cos(np.radians(center_az)), 0.0, -np.sin(np.radians(center_az))])
    r = np.cross(world_up, c); r /= np.linalg.norm(r)
    t = np.cross(c, r)
    dc = d @ c
    front = dc > 1e-3
    x = np.where(front, (d @ r) / np.where(front, dc, 1), 1e9)
    y = np.where(front, (d @ t) / np.where(front, dc, 1), 1e9)
    hx, hy = np.tan(np.radians(w / 2)), np.tan(np.radians(h / 2))
    ex, ey = np.tan(np.radians(edge)) + 1e-9, np.tan(np.radians(edge)) + 1e-9
    fx = np.clip((hx - np.abs(x)) / ex, 0, 1)
    fy = np.clip((hy - np.abs(y)) / ey, 0, 1)
    mask = (fx * fx * (3 - 2 * fx)) * (fy * fy * (3 - 2 * fy))
    hot = 1.0 - 0.25 * ((x / hx) ** 2 + (y / hy) ** 2)
    return mask * np.clip(hot, 0.6, 1.0)


def rig(lights, ground=0.004, horizon=0.02, zenith=0.012):
    az, el = dirs()
    img = np.zeros((H, W, 3), np.float32)
    # background: dark below the horizon, a faint glow at it, a little more above
    up = np.clip(el / 90.0, 0, 1)
    sky = horizon * np.exp(-np.abs(el) / 8.0) + zenith * up
    img[...] = np.where(el[..., None] >= 0, sky[..., None], ground + horizon * np.exp(-np.abs(el) / 4.0)[..., None] * 0.5)
    for L in lights:
        m = softbox(az, el, L["az"], L["el"], L["w"], L["h"], L.get("edge", 1.5))
        img += m[..., None] * np.array(L["rgb"], np.float32)[None, None, :] * L["power"]
    return img


RIGS = {
    # The watch: a dark product set. Big overhead box, two tall strip rims behind, a warm key
    # front-left, a faint fill card front-right.
    "studio_dark": dict(lights=[
        dict(az=90, el=72, w=70, h=28, power=6.0, rgb=(1.0, 1.0, 1.0)),           # overhead
        dict(az=90 - 145, el=22, w=7, h=44, power=14.0, rgb=(0.85, 0.92, 1.0)),   # rim, back-left
        dict(az=90 + 145, el=22, w=7, h=44, power=14.0, rgb=(0.85, 0.92, 1.0)),   # rim, back-right
        dict(az=90 - 40, el=28, w=34, h=26, power=3.5, rgb=(1.0, 0.93, 0.84)),    # key, front-left
        dict(az=90 + 55, el=12, w=26, h=16, power=0.7, rgb=(1.0, 1.0, 1.0)),      # fill, front-right
    ], ground=0.002, horizon=0.006, zenith=0.004),
    # The bike: a car-commercial stage. A long overhead strip bank, cool rims, warm key, and a
    # wide low horizon band so the glossy floor and the paint carry a line of light.
    "studio_stage": dict(lights=[
        dict(az=90, el=62, w=100, h=30, power=2.6, rgb=(1.0, 1.0, 1.0)),          # overhead strip bank
        dict(az=90 - 150, el=20, w=9, h=38, power=12.0, rgb=(0.78, 0.88, 1.0)),   # rim, back-left
        dict(az=90 + 150, el=20, w=9, h=38, power=12.0, rgb=(0.78, 0.88, 1.0)),   # rim, back-right
        dict(az=90 - 70, el=28, w=30, h=40, power=5.0, rgb=(1.0, 0.88, 0.74)),    # key, hard left
        dict(az=90 + 40, el=10, w=24, h=14, power=0.6, rgb=(1.0, 1.0, 1.0)),      # fill, front-right
    ], ground=0.002, horizon=0.008, zenith=0.008),
    # Calibration: red +X, green +Z (behind the camera), blue -X, yellow -Z, white zenith.
    "markers": dict(lights=[
        dict(az=0, el=0, w=30, h=30, power=2.0, rgb=(1, 0, 0)),
        dict(az=90, el=0, w=30, h=30, power=2.0, rgb=(0, 1, 0)),
        dict(az=180, el=0, w=30, h=30, power=2.0, rgb=(0, 0, 1)),
        dict(az=-90, el=0, w=30, h=30, power=2.0, rgb=(1, 1, 0)),
        dict(az=0, el=90, w=360, h=30, power=1.0, rgb=(1, 1, 1)),
    ], ground=0.05, horizon=0.0, zenith=0.05),
}


def write_hdr(path: Path, img: np.ndarray) -> None:
    h, w, _ = img.shape
    m = img.max(axis=2)
    mant, e = np.frexp(m)
    ok = m > 1e-32
    scale = np.where(ok, mant * 256.0 / np.where(ok, m, 1.0), 0.0)
    rgbe = np.zeros((h, w, 4), np.uint8)
    rgbe[..., :3] = np.clip(img * scale[..., None], 0, 255).astype(np.uint8)
    rgbe[..., 3] = np.where(ok, e + 128, 0).astype(np.uint8)
    if rgbe[0, 0, 0] == 2 and rgbe[0, 0, 1] == 2:  # never look like an RLE scanline header
        rgbe[0, 0, 0] = 3
    path.write_bytes(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y %d +X %d\n" % (h, w) + rgbe.tobytes())


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for name, spec in RIGS.items():
        img = rig(**spec)
        write_hdr(out / f"{name}.hdr", img)
        prev = np.clip((img / (1 + img)) ** (1 / 2.2), 0, 1)  # quick look
        try:
            from PIL import Image
            Image.fromarray((prev * 255).astype(np.uint8)).resize((1024, 512)).save(out / f"{name}.preview.png")
        except ImportError:
            pass
        print(name, "max", float(img.max()), "mean", float(img.mean()))
