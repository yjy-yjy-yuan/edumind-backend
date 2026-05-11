from app.utils.subtitle_io import (
    read_subtitle_file_with_fallback,
    repair_mojibake_text,
    srt_to_vtt,
)


def test_read_subtitle_file_prefers_gbk_over_false_utf16(tmp_path):
    subtitle = "1\n00:00:00,000 --> 00:00:02,000\n老师讲解平行四边形\n"
    path = tmp_path / "gbk.srt"
    path.write_bytes(subtitle.encode("gbk"))

    decoded = read_subtitle_file_with_fallback(str(path))

    assert "老师讲解平行四边形" in decoded
    assert "\ufffd" not in decoded
    assert "㨰" not in decoded


def test_read_subtitle_file_supports_utf16_with_bom(tmp_path):
    subtitle = "1\n00:00:00,000 --> 00:00:02,000\n第一句字幕\n"
    path = tmp_path / "utf16.srt"
    path.write_bytes(subtitle.encode("utf-16"))

    decoded = read_subtitle_file_with_fallback(str(path))

    assert "第一句字幕" in decoded


def test_repair_mojibake_text_handles_utf8_decoded_as_latin1():
    mojibake = "ä¸­æå­å¹"

    assert repair_mojibake_text(mojibake) == "中文字幕"


def test_repair_mojibake_text_handles_common_utf8_decoded_as_gbk():
    mojibake = "鑰佸笀璁茶В骞宠屽洓杈瑰舰"

    assert repair_mojibake_text(mojibake) == "老师讲解平行四边形"


def test_srt_to_vtt_preserves_repaired_chinese_text(tmp_path):
    subtitle = "1\n00:00:00,000 --> 00:00:02,000\n中文字幕\n"
    path = tmp_path / "cn.srt"
    path.write_bytes(subtitle.encode("gb18030"))

    vtt = srt_to_vtt(read_subtitle_file_with_fallback(str(path)))

    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.000" in vtt
    assert "中文字幕" in vtt
