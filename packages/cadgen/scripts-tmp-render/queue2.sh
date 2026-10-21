#!/bin/bash
# second GPU queue: EEVEE stills (fixed config), Mitsuba, three.js, the Cycles explode, the bpy-wheel check
R=/private/tmp/claude-501/-Users-jakefitzgerald-robots-text-to-cad--claude-worktrees-cadgen-warm-edits-regression-1c2012/af694b81-4881-420d-b8b0-df0499e5d2ee/scratchpad/render-x
B=$R/app/Blender.app/Contents/MacOS/Blender
F=$R/frames
cd $R/scripts
blend() { $B -b --factory-startup --python $R/scripts/blender_render.py -- "$@" 2>&1 | grep -E "SUMMARY|Error|Traceback" | cut -c1-330; }
echo "== eevee stills"; blend $R/shots/movement_hero.json eevee $R/out/blender --samples 128; blend $R/shots/motorbike_hero.json eevee $R/out/blender --samples 128
echo "== mitsuba stills"
for s in movement_hero motorbike_hero; do $R/venv/bin/python -I mitsuba_render.py $R/shots/$s.json $R/out/mitsuba --spp 256 2>&1 | grep -E "SUMMARY|scene:|Error|Traceback" | cut -c1-330; done
echo "== mitsuba clip"; $R/venv/bin/python -I mitsuba_render.py $R/shots/motorbike_orbit.json $F/mitsuba_motorbike_orbit --spp 64 2>&1 | grep -E "SUMMARY|scene:|Error|Traceback" | cut -c1-330
echo "== three stills"
for s in movement_hero motorbike_hero; do (cd three && node render.mjs $s $F/three_$s 256 1 0:1 4000000 -1 2>&1 | grep -E "SETUP|FRAME|PAGEERROR" | cut -c1-300); $R/venv/bin/python -I three_post.py $F/three_$s $R/out/three ${s}_three.png; done
echo "== three clip"; (cd three && node render.mjs motorbike_orbit $F/three_motorbike_orbit_raw 64 1 0:120 4000000 -1 2>&1 | grep -E "SETUP|PAGEERROR" | cut -c1-300); $R/venv/bin/python -I three_post.py $F/three_motorbike_orbit_raw $F/three_motorbike_orbit
echo "== cycles explode"; blend $R/shots/movement_explode.json cycles $F/blender_cycles_movement_explode --samples 128
echo "== bpy wheel"; $R/venv-bpy/bin/python -I bpy_run.py $R/shots/motorbike_hero.json cycles $R/out/bpy --samples 512 2>&1 | grep -E "SUMMARY|Error|Traceback" | cut -c1-330
echo "== daylight"; blend $R/shots/motorbike_daylight.json cycles $R/out/blender --samples 256
echo "== done $(date)"
