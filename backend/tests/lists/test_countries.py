"""Country normalization for list and subject data (ticket 0038)."""

import pytest

from app.lists.countries import to_iso2


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("Russia", "RU"),
        ("RUSSIAN FEDERATION", "RU"),
        ("Russia.", "RU"),
        ("Korea, North", "KP"),
        ("DPRK", "KP"),
        ("Democratic People's Republic of Korea", "KP"),
        ("Iran (Islamic Republic of)", "IR"),
        ("Syrian Arab Republic", "SY"),
        ("Congo, Democratic Republic of the", "CD"),
        ("Burma", "MM"),
        ("United Kingdom of Great Britain and Northern Ireland", "GB"),
        ("Türkiye", "TR"),
        ("ro", "RO"),
        ("Romania", "RO"),
        ("VIRGIN ISLANDS (BRITISH)", "VG"),
    ],
)
def test_known_spellings_map_to_iso2(value, code):
    assert to_iso2(value) == code


@pytest.mark.parametrize("value", [None, "", "Atlantis", "Region: Nowhere"])
def test_unknown_values_are_none(value):
    assert to_iso2(value) is None
