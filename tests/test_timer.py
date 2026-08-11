#!/usr/bin/env python3
"""systemd unit rendering.

No systemd required: rendering is pure text substitution, and the two
properties that matter — the config path is pinned into the unit, and
Persistent=true survives a sleeping machine — are checkable as strings.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import write_config  # noqa: E402

from catnip import timer  # noqa: E402
from catnip.config import Config  # noqa: E402


class RenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def render(self, **overrides):
        path = write_config(self.root, **overrides)
        return timer.render(Config.load(path, env={}), str(path)), path

    def test_service_points_at_a_real_executable(self):
        units, _ = self.render()
        exec_line = next(ln for ln in units["catnip.service"].splitlines()
                         if ln.startswith("ExecStart="))
        binary = Path(exec_line.split("=", 1)[1].split()[0])
        self.assertTrue(binary.is_file(), f"{binary} does not exist")
        self.assertTrue(exec_line.endswith("run --quiet"))

    def test_config_path_is_pinned_into_the_unit(self):
        # Without this the timer resolves config from its working
        # directory and can silently collect a different account than the
        # one you configured interactively.
        units, path = self.render()
        self.assertIn(f"Environment=CATNIP_CONFIG={path}", units["catnip.service"])

    def test_no_dangling_placeholder_when_there_is_no_config_file(self):
        cfg = Config.load(write_config(self.root), env={})
        cfg.source = None
        units = timer.render(cfg, config_file=None)
        self.assertNotIn("@CATNIP_CONFIG_ENV@", units["catnip.service"])
        self.assertNotIn("Environment=CATNIP_CONFIG=", units["catnip.service"])

    def test_timer_is_persistent_and_uses_the_configured_cadence(self):
        units, _ = self.render(CATNIP_TIMER_ONCALENDAR="*-*-* 04:00:00",
                               CATNIP_TIMER_RANDOM_DELAY="30m")
        unit = units["catnip.timer"]
        self.assertIn("OnCalendar=*-*-* 04:00:00", unit)
        self.assertIn("RandomizedDelaySec=30m", unit)
        # A missed run must fire on wake: GitHub does not backfill traffic.
        self.assertIn("Persistent=true", unit)

    def test_every_placeholder_is_substituted(self):
        units, _ = self.render()
        for name, text in units.items():
            self.assertNotIn("@CATNIP", text, f"{name} still has a placeholder")

    def test_the_default_cadence_is_six_hourly_with_jitter(self):
        # GitHub keeps revising a day for CATNIP_SETTLE_HOURS after it
        # closes. A daily read places a revision no more precisely than
        # "somewhere in the last 24 hours"; four reads a day bound it to
        # six, and the jitter keeps the account off everyone else's
        # top-of-the-hour.
        unit = self.render()[0]["catnip.timer"]
        self.assertIn("OnCalendar=*-*-* 00/6:00:00", unit)
        self.assertIn("RandomizedDelaySec=40m", unit)

    def test_cron_line_carries_the_config_and_binary(self):
        path = write_config(self.root)
        line = timer.cron_line(Config.load(path, env={}), str(path))
        self.assertIn(f"CATNIP_CONFIG={path}", line)
        self.assertIn("run --quiet", line)

    def test_the_cron_line_runs_six_hourly_and_jitters_itself(self):
        # cron has no RandomizedDelaySec, so the line sleeps first.
        path = write_config(self.root)
        line = timer.cron_line(Config.load(path, env={}), str(path))
        self.assertIn("0 */6 * * *", line)
        self.assertIn(timer.CRON_JITTER, line)

    def test_the_cron_jitter_runs_under_sh_not_only_bash(self):
        # $RANDOM is a bashism. crontab runs the command under /bin/sh,
        # where it expands to nothing and the sleep becomes a syntax error
        # — every six hours, in a log nobody reads.
        command = timer.CRON_JITTER.replace("sleep ", "echo ", 1)
        out = subprocess.run(["/bin/sh", "-c", command], capture_output=True,
                             text=True, check=True).stdout.strip()
        self.assertTrue(out.isdigit(), out)
        self.assertLess(int(out), timer.CRON_JITTER_SECONDS)


if __name__ == "__main__":
    unittest.main()
