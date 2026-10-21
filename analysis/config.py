from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUTPUTS = ROOT / "outputs"
FIGURES = OUTPUTS / "figures"

OFFICIAL_LINES = RAW_DIR / "화성_자전거노선_5186.geojson"
OFFICIAL_POLYS = RAW_DIR / "화성_자전거도로면_5186.geojson"
OSM_BIKE = RAW_DIR / "osm_hwaseong_bike_20260914.json"

TEAM_NAME = "북적북적"
