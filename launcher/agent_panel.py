"""AI Agent panel + result gallery for the launcher's run page.

The panel owns the whole agent round-trip: API keys, live model list, the
vision-only filter, the attached pictures, and the draw plan. Drawing itself
goes through core.draw() (HTTP to the running strategy) so the frozen launcher
never needs torch.
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt5.QtCore import QBuffer, QIODevice, QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

import agent
import core
from theme import QSS, CYAN, GREEN, AMBER, RED, TEXT_DIM


class Worker(QThread):
    """Runs a blocking callable; result goes back on the UI thread."""

    ok = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.ok.emit(self.fn())
        except Exception as exc:  # noqa: BLE001
            self.fail.emit(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
#  gallery
# ---------------------------------------------------------------------------
class ImageViewer(QDialog):
    """Full-size view with Save-as and Delete."""

    deleted = pyqtSignal(object)

    def __init__(self, path: Path, info: dict, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(f"出图预览  —  {self.path.name}")
        self.resize(980, 760)
        self.setStyleSheet(QSS)

        lay = QVBoxLayout(self)
        meta = QLabel(f"seed {info.get('seed')}   {info.get('width')}x{info.get('height')}"
                      f"    {self.path.stat().st_size // 1024} KB")
        meta.setObjectName("Hint")
        lay.addWidget(meta)

        area = QScrollArea()
        area.setWidgetResizable(True)
        pic = QLabel()
        pix = QPixmap(str(self.path))
        pic.setPixmap(pix)
        pic.setAlignment(Qt.AlignCenter)
        area.setWidget(pic)
        lay.addWidget(area, 1)

        row = QHBoxLayout()
        b_save = QPushButton("另存为…")
        b_save.setObjectName("Primary")
        b_save.clicked.connect(self.save_as)
        b_del = QPushButton("删除")
        b_del.setObjectName("Danger")
        b_del.clicked.connect(self.delete)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_save)
        row.addWidget(b_del)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)

    def save_as(self):
        default = str(Path.home() / self.path.name)
        dest, _ = QFileDialog.getSaveFileName(self, "另存为", default, "PNG 图片 (*.png)")
        if not dest:
            return
        try:
            Path(dest).write_bytes(self.path.read_bytes())
            QMessageBox.information(self, "已保存", dest)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))

    def delete(self):
        if QMessageBox.question(self, "删除", f"删除这张图？\n{self.path.name}",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self.deleted.emit(self.path)
        self.accept()


class Gallery(QWidget):
    """Clickable thumbnails; click opens the full-size viewer."""

    log = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.dir = core.PROJECT / "outputs" / "agent"
        self.dir.mkdir(parents=True, exist_ok=True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.count = QLabel("还没有出图")
        self.count.setObjectName("Hint")
        head.addWidget(self.count)
        head.addStretch(1)
        b_clear = QPushButton("清空缩略图（不删文件）")
        b_clear.clicked.connect(self.clear_thumbs)
        head.addWidget(b_clear)
        lay.addLayout(head)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(148, 148))
        self.list.setGridSize(QSize(168, 190))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(4)
        self.list.itemClicked.connect(self.open_item)
        self.list.setMinimumHeight(230)
        lay.addWidget(self.list, 1)

    # ---- adding ---------------------------------------------------------
    def add(self, png: bytes, info: dict) -> Path:
        name = f"agent_{time.strftime('%Y%m%d_%H%M%S')}_{info.get('seed', 0)}.png"
        path = self.dir / name
        path.write_bytes(png)
        item = QListWidgetItem()
        pix = QPixmap()
        pix.loadFromData(png)
        item.setIcon(QIcon(pix.scaled(148, 148, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        item.setText(f"#{info.get('index', '?')}  seed {info.get('seed', '?')}")
        item.setData(Qt.UserRole, (str(path), info))
        item.setToolTip(f"{path.name}\n点击放大 · 可另存为或删除")
        self.list.addItem(item)
        self._refresh()
        return path

    def _refresh(self):
        n = self.list.count()
        self.count.setText(f"共 {n} 张  ·  点击缩略图放大" if n else "还没有出图")

    def clear_thumbs(self):
        self.list.clear()
        self._refresh()

    def open_item(self, item):
        path, info = item.data(Qt.UserRole)
        dlg = ImageViewer(Path(path), info, self)
        dlg.deleted.connect(lambda p: self._remove_by_path(Path(p)))
        dlg.exec_()

    def _remove_by_path(self, path: Path):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if Path(it.data(Qt.UserRole)[0]) == path:
                self.list.takeItem(i)
                break
        self._refresh()
        self.log.emit(f"已从画廊移除缩略图（文件仍在）：{path}")


# ---------------------------------------------------------------------------
#  agent panel
# ---------------------------------------------------------------------------
class AgentPanel(QWidget):
    log = pyqtSignal(str)
    # Drawing runs on a worker thread, and Qt widgets may only be touched on the
    # GUI thread. core.draw()'s on_image callback therefore just emits this;
    # building a QListWidgetItem from that thread crashes the process outright.
    image_ready = pyqtSignal(object, object)

    def __init__(self, get_strategy, get_state, port: int = 7860):
        super().__init__()
        self.get_strategy = get_strategy
        self.get_state = get_state
        self.port = port
        self.images: list = []   # [(name, full_png, agent_png)]
        self.plan = None
        self.workers: list = []
        self.mem = core.load_memory()

        self._build()
        self._restore()

    # ---- construction ---------------------------------------------------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # --- provider / model ---
        api = QFrame()
        api.setObjectName("Card")
        g = QGridLayout(api)
        g.setContentsMargins(14, 10, 14, 10)
        g.setHorizontalSpacing(10)

        self.provider = QComboBox()
        self.provider.setMinimumWidth(170)
        for k, p in agent.PROVIDERS.items():
            self.provider.addItem(p.name, k)
        self.provider.currentIndexChanged.connect(self._provider_changed)

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setMinimumWidth(260)
        self.key.setPlaceholderText("粘贴 API Key（只保存在内存，除非勾选下面）")

        self.btn_key = QPushButton("保存 Key")
        self.btn_key.clicked.connect(self._save_key)

        self.lbl_key_state = QLabel("")
        self.lbl_key_state.setObjectName("Hint")

        self.model = QComboBox()
        self.model.setMinimumWidth(260)
        self.btn_refresh = QPushButton("联网刷新 + 实测读图")
        self.btn_refresh.setObjectName("Primary")
        self.btn_refresh.clicked.connect(self.refresh_models)

        self.btn_excluded = QPushButton("实测结果 / 被排除 (0)")
        self.btn_excluded.setEnabled(False)
        self.btn_excluded.clicked.connect(self.show_excluded)
        self.filtered_info = QLabel("")
        self.filtered_info.setObjectName("Hint")
        self.filtered_info.setWordWrap(True)

        g.addWidget(QLabel("提供方"), 0, 0)
        g.addWidget(self.provider, 0, 1)
        g.addWidget(QLabel("API Key"), 0, 2)
        g.addWidget(self.key, 0, 3)
        g.addWidget(self.btn_key, 0, 4)
        g.addWidget(self.lbl_key_state, 1, 3, 1, 2)
        g.addWidget(QLabel("模型"), 2, 0)
        g.addWidget(self.model, 2, 1, 1, 2)
        g.addWidget(self.btn_refresh, 2, 3)
        g.addWidget(self.btn_excluded, 2, 4)
        g.addWidget(self.filtered_info, 3, 1, 1, 4)
        g.setColumnStretch(3, 1)
        outer.addWidget(api)

        self._catalog: dict = {}    # provider -> (confirmed, unverified, excluded)

        # --- inputs ---
        inp = QFrame()
        inp.setObjectName("Card")
        v = QVBoxLayout(inp)
        v.setContentsMargins(14, 10, 14, 10)

        row = QHBoxLayout()
        row.addWidget(QLabel("图片（参考图 / 被编辑图，可多张）"))
        row.addStretch(1)
        self.btn_add = QPushButton("+ 添加图片")
        self.btn_add.clicked.connect(self.add_images)
        self.btn_clr = QPushButton("清空图片")
        self.btn_clr.clicked.connect(self.clear_images)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_clr)
        v.addLayout(row)

        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setIconSize(QSize(72, 72))
        self.thumbs.setGridSize(QSize(88, 100))
        self.thumbs.setFixedHeight(98)
        self.thumbs.setMovement(QListWidget.Static)
        self.thumbs.setResizeMode(QListWidget.Adjust)
        self.thumbs.itemDoubleClicked.connect(self.remove_image)
        self.thumbs.setToolTip("双击移除该图片")
        v.addWidget(self.thumbs)

        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "用自然语言描述你想要什么，例如：\n"
            "把这张照片的背景去掉，做成贴纸，出 3 张\n"
            "给我一只赛博朋克风格的猫，宽屏 16:9，要有霓虹灯"
        )
        self.prompt.setFixedHeight(78)
        v.addWidget(self.prompt)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("张数"))
        self.count = QSpinBox()
        self.count.setRange(1, 8)
        self.count.setValue(1)
        opts.addWidget(self.count)
        self.chk_transparent = QCheckBox("透明背景")
        opts.addWidget(self.chk_transparent)
        hint = QLabel("（这两项只是给 AI 的提示；提示词里写明的张数优先）")
        hint.setObjectName("Hint")
        opts.addWidget(hint)
        opts.addStretch(1)
        v.addLayout(opts)

        run = QHBoxLayout()
        self.btn_plan = QPushButton("① 让 AI 规划")
        self.btn_plan.setObjectName("Primary")
        self.btn_plan.clicked.connect(self.do_plan)
        self.btn_go = QPushButton("② 按规划绘图")
        self.btn_go.setObjectName("Primary")
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self.do_draw)
        self.btn_direct = QPushButton("跳过 AI，直接绘图")
        self.btn_direct.clicked.connect(self.do_direct)
        run.addWidget(self.btn_plan)
        run.addWidget(self.btn_go)
        run.addWidget(self.btn_direct)
        run.addStretch(1)
        v.addLayout(run)
        outer.addWidget(inp)

        # --- plan + gallery ---
        self.plan_box = QPlainTextEdit()
        self.plan_box.setObjectName("Console")
        self.plan_box.setReadOnly(True)
        self.plan_box.setFixedHeight(86)
        self.plan_box.setPlaceholderText("AI 规划结果会显示在这里")
        outer.addWidget(self.plan_box)

        self.gallery = Gallery()
        self.gallery.log.connect(self.log)
        self.image_ready.connect(self.gallery.add)
        outer.addWidget(self.gallery, 1)

    # ---- persistence ----------------------------------------------------
    def _restore(self):
        p = self.mem.get("agent") or {}
        if p.get("provider"):
            i = self.provider.findData(p["provider"])
            if i >= 0:
                self.provider.setCurrentIndex(i)
        if p.get("model"):
            self.model.addItem(p["model"], p["model"])
            self.model.setCurrentIndex(self.model.count() - 1)
        self._load_key_for_provider()

    def _load_key_for_provider(self):
        pkey = self.provider.currentData()
        self.key.setText(core.load_secret(pkey))
        self._show_key_state(pkey)

    def _show_key_state(self, pkey=None):
        pkey = pkey or self.provider.currentData()
        info = core.secret_info(pkey)
        if not info["saved"]:
            self.lbl_key_state.setText("未保存 Key —— 仅本次运行有效")
            return
        mode = ("DPAPI 加密存储 + SHA-256 指纹" if info["has_ciphertext"]
                else "仅存 SHA-256 指纹（不可还原）")
        self.lbl_key_state.setText(f"已保存 sha256…{info['sha256']}  ·  {mode}")

    def persist(self, extra: dict | None = None):
        """Remember UI choices. API keys go through core.save_secret, never here."""
        mem = core.load_memory()
        a = dict(mem.get("agent") or {})
        a["provider"] = self.provider.currentData()
        a["model"] = self.model.currentData() or ""
        mem["agent"] = a
        if extra:
            mem.update(extra)
        core.save_memory(mem)

    # ---- provider / models ----------------------------------------------
    def _provider_changed(self):
        self.filtered_info.setText("")
        self.model.clear()
        self._load_key_for_provider()
        res = self._catalog.get(self.provider.currentData())
        if res:
            self._fill_models(res)

    def _key_text(self) -> str:
        return self.key.text().strip()

    def _save_key(self):
        value = self._key_text()
        if not value:
            QMessageBox.information(self, "提示", "请先粘贴 API Key。")
            return
        pkey = self.provider.currentData()
        res = core.save_secret(pkey, value)
        self._show_key_state(pkey)
        if res["stored"]:
            msg = (f"Key 已保存。\n\n"
                   f"SHA-256 指纹：{res['sha256']}\n"
                   f"存储方式：Windows DPAPI 加密（CryptProtectData），"
                   f"只有当前 Windows 用户在本机能解开。\n"
                   f"磁盘上没有任何明文。")
        else:
            msg = (f"系统不支持 DPAPI，因此没有把 Key 写到磁盘上。\n\n"
                   f"只记录了 SHA-256 指纹：{res['sha256']}\n"
                   f"本程序会继续用你刚输入的 Key 工作，直到关闭。")
        self.log.emit(f"API Key 已记录（{self.provider.currentText()}）——" +
                      (f"DPAPI 加密落盘，sha256…{res['sha256']}" if res["stored"]
                       else f"未落盘，仅指纹 sha256…{res['sha256']}"))
        QMessageBox.information(self, "已记录", msg)

    def refresh_models(self):
        key = self._key_text()
        if not key:
            QMessageBox.information(self, "需要 API Key", "请先粘贴 API Key 再刷新。")
            return
        pkey = self.provider.currentData()
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("探测中…")
        self.log.emit(f"正在向 {agent.PROVIDERS[pkey].name} 拉取模型列表并逐个实测能否读图 …")
        self.log.emit("（每个模型只发一张 1×1 图片、max_tokens=1，用来换取提供方的明确答复）")

        def job():
            return pkey, agent.refresh(pkey, key, on_progress=self.log.emit)

        w = Worker(job)
        w.ok.connect(self._models_ok)
        w.fail.connect(self._models_fail)
        w.finished.connect(lambda: (self.btn_refresh.setEnabled(True),
                                    self.btn_refresh.setText("联网刷新 + 实测读图")))
        self.workers.append(w)
        w.start()

    def _models_ok(self, payload):
        pkey, res = payload
        self._catalog[pkey] = res
        self.log.emit(
            f"实测完成：{len(res['vision'])} 个可读图，"
            f"{len(res['text_only'])} 个确认纯文本，"
            f"{len(res['unknown'])} 个未能判定（仍可选）")
        self._fill_models(res)
        self.persist()

    def show_excluded(self):
        res = self._catalog.get(self.provider.currentData())
        if not res:
            QMessageBox.information(self, "还没有数据", "请先点“联网刷新 + 实测读图”。")
            return
        rows = [("❌ 实测只接受文本（提供方明确回复）", res["text_only"]),
                ("❌ 非对话模型（不探测）", res["excluded"]),
                ("❓ 未能判定（仍可选）", res["unknown"])]
        body = []
        for title, items in rows:
            if not items:
                continue
            body.append(title)
            for name, why in items:
                body.append(f"    {name}\n        {why}")
            body.append("")
        QMessageBox.information(self, "模型实测结果", "\n".join(body) or "没有条目。")

    def _models_fail(self, msg):
        self.log.emit(f"[FAIL] 拉取模型列表失败：{msg}")
        QMessageBox.warning(self, "拉取失败",
                            f"{msg}\n\n请检查 API Key 是否正确、网络是否可达。")

    def _fill_models(self, res=None):
        res = res if isinstance(res, dict) else self._catalog.get(
            self.provider.currentData())
        if not res:
            self.model.clear()
            self.btn_excluded.setText("实测结果 (0)")
            self.btn_excluded.setEnabled(False)
            self.filtered_info.setText(
                "还没有实测结果 —— 填好 Key 后点“联网刷新 + 实测读图”。")
            return
        vision, unknown = res["vision"], res["unknown"]
        keep = self.model.currentData()
        self.model.clear()
        for m in vision:
            self.model.addItem(m, m)
        for m, _why in unknown:
            self.model.addItem(f"⚠ 未实测通过 · {m}", m)
        if keep:
            i = self.model.findData(keep)
            if i >= 0:
                self.model.setCurrentIndex(i)
        n_hidden = len(res["text_only"]) + len(res["excluded"])
        self.btn_excluded.setText(f"实测结果 / 被排除 ({n_hidden})")
        self.btn_excluded.setEnabled(bool(n_hidden or unknown))
        parts = []
        if vision:
            parts.append(f"实测可读图 {len(vision)} 个")
        else:
            parts.append("⚠ 没有实测通过读图的模型")
        if unknown:
            parts.append(f"未能判定 {len(unknown)} 个（前缀 ⚠，仍可选）")
        if n_hidden:
            parts.append(f"已排除 {n_hidden} 个（点右边按钮看每个的具体原因）")
        self.filtered_info.setText("；".join(parts) + "。")

    # ---- images ---------------------------------------------------------
    AGENT_MAX_SIDE = 1568      # what gets sent to the vision model
    REF_MAX_SIDE = 4096        # what is kept for the local model

    @staticmethod
    def decode_image(path, max_side: int = 0) -> bytes:
        """Decode an image to RGBA PNG bytes.

        Pillow is bundled into the frozen EXE (see qwen_launcher.spec). Qt stays
        as a fallback so that one library missing a codec can never make
        reference images impossible to add again.
        """
        errors = []
        try:
            import io

            from PIL import Image
            with Image.open(str(path)) as im:
                im = im.convert("RGBA")
                if max_side and max(im.size) > max_side:
                    try:
                        im.thumbnail((max_side, max_side), Image.LANCZOS)
                    except AttributeError:      # Pillow >= 10 style
                        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                return buf.getvalue()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Pillow: {type(exc).__name__}: {exc}")

        try:
            img = QImage(str(path))
            if img.isNull():
                raise RuntimeError("不是可识别的图片格式（或文件已损坏）")
            if img.format() != QImage.Format_RGBA8888:
                img = img.convertToFormat(QImage.Format_RGBA8888)
            if max_side and max(img.width(), img.height()) > max_side:
                img = img.scaled(max_side, max_side, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            if not img.save(buf, "PNG"):
                raise RuntimeError("PNG 编码失败")
            return bytes(buf.data())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Qt: {type(exc).__name__}: {exc}")

        raise RuntimeError("；".join(errors))

    def add_images(self, checked=False, paths=None):
        """``checked`` must stay: QPushButton.clicked passes its checked flag.

        Without that first positional parameter PyQt handed the flag to
        ``paths``, so clicking 添加图片 called add_images(False) and died with
        "TypeError: 'bool' object is not iterable" -- a silent exit in the
        windowed build.
        """
        if paths is None:
            files, _ = QFileDialog.getOpenFileNames(
                self, "添加图片", str(Path.home()),
                "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif)")
        else:
            files = [str(p) for p in paths]
        for f in files:
            try:
                full = self.decode_image(f, self.REF_MAX_SIDE)
                small = self.decode_image(f, self.AGENT_MAX_SIDE)
                self.images.append((Path(f).name, full, small))
                self.log.emit(f"已添加 {Path(f).name} —— "
                              f"本地参考 {len(full) // 1024} KB / "
                              f"发给模型 {len(small) // 1024} KB")
            except Exception as exc:  # noqa: BLE001
                self.log.emit(f"[WARN] 无法读取 {f}：{exc}")
        self._refresh_thumbs()

    def _refresh_thumbs(self):
        self.thumbs.clear()
        for i, (name, full, _small) in enumerate(self.images):
            it = QListWidgetItem()
            pix = QPixmap()
            pix.loadFromData(full)
            it.setIcon(QIcon(pix.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            it.setText(f"{i}: {name[:10]}")
            it.setData(Qt.UserRole, i)
            it.setToolTip(f"第 {i} 张：{name}\n双击移除")
            self.thumbs.addItem(it)

    def clear_images(self):
        self.images = []
        self._refresh_thumbs()

    def remove_image(self, item):
        i = item.data(Qt.UserRole)
        if 0 <= i < len(self.images):
            self.images.pop(i)
            self._refresh_thumbs()

    # ---- plan -----------------------------------------------------------
    def do_plan(self):
        key = self._key_text()
        model = self.model.currentData() or ""
        if not key or not model:
            QMessageBox.information(
                self, "还需要两步",
                "请先在“设置 API”里填好 API Key，并点“联网刷新模型列表”选一个多模态模型。")
            return
        text = self.prompt.toPlainText().strip()
        if not text and not self.images:
            QMessageBox.information(self, "没有内容", "请写点提示词，或者添加图片。")
            return

        self.btn_plan.setEnabled(False)
        self.btn_plan.setText("AI 规划中…")
        self.log.emit(f"调用 {self.provider.currentText()} / {model} 规划中（{len(self.images)} 张图）…")
        pkey = self.provider.currentData()
        imgs = [(n, small) for n, _full, small in self.images]
        count, transp = self.count.value(), self.chk_transparent.isChecked()

        def job():
            data = agent.ask(pkey, key, model, text, imgs, count, transp)
            return agent.Plan.from_agent(data, len(imgs), 4194304)

        w = Worker(job)
        w.ok.connect(self._plan_ok)
        w.fail.connect(self._plan_fail)
        w.finished.connect(lambda: (self.btn_plan.setEnabled(True),
                                    self.btn_plan.setText("① 让 AI 规划")))
        self.workers.append(w)
        w.start()

    def _plan_ok(self, plan):
        self.plan = plan
        self.plan_box.setPlainText(plan.summary())
        self.btn_go.setEnabled(True)
        self.log.emit("AI 规划完成：")
        for line in plan.summary().splitlines():
            self.log.emit("  " + line)
        self.persist()

    def _plan_fail(self, msg):
        self.plan_box.setPlainText("规划失败：\n" + msg)
        self.log.emit(f"[FAIL] AI 规划失败：{msg}")
        QMessageBox.warning(self, "AI 规划失败", msg)

    def do_direct(self):
        text = self.prompt.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "没有提示词", "请先写提示词。")
            return
        p = agent.Plan(prompt=text, width=1024, height=1024,
                       count=self.count.value(),
                       transparent=self.chk_transparent.isChecked(),
                       latent_mode="reference" if self.images else "custom",
                       mode="reference" if self.images else "text2image",
                       reference_indices=[0] if self.images else [],
                       reasoning="跳过 AI，直接按界面设置绘图")
        self.plan = p
        self.plan_box.setPlainText(p.summary())
        self.do_draw()

    # ---- draw -----------------------------------------------------------
    def do_draw(self):
        if self.plan is None:
            QMessageBox.information(self, "还没有规划", "请先点“① 让 AI 规划”。")
            return
        strategy = self.get_strategy()
        if not strategy:
            QMessageBox.warning(self, "没有可用策略", "请先在 STEP 4 上方选一个运行策略。")
            return
        if not core.strategy_alive(self.port):
            QMessageBox.warning(
                self, "后端没在跑",
                "请先点上面的“启动模型 + WebUI”，等状态变成「WebUI 已就绪」再绘图。")
            return

        plan = self.plan
        ordered = plan.ordered_images(self.images)
        if plan.mode == "edit" and ordered:
            self.log.emit(f"被编辑图片：第 {plan.edit_image_index} 张，已排到第一位")
        self.btn_go.setEnabled(False)
        self.btn_go.setText("绘图中…")
        self.log.emit(f"开始绘图：{plan.width}x{plan.height} × {plan.count} 张"
                      f"{'（含参考图）' if ordered else ''}")

        def job():
            return core.draw(plan, [full for _n, full, _s in ordered], port=self.port,
                             on_line=self.log.emit,
                             on_image=lambda raw, info: self.image_ready.emit(raw, info))

        w = Worker(job)
        w.ok.connect(lambda res: self.log.emit(f"完成，共 {len(res)} 张。"))
        w.fail.connect(lambda msg: (self.log.emit(f"[FAIL] 绘图失败：{msg}"),
                                    QMessageBox.warning(self, "绘图失败", msg)))
        w.finished.connect(lambda: (self.btn_go.setEnabled(True),
                                    self.btn_go.setText("② 按规划绘图")))
        self.workers.append(w)
        w.start()
