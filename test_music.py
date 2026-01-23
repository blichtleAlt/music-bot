"""Unit tests for music.py radio functionality."""

import json
import os
import tempfile
import pytest

from music import (
    is_likely_song,
    build_radio_query,
    normalize_title,
    load_stations,
    save_stations,
    STATIONS_FILE,
    ENERGY_MODIFIERS,
)


class TestIsLikelySong:
    """Tests for the is_likely_song filter function."""

    def test_normal_song_title(self):
        """Normal song titles should pass."""
        assert is_likely_song("Drake - God's Plan", 200) is True
        assert is_likely_song("Kendrick Lamar - HUMBLE.", 180) is True
        assert is_likely_song("The Weeknd - Blinding Lights (Official Audio)", 240) is True

    def test_filters_interviews(self):
        """Interviews should be filtered out."""
        assert is_likely_song("Drake Interview 2024", 600) is False
        assert is_likely_song("Kendrick Lamar Full Interview with Zane Lowe", 3600) is False

    def test_filters_podcasts(self):
        """Podcasts should be filtered out."""
        assert is_likely_song("Joe Rogan Podcast #1234", 7200) is False
        assert is_likely_song("Music Podcast Episode 50", 3600) is False

    def test_filters_reactions(self):
        """Reaction videos should be filtered out."""
        assert is_likely_song("First Time Hearing Drake - God's Plan REACTION", 600) is False
        assert is_likely_song("REACTION to Kendrick Lamar new album", 900) is False

    def test_filters_full_albums(self):
        """Full album uploads should be filtered out."""
        assert is_likely_song("Drake - Scorpion Full Album", 5400) is False
        assert is_likely_song("Complete Album - The Weeknd After Hours", 3600) is False

    def test_filters_compilations(self):
        """Compilations and mixes should be filtered out."""
        assert is_likely_song("Hip Hop Mix 2024", 3600) is False
        assert is_likely_song("Best of Drake Compilation", 7200) is False
        assert is_likely_song("Lo-fi Beats Nonstop", 7200) is False
        assert is_likely_song("Party Megamix 2024", 3600) is False

    def test_filters_hour_long_content(self):
        """Hour-long content should be filtered out."""
        assert is_likely_song("1 Hour of Relaxing Music", 3600) is False
        assert is_likely_song("2 Hour Study Playlist", 7200) is False
        assert is_likely_song("3 Hour Sleep Music", 10800) is False

    def test_filters_tutorials(self):
        """Tutorials and lessons should be filtered out."""
        assert is_likely_song("How to Play God's Plan on Guitar Tutorial", 600) is False
        assert is_likely_song("Piano Lesson - Blinding Lights", 900) is False

    def test_filters_by_duration_too_short(self):
        """Songs under 90 seconds should be filtered."""
        assert is_likely_song("Drake - Intro", 60) is False
        assert is_likely_song("Short Track", 30) is False

    def test_filters_by_duration_too_long(self):
        """Songs over 10 minutes should be filtered."""
        assert is_likely_song("Epic Track Extended", 900) is False
        assert is_likely_song("Long Song", 1200) is False

    def test_duration_edge_cases(self):
        """Test duration boundary conditions."""
        # Exactly at min threshold (90 seconds) - should pass
        assert is_likely_song("Normal Song", 90) is True
        # Exactly at max threshold (600 seconds) - should pass
        assert is_likely_song("Normal Song", 600) is True
        # Just under min
        assert is_likely_song("Too Short", 89) is False
        # Just over max
        assert is_likely_song("Too Long", 601) is False

    def test_zero_duration_passes(self):
        """Zero duration (unknown) should pass the duration check."""
        assert is_likely_song("Unknown Duration Song", 0) is True

    def test_case_insensitive(self):
        """Filter patterns should be case insensitive."""
        assert is_likely_song("INTERVIEW with Drake", 600) is False
        assert is_likely_song("Podcast EPISODE", 3600) is False
        assert is_likely_song("REACTION Video", 600) is False

    def test_filters_livestreams(self):
        """Livestreams should be filtered out."""
        assert is_likely_song("Live Stream Concert 2024", 7200) is False
        assert is_likely_song("Livestream DJ Set", 3600) is False

    def test_filters_documentaries(self):
        """Documentaries and behind the scenes should be filtered."""
        assert is_likely_song("Making of God's Plan Documentary", 1800) is False
        assert is_likely_song("Behind the Scenes - Music Video", 600) is False

    def test_filters_playlists(self):
        """Playlist videos should be filtered."""
        assert is_likely_song("My Playlist 2024", 3600) is False
        assert is_likely_song("Chill Vibes Playlist", 7200) is False


