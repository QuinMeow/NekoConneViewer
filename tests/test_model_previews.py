from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import model_previews as preview


class ModelPreviewTests(unittest.TestCase):
    def test_json_not_identified_as_spine_does_not_request_a_component(self):
        with TemporaryDirectory() as tmp, patch.object(preview.render_spine, "query") as query:
            folder = Path(tmp)
            source = folder / "ordinary.json"
            source.write_text("{}")
            with self.assertRaisesRegex(ValueError, "not a Spine"):
                preview.render_first_frame(source, folder / "out.png", folder)
            query.assert_not_called()
