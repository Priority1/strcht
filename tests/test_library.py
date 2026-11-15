import library

DESC = """River_Talk:
- Background music
- Three persons
- Native russian speech (between models, not loud)
- Not native english speech
- duration: 13min

Nova_Kitchen:
- Two persons
- Native german speech
- Not native english speech
- duration: 16min

Tokyo_0116:
- Japanese speech
- duration: 15min
"""


def test_slug():
    assert library.slug("River_Talk") == "river_talk"
    assert library.slug("My Video (1).final") == "my_video_1_final"
    assert library.slug("../..") == "stream"


def test_descriptions():
    d = library.parse_descriptions(DESC)
    assert list(d) == ["River_Talk", "Nova_Kitchen", "Tokyo_0116"]
    assert d["Tokyo_0116"] == ["Japanese speech", "duration: 15min"]
    assert library.languages_of(d["River_Talk"]) == ["ru", "en"]          # native first
    assert library.languages_of(d["Tokyo_0116"]) == ["ja"]
    assert library.languages_of([]) == []


def test_descriptions_find_their_file():
    d = library.parse_descriptions(DESC)
    m = library.match_descriptions(["river_talk", "nova_kitchin", "tokyo_0116", "someone_else"], d)
    assert set(m) == {"river_talk", "nova_kitchin", "tokyo_0116"}
    assert m["nova_kitchin"][1] == "Native german speech"


def test_codecs():
    v = {"codec_name": "h264", "profile": "High", "level": 40}
    assert library.codecs_of(v, "aac") == "avc1.640028,mp4a.40.2"
    assert library.codecs_of({"codec_name": "h264", "profile": "Main", "level": 13}, None) == "avc1.4d400d"
    assert library.codecs_of({"codec_name": "h264", "profile": "Baseline", "level": 32}, "aac") == "avc1.420020,mp4a.40.2"
    assert library.codecs_of({"codec_name": "hevc"}, "aac") == ""        # unknown: say nothing rather than guess
