#!/bin/bash
# encode.sh FRAMES_DIR OUT.mp4 [vertical]   -- H.264 (crf 18, yuv420p, faststart), 24 fps
set -e
FF=/usr/local/bin/ffmpeg
in=$1; out=$2
$FF -loglevel error -y -framerate 24 -i "$in/%04d.png" -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -movflags +faststart "$out"
if [ "$3" = "vertical" ]; then
  # 9:16 centre crop of the 16:9 clip, scaled to 720x1280 (a social upload; a real one is framed natively)
  $FF -loglevel error -y -i "$out" -vf "crop=ih*9/16:ih,scale=720:1280:flags=lanczos" -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -movflags +faststart "${out%.mp4}_9x16.mp4"
  $FF -loglevel error -y -i "$out" -vf "fps=12,scale=640:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=192[p];[b][p]paletteuse=dither=sierra2_4a" "${out%.mp4}.gif"
fi
ls -la "${out%.mp4}"* | awk '{print $5, $9}'
