# ETVLauncher wallpapers

Photos of places, for [ETVLauncher](https://github.com/Evesain/ETVLauncher). When "Photos of my area" is on and the TV is online, the launcher shows photos from the place nearest to it. The photos built into the app are still used when the TV is offline or the setting is off.

## Layout

```
places.json                                 every place, with its coordinates (the app reads this first)
<Country>/<Region>/<City>/place.json        the photos of one place: title, subtitle, credit, source
<Country>/<Region>/<City>/day/01.jpg ...    day photos
<Country>/<Region>/<City>/night/01.jpg ...  night photos
```

A place covers the area around its city (`radius_km`, 25 km by default). Each place has at least 3 day and 3 night photos, at most 1920 px wide.

## Coverage

One pack per city and one per scenic area, not one per village: the whole United Kingdom (England, Scotland, Wales and Northern Ireland) and Texas. A pack reaches 20 to 60 km around its centre (`radius_km` in `place.json`), wider in empty country. A TV outside every pack falls back to the photos built into the app and asks for a new pack, which arrives as a pull request.

The place lists are in `tools/seed/` (`uk.json`, `texas.json`). Run `python tools/wallpaper_tool.py seed tools/seed/uk.json --shard 0/4` (and shards 1 to 3 in other windows) to fill the missing ones. It skips places that already have a folder. A person should look at every new pack: the tool picks by title and brightness and cannot see the picture.

## Credits and licences

The photos come from [Wikimedia Commons](https://commons.wikimedia.org). Only photos that may be reused with credit are used: CC0, public domain, CC BY and CC BY-SA. Nothing is drawn on the pictures. The photographer and licence are in each `place.json` and are shown as text next to the photo in the app. The resized copies here keep the licence of the original (CC BY-SA photos stay CC BY-SA).

## Add a place

You need Python 3 and Pillow (`pip install pillow`).

```
python tools/wallpaper_tool.py find --work work --centre 53.1631,-2.2125 --query Congleton --query "Mow Cop"
```

This searches Commons near the place and writes contact sheets (`work/sheet_day_1.jpg`, `work/sheet_night_1.jpg`, ...). Look at them and pick 3 or 4 photos for each of day and night. Then:

```
python tools/wallpaper_tool.py add --work work --country "United Kingdom" --region England --city Congleton \
    --lat 53.1631 --lon -2.2125 --day 149206100="Mow Cop Castle|Mow Cop, Cheshire" --night 58607852="Mow Cop Castle|Mow Cop, Cheshire"
```

The number is the picture's id on the contact sheet. `add` resizes the pictures, writes `place.json` and rebuilds `places.json`.
