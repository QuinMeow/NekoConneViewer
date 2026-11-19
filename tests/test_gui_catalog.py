import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
except ImportError:
    tk = None
else:
    import viewer_gui as ui
    from viewer_roles import RoleGrid


@unittest.skipIf(tk is None, "Tk is unavailable")
class CatalogDialogTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(gc.collect)  # Finalize Tk cycles here, not on a later export thread.
        self.addCleanup(self.root.destroy)
        work = Path(self.enter(tempfile.TemporaryDirectory())).resolve()
        self.game, self.cache = work / "game", work / "cache"
        self.game.mkdir()
        folder = self.cache / "index"
        folder.mkdir(parents=True)
        icon = tk.PhotoImage(master=self.root, width=128, height=128)
        icon.put("#348a78", to=(0, 0, 128, 128))
        icon.write(str(folder/"role.png"), format="png")
        (folder/"catalog.json").write_text(json.dumps({
            "characters": {"10101": {"names": {}, "avatars": [], "assets": 3},
                           "10102": {"names": {}, "avatars": [], "assets": 1},
                           "1010201": {"names": {}, "avatars": [{"title": "verified_icon", "file": "index/role.png"}],
                                       "assets": 7},
                           "30101001": {"names": {}, "avatars": [], "assets": 5}}, "issues": []}), encoding="utf8")
        self.enter(patch.dict(os.environ, {"NEKOCONNE_CACHE": str(self.cache)}))
        self.enter(patch.object(ui.core, "detect_game", return_value=self.game))
        self.catalog = self.enter(patch.object(ui.core, "catalog_command", return_value=[sys.executable, "-V"]))
        self.export = self.enter(patch.object(ui.core, "scan_command", return_value=[sys.executable, "-V"]))
        self.v = ui.Viewer(self.root)
        self.settle()

    def enter(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def settle(self):
        limit = time.monotonic()+8
        while self.v.busy or self.v.path_timer is not None:
            self.root.update()
            self.assertLess(time.monotonic(), limit)
            time.sleep(.01)

    def click(self, label):
        buttons = self.root.children["export_roles"].buttons.winfo_children()
        next(button for button in buttons if button.cget("text") == label).invoke()

    def test_startup_is_index_only_and_multiselect_scopes_export(self):
        self.catalog.assert_called_once_with(self.game, self.cache, assets=None, resource_names=None)
        self.export.assert_not_called()
        self.assertIsNone(self.root.grab_current())
        buttons = self.root.children["export_roles"].buttons.winfo_children()
        self.assertNotIn("预览模型…", [button.cget("text") for button in buttons])
        grid = self.root.children["export_roles"].roles
        self.assertEqual(grid.visible, ["10101", "10102"])
        self.assertIsNone(grid.portrait("10101"))
        self.assertEqual(grid.portrait("10102").width(), 96)
        self.click("应用所选角色")  # No selection must not be treated as a request for all.
        self.assertIsNotNone(self.root.children.get("export_roles"))
        grid.toggle("10101")
        grid.toggle("10102")
        self.click("应用所选角色")
        self.export.assert_not_called()
        self.assertEqual(self.v.character.get(), "")  # library filter is independent
        self.assertIn("排除未归属", self.v.export_scope.get())
        self.v.flags["audio"].set(True)
        self.v.run_jobs()
        self.settle()
        self.assertEqual(self.export.call_args.kwargs,
                         {"characters": ("10101", "10102"), "assets": None,
                          "resource_names": None, "include_other_roles": False, "jobs": 2})
        self.assertIn("audio", self.export.call_args.args[2])
        self.v.choose_export_roles()
        self.click("全部素材（1 开头角色、未归属）")
        self.v.run_jobs()
        self.settle()
        self.assertEqual(self.export.call_args.kwargs["characters"], ())
        self.assertIn("未归属", self.v.export_scope.get())
        self.assertFalse(self.export.call_args.kwargs["include_other_roles"])

    def test_explicit_other_and_visible_only_scopes_do_not_include_unassigned(self):
        grid = self.root.children["export_roles"].roles
        grid.other.set(True)
        self.assertIn("30101", grid.visible)
        grid.query.set("301")
        self.click("应用当前可见角色")
        self.v.run_jobs()
        self.settle()
        self.assertEqual(self.export.call_args.kwargs["characters"], ("30101",))
        self.assertFalse(self.export.call_args.kwargs["include_other_roles"])
        self.v.choose_export_roles()
        grid = self.root.children["export_roles"].roles
        self.assertTrue(grid.other.get())
        self.click("全部素材（含其他角色、未归属）")
        self.v.run_jobs()
        self.settle()
        self.assertEqual(self.export.call_args.kwargs["characters"], ())
        self.assertTrue(self.export.call_args.kwargs["include_other_roles"])

    def test_exported_models_use_catalog_thumbnails_and_single_selection(self):
        self.click("稍后")
        self.v.catalog = {}  # A rescan may be in flight; old on-disk keys still work.
        self.v.set_library([ui.core.Asset("cubism", "Model", self.cache/"example.json", category="live2d",
                                          character_id=cid) for cid in ("1010201", "30101001")])
        self.v.choose_character()
        grid = self.root.children["filter_roles"].roles
        self.assertEqual(grid.visible, ["10102"])
        self.assertEqual(grid.entries["10102"]["assets"], 1)
        self.assertEqual(grid.portrait("10102").height(), 96)
        grid.query.set("1010201")
        self.assertEqual(grid.visible, ["10102"])
        grid.query.set("")
        grid.toggle("10102")
        grid.other.set(True)
        grid.toggle("30101")
        self.assertEqual(grid.picked, {"30101"})
        self.assertEqual(self.v.character.get(), "30101")
        self.v.apply_filter()
        self.assertEqual([a.character_id for a in self.v.matches], ["30101001"])
        grid.other.set(False)
        self.v.apply_filter()
        self.assertEqual(len(self.v.matches), 2)
        self.v.character.set("1010201")
        self.v.apply_filter()
        self.assertEqual([a.character_id for a in self.v.matches], ["1010201"])
        self.v.set_library([])
        self.assertEqual(grid.visible, [])

    def test_open_export_picker_revalidates_draft_after_catalog_changes(self):
        grid = self.root.children["export_roles"].roles
        grid.toggle("10101")
        grid.toggle("10102")
        (self.cache/"index/catalog.json").write_text(json.dumps({"characters": {"10102": {"assets": 3}}}))
        self.v.refresh(catalog=True)
        self.settle()
        self.assertEqual(grid.visible, ["10102"])
        self.click("应用所选角色")
        self.v.run_jobs()
        self.settle()
        self.assertEqual(self.export.call_args.kwargs["characters"], ("10102",))

    def test_grid_reflows_and_scrolls_without_loading_every_portrait(self):
        dialog = tk.Toplevel(self.root)
        dialog.geometry("850x400+3000+3000")
        ids = [str(10000+i) for i in range(80)]
        grid = RoleGrid(dialog, self.cache, {cid: {"avatars": [], "assets": 1} for cid in ids})
        grid.pack(fill="both", expand=True)
        def wait(condition):
            deadline = time.monotonic()+3
            while not condition() and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
        wait(lambda: grid.columns >= 5)
        wide = grid.columns
        self.assertGreaterEqual(wide, 5)
        self.assertLess(len(grid.photos), len(ids))
        dialog.geometry("460x400+3000+3000")
        wait(lambda: grid.columns < wide)
        self.assertLess(grid.columns, wide)
        grid.canvas.yview_moveto(1)
        wait(lambda: ids[-1] in grid.photos)
        self.assertIn(ids[-1], grid.photos)
        dialog.destroy()

    def test_deferral_is_respected_and_supplement_follows_both_commands(self):
        self.click("稍后")
        self.v.refresh(catalog=True)
        self.settle()
        self.assertIsNone(self.root.children.get("export_roles"))
        supplement = self.cache.parent/"normal-assets"
        supplement.mkdir()
        names = self.cache.parent/"my-resource-list.txt"
        names.write_text("fixture.png", encoding="utf8")
        self.v.extra_assets.set(str(supplement))
        self.v.resource_names.set(str(names))
        self.settle()
        self.assertIsNone(self.root.children.get("export_roles"))
        self.export.assert_not_called()
        self.assertEqual(self.catalog.call_args.kwargs["assets"], supplement)
        self.assertEqual(self.catalog.call_args.kwargs["resource_names"], names)
        self.v.run_jobs()
        self.settle()
        self.assertEqual(self.export.call_args.kwargs["assets"], supplement)
        self.assertEqual(self.export.call_args.kwargs["resource_names"], names)

    def test_failed_catalog_is_not_displayed_as_a_successful_new_index(self):
        self.click("稍后")
        self.v.catalog.clear()
        self.catalog.return_value = [sys.executable, "-c", "raise SystemExit(3)"]
        self.v.refresh(catalog=True, show_picker=True)
        self.settle()
        self.assertIsNone(self.root.children.get("export_roles"))
        self.assertIn("索引失败", self.v.status.get())
        self.export.assert_not_called()
