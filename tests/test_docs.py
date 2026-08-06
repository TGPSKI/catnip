#!/usr/bin/env python3
"""The documentation must describe the software that exists.

Prose drifts silently. `docs/metrics.md` claimed sixteen analysis CSVs
and a schema-1 history store for as long as it took to notice, the README
advertised two agent skills while three shipped, and both counted the
CSVs differently from each other and from a real run. None of it broke
anything, which is exactly why nothing caught it.

These tests pin only the claims a machine can check: counts, filenames,
view keys, skill directories. A sentence that is merely out of date will
still get past them — but a number that contradicts the code will not.
"""
import io
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import make_run  # noqa: E402

from catnip import doctor  # noqa: E402
from catnip.analyze import analyze_github  # noqa: E402
from catnip.ui import VIEWS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
METRICS = (ROOT / "docs" / "metrics.md").read_text()

WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
         8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
         15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
         19: "nineteen", 20: "twenty"}


class CsvCountTests(unittest.TestCase):
    """Both pages state a CSV count; a run is the arbiter of both."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        run = make_run(Path(cls._tmp.name))
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
        cls.produced = sorted(p.name for p in (run / "analysis").glob("*.csv"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_both_pages_agree_with_a_real_run(self):
        n = len(self.produced)
        for name, text in (("README.md", README), ("docs/metrics.md", METRICS)):
            self.assertIn(f"{n} CSVs", text,
                          f"{name} does not say {n} CSVs; a run produces {n}")

    def test_the_tui_required_set_is_counted_correctly(self):
        word = WORDS[len(doctor.TUI_CSVS)]
        self.assertIn(f"the {word} the TUI cannot open without", METRICS,
                      f"TUI_CSVS holds {len(doctor.TUI_CSVS)} files")

    def test_every_required_csv_is_documented(self):
        undocumented = [c for c in doctor.TUI_CSVS if c not in METRICS]
        self.assertEqual(undocumented, [],
                         f"required but absent from docs/metrics.md: {undocumented}")

    def test_no_documented_csv_has_been_removed(self):
        # The table is the contract downstream readers code against, so a
        # name that survives only in prose is worse than a missing one.
        claimed = set(re.findall(r"`(\w+\.csv)`", METRICS))
        gone = sorted(c for c in claimed if c not in self.produced)
        self.assertEqual(gone, [],
                         f"documented but no longer produced: {gone}")


class ReadmeTests(unittest.TestCase):
    def test_every_view_is_listed_with_its_key(self):
        # The table promises `1`-`0` opens a named view. A view added to
        # VIEWS without a row here is undiscoverable.
        keys = "1234567890"
        self.assertEqual(len(VIEWS), len(keys), "VIEWS no longer fits 1-0")
        for key, view in zip(keys, VIEWS):
            self.assertRegex(README, rf"\|\s*`{key}`\s*\|\s*{view}\s*\|",
                             f"README has no row for {key}:{view}")

    def test_the_view_count_is_stated_correctly(self):
        self.assertIn(f"opens {WORDS[len(VIEWS)]} views", README)

    def test_every_shipped_skill_is_named_and_counted(self):
        skills = sorted(p.name for p in (ROOT / ".agents" / "skills").iterdir()
                        if p.is_dir())
        self.assertIn(f"ships {WORDS[len(skills)]} skills", README,
                      f".agents/skills holds {len(skills)}: {skills}")
        for skill in skills:
            self.assertIn(f"`{skill}`", README, f"{skill} is undocumented")

    def test_every_skill_is_linked_for_claude_code(self):
        # .claude/skills/<name> symlinks are committed so a fresh clone needs
        # no setup. catnip-prowl shipped without one and was invisible to the
        # harness it was written for; `make link-agents` creates them.
        skills = {p.name for p in (ROOT / ".agents" / "skills").iterdir()
                  if p.is_dir()}
        links = {p.name for p in (ROOT / ".claude" / "skills").iterdir()}
        self.assertEqual(sorted(skills - links), [],
                         "unlinked skills — run `make link-agents`")

    def test_every_advertised_command_is_dispatched(self):
        # The commands table is a second place `catnip <cmd>` is promised;
        # test_report.py pins the usage text against the same case arms.
        script = (ROOT / "bin" / "catnip").read_text()
        arms = set()
        for m in re.finditer(r"^\s*([a-z|-]+)\)", script, re.M):
            arms.update(m.group(1).split("|"))
        advertised = re.findall(r"^\| `catnip ([a-z-]+)`", README, re.M)
        self.assertTrue(advertised, "the README commands table stopped parsing")
        missing = sorted(c for c in advertised if c not in arms)
        self.assertEqual(missing, [],
                         f"README advertises commands that never dispatch: {missing}")


if __name__ == "__main__":
    unittest.main()
