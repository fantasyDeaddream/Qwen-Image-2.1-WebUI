"""Qwen-Image-2.1 launcher -- cyberpunk PyQt5 front-end.

Guided flow: detect -> locate (or choose install paths) -> install -> run/stop ->
uninstall. Every action is delegated to the existing PowerShell scripts; see
core.py.

    python launcher/app.py
    launcher\\build_exe.bat          -> dist\\QwenImageLauncher.exe
"""
from __future__ import annotations

import json

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt5.QtCore import QThread, QTimer, Qt, QUrl, pyqtSignal
from PyQt5.QtCore import QLibraryInfo
from PyQt5.QtGui import QDesktopServices, QFont, QIcon, QPainter, QColor, QPen
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QScrollArea, QPushButton, QSizePolicy, QStackedWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

import core
from agent_panel import AgentPanel
from theme import QSS, CYAN, GREEN, AMBER, RED, TEXT_DIM, MAGENTA

STEPS = [
    ("1", "检测环境与模型"),
    ("2", "定位 / 安装路径"),
    ("3", "环境配置与模型安装"),
    ("4", "启动与出图"),
    ("5", "卸载"),
]


# ---------------------------------------------------------------------------
#  background workers
# ---------------------------------------------------------------------------
class Runner(QThread):
    """Runs a blocking callable off the UI thread, streaming its output."""

    line = pyqtSignal(str)
    done = pyqtSignal(int)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            rc = self.fn(self.line.emit)
        except Exception as exc:  # noqa: BLE001
            self.line.emit(f"[FAIL] {type(exc).__name__}: {exc}")
            rc = 1
        self.done.emit(int(rc or 0))


class HealthPoller(QThread):
    """Polls the strategy's /api/health until it answers or we give up."""

    up = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, port=7860, timeout_s=600):
        super().__init__()
        self.port = port
        self.timeout_s = timeout_s
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        import json
        import urllib.request

        url = core.health_url(self.port)
        deadline = time.time() + self.timeout_s
        while not self._stop and time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=3) as r:
                    json.loads(r.read().decode())
                self.up.emit(core.webui_url(self.port))
                return
            except Exception:  # noqa: BLE001
                time.sleep(2)
        if not self._stop:
            self.failed.emit("等待 WebUI 超时（10 分钟）。请看下方日志确认卡在哪一步。")


# ---------------------------------------------------------------------------
#  small widgets
# ---------------------------------------------------------------------------
def card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    return f


def section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Section")
    return lab


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Hint")
    lab.setWordWrap(True)
    return lab


class StepRail(QWidget):
    """Left-hand step indicator."""

    goto = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(2)
        title = QLabel("QWEN-IMAGE-2.1")
        title.setObjectName("Subtitle")
        lay.addWidget(title)
        self.labels = []
        for i, (num, text) in enumerate(STEPS):
            lab = QLabel(f" {num}  {text}")
            lab.setObjectName("Step")
            lab.setCursor(Qt.PointingHandCursor)
            lab.mousePressEvent = lambda _e, idx=i: self.goto.emit(idx)
            lay.addWidget(lab)
            self.labels.append(lab)
        lay.addStretch(1)
        self.set_state(0)

    def set_state(self, active: int):
        for i, lab in enumerate(self.labels):
            state = "active" if i == active else ("done" if i < active else "todo")
            lab.setProperty("state", state)
            lab.style().unpolish(lab)
            lab.style().polish(lab)