class TestBuildRadioQuery:
    """Tests for the build_radio_query function."""

    def test_neutral_energy(self):
        """Neutral energy (0) should not add modifiers."""
        query = build_radio_query("jazz piano", 0)
        assert query == "jazz piano"

    def test_positive_energy(self):
        """Positive energy should add upbeat modifiers."""
        query = build_radio_query("hip hop", 1)
        assert "hip hop" in query
        assert "upbeat" in query or "energetic" in query

        query = build_radio_query("rock", 2)
        assert "rock" in query
        assert "hype" in query or "intense" in query

    def test_negative_energy(self):
        """Negative energy should add chill modifiers."""
        query = build_radio_query("jazz", -1)
        assert "jazz" in query
        assert "chill" in query or "mellow" in query

        query = build_radio_query("ambient", -2)
        assert "ambient" in query
        assert "slow" in query or "calm" in query

    def test_all_energy_levels(self):
        """All energy levels should produce valid queries."""
        for energy in range(-2, 3):
            query = build_radio_query("test", energy)
            assert "test" in query
            assert isinstance(query, str)
            assert len(query) > 0

    def test_strips_whitespace(self):
        """Query should be properly stripped."""
        query = build_radio_query("  jazz  ", 0)
        assert query == "jazz"

    def test_energy_modifiers_exist(self):
        """All energy levels should have modifiers defined."""
        for energy in range(-2, 3):
            assert energy in ENERGY_MODIFIERS


