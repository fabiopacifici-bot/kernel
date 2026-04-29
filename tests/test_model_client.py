"""
test_model_client.py — Unit tests for model_client.py

Tests use mocked sockets — no model, no hardware required.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

# Make src importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import model_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_socket(response_lines: list):
    """Return a mock socket that yields the given JSON response lines on recv."""
    encoded = b"".join((json.dumps(line) + "\n").encode() for line in response_lines)

    mock_sock = MagicMock()
    # Simulate recv: first call returns all data, second returns b"" (EOF)
    mock_sock.recv.side_effect = [encoded, b""]
    mock_sock.settimeout = MagicMock()
    mock_sock.connect = MagicMock()
    mock_sock.sendall = MagicMock()
    mock_sock.close = MagicMock()
    return mock_sock


# ---------------------------------------------------------------------------
# is_server_running tests
# ---------------------------------------------------------------------------

class TestIsServerRunning(unittest.TestCase):

    def test_is_server_running_false_when_no_socket(self):
        """No socket file at all → False."""
        with patch("model_client._get_socket_path", return_value="/tmp/nonexistent_kernel_test.sock"):
            # Ensure file doesn't exist
            path = "/tmp/nonexistent_kernel_test.sock"
            if os.path.exists(path):
                os.unlink(path)
            result = model_client.is_server_running()
        self.assertFalse(result)

    def test_is_server_running_false_when_socket_unconnectable(self):
        """Socket file exists but connection refused → False."""
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            sock_path = f.name

        try:
            with patch("model_client._get_socket_path", return_value=sock_path):
                # File exists but is not a real listening socket
                result = model_client.is_server_running()
            self.assertFalse(result)
        finally:
            if os.path.exists(sock_path):
                os.unlink(sock_path)

    def test_is_server_running_true_when_server_accepts(self):
        """A listening Unix socket → True."""
        sock_path = f"/tmp/test_kernel_mock_{os.getpid()}.sock"
        if os.path.exists(sock_path):
            os.unlink(sock_path)

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)

        def _accept_and_close():
            try:
                conn, _ = server.accept()
                conn.close()
            except Exception:
                pass

        t = threading.Thread(target=_accept_and_close, daemon=True)
        t.start()

        try:
            with patch("model_client._get_socket_path", return_value=sock_path):
                result = model_client.is_server_running()
            self.assertTrue(result)
        finally:
            server.close()
            if os.path.exists(sock_path):
                os.unlink(sock_path)
            t.join(timeout=2)


# ---------------------------------------------------------------------------
# infer() tests
# ---------------------------------------------------------------------------

class TestInfer(unittest.TestCase):

    def test_model_client_sends_correct_json(self):
        """infer() sends correctly structured JSON-RPC request."""
        response_lines = [{"result": "Hello!"}]
        mock_sock = _make_mock_socket(response_lines)

        with patch("socket.socket", return_value=mock_sock), \
             patch("model_client._get_socket_path", return_value="/tmp/fake.sock"):
            result = model_client.infer(
                messages=[{"role": "user", "content": "Hi"}],
                max_new_tokens=256,
            )

        # Verify the payload sent matches schema
        sent_payload = mock_sock.sendall.call_args[0][0].decode("utf-8").strip()
        request = json.loads(sent_payload)

        self.assertEqual(request["method"], "infer")
        self.assertIn("messages", request["params"])
        self.assertEqual(request["params"]["max_new_tokens"], 256)
        self.assertEqual(request["params"]["messages"][0]["role"], "user")

    def test_model_client_parses_result(self):
        """infer() correctly returns the result string from server response."""
        response_lines = [{"result": "The answer is 42."}]
        mock_sock = _make_mock_socket(response_lines)

        with patch("socket.socket", return_value=mock_sock), \
             patch("model_client._get_socket_path", return_value="/tmp/fake.sock"):
            result = model_client.infer(
                messages=[{"role": "user", "content": "What is the answer?"}]
            )

        self.assertEqual(result, "The answer is 42.")

    def test_model_client_timeout_raises(self):
        """Timeout during inference is handled gracefully — returns error string, doesn't crash."""
        mock_sock = MagicMock()
        mock_sock.settimeout = MagicMock()
        mock_sock.connect = MagicMock()
        mock_sock.sendall = MagicMock()
        mock_sock.recv.side_effect = socket.timeout("timed out")
        mock_sock.close = MagicMock()

        with patch("socket.socket", return_value=mock_sock), \
             patch("model_client._get_socket_path", return_value="/tmp/fake.sock"):
            result = model_client.infer(
                messages=[{"role": "user", "content": "Hello"}]
            )

        # Should not raise; should return a string containing "timeout" or "error"
        self.assertIsInstance(result, str)
        self.assertTrue(
            "timeout" in result.lower() or "error" in result.lower(),
            f"Expected timeout/error message, got: {result!r}"
        )

    def test_model_client_server_error_in_response(self):
        """Server-side error in response → returns error string, doesn't crash."""
        response_lines = [{"error": "CUDA OOM"}]
        mock_sock = _make_mock_socket(response_lines)

        with patch("socket.socket", return_value=mock_sock), \
             patch("model_client._get_socket_path", return_value="/tmp/fake.sock"):
            result = model_client.infer(messages=[{"role": "user", "content": "Hi"}])

        self.assertIn("CUDA OOM", result)