class Console(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setObjectName("Console")
        self.setReadOnly(True)
        self.setMaximumBlockCount(4000)

    def log(self, text: str):
        self.appendPlainText(text)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


# ---------------------------------------------------------------------------
#  main window
# ---------------------------------------------------------------------------
class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Qwen-Image-2.1  启动器")
        self.resize(1600, 900)
        self.setMinimumSize(1180, 660)

        self.state: dict = {}
        self.hw: dict = {}
        self.choices: list = []
        self.install_root: str = ""
        self.manual_venv: str = ""
        self.manual_comfy: str = ""
        self.manual_cache: str = ""
        self.runner: Runner | None = None
        self.poller: HealthPoller | None = None
        self.pending_goto = None
        self.mem_strategy = ""
        self.strategy_proc = None
        self.current_strategy: str = ""

        self._build()
        self.setStyleSheet(QSS)
        QTimer.singleShot(150, self.boot)

    # ---- chrome ----------------------------------------------------------
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        # header
        head = QHBoxLayout()
        titles = QVBoxLayout()
        t = QLabel("QWEN-IMAGE-2.1")
        t.setObjectName("Title")
        sub = QLabel("本地部署控制台  //  environment - models - runtime")
        sub.setObjectName("Subtitle")
        titles.addWidget(t)
        titles.addWidget(sub)
        head.addLayout(titles)
        head.addStretch(1)
        self.btn_help = QPushButton("使用说明")
        self.btn_help.clicked.connect(self.show_help)
        head.addWidget(self.btn_help)
        self.btn_full = QPushButton("全屏 F11")
        self.btn_full.clicked.connect(self.toggle_fullscreen)
        head.addWidget(self.btn_full)
        outer.addLayout(head)
        outer.addWidget(self._rule())

        # body
        body = QHBoxLayout()
        body.setSpacing(12)
        self.rail = StepRail()
        self.rail.setFixedWidth(240)
        self.rail.goto.connect(self.goto_step)
        body.addWidget(self.rail)

        right = QVBoxLayout()
        right.setSpacing(8)
        # The console has to exist before the pages: the run page wires the agent
        # panel's log signal straight into it.
        self.console = Console()
        self.console.setFixedHeight(150)
        self.stack = QStackedWidget()
        self.pages = [self.page_detect(), self.page_paths(), self.page_install(),
                      self.page_run(), self.page_uninstall()]
        for p in self.pages:
            # A scroll area per page: the run page is dense, and this guarantees
            # nothing overlaps no matter how small the window gets.
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.NoFrame)
            sa.setWidget(p)
            self.stack.addWidget(sa)
        right.addWidget(self.stack, 1)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        outer.addWidget(self.console)

        bar = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setFixedWidth(180)
        self.status = QLabel("就绪")
        self.status.setObjectName("Hint")
        bar.addWidget(self.status, 1)
        bar.addWidget(self.progress)
        outer.addLayout(bar)

    @staticmethod
    def _rule() -> QFrame:
        f = QFrame()
        f.setObjectName("Rule")
        f.setFrameShape(QFrame.HLine)
        f.setFixedHeight(1)
        return f

    def show_help(self):
        """The user guide, bundled into the EXE -- the distribution ships no README."""
        dlg = QDialog(self)
        dlg.setWindowTitle("使用说明")
        dlg.resize(860, 720)
        dlg.setStyleSheet(QSS)
        lay = QVBoxLayout(dlg)
        view = QTextEdit()
        view.setObjectName("Console")
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.WidgetWidth)
        try:
            view.setMarkdown(core.user_guide())     # Qt >= 5.14
        except AttributeError:
            view.setPlainText(core.user_guide())
        lay.addWidget(view, 1)
        row = QHBoxLayout()
        where = QLabel(f"脚本目录：{core.PROJECT}" +
                       ("（由 EXE 自动释放）" if core.bundling() else "（开发版，直接使用源码）"))
        where.setObjectName("Hint")
        row.addWidget(where, 1)
        b = QPushButton("关闭")
        b.setObjectName("Primary")
        b.clicked.connect(dlg.accept)
        row.addWidget(b)
        lay.addLayout(row)
        dlg.exec_()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.btn_full.setText("全屏 F11")
        else:
            self.showFullScreen()
            self.btn_full.setText("退出全屏")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F11:
            self.toggle_fullscreen()
        elif e.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
            self.btn_full.setText("全屏 F11")
        else:
            super().keyPressEvent(e)

    def busy(self, on: bool, text: str = ""):
        self.progress.setVisible(on)
        if text:
            self.status.setText(text)

    # ---- page 1: detect --------------------------------------------------
    def page_detect(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addWidget(section("STEP 1  //  硬件与现状检测"))
        hw_card = card()
        g = QGridLayout(hw_card)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(18)
        self.hw_labels = {}
        for row, (key, label) in enumerate([
                ("gpu", "显卡"), ("vram_gib", "显存"), ("ram_gib", "内存"),
                ("free_disk_gib", "C 盘可用"), ("driver", "驱动")]):
            k = QLabel(label)
            k.setObjectName("Hint")
            v = QLabel("—")
            v.setObjectName("Mono")
            g.addWidget(k, row, 0)
            g.addWidget(v, row, 1)
            self.hw_labels[key] = v
        g.setColumnStretch(2, 1)
        lay.addWidget(hw_card)

        lay.addWidget(section("已检测到的环境与模型"))
        self.detect_box = QPlainTextEdit()
        self.detect_box.setObjectName("Console")
        self.detect_box.setReadOnly(True)
        self.detect_box.setMinimumHeight(210)
        lay.addWidget(self.detect_box, 1)

        row = QHBoxLayout()
        self.btn_probe = QPushButton("重新检测")
        self.btn_probe.setObjectName("Primary")
        self.btn_probe.clicked.connect(self.do_probe)
        b_next = QPushButton("下一步：定位 / 安装路径  >")
        b_next.clicked.connect(lambda: self.goto_step(1))
        row.addWidget(self.btn_probe)
        row.addStretch(1)
        row.addWidget(b_next)
        lay.addLayout(row)
        return w

    def boot(self):
        """First thing on startup: honour a remembered install if we have one."""
        mem = core.load_memory()
        ok, why = core.memory_is_usable(mem)
        if not ok:
            if mem:
                self.console.log(f"[WARN] 记忆文件不可用：{why}")
                self.console.log("       回到常规流程，重新检测。")
            self.do_probe()
            return

        self.console.log("=== 读到记忆文件，使用上次的路径 ===")
        for key in ("venv", "comfyui", "models_cache", "install_root"):
            if mem.get(key):
                self.console.log(f"  {key:<12} = {mem[key]}")
        os.environ["QWEN_VENV_DIR"] = mem.get("venv") or ""
        if not mem.get("venv"):
            os.environ.pop("QWEN_VENV_DIR", None)
        if mem.get("comfyui"):
            os.environ["QWEN_COMFYUI_DIR"] = mem["comfyui"]
        if mem.get("models_cache"):
            os.environ["QWEN_MODELS_CACHE"] = mem["models_cache"]
        self.install_root = mem.get("install_root") or ""

        self.edit_venv.setText(mem.get("venv") or "")
        self.edit_comfy.setText(mem.get("comfyui") or "")
        self.edit_cache.setText(mem.get("models_cache") or "")
        self.edit_root.setText(self.install_root)
        self.mem_strategy = mem.get("strategy") or ""

        # still probe (cheap, and it is what tells us the models are really there)
        # but go straight to STEP 4 when it lands
        self.pending_goto = 3
        self.console.log("检测完成后将直接进入 STEP 4（启动与出图）。")
        self.do_probe()

    def remember_paths(self):
        """Persist the resolved paths so the next launch can skip the wizard."""
        if not getattr(self, "chk_remember", None) or not self.chk_remember.isChecked():
            return
        venv = (self.state.get("venv") or {}).get("path")
        comfy = (self.state.get("comfyui") or {}).get("path")
        cache = (self.state.get("models_cache") or {}).get("path")
        if not venv and not comfy:
            return
        mem = core.load_memory()
        mem.update({"venv": venv, "comfyui": comfy, "models_cache": cache,
                    "install_root": self.install_root or "",
                    "strategy": (self.combo.currentData() if hasattr(self, "combo") else "") or "",
                    "skip_wizard": True})
        core.save_memory(mem)
        self.console.log(f"已记住路径（下次启动直接进 STEP 4）：{core.memory_path()}")

    def do_probe(self):
        if self.runner and self.runner.isRunning():
            return
        self.busy(True, "正在检测…")
        self.console.log("=== 检测环境中（调用 qwen_setup.ps1 -Action probe）===")

        def job(emit):
            self.hw = core.hardware(on_line=emit)
            self.state = core.probe(on_line=None)
            self.choices = core.build_choices(self.hw, self.state)
            return 0

        self.runner = Runner(job)
        self.runner.line.connect(self.console.log)
        self.runner.done.connect(self.after_probe)
        self.runner.start()

    def after_probe(self, rc: int):
        self.busy(False, "检测完成")
        if rc != 0:
            self.console.log("[FAIL] 检测失败，请看上方输出。")
            return
        for key, lab in self.hw_labels.items():
            val = self.hw.get(key)
            lab.setText(str(val) if val not in (None, "") else "未检测到")
        self.render_detect()
        self.render_paths()
        self.render_install()
        self.render_run()
        self.remember_paths()
        if self.pending_goto is not None and hasattr(self, "agent_panel"):
            self.console.log("跳转到 STEP 4：启动与出图。")
            self.goto_step(self.pending_goto)
            self.pending_goto = None

    def render_detect(self):
        st = self.state
        m = st.get("models", {})
        rows = [
            ("项目根目录", st.get("project_root")),
            ("Python 环境", (st.get("venv") or {}).get("path")),
            ("torch", (st.get("venv") or {}).get("torch")),
            ("ComfyUI", (st.get("comfyui") or {}).get("path")),
            ("模型缓存", (st.get("models_cache") or {}).get("path")),
        ]
        lines = []
        for k, v in rows:
            lines.append(f"[{'OK  ' if v else 'MISS'}] {k:<10} {v or '-'}")
        lines.append("")
        names = {"encoder": "文本编码器 (INT8)", "vae": "VAE (BF16)",
                 "diffusion": "扩散模型 (W4/INT8)", "turbo_diffusion": "Turbo 扩散 (INT8)",
                 "turbo_lora": "Turbo LoRA", "turbo_gguf": "Turbo GGUF Q4_K_M"}
        for key, label in names.items():
            e = m.get(key, {})
            if key == "turbo_gguf" and not e.get("present"):
                lines.append(f"[MISS] {label:<22} 未安装（可选）")
                continue
            mark = "OK  " if e.get("present") else "MISS"
            extra = f"  {e.get('gib')} GiB" if not e.get("present") else ""
            kind = f" [{e.get('kind')}]" if key == "diffusion" and e.get("kind") else ""
            lines.append(f"[{mark}] {label:<22}{kind}{extra}")
        if (st.get("models_cache") or {}).get("native"):
            lines.append("[OK  ] 原生 BF16 全套           已就绪")
        else:
            lines.append("[MISS] 原生 BF16 全套           30.21 GiB")
        self.detect_box.setPlainText("\n".join(lines))

    # ---- page 2: locate / install root ----------------------------------
    def page_paths(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(section("STEP 2  //  已有环境在别处？或选择新的安装位置"))

        lay.addWidget(hint(
            "如果 STEP 1 已经全部检测到（都是 OK），可以直接下一步。\n"
            "否则：先在下面手动指认已经装好的环境/模型目录；确实没有，再选一个安装根目录，"
            "环境与模型都会装在那里。"
        ))

        mc = card()
        g = QGridLayout(mc)
        g.setContentsMargins(14, 12, 14, 12)
        self.edit_venv = QLineEdit()
        self.edit_comfy = QLineEdit()
        self.edit_cache = QLineEdit()
        self.edit_venv.setPlaceholderText("例如 D:\\Qwen\\venv   （含 Scripts\\python.exe）")
        self.edit_comfy.setPlaceholderText("例如 D:\\Qwen\\ComfyUI（含 main.py 与 comfy_extras\\nodes_qwen.py）")
        self.edit_cache.setPlaceholderText("例如 D:\\Qwen\\models_cache（原生 BF16 缓存，可留空）")
        g.addWidget(QLabel("Python 环境"), 0, 0)
        g.addWidget(self.edit_venv, 0, 1)
        b1 = QPushButton("浏览…")
        b1.clicked.connect(lambda: self._pick_dir(self.edit_venv))
        g.addWidget(b1, 0, 2)
        g.addWidget(QLabel("模型缓存目录"), 2, 0)
        g.addWidget(self.edit_cache, 2, 1)
        b2c = QPushButton("浏览…")
        b2c.clicked.connect(lambda: self._pick_dir(self.edit_cache))
        g.addWidget(b2c, 2, 2)
        g.addWidget(QLabel("ComfyUI 目录"), 1, 0)
        g.addWidget(self.edit_comfy, 1, 1)
        b2 = QPushButton("浏览…")
        b2.clicked.connect(lambda: self._pick_dir(self.edit_comfy))
        g.addWidget(b2, 1, 2)
        g.setColumnStretch(1, 1)
        lay.addWidget(mc)

        row = QHBoxLayout()
        b_apply = QPushButton("用这两个目录重新检测")
        b_apply.clicked.connect(self.apply_manual)
        row.addWidget(b_apply)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(section("安装根目录（仅当环境/模型确实不存在时才需要）"))
        ic = card()
        g2 = QGridLayout(ic)
        g2.setContentsMargins(14, 12, 14, 12)
        self.edit_root = QLineEdit()
        self.edit_root.setPlaceholderText("留空 = 装在项目目录（先前的默认行为）")
        g2.addWidget(QLabel("安装到"), 0, 0)
        g2.addWidget(self.edit_root, 0, 1)
        b3 = QPushButton("浏览…")
        b3.clicked.connect(lambda: self._pick_dir(self.edit_root))
        g2.addWidget(b3, 0, 2)
        g2.setColumnStretch(1, 1)
        lay.addWidget(ic)
        lay.addWidget(hint(
            "选定后，Python 环境装到 <根目录>\\.venv，ComfyUI 装到 <根目录>\\ComfyUI，"
            "模型装到 <根目录>\\ComfyUI\\models，原生缓存装到 <根目录>\\models_cache。\n"
            "后续安装会复用同一位置。"
        ))

        self.chk_remember = QCheckBox("记住以上路径 —— 下次启动直接进入 STEP 4，不再重新检测")
        self.chk_remember.setChecked(True)
        lay.addWidget(self.chk_remember)
        lay.addStretch(1)
        row2 = QHBoxLayout()
        b_back = QPushButton("< 上一步")
        b_back.clicked.connect(lambda: self.goto_step(0))
        b_next = QPushButton("下一步：安装  >")
        b_next.setObjectName("Primary")
        b_next.clicked.connect(lambda: self.goto_step(2))
        row2.addWidget(b_back)
        row2.addStretch(1)
        row2.addWidget(b_next)
        lay.addLayout(row2)
        return w

    def _pick_dir(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "选择目录", edit.text() or str(core.PROJECT))
        if d:
            edit.setText(d)

    def manual_overrides(self) -> dict:
        """The paths the user typed, as QWEN_* overrides.

        These used to live only inside the probe job: apply_manual() set the env
        vars, ran the probe, then restored them, so the install actions never saw
        them and searched from the executable's own folder instead -- which is
        what produced "请先配置 Python 环境" for an install kept elsewhere.
        """
        out = {}
        for key, val in (("QWEN_VENV_DIR", getattr(self, "manual_venv", "")),
                         ("QWEN_COMFYUI_DIR", getattr(self, "manual_comfy", "")),
                         ("QWEN_MODELS_CACHE", getattr(self, "manual_cache", ""))):
            if val:
                out[key] = val
        return out

    def render_paths(self):
        if not self.edit_venv.text():
            self.edit_venv.setText((self.state.get("venv") or {}).get("path") or "")
        if not self.edit_comfy.text():
            self.edit_comfy.setText((self.state.get("comfyui") or {}).get("path") or "")
        if not self.edit_cache.text():
            self.edit_cache.setText((self.state.get("models_cache") or {}).get("path") or "")

    def apply_manual(self):
        self.manual_venv = self.edit_venv.text().strip()
        self.manual_comfy = self.edit_comfy.text().strip()
        self.manual_cache = self.edit_cache.text().strip()
        self.install_root = self.edit_root.text().strip()
        self.console.log("=== 用指定路径重新检测 ===")
        for label, val in (("QWEN_VENV_DIR", self.manual_venv),
                           ("QWEN_COMFYUI_DIR", self.manual_comfy),
                           ("QWEN_MODELS_CACHE", self.manual_cache)):
            if val:
                self.console.log(f"{label:<17} = {val}")
        if self.install_root:
            self.console.log(f"QWEN_INSTALL_ROOT = {self.install_root}")

        self.busy(True, "重新检测…")

        def job(emit):
            # pass the overrides explicitly; do NOT mutate os.environ, because the
            # old save/restore meant later steps lost them again
            self.state = core.probe(install_root=self.install_root or None,
                                    on_line=emit, manual=self.manual_overrides())
            self.choices = core.build_choices(self.hw, self.state)
            return 0

        self.runner = Runner(job)
        self.runner.line.connect(self.console.log)
        self.runner.done.connect(self.after_probe)
        self.runner.start()

    # ---- page 3: install ------------------------------------------------
    def page_install(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(section("STEP 3  //  环境配置与模型安装"))
        lay.addWidget(hint(
            "推荐项已按你的显卡/内存/磁盘自动标记。安装过程会实时输出到下方日志。"
        ))

        self.env_card = card()
        ev = QVBoxLayout(self.env_card)
        ev.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        self.lbl_env = QLabel("Python 环境")
        self.lbl_env.setObjectName("Mono")
        top.addWidget(self.lbl_env)
        top.addStretch(1)
        self.btn_env = QPushButton("配置环境 (venv + torch)")
        self.btn_env.clicked.connect(lambda: self.run_install("env"))
        top.addWidget(self.btn_env)
        ev.addLayout(top)
        self.lbl_comfy = QLabel("ComfyUI")
        self.lbl_comfy.setObjectName("Mono")
        ev.addWidget(self.lbl_comfy)
        rowc = QHBoxLayout()
        rowc.addStretch(1)
        self.btn_comfy = QPushButton("安装 / 校验 ComfyUI")
        self.btn_comfy.clicked.connect(lambda: self.run_install("comfyui"))
        rowc.addWidget(self.btn_comfy)
        ev.addLayout(rowc)
        lay.addWidget(self.env_card)

        lay.addWidget(section("模型选择（含硬件建议）"))
        self.model_box = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.model_box)
        lay.addWidget(holder, 1)

        nav = QHBoxLayout()
        b_back = QPushButton("< 上一步：定位 / 安装路径")
        b_back.clicked.connect(lambda: self.goto_step(1))
        b_next = QPushButton("下一步：启动与出图  >")
        b_next.setObjectName("Primary")
        b_next.clicked.connect(lambda: self.goto_step(3))
        nav.addWidget(b_back)
        nav.addStretch(1)
        nav.addWidget(b_next)
        lay.addLayout(nav)
        return w

    def render_install(self):
        venv_ok = bool((self.state.get("venv") or {}).get("ok"))
        comfy_ok = bool((self.state.get("comfyui") or {}).get("ok"))
        vr = self.hw.get("vram_gib")
        self.lbl_env.setText(
            f"[{'OK  ' if venv_ok else 'MISS'}] Python 环境   "
            f"{(self.state.get('venv') or {}).get('torch') or '未安装'}    "
            f"显卡 {vr or '?'} GiB"
        )
        self.lbl_comfy.setText(
            f"[{'OK  ' if comfy_ok else 'MISS'}] ComfyUI   "
            f"{(self.state.get('comfyui') or {}).get('path') or '未安装'}"
        )

        while self.model_box.count():
            item = self.model_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for c in self.choices:
            box = card()
            if c.recommend:
                box.setProperty("active", "true")
            h = QHBoxLayout(box)
            h.setContentsMargins(14, 10, 14, 10)
            left = QVBoxLayout()
            title = QLabel(("★ 推荐  " if c.recommend else "") + c.title)
            title.setObjectName("Mono")
            if c.recommend:
                title.setStyleSheet(f"color: {GREEN};")
            left.addWidget(title)
            d = hint(c.detail)
            left.addWidget(d)
            h.addLayout(left, 1)
            v = QLabel(c.verdict)
            v.setObjectName("Mono")
            v.setStyleSheet(f"color: {GREEN if c.ready else AMBER};")
            h.addWidget(v)
            b = QPushButton("已安装" if c.ready else "安装")
            b.setObjectName("Primary")
            b.setEnabled(not c.ready)
            b.clicked.connect(lambda _=False, ch=c: self.run_install(ch.key))
            h.addWidget(b)
            self.model_box.addWidget(box)
        self.model_box.addStretch(1)

    def run_install(self, what: str):
        if self.runner and self.runner.isRunning():
            QMessageBox.information(self, "请稍候", "还有安装任务正在进行。")
            return
        root = self.install_root or self.edit_root.text().strip() or None
        if root:
            try:
                Path(root).mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "路径不可用", f"{root}\n{exc}")
                return

        if what == "env":
            title, fn = "配置 Python 环境", lambda emit: core.install_env(
                root, emit, state=self.state, manual=self.manual_overrides())
        elif what == "comfyui":
            title, fn = "安装 / 校验 ComfyUI", lambda emit: core.install_comfyui(
                root, emit, state=self.state, manual=self.manual_overrides())
        else:
            ch = next((c for c in self.choices if c.key == what), None)
            if ch is None:
                return
            title = f"安装 {ch.title}"
            fn = lambda emit, c=ch: core.install(  # noqa: E731
                c, root, emit, state=self.state, manual=self.manual_overrides())

        if QMessageBox.question(
                self, "确认安装",
                f"{title}\n\n安装位置：{root or (core.PROJECT)}（留空即项目目录）\n\n"
                "安装脚本会在下载前再确认一次。继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return

        self.console.log("")
        self.console.log(f"=== {title} ===")
        self.busy(True, title)

        def job(emit):
            rc = fn(emit)
            emit("")
            emit("正在重新检测…")
            try:
                self.state = core.probe(install_root=root, on_line=None)
                self.choices = core.build_choices(self.hw, self.state)
            except Exception as exc:  # noqa: BLE001
                emit(f"[WARN] 重新检测失败：{exc}")
            return rc

        self.runner = Runner(job)
        self.runner.line.connect(self.console.log)
        self.runner.done.connect(self.after_install)
        self.runner.start()

    def after_install(self, rc: int):
        self.busy(False, "安装结束" if rc == 0 else f"安装失败 (exit {rc})")
        self.console.log(f"=== 结束，退出码 {rc} ===")
        self.render_detect()
        self.render_install()
        self.render_run()

    # ---- page 4: run -----------------------------------------------------
    def page_run(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(section("STEP 4  //  调起模型与 WebUI"))

        rc = card()
        g = QGridLayout(rc)
        g.setContentsMargins(14, 12, 14, 12)
        g.addWidget(QLabel("运行策略"), 0, 0)
        self.combo = QComboBox()
        g.addWidget(self.combo, 0, 1, 1, 2)
        self.combo.currentIndexChanged.connect(self.update_advice)
        self.advice = hint("")
        g.addWidget(self.advice, 1, 0, 1, 3)
        g.setColumnStretch(1, 1)
        lay.addWidget(rc)

        row = QHBoxLayout()
        self.btn_start = QPushButton("启动模型 + WebUI")
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self.do_start)
        self.btn_web = QPushButton("打开 WebUI")
        self.btn_web.clicked.connect(self.open_webui)
        self.btn_web.setEnabled(False)
        self.btn_stop = QPushButton("停止并释放模型")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.clicked.connect(self.do_stop)
        self.btn_stop.setEnabled(False)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_web)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(hint(
            "启动后 7860 端口提供 WebUI 与 HTTP API（/docs 是接口文档）。\n"
            "「停止」会调用 qwen_stop.ps1，先请求后端退出，再释放显存。"
        ))

        lay.addWidget(section("AI AGENT  //  用自然语言驱动本地模型"))
        self.agent_panel = AgentPanel(get_strategy=lambda: self.combo.currentData(),
                                      get_state=lambda: self.state)
        self.agent_panel.log.connect(self.console.log)
        lay.addWidget(self.agent_panel, 1)

        nav = QHBoxLayout()
        b_back = QPushButton("< 上一步：环境配置与模型安装")
        b_back.clicked.connect(lambda: self.goto_step(2))
        b_forget = QPushButton("忘记记忆并重新检测")
        b_forget.clicked.connect(self.forget_and_redetect)
        nav.addWidget(b_back)
        nav.addStretch(1)
        nav.addWidget(b_forget)
        lay.addLayout(nav)
        return w

    def forget_and_redetect(self):
        if not core.clear_memory():
            QMessageBox.information(self, "没有记忆文件", "当前没有保存过路径记忆。")
            return
        self.console.log("已删除记忆文件，正在重新检测…")
        self.do_probe()

    STRATEGY_INFO = {
        "mixed-vram": ("量化 W4（显式放置）", "文本编码器在内存、扩散与 VAE 在显存。8 GB 显存推荐。"),
        "mixed": ("量化 W4（自适应）", "放置交给 ComfyUI 自行调度。"),
        "turbo": ("量化 W4 + Turbo LoRA", "8 步出图，实测快约 2.8 倍且画质与 25 步同档。"),
        "native": ("全量原生 BF16", "diffusers 后端，画质上限最高，显存/内存要求也最高。"),
        "gguf": ("Turbo GGUF Q4_K_M", "隔离的 ComfyUI 实例（端口 8189），实测比 W4 慢约 2.9 倍。"),
    }

    def render_run(self):
        m = self.state.get("models", {})
        have_core = all(m.get(k, {}).get("present") for k in ("encoder", "vae", "diffusion"))
        have_turbo = have_core and m.get("turbo_lora", {}).get("present")
        have_native = (self.state.get("models_cache") or {}).get("native")
        have_gguf = m.get("turbo_gguf", {}).get("present")
        have_comfy = (self.state.get("comfyui") or {}).get("ok")

        avail = []
        if have_core and have_comfy:
            avail += ["mixed-vram", "mixed"]
        if have_turbo and have_comfy:
            avail.append("turbo")
        if have_native:
            avail.append("native")
        if have_gguf and have_comfy:
            avail.append("gguf")

        keep = self.combo.currentText()
        self.combo.clear()
        for s in avail:
            name, desc = self.STRATEGY_INFO[s]
            self.combo.addItem(f"{s}  —  {name}", s)
        if keep:
            i = self.combo.findData(keep)
            if i >= 0:
                self.combo.setCurrentIndex(i)
        if not avail:
            self.combo.addItem("（还没有可用的模型，请先完成 STEP 3）", "")
        self.update_advice()

    def update_advice(self):
        s = self.combo.currentData()
        if not s:
            self.advice.setText("没有可运行的策略。")
            return
        name, desc = self.STRATEGY_INFO[s]
        vr = self.hw.get("vram_gib") or 0
        extra = ""
        if s == "turbo" and 6 <= vr < 12:
            extra = "  ← 你当前硬件下的推荐"
        elif s in ("mixed-vram", "mixed") and vr and vr < 6:
            extra = "  显存偏小，建议分辨率 ≤ 1024×1024"
        elif s == "native" and vr and vr < 16:
            extra = "  ⚠ 显存低于 16 GiB，原生 BF16 可能溢出到共享显存"
        self.advice.setText(f"{name}：{desc}{extra}")

    def do_start(self):
        s = self.combo.currentData()
        if not s:
            QMessageBox.warning(self, "无法启动", "请先安装至少一套模型（STEP 3）。")
            return
        if self.strategy_proc and self.strategy_proc.poll() is None:
            QMessageBox.information(self, "已在运行", "已经有一个策略在运行，请先停止。")
            return

        self.current_strategy = s
        self.console.log("")
        self.console.log(f"=== 启动策略 {s} ===")
        self.busy(True, f"启动 {s} …")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_web.setEnabled(False)

        def job(emit):
            # core.launch hands the backend its own file handle, so the model
            # survives this GUI exiting; we simply tail that file for the console.
            self.strategy_proc, logpath = core.launch(
                s, self.state, manual=self.manual_overrides())
            emit(f"后端日志 / backend log: {logpath}")
            core.follow_log(logpath, emit,
                            lambda: self.strategy_proc.poll() is not None)
            return self.strategy_proc.wait()

        self.runner = Runner(job)
        self.runner.line.connect(self.console.log)
        self.runner.done.connect(self.after_start_exit)
        self.runner.start()

        # the app takes a while to boot the backend; poll health on its own thread
        self.poller = HealthPoller(port=7860, timeout_s=900)
        self.poller.up.connect(self.on_up)
        self.poller.failed.connect(lambda msg: self.console.log(f"[WARN] {msg}"))
        self.poller.start()

    def on_up(self, url: str):
        self.busy(False, "WebUI 已就绪")
        self.btn_web.setEnabled(True)
        self.console.log(f"[ OK ] WebUI 就绪：{url}")
        QDesktopServices.openUrl(QUrl(url))

    def after_start_exit(self, rc: int):
        self.busy(False, "已停止")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_web.setEnabled(False)
        self.console.log(f"=== 策略进程结束，退出码 {rc} ===")

    def after_stop(self, rc: int):
        self.busy(False, "已停止")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_web.setEnabled(False)

    def open_webui(self):
        QDesktopServices.openUrl(QUrl(core.webui_url(7860)))
        QDesktopServices.openUrl(QUrl(core.webui_url(7860) + "docs"))

    def do_stop(self):
        s = self.current_strategy or self.combo.currentData()
        if not s:
            return
        if QMessageBox.question(self, "停止", f"停止 {s} 并释放显存？",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.Yes) != QMessageBox.Yes:
            return
        if self.poller:
            self.poller.stop()
        self.console.log("")
        self.console.log(f"=== 停止 {s} ===")
        self.busy(True, "停止中…")

        def job(emit):
            rc = core.stop(s, emit, state=self.state, manual=self.manual_overrides())
            if self.strategy_proc and self.strategy_proc.poll() is None:
                self.strategy_proc.terminate()
            return rc

        self.runner = Runner(job)
        self.runner.line.connect(self.console.log)
        self.runner.done.connect(self.after_stop)
        self.runner.start()

    # ---- page 5: uninstall ----------------------------------------------
    def page_uninstall(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(section("STEP 5  //  卸载"))
        lay.addWidget(hint("三个卸载入口互相独立。删除前都会显示具体路径并二次确认。"))

        # env
        c1 = card()
        v1 = QVBoxLayout(c1)
        v1.setContentsMargins(14, 12, 14, 12)
        v1.addWidget(QLabel("① 卸载环境"))
        v1.addWidget(hint("删除 Python 环境（.venv）与 ComfyUI 检出。已下载的模型文件会保留。"))
        r1 = QHBoxLayout()
        r1.addStretch(1)
        b1 = QPushButton("卸载环境")
        b1.setObjectName("Danger")
        b1.clicked.connect(self.do_uninstall_env)
        r1.addWidget(b1)
        v1.addLayout(r1)
        lay.addWidget(c1)

        # model
        c2 = card()
        v2 = QVBoxLayout(c2)
        v2.setContentsMargins(14, 12, 14, 12)
        v2.addWidget(QLabel("② 卸载某个模型"))
        v2.addWidget(hint("只删除选中的模型文件，环境与其它模型不受影响。"))
        r2 = QHBoxLayout()
        self.combo_model = QComboBox()
        r2.addWidget(self.combo_model, 1)
        b2 = QPushButton("卸载该模型")
        b2.setObjectName("Danger")
        b2.clicked.connect(self.do_uninstall_model)
        r2.addWidget(b2)
        v2.addLayout(r2)
        lay.addWidget(c2)

        # launcher
        c3 = card()
        v3 = QVBoxLayout(c3)
        v3.setContentsMargins(14, 12, 14, 12)
        v3.addWidget(QLabel("③ 卸载本启动器组件"))
        v3.addWidget(hint("删除这个启动器本身（launcher 目录 / EXE）。"))
        self.chk_keep = QCheckBox("保留已下载的环境与模型（只删启动器）")
        self.chk_keep.setChecked(True)
        v3.addWidget(self.chk_keep)
        v3.addWidget(hint("取消勾选 = 连同环境与全部模型一起彻底卸载。"))
        r3 = QHBoxLayout()
        r3.addStretch(1)
        b3 = QPushButton("卸载启动器")
        b3.setObjectName("Danger")
        b3.clicked.connect(self.do_uninstall_launcher)
        r3.addWidget(b3)
        v3.addLayout(r3)
        lay.addWidget(c3)

        lay.addStretch(1)
        b = QPushButton("< 上一步")
        b.clicked.connect(lambda: self.goto_step(3))
        lay.addWidget(b)
        return w

    def render_uninstall(self):
        t = core.uninstall_targets(self.state)
        self.combo_model.clear()
        names = {"encoder": "文本编码器 (INT8)", "vae": "VAE (BF16)",
                 "diffusion": "扩散模型 (W4/INT8)", "turbo_diffusion": "Turbo 扩散 (INT8)",
                 "turbo_lora": "Turbo LoRA", "turbo_gguf": "Turbo GGUF Q4_K_M"}
        for key, path in t["models"].items():
            self.combo_model.addItem(f"{names.get(key, key)}  —  {path}", key)
        if not t["models"]:
            self.combo_model.addItem("（没有检测到已安装的模型）", "")

    def _confirm(self, title: str, body: str) -> bool:
        return QMessageBox.question(self, title, body, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def _run_uninstall(self, title: str, fn):
        if self.runner and self.runner.isRunning():
            QMessageBox.information(self, "请稍候", "还有任务在进行。")
            return
        self.console.log("")
        self.console.log(f"=== {title} ===")
        self.busy(True, title)

        def job(emit):
            rc = fn(emit)
            try:
                self.state = core.probe(install_root=self.install_root or None, on_line=None)
            except Exception:  # noqa: BLE001
                pass
            return rc

        self.runner = Runner(job)
        self.runner.line.connect(self.console.log)
        self.runner.done.connect(self.after_uninstall)
        self.runner.start()

    def after_uninstall(self, rc: int):
        self.busy(False, "卸载完成")
        self.render_detect()
        self.render_install()
        self.render_run()
        self.render_uninstall()

    def do_uninstall_env(self):
        t = core.uninstall_targets(self.state)["env"]
        listing = "\n".join(f"  {k}: {v or '（未检测到）'}" for k, v in t.items())
        if not self._confirm("卸载环境", f"将删除：\n{listing}\n\n模型文件会保留。继续？"):
            return
        self._run_uninstall("卸载环境", lambda emit: core.uninstall_env(self.state, emit))

    def do_uninstall_model(self):
        key = self.combo_model.currentData()
        if not key:
            QMessageBox.information(self, "没有可卸载的模型", "未检测到已安装的模型。")
            return
        path = core.uninstall_targets(self.state)["models"].get(key)
        if not self._confirm("卸载模型", f"将删除：\n  {path}\n\n继续？"):
            return
        self._run_uninstall("卸载模型", lambda emit: core.uninstall_model(self.state, key, emit))

    def do_uninstall_launcher(self):
        keep = self.chk_keep.isChecked()
        extra = ("只删除启动器目录，环境与模型保留。" if keep else
                 "将删除启动器 + Python 环境 + ComfyUI + 全部模型 + 模型缓存。")
        if not self._confirm("卸载启动器", f"{extra}\n\n此操作不可撤销，继续？"):
            return
        if not keep and not self._confirm(
                "再次确认", "你选择了「连同环境和模型彻底卸载」。\n\n确定要全部删除吗？"):
            return
        self._run_uninstall("卸载启动器",
                            lambda emit: core.uninstall_launcher(keep, self.state, emit))

    # ---- hooks -----------------------------------------------------------
    def goto_step(self, idx: int):
        if idx == 4:
            self.render_uninstall()
        self.stack.setCurrentIndex(idx)
        self.rail.set_state(idx)

    def closeEvent(self, e):
        if self.strategy_proc and self.strategy_proc.poll() is None:
            if QMessageBox.question(
                    self, "还在运行",
                    f"策略 {self.current_strategy} 仍在运行。要停止它并退出吗？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes:
                core.stop(self.current_strategy, lambda _s: None,
                      state=self.state, manual=self.manual_overrides())
            else:
                e.ignore()
                return
        if self.poller:
            self.poller.stop()
        super().closeEvent(e)


def resource(name: str) -> Path:
    """Locate a bundled data file, both from source and from the frozen EXE."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def image_selftest(paths, report_path=None):
    """Decode images with whatever Qt the *running* bundle has, then report.

    Exists so the packaged EXE can be verified without a human clicking around:
    the launcher has no console, so the verdict is written to a JSON file next to
    the executable.

        QwenImageLauncher.exe --selftest-image "a.png" "b.webp"
    """
    app = QApplication.instance() or QApplication(sys.argv)
    from agent_panel import AgentPanel
    report = {"frozen": bool(getattr(sys, "frozen", False)),
              "qt": QLibraryInfo.version().toString(), "results": []}
    for p in paths:
        entry = {"path": p}
        try:
            entry["pillow"] = __import__("PIL").__version__
        except Exception as exc:  # noqa: BLE001
            entry["pillow"] = f"MISSING: {exc}"
        try:
            full = AgentPanel.decode_image(p, AgentPanel.REF_MAX_SIDE)
            small = AgentPanel.decode_image(p, AgentPanel.AGENT_MAX_SIDE)
            entry.update(ok=True, full_bytes=len(full), agent_bytes=len(small))
        except Exception as exc:  # noqa: BLE001
            entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        report["results"].append(entry)

    # --- the button path, exactly as a user hits it -----------------------
    # The crash was NOT decoding: QPushButton.clicked passes its checked flag,
    # so add_images received False where it expected paths. Only actually
    # clicking through the slot reproduces that. The file dialog itself is
    # stubbed out -- a modal picker would hang a headless run.
    quiet = {"strategy": "turbo"}
    import agent_panel as _ap
    real_dialog = _ap.QFileDialog.getOpenFileNames
    _ap.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
    try:
        panel = AgentPanel(get_strategy=lambda: "turbo", get_state=lambda: quiet)
        panel.add_images(False)
        report["button_click_ok"] = True
        report["button_click_note"] = "add_images(False) 不再抛异常（文件对话框已打桩）"
    except Exception as exc:  # noqa: BLE001
        report["button_click_ok"] = False
        report["button_click_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _ap.QFileDialog.getOpenFileNames = real_dialog

    try:
        panel2 = AgentPanel(get_strategy=lambda: "turbo", get_state=lambda: quiet)
        panel2.add_images(False, paths)
        report["loaded_images"] = len(panel2.images)
        report["tuple_width"] = len(panel2.images[0]) if panel2.images else 0
    except Exception as exc:  # noqa: BLE001
        report["loaded_images"] = 0
        report["load_error"] = f"{type(exc).__name__}: {exc}"

    report["all_ok"] = (all(r["ok"] for r in report["results"]) and bool(paths)
                        and report.get("button_click_ok", False)
                        and report.get("loaded_images") == len(paths))
    out = Path(report_path) if report_path else core.launcher_dir() / "selftest_image.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    del app
    return report, out


def main():
    argv = sys.argv[1:]
    if "--selftest-image" in argv:
        i = argv.index("--selftest-image")
        rest = argv[i + 1:]
        rp = None
        if "--report" in rest:
            j = rest.index("--report")
            rp = rest[j + 1]
            rest = rest[:j] + rest[j + 2:]
        _rep, out = image_selftest([a for a in rest if not a.startswith("--")], rp)
        print(json.dumps(_rep, ensure_ascii=False)[:2000])
        sys.exit(0 if _rep["all_ok"] else 1)
    app = QApplication(sys.argv)
    app.setApplicationName("Qwen-Image-2.1 Launcher")
    app.setFont(QFont("Segoe UI", 10))
    icon = resource("icon.ico")
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    win = Launcher()
    if icon.is_file():
        win.setWindowIcon(QIcon(str(icon)))
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
