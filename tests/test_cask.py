from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cask


class RenderingTests(unittest.TestCase):
    def test_universal_cask_has_one_immutable_identity_and_no_arch_filter(self):
        receipt = {"version": "0.1.2", "build": 1074, "artifact": {"sha256": "b" * 64,
                   "url": "https://github.com/PositionBar/releases/releases/download/positionbar-0.1.2-1074/PositionBar-0.1.2-1074.dmg",
                   "minimumSystemVersion": "26.0.0", "architectures": ["arm64", "x86_64"]}}
        contents = cask.render_cask(receipt)
        self.assertEqual(cask.cask_identity(contents), ((0, 1, 2, 1074), receipt['artifact']['url'], 'b' * 64))
        self.assertIn('depends_on macos: :tahoe', contents)
        self.assertNotIn('depends_on arch:', contents)


if __name__ == '__main__': unittest.main()
