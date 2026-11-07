"""The job table must stay readable while a job runs.

The page polls /api/jobs once a second to draw the progress bar. If the worker
holds JOBS_LOCK for the whole encode, that poll waits behind ffmpeg and the
dialog sits on "Waiting for the engine" until the run ends. This test holds a
job open and asks for the lock the way /api/jobs does.
"""

import importlib.util
import os
import sys
import threading
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The queue does not touch audio, and mutagen is only present inside the frozen
# app on a machine that has never installed it. A stub keeps the test runnable
# from a bare checkout with the system Python.
try:
    import mutagen  # noqa: F401
except ImportError:
    mutagen = types.ModuleType("mutagen")
    mutagen.File = lambda *a, **k: None
    sys.modules["mutagen"] = mutagen

SPEC = importlib.util.spec_from_file_location(
    "bookbind_app", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


def plan():
    return {
        "out": "/tmp/bookbind-test.m4b",
        "out_name": "bookbind-test.m4b",
        "folder": "/tmp",
        "files": [],
        "n": 0,
        "n_chapters": 0,
        "total_secs": 1,
        "est_bytes": 0,
    }


class JobQueueTest(unittest.TestCase):
    def test_the_job_table_is_readable_while_a_job_runs(self):
        started = threading.Event()
        release = threading.Event()
        original = app.run_job

        def slow_job(job):
            started.set()
            release.wait(5)

        app.run_job = slow_job
        try:
            job = app.Job(plan())
            app.QUEUE.put(job)
            self.assertTrue(started.wait(3), "no worker picked the job up")

            got = app.JOBS_LOCK.acquire(timeout=1.0)
            try:
                self.assertTrue(
                    got, "the jobs lock was held for the whole run")
                self.assertEqual(app.jsonable(job)["state"], "queued")
            finally:
                if got:
                    app.JOBS_LOCK.release()
        finally:
            release.set()
            app.run_job = original


if __name__ == "__main__":
    unittest.main()
