"""OSM way 태그로 자전거 시설 여부/횡단 여부를 판정한다.

`cycleway*=no`(도로에 자전거 시설 없음 표시)나 `separate`(별도로 매핑된 시설이
있어 이 way 자체는 시설이 아님)를 시설로 잘못 세지 않도록, 실제로 시설이
존재함을 뜻하는 값만 인정한다.
"""

ON_ROAD = {"lane", "track", "shared_lane", "opposite_lane", "opposite_track"}
CYCLEWAY_KEYS = ("cycleway", "cycleway:both", "cycleway:left", "cycleway:right")


def is_bike_facility(tags: dict) -> bool:
    if tags.get("highway") == "cycleway":
        return True
    if tags.get("bicycle") == "designated":
        return True
    return any(tags.get(k) in ON_ROAD for k in CYCLEWAY_KEYS)


def is_crossing(tags: dict) -> bool:
    return (tags.get("cycleway") == "crossing" or tags.get("footway") == "crossing"
            or tags.get("path") == "crossing")


# 저위계 도로 허용망(low-stress network): 자전거 시설 + 자전거로 이용 가능한
# OSM 소로. maxspeed/lanes 태그가 너무 희소(1~2%)해 highway 등급만으로 판정한다.
_LOW_STRESS_ALWAYS = {
    "cycleway", "residential", "living_street", "service", "unclassified",
    "track", "path", "pedestrian", "footway",
}
_LOW_STRESS_CONDITIONAL = {
    "tertiary", "tertiary_link", "secondary", "secondary_link",
    "primary", "primary_link",
}
# 한국은 자전거 통행이 기본적으로 허용되지 않는 보도 성격의 도로 -- bicycle
# 태그로 명시적으로 허용된 경우만 포함한다.
_SIDEWALK_LIKE = {"path", "pedestrian", "footway"}


def is_low_stress(tags: dict) -> bool:
    highway = tags.get("highway")

    if highway == "steps":
        return False
    if tags.get("bicycle") in {"no", "dismount"}:
        return False
    if tags.get("access") in {"no", "private"}:
        return False

    if highway in _SIDEWALK_LIKE:
        return tags.get("bicycle") in {"yes", "designated", "permissive"}
    if highway in _LOW_STRESS_ALWAYS:
        return True
    if highway in _LOW_STRESS_CONDITIONAL:
        return is_bike_facility(tags)
    return False
