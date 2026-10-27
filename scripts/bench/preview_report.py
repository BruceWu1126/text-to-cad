#!/usr/bin/env python3
"""Make contact sheets and native-resolution detail comparisons for manual Preview review.

The images are production captures, never retouched. Crops use the same coordinates
for every profile. Image differences are supporting evidence, not a photorealism score.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--scene", action="append", help="Technical comparison scene basename (repeatable)")
    parser.add_argument("--beauty-scene", action="append", help="Beauty-only scene basename (repeatable)")
    args = parser.parse_args()
    folder = args.folder
    font = ImageFont.load_default(size=18)
    names = args.scene or sorted(p.name.removesuffix("-candidate.png") for p in folder.glob("*-candidate.png"))
    beauty_names = args.beauty_scene or names
    for name in set(names+beauty_names):
        if not (folder/f"{name}-candidate.png").exists():
            parser.error(f"Missing production candidate for {name}")
    profiles = [("raster", "Raster studio"), ("candidate", "Production OIDN"), ("reference", "Raw reference")]
    tile_w, tile_h, heading = 600, 450, 48
    sheet = Image.new("RGB", (tile_w * 3, (tile_h + heading) * len(names)), "#f5f5f3")
    draw = ImageDraw.Draw(sheet)
    metrics = []
    for row, name in enumerate(names):
        images = {}
        for col, (profile, label) in enumerate(profiles):
            path = folder / f"{name}-{profile}.png"
            x, y = col * tile_w, row * (tile_h + heading)
            draw.text((x + 12, y + 4), name.replace("_", " "), fill="#151515", font=font)
            draw.text((x + 12, y + 25), label, fill="#555555", font=font)
            if not path.exists():
                draw.text((x + 12, y + 90), "Not rendered", fill="#555555", font=font)
                continue
            rgba = Image.open(path).convert("RGBA")
            image = Image.new("RGB", rgba.size, "#202226")
            image.paste(rgba, mask=rgba.getchannel("A"))
            images[profile] = image
            tile = image.copy()
            tile.thumbnail((tile_w, tile_h), Image.Resampling.LANCZOS)
            sheet.paste(tile, (x + (tile_w - tile.width) // 2, y + heading))
        if not images:
            continue
        width, height = next(iter(images.values())).size
        crops = [("centre", (width//4, height//4, width*3//4, height*3//4)),
                 ("contact_and_edges", (width//4, height//2, width*3//4, height))]
        for title, box in crops:
            crop_w, crop_h = box[2]-box[0], box[3]-box[1]
            detail = Image.new("RGB", (crop_w * 3, crop_h + 28), "#f5f5f3")
            pen = ImageDraw.Draw(detail)
            for col, (profile, label) in enumerate(profiles):
                pen.text((col*crop_w+8, 4), label, fill="#222222", font=font)
                if profile in images:
                    detail.paste(images[profile].crop(box), (col*crop_w, 28))
            detail.save(folder / f"{name}-detail-{title}.png")
        if "candidate" in images and "reference" in images:
            candidate = np.asarray(images["candidate"], dtype=float)
            reference = np.asarray(images["reference"], dtype=float)
            delta = candidate - reference
            metrics.append({"scene":name,"rgb_mae_255":float(np.abs(delta).mean()),
                            "rgb_rmse_255":float(np.sqrt((delta**2).mean())),
                            "note":"Whole-frame displayed sRGB difference, includes lighting/background and residual reference noise; not a photorealism score."})
    sheet.save(folder / "contact-sheet.png")
    beauty_h = 480
    beauty = Image.new("RGB", (1200, beauty_h*((len(beauty_names)+1)//2)), "#ededeb")
    beauty_draw = ImageDraw.Draw(beauty)
    for index, name in enumerate(beauty_names):
        row, col = divmod(index, 2)
        x = col*600
        if len(beauty_names)%2 and index==len(beauty_names)-1:
            x=300  # Deliberately centre an unpaired final scene; no empty placeholder.
        rgba=Image.open(folder/f"{name}-candidate.png").convert("RGBA")
        image=Image.new("RGB",rgba.size,"#202226")
        image.paste(rgba,mask=rgba.getchannel("A"))
        image.thumbnail((600,450),Image.Resampling.LANCZOS)
        beauty.paste(image,(x+(600-image.width)//2,row*beauty_h))
        beauty_draw.text((x+14,row*beauty_h+452),name.replace("_"," ").capitalize(),fill="#252525",font=font)
    beauty.save(folder/"beauty-sheet.png")
    (folder / "image-differences.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(folder / "contact-sheet.png")


if __name__ == "__main__":
    main()
