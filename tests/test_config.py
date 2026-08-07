#!/usr/bin/env python3
"""Configuration resolution and repo selection.

These are the rules every other component trusts without re-checking, so
they are tested at the level of "which file won and why" rather than
"does the parser parse".
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catnip.config import (  # noqa: E402
    Config,
    ConfigError,
    select_repos,
    slug_for,
)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text, name="catnip.conf"):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_keys_comments_quotes_and_export(self):
        path = self.write(
            "# a comment\n"
            "CATNIP_OWNER=octocat\n"
            "export CATNIP_OWNER_TYPE=org\n"
            'CATNIP_EXCLUDE="dotfiles *-private"\n'
            "CATNIP_RETAIN_DAYS=7   # trailing comment\n"
            "\n")
        cfg = Config.load(path, env={})
        self.assertEqual(cfg["CATNIP_OWNER"], "octocat")
        self.assertEqual(cfg["CATNIP_OWNER_TYPE"], "org")
        self.assertEqual(cfg.patterns("CATNIP_EXCLUDE"), ["dotfiles", "*-private"])
        self.assertEqual(cfg.integer("CATNIP_RETAIN_DAYS"), 7)

    def test_unknown_key_is_an_error_not_a_shrug(self):
        # A silently ignored CATNIP_EXCLUDE typo fetches hundreds of repos
        # and looks like catnip ignoring the config.
        path = self.write("CATNIP_EXCLUDES=oops\n")
        with self.assertRaises(ConfigError) as ctx:
            Config.load(path, env={})
        self.assertIn("CATNIP_EXCLUDES", str(ctx.exception))
        self.assertIn("Known keys", str(ctx.exception))

    def test_malformed_line_is_an_error(self):
        path = self.write("CATNIP_OWNER octocat\n")
        with self.assertRaises(ConfigError):
            Config.load(path, env={})

    def test_environment_beats_the_file(self):
        path = self.write("CATNIP_OWNER=fromfile\n")
        cfg = Config.load(path, env={"CATNIP_OWNER": "fromenv"})
        self.assertEqual(cfg["CATNIP_OWNER"], "fromenv")

    def test_named_but_missing_config_is_fatal(self):
        # Falling through to ./catnip.conf would run a different account's
        # collection and report success.
        with self.assertRaises(ConfigError):
            Config.load(self.root / "nope.conf", env={})

    def test_defaults_apply_to_unset_keys(self):
        cfg = Config.load(self.write("CATNIP_OWNER=octocat\n"), env={})
        self.assertEqual(cfg["CATNIP_OWNER_TYPE"], "auto")
        self.assertFalse(cfg.flag("CATNIP_INCLUDE_FORKS"))

    def test_bad_boolean_and_integer_are_reported(self):
        cfg = Config.load(self.write("CATNIP_INCLUDE_FORKS=maybe\nCATNIP_RETAIN_DAYS=lots\n"),
                          env={})
        with self.assertRaises(ConfigError):
            cfg.flag("CATNIP_INCLUDE_FORKS")
        with self.assertRaises(ConfigError):
            cfg.integer("CATNIP_RETAIN_DAYS")

    def test_paths_derive_from_the_data_dir(self):
        cfg = Config.load(self.write(f"CATNIP_DATA_DIR={self.root}/d\n"), env={})
        self.assertEqual(cfg.runs_dir, self.root / "d" / "runs")
        self.assertEqual(cfg.stats_file, self.root / "d" / "stats" / "totals.json")
        self.assertEqual(cfg.history_file,
                         self.root / "d" / "stats" / "history" / "traffic_daily.json")

    def test_latest_run_is_the_newest_stamp(self):
        cfg = Config.load(self.write(f"CATNIP_DATA_DIR={self.root}/d\n"), env={})
        for name in ("20260101T000000Z", "20260805T120000Z", "not-a-run", "20260301T000000Z"):
            (cfg.runs_dir / name).mkdir(parents=True)
        self.assertEqual(cfg.latest_run().name, "20260805T120000Z")


class SelectionTests(unittest.TestCase):
    def cfg(self, **overrides):
        values = {"CATNIP_INCLUDE": "", "CATNIP_EXCLUDE": "",
                  "CATNIP_INCLUDE_FORKS": "false", "CATNIP_INCLUDE_ARCHIVED": "true",
                  "CATNIP_INCLUDE_PRIVATE": "true"}
        values.update(overrides)
        return Config(values)

    def repos(self):
        return [
            {"name": "pane"},
            {"name": "catnip"},
            {"name": "someone-elses", "fork": True},
            {"name": "old-thing", "archived": True},
            {"name": "secret", "private": True},
        ]

    def test_forks_are_excluded_by_default_with_a_reason(self):
        selected, rejected = select_repos(self.repos(), self.cfg())
        self.assertEqual({r["name"] for r in selected},
                         {"pane", "catnip", "old-thing", "secret"})
        reasons = dict(rejected)
        self.assertIn("fork", reasons["someone-elses"])

    def test_include_patterns_are_exclusive(self):
        selected, rejected = select_repos(self.repos(), self.cfg(CATNIP_INCLUDE="cat*"))
        self.assertEqual([r["name"] for r in selected], ["catnip"])
        self.assertIn("no CATNIP_INCLUDE pattern matched", dict(rejected)["pane"])

    def test_exclude_beats_include(self):
        selected, _ = select_repos(
            self.repos(), self.cfg(CATNIP_INCLUDE="*a*", CATNIP_EXCLUDE="pane"))
        self.assertEqual([r["name"] for r in selected], ["catnip"])

    def test_archived_and_private_toggles(self):
        selected, _ = select_repos(self.repos(),
                                   self.cfg(CATNIP_INCLUDE_ARCHIVED="false",
                                            CATNIP_INCLUDE_PRIVATE="false"))
        self.assertEqual({r["name"] for r in selected}, {"pane", "catnip"})

    def test_every_repo_is_either_selected_or_explained(self):
        repos = self.repos()
        selected, rejected = select_repos(repos, self.cfg(CATNIP_INCLUDE="pane"))
        self.assertEqual(len(selected) + len(rejected), len(repos))
        self.assertTrue(all(why for _, why in rejected))


class SlugTests(unittest.TestCase):
    def test_slug_is_injective_across_dash_and_underscore(self):
        # Without doubling the dash, a-b and a_b collide and overwrite each
        # other's raw JSON — one repo's traffic silently becomes another's.
        self.assertNotEqual(slug_for("a-b"), slug_for("a_b"))

    def test_slug_matches_the_analyzer(self):
        from catnip.analyze import slug_for as analyzer_slug
        for name in ("a-b", "a_b", "dots.in.name", "weird name/slash", "UPPER-case"):
            self.assertEqual(slug_for(name), analyzer_slug(name), name)


class ShellOutputTests(unittest.TestCase):
    def test_select_cli_writes_filtered_json_and_reasons(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repos.json").write_text(json.dumps([
                {"name": "keep"}, {"name": "drop", "fork": True}]), encoding="utf-8")
            (root / "catnip.conf").write_text("CATNIP_OWNER=x\n", encoding="utf-8")
            out = subprocess.run(
                [sys.executable, "-m", "catnip.config",
                 "--config", str(root / "catnip.conf"),
                 "--select", str(root / "repos.json"),
                 "--rejects", str(root / "rejects.tsv"),
                 "--selected-json", str(root / "selected.json")],
                capture_output=True, text=True, check=True,
                env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                     "PATH": "/usr/bin:/bin"})
            self.assertEqual(out.stdout.strip(), "keep\tkeep")
            self.assertIn("drop", (root / "rejects.tsv").read_text())
            self.assertEqual(
                [r["name"] for r in json.loads((root / "selected.json").read_text())],
                ["keep"])


if __name__ == "__main__":
    unittest.main()


class DispatcherModeTests(unittest.TestCase):
    """`bin/catnip config` must not eat the mode flag the caller asked for.

    The dispatcher hardcoded `--show`, which is one of an argparse
    mutually-exclusive group, so `catnip config --json` errored out every
    single time — while being documented as the way an agent finds the
    store path. The command existed, dispatched, and could never work.

    test_report.py's DispatcherTests catches "advertised but no case arm";
    this catches "arm exists but mangles the arguments", which is the same
    defect one layer down.
    """

    def _run(self, *args, conf=None):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [str(root / "bin" / "catnip"), "config", *args],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(conf.parent),
                 "CATNIP_CONFIG": str(conf)})

    def test_every_mode_flag_survives_the_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "catnip.conf"
            conf.write_text(f"CATNIP_OWNER=x\nCATNIP_DATA_DIR={tmp}\n", encoding="utf-8")

            out = self._run("--json", conf=conf)
            self.assertEqual(out.returncode, 0, out.stderr)
            paths = json.loads(out.stdout)["paths"]
            for key in ("history_file", "runs_dir"):
                self.assertIn(key, paths)

            out = self._run("--shell", conf=conf)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("CATNIP_OWNER=", out.stdout)

            # No mode named: the human summary is still the default.
            out = self._run(conf=conf)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("owner", out.stdout)
