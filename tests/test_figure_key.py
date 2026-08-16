"""A sheet's figure identity decides whether duplication between two sheets is expected.

Two sheets sharing a key are treated as panels of one display item, which annotates the
overlap as "expected, not a cross-experiment reuse" and downgrades its severity. Getting
that identity wrong therefore does not merely mislabel a finding, it suppresses one.
"""
from __future__ import annotations

import pytest

from paperconan._audit import figure_key


@pytest.mark.parametrize("sheet", [
    "Supplemental Figure 2",
    "Suppl Figure 2",
    "Supplementary Figure 2",
    "Supplementary Fig. 2",
    "Fig S2",
    "FigS2b",
])
def test_a_supplementary_figure_is_not_the_main_figure_of_the_same_number(sheet: str) -> None:
    """These are different display items. Sharing data between them is not expected."""
    assert figure_key(sheet) != figure_key("Figure 2")


@pytest.mark.parametrize("sheet", [
    "Extended Data Figure 6",
    "Extended Data Fig. 6",
    "SourceData_ED_Fig6",
    "ED_Fig6f",
])
def test_an_extended_data_figure_is_not_the_main_figure_of_the_same_number(sheet: str) -> None:
    assert figure_key(sheet) != figure_key("Figure 6")


def test_panels_of_one_display_item_still_share_a_key() -> None:
    """The suppression this powers is correct when the sheets really are one item."""
    assert figure_key("Figure 5a") == figure_key("Figure 5b") == figure_key("Fig. 5")
    assert figure_key("Supplementary Figure 3a") == figure_key("Suppl Fig 3c")


def test_a_sheet_naming_no_figure_has_no_identity() -> None:
    assert figure_key("Sheet1") is None
    assert figure_key("") is None
