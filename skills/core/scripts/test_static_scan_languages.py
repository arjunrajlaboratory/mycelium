"""Static I/O scanning of R sources, and comments in R and Python sources.

The static scan is the inferred second source next to observed I/O. A call
inside a comment is not evidence of a read or a write, and R readers and
writers are recognized only in call form with a literal path argument.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_data_lineage_event import scan_source  # noqa: E402

HELPER = Path(__file__).resolve().parent / "extract_data_lineage_event.py"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('df <- read.csv("data/raw/a.csv")', "data/raw/a.csv"),
        ("df <- read.csv(file = 'data/raw/a.csv', header = TRUE)", "data/raw/a.csv"),
        ('df <- utils::read.table("counts.tsv", sep = "\\t")', "counts.tsv"),
        ('df <- read.delim("counts.tsv")', "counts.tsv"),
        ('df <- readr::read_csv("data/b.csv")', "data/b.csv"),
        ('df <- read_tsv("data/b.tsv", col_types = cols())', "data/b.tsv"),
        ('dt <- data.table::fread("big.tsv.gz")', "big.tsv.gz"),
        ('dt <- fread(file = "big.tsv")', "big.tsv"),
        ('obj <- readRDS("fit.rds")', "fit.rds"),
        ('load("session.RData")', "session.RData"),
        ('x <- readxl::read_excel("sheet.xlsx", sheet = 2)', "sheet.xlsx"),
        ('m <- Seurat::Read10X(data.dir = "filtered_feature_bc_matrix")', "filtered_feature_bc_matrix"),
        ('m <- Read10X_h5("sample.h5")', "sample.h5"),
        ('m <- Matrix::readMM("matrix.mtx")', "matrix.mtx"),
        ('t <- arrow::read_parquet("cells.parquet")', "cells.parquet"),
        ('v <- vroom::vroom("wide.csv")', "wide.csv"),
        ('h <- rhdf5::h5read("store.h5", "counts")', "store.h5"),
    ],
)
def test_r_reader_calls_are_inputs(source: str, expected: str) -> None:
    inputs, outputs, _, _ = scan_source(source, language="r")
    assert inputs == [expected]
    assert outputs == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('write.csv(df, "out/a.csv", row.names = FALSE)', "out/a.csv"),
        ('utils::write.table(res$tab, file = "out/t.tsv", sep = "\\t")', "out/t.tsv"),
        ('write.csv(df[df$x > 1, ], file = "out/f.csv")', "out/f.csv"),
        ('readr::write_csv(df, "out/b.csv")', "out/b.csv"),
        ('write_tsv(df, file = "out/b.tsv")', "out/b.tsv"),
        ('data.table::fwrite(dt, "out/c.tsv.gz")', "out/c.tsv.gz"),
        ('saveRDS(fit, "out/fit.rds")', "out/fit.rds"),
        ('saveRDS(object = fit, file = "out/fit2.rds")', "out/fit2.rds"),
        ('save(a, b, file = "out/s.RData")', "out/s.RData"),
        ('ggsave("figs/p.png", p, width = 4)', "figs/p.png"),
        ('ggplot2::ggsave(filename = "figs/q.pdf", plot = p)', "figs/q.pdf"),
        ('pdf("figs/r.pdf", width = 5)', "figs/r.pdf"),
        ('grDevices::png(filename = "figs/s.png")', "figs/s.png"),
        ('arrow::write_parquet(tab, "out/cells.parquet")', "out/cells.parquet"),
    ],
)
def test_r_writer_calls_are_outputs(source: str, expected: str) -> None:
    inputs, outputs, _, _ = scan_source(source, language="r")
    assert outputs == [expected]
    assert inputs == []


@pytest.mark.parametrize(
    "source",
    [
        # A commented-out call is not a read.
        '# df <- read.csv("old.csv")',
        'x <- 1  # was: saveRDS(fit, "old.rds")',
        # A string never passed to a reader.
        'label <- "read.csv(\\"ghost.csv\\")"',
        "message('see readRDS(\"ghost.rds\") for details')",
        # Defining a function with a reader's name is not a call.
        'read.csv <- function(path) NULL',
        # A dynamic path is not a literal path.
        'df <- read.csv(file.path(root, "a.csv"))',
        'df <- read.csv(paste0(dir, "/a.csv"))',
        # A method of another object is not the base reader.
        'df <- obj$read.csv("a.csv")',
        'df <- my.read.csv("a.csv")',
        # A writer whose first argument is not a simple object stays unresolved.
        'write.csv(f(df, "x"), "out.csv")',
    ],
)
def test_r_near_neighbours_claim_nothing(source: str) -> None:
    inputs, outputs, _, _ = scan_source(source, language="r")
    assert inputs == []
    assert outputs == []


def test_r_comment_marker_inside_string_is_not_a_comment() -> None:
    source = 'df <- read.csv("data/#1.csv"); saveRDS(df, "out/one.rds")'
    inputs, outputs, _, _ = scan_source(source, language="r")
    assert inputs == ["data/#1.csv"]
    assert outputs == ["out/one.rds"]


def test_r_raw_string_hides_its_contents() -> None:
    source = 'pat <- r"(read.csv("ghost.csv") # )"\ndf <- read.csv("real.csv")'
    inputs, _, _, _ = scan_source(source, language="r")
    assert inputs == ["real.csv"]


def test_r_unterminated_string_falls_back_to_unstripped_scan() -> None:
    # A lexer that cannot finish must not hide a real call.
    source = 'df <- read.csv("a.csv")\nbroken <- "never closed'
    inputs, _, _, _ = scan_source(source, language="r")
    assert inputs == ["a.csv"]


def test_python_commented_reader_is_not_an_input() -> None:
    source = (
        "import pandas as pd\n"
        "# df = pd.read_csv('old.csv')\n"
        "df = pd.read_csv('new.csv')  # not pd.read_csv('older.csv')\n"
    )
    inputs, _, _, _ = scan_source(source, language="python")
    assert inputs == ["new.csv"]


def test_python_hash_inside_string_is_kept() -> None:
    source = "import pandas as pd\ndf = pd.read_csv('data/#2.csv')\n"
    inputs, _, _, _ = scan_source(source, language="python")
    assert inputs == ["data/#2.csv"]


def test_python_untokenizable_source_falls_back_to_unstripped_scan() -> None:
    source = "df = pd.read_csv('a.csv')\nif (:\n"
    inputs, _, _, _ = scan_source(source, language="python")
    assert inputs == ["a.csv"]


def test_unknown_language_keeps_previous_behaviour() -> None:
    # No language: nothing is stripped, both pattern sets apply.
    source = "# pd.read_csv('c.csv')\n"
    inputs, _, _, _ = scan_source(source)
    assert inputs == ["c.csv"]


def test_r_script_event_records_r_io(tmp_path: Path) -> None:
    script = tmp_path / "fit.R"
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "in.csv").write_text("a\n1\n")
    script.write_text(
        '# df <- read.csv("data/old.csv")\n'
        'df <- read.csv("data/in.csv")\n'
        'saveRDS(df, "fit.rds")\n'
    )
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-11-21T00:00:00Z",
            "--bash-cmd",
            "Rscript fit.R",
            "--bash-exit",
            "0",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    event = json.loads(result.stdout)
    assert [Path(r["path"]).name for r in event["inputs"]] == ["in.csv"]
    assert [Path(r["path"]).name for r in event["outputs"]] == ["fit.rds"]
    assert event["io_detection"] == "static"
