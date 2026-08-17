from pathlib import Path

import pandas as pd
import pytest

from utils.excel_handler import ExcelHandler


DATA_FILE = Path("data/Quotazioni_Fantacalcio_Stagione_2025_26.xlsx")


# ponytail: real-data check runs only where the workbook exists (data/ is gitignored);
# commit a season fixture if CI must cover the real spreadsheet too.
@pytest.mark.skipif(not DATA_FILE.exists(), reason="real data workbook not tracked in git")
def test_load_players_skips_title_row_and_reads_all_525_players():
    players = ExcelHandler(DATA_FILE).load_players()

    assert len(players) == 525
    assert {
        position: sum(player.position.value == position for player in players)
        for position in "PDCA"
    } == {"P": 65, "D": 184, "C": 178, "A": 98}
    assert len({player.id for player in players}) == 525


def test_validate_schema_rejects_duplicate_ids():
    frame = pd.DataFrame(
        {
            "Id": [1, 1],
            "R": ["P", "P"],
            "Nome": ["A", "B"],
            "Squadra": ["T", "T"],
            "Qt.A": [1, 1],
        }
    )

    with pytest.raises(ValueError, match="(?i)duplic"):
        ExcelHandler("unused.xlsx").validate_schema(frame)


def test_validate_schema_rejects_invalid_roles():
    frame = pd.DataFrame(
        {
            "Id": [1],
            "R": ["X"],
            "Nome": ["A"],
            "Squadra": ["T"],
            "Qt.A": [1],
        }
    )

    with pytest.raises(ValueError, match="role|ruol|position"):
        ExcelHandler("unused.xlsx").validate_schema(frame)