class TestNormalizeTitle:
    """Tests for the normalize_title function."""

    def test_removes_official_video(self):
        """Should remove (Official Video) variations."""
        assert "official" not in normalize_title("Song (Official Video)")
        assert "official" not in normalize_title("Song (Official Music Video)")
        assert "official" not in normalize_title("Song [Official Video]")

    def test_removes_official_audio(self):
        """Should remove (Official Audio) variations."""
        assert "official" not in normalize_title("Song (Official Audio)")
        assert "official" not in normalize_title("Song [Official Audio]")

    def test_removes_lyrics(self):
        """Should remove lyrics indicators."""
        assert "lyric" not in normalize_title("Song (Lyrics)")
        assert "lyric" not in normalize_title("Song (Lyric Video)")
        assert "lyric" not in normalize_title("Song [Lyrics]")

    def test_removes_quality_indicators(self):
        """Should remove HD/HQ/4K indicators."""
        assert "hd" not in normalize_title("Song (HD)")
        assert "hq" not in normalize_title("Song (HQ)")
        assert "4k" not in normalize_title("Song (4K)")

    def test_removes_after_pipe(self):
        """Should remove everything after |."""
        result = normalize_title("Song Title | Artist Name")
        assert "artist" not in result
        assert "song title" in result

    def test_removes_topic_suffix(self):
        """Should remove YouTube auto-generated ' - Topic' suffix."""
        result = normalize_title("Artist Name - Topic")
        assert "topic" not in result

    def test_lowercases(self):
        """Should lowercase the result."""
        result = normalize_title("UPPERCASE SONG")
        assert result == result.lower()

    def test_collapses_whitespace(self):
        """Should collapse multiple spaces."""
        result = normalize_title("Song    with    spaces")
        assert "  " not in result

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace."""
        result = normalize_title("  Song Title  ")
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_complex_title(self):
        """Should handle complex titles with multiple patterns."""
        result = normalize_title("Artist - Song (Official Music Video) (HD) [Lyrics]")
        assert "official" not in result
        assert "hd" not in result
        assert "lyrics" not in result


class TestStationPersistence:
    """Tests for station save/load functions."""

    def setup_method(self):
        """Create a temporary file for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.original_stations_file = STATIONS_FILE
        # We need to patch the module-level constant
        import music
        self.temp_file = os.path.join(self.temp_dir, "test_stations.json")
        music.STATIONS_FILE = self.temp_file

    def teardown_method(self):
        """Clean up temporary files."""
        import music
        music.STATIONS_FILE = self.original_stations_file
        if os.path.exists(self.temp_file):
            os.remove(self.temp_file)
        os.rmdir(self.temp_dir)

    def test_load_empty_when_no_file(self):
        """Should return empty dict when file doesn't exist."""
        result = load_stations()
        assert result == {}

    def test_save_and_load(self):
        """Should save and load stations correctly."""
        test_data = {
            "123456": {
                "chill": {"description": "lo-fi beats", "energy": -1},
                "hype": {"description": "workout music", "energy": 2},
            }
        }
        save_stations(test_data)
        loaded = load_stations()
        assert loaded == test_data

    def test_save_overwrites(self):
        """Should overwrite existing data."""
        save_stations({"old": "data"})
        save_stations({"new": "data"})
        loaded = load_stations()
        assert loaded == {"new": "data"}

    def test_handles_invalid_json(self):
        """Should return empty dict on invalid JSON."""
        import music
        with open(music.STATIONS_FILE, "w") as f:
            f.write("not valid json {{{")
        result = load_stations()
        assert result == {}

    def test_nested_station_structure(self):
        """Should handle the expected nested structure."""
        test_data = {
            "guild_1": {
                "station_a": {"description": "jazz cafe", "energy": 0},
            },
            "guild_2": {
                "station_b": {"description": "rock classics", "energy": 1},
            },
        }
        save_stations(test_data)
        loaded = load_stations()

        assert "guild_1" in loaded
        assert "station_a" in loaded["guild_1"]
        assert loaded["guild_1"]["station_a"]["description"] == "jazz cafe"
        assert loaded["guild_2"]["station_b"]["energy"] == 1


class TestIntegration:
    """Integration tests for radio helper functions working together."""

    def test_normalized_title_dedup(self):
        """Same song with different suffixes should normalize to same value."""
        titles = [
            "Artist - Song (Official Video)",
            "Artist - Song (Official Audio)",
            "Artist - Song [Official Music Video]",
            "Artist - Song (Lyrics)",
            "Artist - Song (HD)",
        ]
        normalized = [normalize_title(t) for t in titles]
        # All should normalize to the same base
        assert len(set(normalized)) == 1

    def test_filter_and_query_workflow(self):
        """Test typical workflow of building query and filtering results."""
        # Build a query
        query = build_radio_query("indie rock", 1)
        assert "indie rock" in query

        # Simulate filtering results
        mock_results = [
            ("Indie Band - Great Song", 200),  # Good
            ("Indie Rock Interview 2024", 3600),  # Bad - interview
            ("Another Indie Track", 180),  # Good
            ("Indie Rock Full Album", 5400),  # Bad - full album
            ("Short Clip", 30),  # Bad - too short
        ]

        filtered = [
            (title, dur) for title, dur in mock_results
            if is_likely_song(title, dur)
        ]

        assert len(filtered) == 2
        assert filtered[0][0] == "Indie Band - Great Song"
        assert filtered[1][0] == "Another Indie Track"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
