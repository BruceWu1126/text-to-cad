# brand

Studio's identity, read through `T3CODE_BRAND_DIR` (`scripts/studio.ts` sets it; `apps/web/vite/brand.ts`
applies it):

- `brand.json`: the app's name and the loading splash's colors.
- `theme.css`: text-to-cad's look on T3 Code's color roles: the CAD viewer's neutral surfaces, charcoal in
  dark, blue only for brand emphasis and primary actions. It is scoped to T3's default look, so a theme the
  user picks keeps its own colors.
- `splash.svg`: the loading splash, text-to-cad's animated build of its mark.
- `mark.svg`: the mark beside the app's name, in place of the T3 wordmark.
- `favicon*`, `apple-touch-icon.png`, `app-icon.png`: browser icons and the desktop dock icon.

Everything comes from text-to-cad's own brand kit, so nothing here is designed in this fork: the files are
its `apps/docs/public/brand/logo-c.svg` (`mark.svg`), `logo-c-animated.svg` (`splash.svg`), `logo-c.png`
(`app-icon.png`) and `apps/docs/public/favicon.ico`; the colors are its docs site's palette
(`apps/docs/src/app/globals.css`), which matches the viewer. To refresh from a text-to-cad checkout at `$T`:

```sh
cp $T/apps/docs/public/brand/logo-c.svg brand/mark.svg
cp $T/apps/docs/public/brand/logo-c-animated.svg brand/splash.svg
cp $T/apps/docs/public/brand/logo-c.png brand/app-icon.png
cp $T/apps/docs/public/favicon.ico brand/favicon.ico
for s in 16 32; do sips -z $s $s brand/app-icon.png --out brand/favicon-${s}x${s}.png; done
sips -z 180 180 brand/app-icon.png --out brand/apple-touch-icon.png
```

Then compare `theme.css` with the docs palette by hand.
