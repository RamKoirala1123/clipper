import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import social_accounts
import posting


class TestSocialUploads(unittest.TestCase):
    def test_check_helper(self):
        # Successful response
        ok = social_accounts._check(200, json.dumps({"video_id": "123"}), "Test")
        self.assertEqual(ok.get("video_id"), "123")

        # HTTP error
        with self.assertRaises(RuntimeError) as ctx:
            social_accounts._check(400, "Bad Request", "Test")
        self.assertIn("failed (400)", str(ctx.exception))

        # Facebook JSON error payload
        fb_err = json.dumps({"error": {"message": "Invalid OAuth access token", "code": 190, "error_subcode": 463}})
        with self.assertRaises(RuntimeError) as ctx:
            social_accounts._check(200, fb_err, "Facebook Test")
        self.assertIn("Invalid OAuth access token", str(ctx.exception))
        self.assertIn("code 190", str(ctx.exception))

    def test_facebook_upload_flow(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            # Create a 9MB dummy file (3 chunks at 4MB)
            tf.write(b"A" * (9 * 1024 * 1024))
            temp_path = Path(tf.name)

        progress_events = []
        def on_prog(pct, up, tot, detail):
            progress_events.append((pct, up, tot, detail))

        acc = {"access_token": "mock_token", "page_id": "123456"}

        # Mock _http_request for Phase 1 (start), Phase 2 (chunks), and Phase 3 (finish)
        calls = []
        def mock_http(url, method="GET", headers=None, data=None, json_data=None, timeout=60):
            calls.append((url, method))
            if "videos" in url and data:
                # Check for Phase 1 start
                if b"upload_phase=start" in data:
                    return 200, {}, None, json.dumps({
                        "video_id": "vid_999",
                        "upload_session_id": "sess_111",
                        "start_offset": "0",
                        "end_offset": str(4 * 1024 * 1024)
                    })
                # Check for Phase 3 finish
                elif b"upload_phase=finish" in data:
                    return 200, {}, None, json.dumps({"success": True})
                # Check for Phase 2 transfer
                elif b"upload_phase" in data:
                    # Look for start_offset in multipart body
                    if b"start_offset\"\r\n\r\n0\r\n" in data:
                        return 200, {}, None, json.dumps({"start_offset": str(4 * 1024 * 1024), "end_offset": str(8 * 1024 * 1024)})
                    elif b"start_offset\"\r\n\r\n4194304\r\n" in data:
                        return 200, {}, None, json.dumps({"start_offset": str(8 * 1024 * 1024), "end_offset": str(9 * 1024 * 1024)})
                    elif b"start_offset\"\r\n\r\n8388608\r\n" in data:
                        return 200, {}, None, json.dumps({"start_offset": str(9 * 1024 * 1024), "end_offset": str(9 * 1024 * 1024)})
            return 200, {}, None, "{}"

        try:
            with patch("social_accounts._http_request", side_effect=mock_http):
                vid, url = social_accounts.facebook_upload(acc, temp_path, "Test Title", "Test Desc", is_reel=True, progress_cb=on_prog)

            self.assertEqual(vid, "vid_999")
            self.assertIn("facebook.com/123456/videos/vid_999", url)
            self.assertTrue(len(progress_events) >= 3)
            # Final progress should be 100%
            self.assertEqual(progress_events[-1][0], 100)
            self.assertEqual(progress_events[-1][2], 9 * 1024 * 1024)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_posting_progress_tracking(self):
        post = {
            "id": "post_test_123",
            "clip_id": "clip_abc",
            "account_id": "acc_1",
            "account_name": "My Page",
            "platform": "facebook",
            "status": "scheduled",
        }

        # Mock clip file
        with tempfile.TemporaryDirectory() as td:
            temp_data = Path(td)
            clip_file = temp_data / "clip_abc.json"
            clip_file.write_text(json.dumps({"title": "Test Clip"}), encoding="utf-8")

            with patch("posting.DATA", temp_data):
                with patch("posting._save_posts"):
                    # Test mirror
                    post["progress"] = "45%"
                    post["progress_pct"] = 45
                    post["uploaded_bytes"] = 4500000
                    post["total_bytes"] = 10000000
                    post["progress_detail"] = "Uploaded 4.5 MB / 10.0 MB (45%)"
                    posting._mirror_to_clip(post)

                    # Verify mirrored content
                    mirrored = json.loads(clip_file.read_text(encoding="utf-8"))
                    clip_post = mirrored.get("posts", {}).get("post_test_123", {})
                    self.assertEqual(clip_post.get("progress_pct"), 45)
                    self.assertEqual(clip_post.get("uploaded_bytes"), 4500000)
                    self.assertEqual(clip_post.get("progress_detail"), "Uploaded 4.5 MB / 10.0 MB (45%)")


if __name__ == "__main__":
    unittest.main()
