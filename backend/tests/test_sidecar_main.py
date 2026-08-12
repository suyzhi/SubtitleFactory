import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import sidecar_main


class ParentWatchdogTests(unittest.TestCase):
    def test_parent_pipe_eof_triggers_exactly_one_termination(self):
        read_descriptor, write_descriptor = os.pipe()
        stream = os.fdopen(read_descriptor, "rb", buffering=0)
        terminated = threading.Event()
        calls: list[str] = []

        def terminate():
            calls.append("terminate")
            terminated.set()

        worker = threading.Thread(
            target=sidecar_main._watch_parent_pipe,
            args=(stream, terminate),
            daemon=True,
        )
        worker.start()
        try:
            self.assertFalse(terminated.wait(0.05))
        finally:
            os.close(write_descriptor)
        self.assertTrue(terminated.wait(1))
        worker.join(1)
        stream.close()
        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, ["terminate"])

    def test_standalone_sidecar_does_not_enable_parent_watchdog(self):
        with patch.dict(os.environ, {sidecar_main.PARENT_WATCHDOG_ENV: ""}):
            self.assertIsNone(sidecar_main._start_parent_watchdog())


if __name__ == "__main__":
    unittest.main()
