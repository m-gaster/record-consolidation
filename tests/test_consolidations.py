from pathlib import Path

import polars as pl
import pytest
from regression_tester import RegressionTestPackage

from record_consolidation.df_consolidations import (
    consolidate_normalized_table,
    extract_normalized_atomic,
)


@pytest.fixture()
def MSFTS_AND_AMZNS() -> pl.DataFrame:
    return pl.read_parquet("test_data/intra_field/msfts_and_amzns.parquet")


@pytest.fixture()
def MSFTS() -> pl.DataFrame:
    return pl.read_parquet("test_data/intra_field/msfts_and_amzns.parquet").filter(
        pl.col("issuer_name") == pl.lit("MICROSOFT CORPORATION")
    )


@pytest.mark.parametrize(
    "depth",
    [
        ("intra_field"),
        ("intra_and_inter_field"),
    ],
)
def test_consolidation(MSFTS, depth) -> None:
    root_path = Path("test_data") / depth
    raw_input_path = root_path / "msfts_and_amzns.parquet"
    reg_tester = RegressionTestPackage(
        root_path=root_path,
        extraction_fnc=lambda _: consolidate_normalized_table(MSFTS, depth=depth)
        .unique()
        .sort(pl.all()),
        optional_raw_input_path=raw_input_path,  # have to put an extant path here
    )
    reg_tester.execute_regression_test()


def test_consolidation_via_normalized_atomizer(MSFTS) -> None:
    """
    Should produce the desired output of `consolidate_normalized_table(df, depth="intra_and_inter_field")`!
    """
    root_path = Path("test_data") / "intra_and_inter_field"
    raw_input_path = root_path / "msfts_and_amzns.parquet"
    reg_tester = RegressionTestPackage(
        root_path=root_path,
        extraction_fnc=lambda x: extract_normalized_atomic(MSFTS)
        .unique()
        .sort(pl.all()),
        optional_raw_input_path=raw_input_path,  # have to put an extant path here
    )
    reg_tester.execute_regression_test()
