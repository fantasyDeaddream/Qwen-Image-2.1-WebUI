"""Offscreen unit tests for launcher/agent_panel.py -- no network, no backend.

These pin down the four failure modes that actually bit this project:

  1. QPushButton.clicked passes its checked flag -> add_images(False) must not
     treat it as a list of paths (that killed the frozen EXE on click);
  2. reference images are decoded by PIL first, Qt second, and the copy sent to
     the vision model is downscaled separately from the copy sent to ComfyUI;
  3. gallery thumbnails are clickable and hold the file path; deleting from the
     viewer removes the file, dropping a thumbnail never does;
  4. only models that passed (or never got) a vision verdict may be selected.

    cd Qwen-Image-2.1-WebUI\\launcher
    ..\\.venv\\Scripts\\python.exe tests\\test_agent_panel.py

Qt runs on the offscreen platform, nothing is shown and no dialog is modal.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # launcher dir

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:                                               # noqa: BLE001
    HAVE_PIL = False

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget
    HAVE_QT = True
except Exception:                                               # noqa: BLE001
    HAVE_QT = False

if HAVE_QT:
    import agent
    import agent_panel


def png_bytes(size=(64, 64), color=(10, 200, 30, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


@unittest.skipUnless(HAVE_QT and HAVE_PIL, "需要 PyQt5 + Pillow")
class PanelTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="s4_panel_")
        self.tmp = Path(self._tmpdir.name)
        # never touch the user's real memory / secret store / outputs dir
        self._patchers = [
            mock.patch.object(agent_panel.core, "PROJECT", self.tmp),
            mock.patch.object(agent_panel.core, "load_memory", return_value={}),
            mock.patch.object(agent_panel.core, "save_memory",
                              side_effect=lambda data: self.tmp / "memory.json"),
            mock.patch.object(agent_panel.core, "load_secret", return_value=""),
        ]
        for p in self._patchers:
            p.start()
        self.said = []
        self.panel = agent_panel.AgentPanel(get_strategy=lambda: "turbo",
                                            get_state=lambda: {"strategy": "turbo"})
        self.panel.log.connect(self.said.append)
        self.panel.gallery.log.connect(self.said.append)

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    def write_image(self, name, size=(64, 64), fmt=None, color=(10, 200, 30)):
        path = self.tmp / name
        im = Image.new("RGB", size, color)
        im.save(path, format=fmt)
        return path

    def quiet_boxes(self):
        """Silence every QMessageBox and remember what was asked."""
        calls = []
        patcher = mock.patch.multiple(
            QMessageBox,
            information=lambda *a, **k: calls.append(
                ("info", a[2] if len(a) > 2 else "")),
            warning=lambda *a, **k: calls.append(
                ("warn", a[2] if len(a) > 2 else "")),
            question=lambda *a, **k: QMessageBox.Yes,
            critical=lambda *a, **k: calls.append(
                ("crit", a[2] if len(a) > 2 else "")))
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


class TestDecodeImage(PanelTestBase):
    def test_decodes_every_supported_format_to_rgba_png(self):
        files = [self.write_image("a.png", fmt="PNG"),
                 self.write_image("b.jpg", fmt="JPEG"),
                 self.write_image("c.bmp", fmt="BMP"),
                 self.write_image("d.webp", fmt="WEBP")]
        for f in files:
            raw = agent_panel.AgentPanel.decode_image(f)
            self.assertTrue(raw.startswith(b"\x89PNG"), f.suffix)
            self.assertEqual(Image.open(io.BytesIO(raw)).mode, "RGBA", f.suffix)

    def test_transparency_survives(self):
        path = self.tmp / "t.png"
        Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(path)
        raw = agent_panel.AgentPanel.decode_image(path)
        self.assertEqual(Image.open(io.BytesIO(raw)).getpixel((5, 5))[3], 0)

    def test_max_side_downscales_the_agent_copy_only(self):
        path = self.tmp / "wide.png"
        Image.new("RGB", (5000, 1000), (30, 60, 90)).save(path)
        small = agent_panel.AgentPanel.decode_image(path, agent_panel.AgentPanel.AGENT_MAX_SIDE)
        full = agent_panel.AgentPanel.decode_image(path, agent_panel.AgentPanel.REF_MAX_SIDE)
        self.assertEqual(max(Image.open(io.BytesIO(small)).size), 1568)
        self.assertEqual(max(Image.open(io.BytesIO(full)).size), 4096)

    def test_no_max_side_keeps_the_original_size(self):
        path = self.write_image("small.png", (300, 200))
        raw = agent_panel.AgentPanel.decode_image(path)
        self.assertEqual(Image.open(io.BytesIO(raw)).size, (300, 200))

    def test_missing_file_raises_with_both_decoders_named(self):
        with self.assertRaises(RuntimeError) as ctx:
            agent_panel.AgentPanel.decode_image(self.tmp / "nope.png")
        msg = str(ctx.exception)
        self.assertIn("Pillow", msg)
        self.assertIn("Qt", msg)


class TestAddImages(PanelTestBase):
    def test_click_slot_passes_checked_false_and_must_not_crash(self):
        """QPushButton.clicked -> add_images(False); this used to take the
        frozen launcher down with 'bool object is not iterable'."""
        patcher = mock.patch.object(agent_panel.QFileDialog, "getOpenFileNames",
                                    lambda *a, **k: ([], ""))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.panel.add_images(False)          # exactly the signal payload
        self.assertEqual(self.panel.images, [])

    def test_adds_all_tuples_of_three(self):
        files = [self.write_image("a.png"), self.write_image("b.jpg", fmt="JPEG"),
                 self.write_image("c.png")]
        self.panel.add_images(False, files)
        self.assertEqual(len(self.panel.images), 3)
        self.assertEqual([e[0] for e in self.panel.images], ["a.png", "b.jpg", "c.png"])
        for _name, full, small in self.panel.images:
            self.assertTrue(full.startswith(b"\x89PNG"))
            self.assertTrue(small.startswith(b"\x89PNG"))
        self.assertEqual(self.panel.thumbs.count(), 3)
        self.assertTrue(any("已添加" in s for s in self.said), self.said)

    def test_bad_file_is_reported_not_fatal(self):
        good = self.write_image("ok.png")
        bad = self.tmp / "broken.png"
        bad.write_bytes(b"not an image")
        self.panel.add_images(False, [str(bad), str(good)])
        self.assertEqual(len(self.panel.images), 1)
        self.assertTrue(any("无法读取" in s for s in self.said), self.said)

    def test_clear_and_double_click_remove(self):
        self.panel.add_images(False, [self.write_image("a.png"), self.write_image("b.png")])
        self.panel.remove_image(self.panel.thumbs.item(0))
        self.assertEqual(len(self.panel.images), 1)
        self.assertEqual(self.panel.thumbs.count(), 1)
        self.panel.clear_images()
        self.assertEqual(self.panel.images, [])
        self.assertEqual(self.panel.thumbs.count(), 0)


class TestModels(PanelTestBase):
    CATALOG = {"listed": ["glm-4.6v", "glm-4-flash", "glm-ocr", "glm-4v-flash"],
               "vision": ["glm-4.6v", "glm-4v-flash"],
               "text_only": [("glm-4-flash", "实测：提供方明确回复只接受 text")],
               "unknown": [("glm-5.3-flash", "无法判定：quota")],
               "excluded": [("glm-ocr", "OCR / 固定格式识别，不接受 Agent 指令")],
               "extras_tried": []}

    def test_only_vision_and_unverified_models_are_selectable(self):
        self.panel._fill_models(dict(self.CATALOG))
        items = [self.panel.model.itemText(i) for i in range(self.panel.model.count())]
        data = [self.panel.model.itemData(i) for i in range(self.panel.model.count())]
        self.assertEqual(data, ["glm-4.6v", "glm-4v-flash", "glm-5.3-flash"])
        self.assertTrue(any(t.startswith("⚠ 未实测通过") for t in items), items)
        self.assertNotIn("glm-4-flash", data)          # proven text-only: hidden
        self.assertNotIn("glm-ocr", data)              # not a chat model: hidden
        self.assertIn("已排除 2 个", self.panel.filtered_info.text())

    def test_excluded_button_counts_and_enables(self):
        self.panel._fill_models(dict(self.CATALOG))
        self.assertIn("(2)", self.panel.btn_excluded.text())
        self.assertTrue(self.panel.btn_excluded.isEnabled())

    def test_empty_catalog_explains_what_to_do(self):
        self.panel._fill_models(None)
        self.assertEqual(self.panel.model.count(), 0)
        self.assertIn("联网刷新", self.panel.filtered_info.text())
        self.assertFalse(self.panel.btn_excluded.isEnabled())

    def test_refresh_without_a_key_asks_for_one_instead_of_working(self):
        calls = self.quiet_boxes()
        self.panel.refresh_models()
        self.assertTrue(calls)
        self.assertEqual(self.panel.workers, [], "没有 Key 不能起后台探测")

    def test_plan_without_key_or_model_asks_for_them(self):
        calls = self.quiet_boxes()
        self.panel.do_plan()
        self.assertTrue(any("API Key" in c[1] for c in calls), calls)


class TestGallery(PanelTestBase):
    def test_add_writes_file_and_makes_a_clickable_thumbnail(self):
        path = self.panel.gallery.add(png_bytes(), {"index": 1, "seed": 42,
                                                    "width": 64, "height": 64})
        self.assertTrue(path.is_file())
        self.assertTrue(str(path).startswith(str(self.tmp)))
        self.assertEqual(self.panel.gallery.list.count(), 1)
        item = self.panel.gallery.list.item(0)
        self.assertIsNotNone(item.icon())
        self.assertEqual(item.data(Qt.UserRole)[0], str(path))
        self.assertIn("共 1 张", self.panel.gallery.count.text())

    def test_clear_thumbs_does_not_touch_files(self):
        path = self.panel.gallery.add(png_bytes(), {"index": 1, "seed": 1})
        self.panel.gallery.clear_thumbs()
        self.assertEqual(self.panel.gallery.list.count(), 0)
        self.assertTrue(path.is_file())

    def test_dropping_a_thumbnail_keeps_the_file(self):
        path = self.panel.gallery.add(png_bytes(), {"index": 1, "seed": 1})
        self.panel.gallery._remove_by_path(path)
        self.assertEqual(self.panel.gallery.list.count(), 0)
        self.assertTrue(path.is_file(), "画廊只移除缩略图，不删文件")
        self.assertTrue(any("文件仍在" in s for s in self.said), self.said)

    def test_image_ready_signal_is_wired_to_the_gallery(self):
        self.panel.image_ready.emit(png_bytes(), {"index": 1, "seed": 7})
        self.assertEqual(self.panel.gallery.list.count(), 1)

    def test_viewer_delete_removes_the_file_and_reports_it(self):
        path = self.panel.gallery.add(png_bytes(), {"index": 1, "seed": 1})
        gone = []
        with mock.patch.object(QMessageBox, "question",
                               lambda *a, **k: QMessageBox.Yes):
            dlg = agent_panel.ImageViewer(path, {"index": 1, "seed": 1})
            dlg.deleted.connect(gone.append)
            dlg.delete()
        self.assertFalse(path.exists())
        self.assertEqual(gone, [path])

    def test_viewer_save_as_copies_the_png(self):
        path = self.panel.gallery.add(png_bytes(), {"index": 1, "seed": 1})
        dest = self.tmp / "copy.png"
        with mock.patch.object(agent_panel.QFileDialog, "getSaveFileName",
                               lambda *a, **k: (str(dest), "")), \
             mock.patch.object(QMessageBox, "information",
                               lambda *a, **k: QMessageBox.Ok):
            agent_panel.ImageViewer(path, {"index": 1, "seed": 1}).save_as()
        self.assertEqual(dest.read_bytes(), path.read_bytes())


class TestDrawGuards(PanelTestBase):
    def test_no_plan_asks_for_a_plan_first(self):
        calls = self.quiet_boxes()
        self.panel.do_draw()
        self.assertTrue(calls)

    def test_no_strategy_asks_to_pick_one(self):
        calls = self.quiet_boxes()
        self.panel.plan = agent.Plan(prompt="a cat")
        self.panel.get_strategy = lambda: None
        self.panel.do_draw()
        self.assertTrue(any("策略" in c[1] for c in calls), calls)

    def test_dead_backend_tells_the_user_to_start_it(self):
        calls = self.quiet_boxes()
        self.panel.plan = agent.Plan(prompt="a cat")
        with mock.patch.object(agent_panel.core, "strategy_alive", return_value=False):
            self.panel.do_draw()
        self.assertTrue(any("启动" in c[1] for c in calls), calls)

    def test_direct_plan_mirrors_the_ui(self):
        path = self.write_image("a.png")
        self.panel.add_images(False, [path])
        self.panel.prompt.setPlainText("一只赛博朋克风格的猫")
        self.panel.count.setValue(3)
        self.panel.chk_transparent.setChecked(True)
        calls = self.quiet_boxes()
        with mock.patch.object(agent_panel.core, "strategy_alive", return_value=False):
            self.panel.do_direct()
        plan = self.panel.plan
        self.assertEqual(plan.prompt, "一只赛博朋克风格的猫")
        self.assertEqual(plan.count, 3)
        self.assertTrue(plan.transparent)
        self.assertEqual(plan.mode, "reference")
        self.assertEqual(plan.reference_indices, [0])
        self.assertIn("跳过 AI", plan.reasoning)
        self.assertTrue(calls)                    # it got as far as the backend check


class TestPlanBox(PanelTestBase):
    def test_plan_result_is_shown_with_its_warnings(self):
        plan = agent.Plan.from_agent({"mode": "video", "prompt": "x", "count": 99},
                                     0, 4194304)
        self.panel._plan_ok(plan)
        text = self.panel.plan_box.toPlainText()
        self.assertIn("text2image", text)
        self.assertIn("⚠", text)
        self.assertTrue(self.panel.btn_go.isEnabled())

    def test_failed_plan_is_shown_too(self):
        self.quiet_boxes()
        self.panel._plan_fail("HTTP 401：bad key")
        self.assertIn("规划失败", self.panel.plan_box.toPlainText())
        self.assertFalse(self.panel.btn_go.isEnabled())


@unittest.skipUnless(HAVE_QT, "需要 PyQt5")
class TestOwnershipSurface(unittest.TestCase):
    """The interfaces app.py and the ledger agree on."""

    def test_panel_signals_and_constructor(self):
        panel = agent_panel.AgentPanel(get_strategy=lambda: None, get_state=lambda: {})
        self.assertIsInstance(panel, QWidget)
        for sig in ("log", "image_ready"):
            self.assertTrue(hasattr(panel, sig), sig)
        self.assertTrue(callable(panel.add_images))
        self.assertTrue(callable(panel.refresh_models))
        self.assertTrue(callable(panel.do_plan))
        self.assertTrue(callable(panel.do_draw))

    def test_clicked_slot_signature_has_checked_first(self):
        import inspect
        params = list(inspect.signature(agent_panel.AgentPanel.add_images).parameters)
        self.assertEqual(params[:3], ["self", "checked", "paths"],
                         "QPushButton.clicked 会把 checked 传进第一个位置参数")

    def test_draw_uses_core_draw_with_ordered_images(self):
        src = Path(agent_panel.__file__).read_text(encoding="utf-8")
        self.assertIn("core.draw(", src)
        self.assertIn("ordered_images", src)
        self.assertIn("image_ready.emit", src)
        self.assertNotIn("QPixmap", src.split("def do_draw")[1].split("def ")[0],
                         "worker 线程不能创建控件")

    def test_no_plaintext_secret_storage_in_the_panel(self):
        src = Path(agent_panel.__file__).read_text(encoding="utf-8")
        self.assertIn("core.save_secret", src)
        self.assertIn("core.load_secret", src)
        # the key field is masked
        self.assertIn("QLineEdit.Password", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