# ---------------------------------------------------------------------------
# infer_with_tools() tests
# ---------------------------------------------------------------------------

class TestInferWithTools(unittest.TestCase):

    def test_step_callback_called_for_each_step(self):
        """infer_with_tools() calls step_callback for each step line."""
        response_lines = [
            {"type": "step", "step": 1, "tool": "exec_shell", "args": {"command": "ls"}, "result": "file.txt"},
            {"type": "step", "step": 2, "tool": "read_file", "args": {"path": "file.txt"}, "result": "content"},
            {"type": "result", "result": "Done."},
        ]
        mock_sock = _make_mock_socket(response_lines)
        steps = []

        def callback(step, tool, args, result):
            steps.append((step, tool))

        with patch("socket.socket", return_value=mock_sock), \
             patch("model_client._get_socket_path", return_value="/tmp/fake.sock"):
            result = model_client.infer_with_tools(
                messages=[{"role": "user", "content": "do it"}],
                tools=[],
                step_callback=callback,
            )

        self.assertEqual(result, "Done.")
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0], (1, "exec_shell"))
        self.assertEqual(steps[1], (2, "read_file"))

    def test_infer_with_tools_no_steps(self):
        """infer_with_tools() works with a direct result (no steps)."""
        response_lines = [{"type": "result", "result": "Direct answer."}]
        mock_sock = _make_mock_socket(response_lines)

        with patch("socket.socket", return_value=mock_sock), \
             patch("model_client._get_socket_path", return_value="/tmp/fake.sock"):
            result = model_client.infer_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )

        self.assertEqual(result, "Direct answer.")


# ---------------------------------------------------------------------------
# vram_free_mb() test
# ---------------------------------------------------------------------------

class TestVramFreeMb(unittest.TestCase):

    def test_vram_free_mb_returns_int(self):
        """vram_free_mb() returns integer from server response."""
        response_lines = [{"vram_free_mb": 8192}]
        mock_sock = _make_mock_socket(response_lines)

        with patch("socket.socket", return_value=mock_sock), \
             patch("model_client._get_socket_path", return_value="/tmp/fake.sock"):
            result = model_client.vram_free_mb()

        self.assertEqual(result, 8192)
        self.assertIsInstance(result, int)

    def test_vram_free_mb_returns_zero_on_error(self):
        """vram_free_mb() returns 0 if server is unavailable."""
        with patch("socket.socket", side_effect=ConnectionRefusedError), \
             patch("model_client._get_socket_path", return_value="/tmp/fake.sock"):
            result = model_client.vram_free_mb()

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
