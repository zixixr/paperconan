from paperconan._audit import find_numeric_blocks
from paperconan._sheet import Sheet


def test_short_seed_column_does_not_hide_neighboring_valid_block():
    sheet = Sheet.from_rows([
        [1, 10],
        [2, 11],
        [None, 12],
    ])
    assert find_numeric_blocks(sheet) == [(0, 3, 1, 2)]
