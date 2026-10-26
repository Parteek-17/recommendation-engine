"""One tiny generated dataset shared by all data_gen tests (generating takes a few seconds)."""

import pytest

from data_gen.generate import generate
from data_gen.utils import write_ground_truth, write_tables


@pytest.fixture(scope="session")
def tiny():
    """(tables, ground_truth) for the 'tiny' size, in memory."""
    return generate("tiny", verbose=False)


@pytest.fixture(scope="session")
def tiny_folders(tiny, tmp_path_factory):
    """The tiny dataset written to disk: (raw_dir, ground_truth_dir)."""
    tables, truth = tiny
    root = tmp_path_factory.mktemp("tiny")
    raw, gt = root / "raw", root / "ground_truth"
    write_tables(tables, raw)
    write_ground_truth(truth["user_hidden_profile"], truth["product_relations"], gt)
    return raw, gt
