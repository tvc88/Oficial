# streamlink_gui_recorder.py — versão 2.4 (2025-05-25)
# ------------------------------------------------------------------
# Requisitos:
#   pip install PyQt6 streamlink   +   ffmpeg no PATH
# ------------------------------------------------------------------
"""Aplicativo GUI para gravação via Streamlink."""

import json
import logging
import os
import queue
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from utils import sanitize, human_size, human_time, is_live
from recorder import Recorder, EXEC_CONV
from config import load_config, save_config
from telegram_utils import enviar_notificacao_telegram, update_creds
from telegram_logic import enviar_mensagem_teste, verificar_status_command

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QFont, QDesktopServices, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSpacerItem,
    QSizePolicy,
    QLineEdit,
    QPushButton,
    QComboBox,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QPlainTextEdit,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QAbstractItemView,
    QCheckBox,
    QMenu,
    QProgressBar,
)

# ---------------- Config ------------------------------------------------
CONFIG_FILE = Path(__file__).with_name("config.json")
EXEC_LIVE = ThreadPoolExecutor(max_workers=10)
POLL_STATS = 30  # segundos p/ atualizar tamanho/duração
POLL_LIVE = 60  # segundos p/ checar status LIVE
POLL_QUEUE = 1  # segundos p/ processar fila
MAX_CHANNELS = 200
WATCHDOG_MAX = 3  # ciclos de inatividade do .ts antes de forçar stop
MAX_ERROR_TEXT = 160

BUTTON_STYLE = """
QPushButton{
    background:#1976D2;color:white;border:none;border-radius:8px;
    padding:6px 12px;font-weight:600;}
QPushButton:hover{background:#1565C0;}
QPushButton:pressed{background:#0D47A1;}
"""

# ---------------- Logging ----------------------------------------------
LOG_DIR = Path(__file__).with_name("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"

handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
fmt = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=fmt, handlers=[handler])
logger = logging.getLogger(__name__)


def excepthook(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
    else:
        logger.exception("Exceção não tratada", exc_info=(exc_type, exc, tb))


sys.excepthook = excepthook


# ---------------- Classe principal -------------------------------------
class MainWindow(QMainWindow):
    """Janela principal da aplicação."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Streamlink Recorder v2.4")
        self.resize(1200, 800)

        self.output_dir_manual = Path.home() / "GRAVACOES MANUAIS"
        self.output_dir_monitor = Path.home() / "MONITORAMENTO"
        self.output_dir_manual.mkdir(parents=True, exist_ok=True)
        self.output_dir_monitor.mkdir(parents=True, exist_ok=True)

        self.recorder = Recorder()
        self.manual_last_size, self.manual_inact = {}, {}
        self.auto_last_size, self.auto_inact = {}, {}
        self.manual_finishing, self.auto_finishing = set(), set()
        self.live_queue: queue.Queue[tuple[int, bool, str]] = queue.Queue()

        self.telegram_token = None
        self.telegram_chat_id = None
        self.telegram_offset = None

        self._load_cfg()

        self._build_ui()
        self._load_monitored()

        self.t_stats = QTimer(self)
        self.t_stats.timeout.connect(self._update_stats)
        self.t_stats.start(POLL_STATS * 1000)
        self.t_live = QTimer(self)
        self.t_live.timeout.connect(self._dispatch_live_checks)
        self.t_live.start(POLL_LIVE * 1000)
        self.t_queue = QTimer(self)
        self.t_queue.timeout.connect(self._process_live_queue)
        self.t_queue.start(POLL_QUEUE * 1000)

        self.t_manual_check = QTimer(self)
        self.t_manual_check.timeout.connect(self._check_manual_live)
        self.t_manual_check.start(POLL_LIVE * 1000)

        self.t_telegram = QTimer(self)
        self.t_telegram.timeout.connect(self._check_telegram_commands)
        self.t_telegram.start(30 * 1000)

    # ---------------- Nova função: importar inscrições -----------------
    def importar_inscricoes(self):
        """Importa inscrições a partir de subscriptions.json exportado pelo Google Takeout."""

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar arquivo de inscrições (subscriptions.json)",
            str(Path.home()),
            "Arquivos JSON (*.json)",
        )

        if not file_path:
            return  # Usuário cancelou

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            count_importados = 0

            for sub in data.get("subscriptions", []):
                nome = sub.get("title", "").strip()
                url = sub.get("channelUrl", "").strip()

                if not nome or not url:
                    continue

                duplicado = False
                for ch in self._iter_mon():
                    if ch.text(2).lower() == nome.lower() or ch.text(3).lower() == url.lower():
                        duplicado = True
                        break

                if duplicado:
                    continue

                self.mon_name.setText(nome)
                self.mon_url.setText(url)
                self._add_channel()

                count_importados += 1

            QMessageBox.information(
                self,
                "Importação concluída",
                f"{count_importados} inscrições importadas com sucesso.",
            )

        except Exception as e:
            QMessageBox.critical(self, "Erro na importação", str(e))

        # Timer de checagem para gravações manuais
        self.t_manual_check.start(POLL_LIVE * 1000)

    # ---------------- Interface ----------------------------------------
    def _build_ui(self):
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # ========== ABA CENTRAL – Gravação Manual ==========
        central = QWidget()
        tabs.addTab(central, "Gravação Manual")
        c_root = QVBoxLayout(central)

        top = QHBoxLayout()
        self.dir_btn = QPushButton("Arquivar em", styleSheet=BUTTON_STYLE)
        top.addWidget(self.dir_btn)
        self.label_in = QLineEdit(placeholderText="Etiqueta")
        top.addWidget(self.label_in)
        self.url_in = QLineEdit(placeholderText="URL do canal ou live")
        top.addWidget(self.url_in, 3)

        qual_layout = QVBoxLayout()
        qual_layout.setContentsMargins(0, 0, 0, 0)
        self.qual_in = QComboBox()
        self.qual_in.addItems(["1080p", "720p", "480p", "360p", "best"])
        self.qual_in.setCurrentText("best")
        qual_layout.addWidget(self.qual_in)
        top.addLayout(qual_layout)

        self.add_btn = QPushButton("+ Adicionar Link", styleSheet=BUTTON_STYLE)
        top.addWidget(self.add_btn)
        self.rem_entry_btn = QPushButton("− Remover Link", styleSheet=BUTTON_STYLE)
        top.addWidget(self.rem_entry_btn)
        c_root.addLayout(top)

        self.entry_tree = QTreeWidget()
        self.entry_tree.setFont(QFont("", 10))
        self.entry_tree.setHeaderLabels(["Etiqueta", "URL - Endereço", "Qualidade"])
        self.entry_tree.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.entry_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.entry_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.entry_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.order_tree = QTreeWidget()
        self.order_tree.setFont(QFont("", 10))
        self.order_tree.setHeaderLabels(
            ["Etiqueta", "URL - Endereço", "Qualidade", "Status", "Informações"]
        )
        self.order_tree.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.order_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3, 4):
            self.order_tree.header().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )

        c_root.addWidget(self.entry_tree, 2)
        c_root.addWidget(self.order_tree, 2)

        # log manual
        self.manual_log = QPlainTextEdit(readOnly=True)
        self.manual_log.setMaximumHeight(100)
        c_root.addWidget(self.manual_log)

        # Barra de progresso da conversão
        self.conv_progress = QProgressBar()
        self.conv_progress.setValue(0)
        c_root.addWidget(self.conv_progress)

        act = QHBoxLayout()
        self.rem_btn = QPushButton("− Remover Linha", styleSheet=BUTTON_STYLE)
        act.addWidget(self.rem_btn)
        act.addItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self.grv_btn = QPushButton("Gravar", styleSheet=BUTTON_STYLE)
        act.addWidget(self.grv_btn)
        self.batch_btn = QPushButton("Gravar em Lote", styleSheet=BUTTON_STYLE)
        act.addWidget(self.batch_btn)
        self.stop_btn = QPushButton("Encerrar", styleSheet=BUTTON_STYLE)
        act.addWidget(self.stop_btn)
        c_root.addLayout(act)

        # ========== ABA MONITORAMENTO ==========
        mon = QWidget()
        tabs.addTab(mon, "Monitoramento de Canais")
        m_root = QVBoxLayout(mon)

        m_top = QHBoxLayout()
        self.mon_dir_btn = QPushButton("Arquivar em", styleSheet=BUTTON_STYLE)
        m_top.addWidget(self.mon_dir_btn)
        self.mon_name = QLineEdit(placeholderText="Nome")
        m_top.addWidget(self.mon_name)
        self.mon_url = QLineEdit(placeholderText="URL do Canal")
        m_top.addWidget(self.mon_url, 3)

        mon_qual_layout = QVBoxLayout()
        mon_qual_layout.setContentsMargins(0, 0, 0, 0)
        self.mon_qual = QComboBox()
        self.mon_qual.addItems(["1080p", "720p", "480p", "360p", "best"])
        self.mon_qual.setCurrentText("best")
        mon_qual_layout.addWidget(self.mon_qual)
        m_top.addLayout(mon_qual_layout)

        self.mon_add = QPushButton("+ Adicionar Canal", styleSheet=BUTTON_STYLE)
        m_top.addWidget(self.mon_add)
        self.mon_rem = QPushButton("− Remover Canal", styleSheet=BUTTON_STYLE)
        m_top.addWidget(self.mon_rem)
        m_root.addLayout(m_top)

        self.toggle_all_checkbox = QCheckBox("Ativar/Desativar Todos")
        self.toggle_all_checkbox.clicked.connect(self._toggle_all_clicked)

        self.mon_tree = QTreeWidget()
        self.mon_tree.setFont(QFont("", 10))
        self.mon_tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.mon_tree.setHeaderLabels(
            [
                "#",
                "Ativo",
                "Canal",
                "URL - Endereço",
                "Status",
                "Informações",
                "Histórico",
                "Qualidade",
            ]
        )
        self.mon_tree.header().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mon_tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for col in (0, 1, 6, 7, 2, 4, 5):
            self.mon_tree.header().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.mon_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        m_root.addWidget(self.mon_tree, 7)
        self.mon_log = QPlainTextEdit(readOnly=True)
        self.mon_log.setMaximumHeight(120)
        m_root.addWidget(self.mon_log, 1)

        self.mon_stop = QPushButton("Encerrar gravação", styleSheet=BUTTON_STYLE)
        self.reset_hist_btn = QPushButton("Resetar Histórico", styleSheet=BUTTON_STYLE)

        self.import_subs_btn = QPushButton("Importar Inscrições", styleSheet=BUTTON_STYLE)
        self.import_subs_btn.setText("📥 Importar Inscrições")
        self.import_subs_btn.setFixedWidth(140)

        m_bot = QHBoxLayout()
        m_bot.addWidget(self.toggle_all_checkbox)
        self.move_up_btn = QPushButton("▲ Mover Cima", styleSheet=BUTTON_STYLE)
        self.move_down_btn = QPushButton("▼ Mover Baixo", styleSheet=BUTTON_STYLE)
        m_bot.addWidget(self.move_up_btn)
        m_bot.addWidget(self.move_down_btn)
        m_bot.addWidget(self.import_subs_btn)
        m_bot.addStretch()
        m_bot.addWidget(self.reset_hist_btn)
        m_bot.addWidget(self.mon_stop)
        m_root.addLayout(m_bot)

        # ========== ABA TELEGRAM ==========
        tele = QWidget()
        tabs.addTab(tele, "Telegram")
        t_root = QVBoxLayout(tele)
        form = QHBoxLayout()
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Token")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.chat_edit = QLineEdit()
        self.chat_edit.setPlaceholderText("Chat ID")
        form.addWidget(self.token_edit)
        form.addWidget(self.chat_edit)
        t_root.addLayout(form)
        self.save_telegram_btn = QPushButton("Salvar", styleSheet=BUTTON_STYLE)
        self.test_telegram_btn = QPushButton("Enviar mensagem teste", styleSheet=BUTTON_STYLE)
        t_root.addWidget(self.save_telegram_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        t_root.addWidget(self.test_telegram_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self.test_telegram_btn.setEnabled(bool(self.telegram_token and self.telegram_chat_id))

        self.token_edit.setText(self.telegram_token or "")
        self.chat_edit.setText(self.telegram_chat_id or "")

        # === Conexões ===
        self.dir_btn.clicked.connect(self._choose_dir_manual)
        self.mon_dir_btn.clicked.connect(self._choose_dir_monitor)

        self.add_btn.clicked.connect(self._add_entry)
        self.rem_entry_btn.clicked.connect(self._remove_entry)

        self.rem_btn.clicked.connect(self._remove_order)
        self.grv_btn.clicked.connect(self._start_entry)
        self.batch_btn.clicked.connect(self._start_batch)
        self.stop_btn.clicked.connect(self._stop_selected)

        self.mon_add.clicked.connect(self._add_channel)
        self.mon_rem.clicked.connect(self._remove_channel)
        self.mon_stop.clicked.connect(self._confirm_stop_channel_record)
        self.import_subs_btn.clicked.connect(self.importar_inscricoes)

        self.reset_hist_btn.clicked.connect(self._reset_history)

        self.mon_tree.itemChanged.connect(lambda *_: self._save_monitored())
        self.move_up_btn.clicked.connect(self._move_up_selected)
        self.move_down_btn.clicked.connect(self._move_down_selected)
        self.save_telegram_btn.clicked.connect(self._save_telegram)
        self.test_telegram_btn.clicked.connect(self._send_telegram_test)

        self.mon_tree.customContextMenuRequested.connect(self._mon_context_menu)
        self.order_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.order_tree.customContextMenuRequested.connect(self._order_context_menu)

    # ---------------- Função de reset de histórico ---------------------
    def _reset_history(self):
        confirm = QMessageBox.question(
            self,
            "Resetar Histórico",
            "Deseja zerar o histórico de todas as entradas?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        for ch in self._iter_mon():
            ch.setText(6, "-")
        self._save_monitored()
        self._mon_log("Histórico resetado por solicitação do usuário.")

    # ---------------- Função de renumeração ---------------------------
    def _renumerar_mon_tree(self):
        for i in range(self.mon_tree.topLevelItemCount()):
            item = self.mon_tree.topLevelItem(i)
            item.setText(0, str(i + 1))
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(6, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(7, Qt.AlignmentFlag.AlignCenter)

    # ---------------- Mover itens na lista ----------------------------
    def _move_up_selected(self):
        sel = self.mon_tree.selectedItems()
        if not sel:
            return
        item = sel[0]
        idx = self.mon_tree.indexOfTopLevelItem(item)
        if idx > 0:
            self.mon_tree.takeTopLevelItem(idx)
            self.mon_tree.insertTopLevelItem(idx - 1, item)
            self.mon_tree.setCurrentItem(item)
            self._renumerar_mon_tree()
            self._save_monitored()

    def _move_down_selected(self):
        sel = self.mon_tree.selectedItems()
        if not sel:
            return
        item = sel[0]
        idx = self.mon_tree.indexOfTopLevelItem(item)
        if idx < self.mon_tree.topLevelItemCount() - 1:
            self.mon_tree.takeTopLevelItem(idx)
            self.mon_tree.insertTopLevelItem(idx + 1, item)
            self.mon_tree.setCurrentItem(item)
            self._renumerar_mon_tree()
            self._save_monitored()

    # ---------------- Menus de contexto -------------------------------
    def _open_folder(self, path: Path):
        if not path.exists():
            return
        logger.info("Abrindo pasta: %s", path)
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception:
            # Fallback para sistemas sem QDesktopServices funcional
            if sys.platform.startswith("darwin"):
                subprocess.run(["open", str(path)])
            elif os.name == "posix":
                subprocess.run(["xdg-open", str(path)])
            else:
                webbrowser.open(str(path))

    def _mon_context_menu(self, pos):
        item = self.mon_tree.itemAt(pos)
        if not item:
            return
        cid = id(item)
        if cid in self.recorder.ats:
            folder = self.recorder.ats[cid].parent
        else:
            folder = self.output_dir_monitor / sanitize(item.text(2))
        menu = QMenu(self)
        act = QAction("Abrir pasta de gravação", self)
        act.triggered.connect(lambda: self._open_folder(folder))
        menu.addAction(act)
        menu.exec(self.mon_tree.viewport().mapToGlobal(pos))

    def _order_context_menu(self, pos):
        item = self.order_tree.itemAt(pos)
        if not item:
            return
        iid = id(item)
        if iid in self.recorder.ts:
            folder = self.recorder.ts[iid].parent
        else:
            folder = self.output_dir_manual
        menu = QMenu(self)
        act = QAction("Abrir pasta de gravação", self)
        act.triggered.connect(lambda: self._open_folder(folder))
        menu.addAction(act)
        menu.exec(self.order_tree.viewport().mapToGlobal(pos))

    # ---------------- Métodos de confirmação ---------------------------
    def _confirm_stop_channel_record(self):
        reply = QMessageBox.question(
            self,
            "Confirmar Encerramento",
            "Tem certeza de que deseja encerrar a gravação selecionada?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._stop_channel_record()

    # ---------------- Logging helpers ----------------------------------
    def _mon_log(self, msg):
        self.mon_log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {msg}")
        self.mon_log.verticalScrollBar().setValue(self.mon_log.verticalScrollBar().maximum())

    def _manual_log(self, msg):
        self.manual_log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {msg}")
        self.manual_log.verticalScrollBar().setValue(self.manual_log.verticalScrollBar().maximum())

    @staticmethod
    def _summarize_stderr(stderr: str | None, fallback: str = "Falha desconhecida") -> str:
        if not stderr:
            return fallback
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        if not lines:
            return fallback
        text = lines[-1]
        if len(text) > MAX_ERROR_TEXT:
            return f"{text[: MAX_ERROR_TEXT - 1]}…"
        return text

    @staticmethod
    def _safe_size(path: Path | None) -> int:
        if not path or not path.exists():
            return 0
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _finalize_manual_failure(self, item, iid, returncode, stderr):
        details = self._summarize_stderr(stderr, "Streamlink encerrou sem capturar dados")
        item.setText(3, "Erro")
        item.setText(4, details)
        self._manual_log(
            f"❌ Falha na gravação de {item.text(0)} (código {returncode}): {details}"
        )
        logger.error(
            "Falha na gravação manual de %s (%s). Código=%s. stderr=%s",
            item.text(0),
            item.text(1),
            returncode,
            stderr,
        )
        ts_path = self.recorder.ts.get(iid)
        if ts_path:
            ts_path.unlink(missing_ok=True)
        self.recorder.finish_manual(iid)
        self.manual_finishing.discard(iid)
        for d in (self.manual_last_size, self.manual_inact):
            d.pop(iid, None)

    def _finalize_auto_failure(self, ch, cid, returncode, stderr):
        details = self._summarize_stderr(stderr, "Streamlink encerrou sem capturar dados")
        ch.setText(4, "Erro")
        ch.setText(5, details)
        self._mon_log(
            f"❌ Falha na gravação automática de {ch.text(2)} (código {returncode}): {details}"
        )
        logger.error(
            "Falha na gravação automática de %s (%s). Código=%s. stderr=%s",
            ch.text(2),
            ch.text(3),
            returncode,
            stderr,
        )
        ts_path = self.recorder.ats.get(cid)
        if ts_path:
            ts_path.unlink(missing_ok=True)
        self.recorder.finish_auto(cid)
        self.auto_finishing.discard(cid)
        for d in (self.auto_last_size, self.auto_inact):
            d.pop(cid, None)

    # ---------------- Config load/save ---------------------------------
    def _load_cfg(self):
        (
            self.output_dir_manual,
            self.output_dir_monitor,
            self._loaded_monitored,
            self.telegram_token,
            self.telegram_chat_id,
        ) = load_config(CONFIG_FILE)
        self.output_dir_manual.mkdir(parents=True, exist_ok=True)
        self.output_dir_monitor.mkdir(parents=True, exist_ok=True)

    # ---------------- Diretórios ---------------------------------------
    def _choose_dir_manual(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Pasta Base (Gravações Manuais)", str(self.output_dir_manual.parent)
        )
        if folder:
            self.output_dir_manual = Path(folder) / "GRAVACOES MANUAIS"
            self.output_dir_manual.mkdir(parents=True, exist_ok=True)
            self._save_monitored()

    def _choose_dir_monitor(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Pasta Base (Monitoramento)", str(self.output_dir_monitor.parent)
        )
        if folder:
            self.output_dir_monitor = Path(folder) / "MONITORAMENTO"
            self.output_dir_monitor.mkdir(parents=True, exist_ok=True)
            self._save_monitored()

    # ---------------- Entrada manual -----------------------------------
    def _add_entry(self):
        etq, url = self.label_in.text().strip(), self.url_in.text().strip()
        if not etq or not url:
            QMessageBox.warning(self, "Campos", "Preencha.")
            return
        item = QTreeWidgetItem([etq, url, self.qual_in.currentText()])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setToolTip(1, url)
        item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
        item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
        self.entry_tree.addTopLevelItem(item)
        self.label_in.clear()
        self.url_in.clear()

    def _remove_entry(self):
        for it in self.entry_tree.selectedItems():
            self.entry_tree.takeTopLevelItem(self.entry_tree.indexOfTopLevelItem(it))

    # ---------------- Gravação manual ----------------------------------
    def _start_entry(self):
        while self.entry_tree.topLevelItemCount():
            src = self.entry_tree.takeTopLevelItem(0)
            item = QTreeWidgetItem([src.text(0), src.text(1), src.text(2), "Aguardando", "-"])
            item.setToolTip(1, src.text(1))
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignCenter)
            self.order_tree.addTopLevelItem(item)
            self._start_manual(item)

    def _start_batch(self):
        self._start_entry()

    def _start_manual(self, item):
        iid = id(item)
        if iid in self.recorder.proc:
            return
        etq, url, qual = item.text(0), item.text(1), item.text(2)
        try:
            self.recorder.start_manual(iid, etq, url, qual, self.output_dir_manual)
            item.setText(3, "Gravando")
            item.setText(4, "0 MB / 0 s")
            item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignCenter)
            self._manual_log(f"Iniciando gravação: {etq} ({url})")
            logger.info("Gravação manual iniciada para %s (%s)", etq, url)
            enviar_notificacao_telegram(f"\U0001f4fa Gravação iniciada para {etq}")
        except Exception as e:
            QMessageBox.critical(self, "Erro", str(e))
            logger.error("Falha ao iniciar gravação manual: %s", e)

    # ---------------- Lista de gravações -------------------------------
    def _remove_order(self):
        for it in self.order_tree.selectedItems():
            if id(it) in self.recorder.proc:
                QMessageBox.warning(self, "Ativo", "Pare antes.")
                continue
            self.order_tree.takeTopLevelItem(self.order_tree.indexOfTopLevelItem(it))

    def _stop_selected(self):
        reply = QMessageBox.question(
            self,
            "Confirmar Encerramento",
            "Tem certeza de que deseja encerrar a gravação selecionada?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for it in self.order_tree.selectedItems():
            self._stop_manual(it)

    def _stop_manual(self, item):
        iid = id(item)
        if iid not in self.recorder.proc:
            return
        self.manual_finishing.add(iid)
        item.setText(3, "Convertendo…")
        self.conv_progress.setRange(0, 0)
        self.recorder.stop_manual(iid, lambda fut, k=iid, it=item: self._finish_manual(fut, it, k))

    def _finish_manual(self, fut, item, iid):
        try:
            success, result = fut.result()
        except Exception as exc:  # Executado fora da thread principal
            success, result = False, exc
        QTimer.singleShot(0, lambda: self._finish_manual_ui(item, iid, success, result))

    def _finish_manual_ui(self, item, iid, success, result):
        if isinstance(result, Exception):
            self._manual_log(f"❌ Erro crítico na conversão: {result}")
            logger.error("Erro crítico na conversão: %s", result)
        else:
            if success:
                self._manual_log(f"✅ Conversão concluída: {item.text(0)} → {result.name}")
                logger.info("Conversão concluída para %s", item.text(0))
                enviar_notificacao_telegram(
                    f"\U0001f4f4 Gravação finalizada com sucesso: {item.text(0)}"
                )
            else:
                self._manual_log(f"❌ Erro na conversão: {item.text(0)}")
                logger.warning("Erro na conversão de %s", item.text(0))
                enviar_notificacao_telegram(f"⚠️ Erro na conversão da gravação: {item.text(0)}")

        if item in [
            self.order_tree.topLevelItem(i) for i in range(self.order_tree.topLevelItemCount())
        ]:
            self.order_tree.takeTopLevelItem(self.order_tree.indexOfTopLevelItem(item))

        self.recorder.finish_manual(iid)
        self.manual_finishing.discard(iid)
        for d in (self.manual_last_size, self.manual_inact):
            d.pop(iid, None)
        self.conv_progress.setRange(0, 1)
        self.conv_progress.setValue(1)
        QTimer.singleShot(1500, lambda: self.conv_progress.setValue(0))

    # ---------------- Checagem automática fim live manual --------------
    def _check_manual_live(self):
        for i in range(self.order_tree.topLevelItemCount()):
            item = self.order_tree.topLevelItem(i)
            iid = id(item)
            if iid not in self.recorder.proc:
                continue
            url = item.text(1)
            live, _ = is_live(url)
            if not live:
                self._manual_log(
                    f"Live {item.text(0)} ficou offline, encerrando gravação automaticamente."
                )
                logger.info("Live %s offline, encerrando gravação", item.text(0))
                self._stop_manual(item)

    def _get_recording_status(self) -> str:
        """Resumo das gravações em andamento."""
        lines = []
        now = time.time()
        for i in range(self.order_tree.topLevelItemCount()):
            item = self.order_tree.topLevelItem(i)
            iid = id(item)
            proc = self.recorder.proc.get(iid)
            if not proc or proc.poll() is not None:
                continue
            if not self.recorder.ts[iid].exists():
                continue
            size = human_size(self.recorder.ts[iid].stat().st_size)
            dur = human_time(int(now - self.recorder.start[iid]))
            lines.append(f"Manual: {item.text(0)} - {size} / {dur}")
        for ch in self._iter_mon():
            cid = id(ch)
            proc = self.recorder.aproc.get(cid)
            if not proc or proc.poll() is not None:
                continue
            if not self.recorder.ats[cid].exists():
                continue
            size = human_size(self.recorder.ats[cid].stat().st_size)
            dur = human_time(int(now - self.recorder.astart[cid]))
            lines.append(f"Auto: {ch.text(2)} - {size} / {dur}")
        if not lines:
            return "Nenhuma gravação em andamento."
        return "\n".join(lines)

    def _check_telegram_commands(self):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        found, self.telegram_offset = verificar_status_command(
            self.telegram_token, self.telegram_chat_id, self.telegram_offset
        )
        if found:
            enviar_notificacao_telegram(self._get_recording_status())

    # ---------------- Monitoramento ------------------------------------
    def _add_channel(self):
        if self.mon_tree.topLevelItemCount() >= MAX_CHANNELS:
            QMessageBox.warning(self, "Aviso", "Muitos canais.")
            return
        nome, url = self.mon_name.text().strip(), self.mon_url.text().strip()
        if not nome or not url:
            QMessageBox.warning(self, "Campos", "Preencha.")
            return

        for ch in self._iter_mon():
            if ch.text(2).lower() == nome.lower():
                QMessageBox.warning(self, "Duplicado", "Etiqueta já existe.")
                return
            if ch.text(3).lower() == url.lower():
                QMessageBox.warning(self, "Duplicado", "URL já existe.")
                return

        qual_sel = self.mon_qual.currentText()
        item = QTreeWidgetItem(["", "", nome, url, "offline (aguardando)", "-", "-", qual_sel])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setCheckState(1, Qt.CheckState.Unchecked)
        item.setToolTip(3, url)
        for col in (0, 2, 6, 7):
            item.setTextAlignment(col, Qt.AlignmentFlag.AlignCenter)
        self.mon_tree.addTopLevelItem(item)
        self._renumerar_mon_tree()
        self._save_monitored()
        self.mon_name.clear()
        self.mon_url.clear()

    def _remove_channel(self):
        for it in self.mon_tree.selectedItems():
            if id(it) in self.recorder.aproc:
                QMessageBox.warning(self, "Gravando", "Pare antes.")
                return
            self.mon_tree.takeTopLevelItem(self.mon_tree.indexOfTopLevelItem(it))
        self._renumerar_mon_tree()
        self._save_monitored()

    def _stop_channel_record(self):
        for ch in self.mon_tree.selectedItems():
            cid = id(ch)
            if cid not in self.recorder.aproc:
                continue
            self.auto_finishing.add(cid)
            ch.setText(4, "Convertendo…")
            self.conv_progress.setRange(0, 0)
            self.recorder.stop_auto(cid, lambda fut, c=cid, it=ch: self._finish_auto(fut, it, c))

    def _finish_auto(self, fut, ch, cid):
        try:
            success, result = fut.result()
        except Exception as exc:
            success, result = False, exc
        QTimer.singleShot(0, lambda: self._finish_auto_ui(ch, cid, success, result))

    def _finish_auto_ui(self, ch, cid, success, result):
        if isinstance(result, Exception):
            self._mon_log(f"❌ Erro crítico na conversão: {result}")
            logger.error("Erro crítico na conversão automática: %s", result)
        else:
            if success:
                self._mon_log(f"✅ Conversão concluída: {ch.text(2)} → {result.name}")
                logger.info("Conversão automática concluída: %s", ch.text(2))
                enviar_notificacao_telegram(
                    f"\U0001f4f4 Gravação automática finalizada com sucesso: {ch.text(2)}"
                )
            else:
                self._mon_log(f"❌ Erro na conversão: {ch.text(2)}")
                logger.warning("Erro na conversão automática de %s", ch.text(2))
                enviar_notificacao_telegram(
                    f"⚠️ Erro na conversão da gravação automática: {ch.text(2)}"
                )

        for d in (self.recorder.aproc, self.recorder.astart, self.recorder.ats):
            d.pop(cid, None)
        self.auto_finishing.discard(cid)
        for d in (self.auto_last_size, self.auto_inact):
            d.pop(cid, None)
        ch.setText(4, "offline (aguardando)")
        ch.setText(5, "-")
        self._record_history(ch.text(3))
        self._renumerar_mon_tree()
        self._save_monitored()
        logger.info("Gravação automática finalizada para %s", ch.text(2))
        self.conv_progress.setRange(0, 1)
        self.conv_progress.setValue(1)
        QTimer.singleShot(1500, lambda: self.conv_progress.setValue(0))

    def _record_history(self, url):
        now = datetime.now()
        time_str = now.strftime("%H:%M:%S")
        date_str = now.strftime("%d/%m/%Y")
        for ch in self._iter_mon():
            if ch.text(3) == url:
                ch.setText(6, f"Última live às {time_str} do dia {date_str}")
                ch.setTextAlignment(6, Qt.AlignmentFlag.AlignCenter)
                break

    def _toggle_all_clicked(self, _checked: bool):
        confirm = QMessageBox.question(
            self,
            "Confirmar Ação",
            "Tem certeza de que deseja alterar o estado de todos os canais monitorados?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self.toggle_all_checkbox.blockSignals(True)
            current = Qt.CheckState.Checked if _checked else Qt.CheckState.Unchecked
            self.toggle_all_checkbox.setCheckState(current)
            self.toggle_all_checkbox.blockSignals(False)
            return

        all_marked = all(ch.checkState(1) == Qt.CheckState.Checked for ch in self._iter_mon())
        target_state = Qt.CheckState.Unchecked if all_marked else Qt.CheckState.Checked
        self.toggle_all_checkbox.blockSignals(True)
        self.toggle_all_checkbox.setCheckState(target_state)
        self.toggle_all_checkbox.blockSignals(False)
        for ch in self._iter_mon():
            ch.setCheckState(1, target_state)
        self._save_monitored()

    def _iter_mon(self):
        for i in range(self.mon_tree.topLevelItemCount()):
            yield self.mon_tree.topLevelItem(i)

    # ------------- Persistência monitorados ---------------------------

    def _save_monitored(self):
        data = []
        for ch in self._iter_mon():
            data.append(
                {
                    "num": ch.text(0),
                    "nome": ch.text(2),
                    "url": ch.text(3),
                    "active": ch.checkState(1) == Qt.CheckState.Checked,
                    "qual": ch.text(7),
                    "hist": ch.text(6),
                }
            )

        try:
            save_config(
                CONFIG_FILE,
                self.output_dir_manual,
                self.output_dir_monitor,
                data,
                self.telegram_token,
                self.telegram_chat_id,
            )
        except Exception as e:
            self._mon_log(f"Erro ao salvar configuração: {e}")
            logger.error("Falha ao salvar configuração: %s", e)

    def _load_monitored(self):
        mon_list = getattr(self, "_loaded_monitored", None)
        if mon_list is None:
            _, _, mon_list, self.telegram_token, self.telegram_chat_id = load_config(CONFIG_FILE)
        if mon_list:
            try:
                for ch in mon_list:
                    item = QTreeWidgetItem(
                        [
                            "",
                            "",
                            ch["nome"],
                            ch["url"],
                            "offline (aguardando)",
                            "-",
                            ch.get("hist", "-"),
                            ch.get("qual", "best"),
                        ]
                    )
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip(3, ch["url"])
                    for col in (0, 2, 6, 7):
                        item.setTextAlignment(col, Qt.AlignmentFlag.AlignCenter)
                    state = Qt.CheckState.Checked if ch.get("active") else Qt.CheckState.Unchecked
                    item.setCheckState(1, state)
                    self.mon_tree.addTopLevelItem(item)
            except Exception as e:
                logger.error("Erro ao carregar canais monitorados: %s", e)
        self._renumerar_mon_tree()

    # ------------- Salvamento de credenciais Telegram -----------------
    def _save_telegram(self):
        self.telegram_token = self.token_edit.text().strip() or None
        self.telegram_chat_id = self.chat_edit.text().strip() or None
        try:
            save_config(
                CONFIG_FILE,
                self.output_dir_manual,
                self.output_dir_monitor,
                [
                    {
                        "num": ch.text(0),
                        "nome": ch.text(2),
                        "url": ch.text(3),
                        "active": ch.checkState(1) == Qt.CheckState.Checked,
                        "qual": ch.text(7),
                        "hist": ch.text(6),
                    }
                    for ch in self._iter_mon()
                ],
                self.telegram_token,
                self.telegram_chat_id,
            )
            update_creds(self.telegram_token, self.telegram_chat_id)
            self.test_telegram_btn.setEnabled(bool(self.telegram_token and self.telegram_chat_id))
            QMessageBox.information(self, "Telegram", "Credenciais salvas com sucesso.")
            if self.telegram_token and self.telegram_chat_id:
                reply = QMessageBox.question(
                    self,
                    "Telegram",
                    "Deseja enviar uma mensagem de teste?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._send_telegram_test()
        except Exception as e:
            QMessageBox.critical(self, "Erro", str(e))
            logger.error("Falha ao salvar credenciais Telegram: %s", e)

    def _send_telegram_test(self):
        if not self.telegram_token or not self.telegram_chat_id:
            QMessageBox.warning(self, "Telegram", "Credenciais ausentes.")
            return
        result = enviar_mensagem_teste(self.telegram_token, self.telegram_chat_id)
        if result == "sucesso":
            QMessageBox.information(self, "Telegram", "Mensagem de teste enviada com sucesso.")
        else:
            QMessageBox.critical(self, "Erro", f"Falha ao enviar mensagem de teste: {result}")

    # ------------- Timers --------------------------------------------
    def _update_stats(self):
        # ---- MANUAIS ----
        for i in range(self.order_tree.topLevelItemCount()):
            it = self.order_tree.topLevelItem(i)
            iid = id(it)
            proc = self.recorder.proc.get(iid)
            if not proc:
                continue
            if proc.poll() is not None:
                if iid in self.manual_finishing:
                    continue
                returncode, stderr = self.recorder.get_manual_exit_info(iid)
                if self._safe_size(self.recorder.ts.get(iid)) > 0:
                    self._manual_log(
                        f"Streamlink encerrou para {it.text(0)}; iniciando finalização automática."
                    )
                    self._stop_manual(it)
                else:
                    self._finalize_manual_failure(it, iid, returncode, stderr)
                continue
            if not self.recorder.ts[iid].exists():
                continue
            size = self.recorder.ts[iid].stat().st_size
            size_str = human_size(size)
            dur = human_time(int(time.time() - self.recorder.start[iid]))
            it.setText(4, f"{size_str} / {dur}")

            if self.manual_last_size.get(iid) == size:
                self.manual_inact[iid] = self.manual_inact.get(iid, 0) + 1
            else:
                self.manual_inact[iid] = 0
            self.manual_last_size[iid] = size
            if self.manual_inact[iid] >= WATCHDOG_MAX:
                self._manual_log(f"Watchdog: encerrando {it.text(0)} por inatividade.")
                self._stop_manual(it)

        # ---- AUTOMÁTICO ----
        for ch in self._iter_mon():
            cid = id(ch)
            proc = self.recorder.aproc.get(cid)
            if not proc:
                continue
            if proc.poll() is not None:
                if cid in self.auto_finishing:
                    continue
                returncode, stderr = self.recorder.get_auto_exit_info(cid)
                if self._safe_size(self.recorder.ats.get(cid)) > 0:
                    self._mon_log(
                        f"Streamlink encerrou para {ch.text(2)}; iniciando finalização automática."
                    )
                    ch.setText(4, "Convertendo…")
                    self.conv_progress.setRange(0, 0)
                    self.auto_finishing.add(cid)
                    self.recorder.stop_auto(
                        cid, lambda fut, c=cid, it=ch: self._finish_auto(fut, it, c)
                    )
                else:
                    self._finalize_auto_failure(ch, cid, returncode, stderr)
                continue
            if not self.recorder.ats[cid].exists():
                continue
            size = self.recorder.ats[cid].stat().st_size
            size_str = human_size(size)
            dur = human_time(int(time.time() - self.recorder.astart[cid]))
            ch.setText(5, f"{size_str} / {dur}")

            if self.auto_last_size.get(cid) == size:
                self.auto_inact[cid] = self.auto_inact.get(cid, 0) + 1
            else:
                self.auto_inact[cid] = 0
            self.auto_last_size[cid] = size
            if self.auto_inact[cid] >= WATCHDOG_MAX:
                self._mon_log(f"Watchdog: encerrando {ch.text(2)} por inatividade.")
                ch.setText(4, "Convertendo…")
                self.conv_progress.setRange(0, 0)
                self.recorder.stop_auto(
                    cid, lambda fut, c=cid, it=ch: self._finish_auto(fut, it, c)
                )

    def _dispatch_live_checks(self):
        for ch in self._iter_mon():
            if ch.checkState(1) != Qt.CheckState.Checked:
                continue
            url = ch.text(3)
            cid = id(ch)
            EXEC_LIVE.submit(self._check_live_status, cid, url)

    def _check_live_status(self, cid, url):
        """Método auxiliar para evitar closure problems"""
        try:
            live, data = is_live(url)
            self.live_queue.put((cid, live, data))
        except Exception as e:
            logger.error("Erro ao verificar status da live: %s", e)
            self.live_queue.put((cid, False, None))

    def _process_live_queue(self):
        while not self.live_queue.empty():
            cid, live, title = self.live_queue.get()
            self._apply_live_result(cid, live, title)

    def _apply_live_result(self, cid, live, title):
        for ch in self._iter_mon():
            if id(ch) != cid:
                continue
            status = "LIVE" if live else "offline (aguardando)"
            ch.setText(4, status)
            ch.setTextAlignment(4, Qt.AlignmentFlag.AlignCenter)
            self._mon_log(f"Checando canal: {ch.text(2)} → status: {status}")
            if live and cid not in self.recorder.aproc:
                self.mon_tree.takeTopLevelItem(self.mon_tree.indexOfTopLevelItem(ch))
                self.mon_tree.insertTopLevelItem(0, ch)
                self._renumerar_mon_tree()
                self._start_auto_record(ch)
            elif not live and cid in self.recorder.aproc:
                self._mon_log(f"{ch.text(2)} ficou offline, encerrando gravação automaticamente.")
                ch.setText(4, "Convertendo…")
                self.conv_progress.setRange(0, 0)
                self.recorder.stop_auto(
                    cid, lambda fut, c=cid, it=ch: self._finish_auto(fut, it, c)
                )
            break

    # ------------- Auto gravação -------------------------------------
    def _start_auto_record(self, ch):
        cid = id(ch)
        nome, url, qual = ch.text(2), ch.text(3), ch.text(7)
        try:
            self.recorder.start_auto(cid, nome, url, qual, self.output_dir_monitor)
            ch.setText(4, "Gravando")
            self._mon_log(f"Iniciando gravação: {nome}")
            logger.info("Gravação automática iniciada: %s", nome)
            enviar_notificacao_telegram(f"\U0001f4fa Gravação automática iniciada: {nome}")
        except Exception as e:
            QMessageBox.critical(self, "Erro", str(e))
            self._mon_log(f"Erro {nome}: {e}")
            logger.error("Erro ao iniciar gravação automática: %s", e)

    # ------------- Fechamento ----------------------------------------
    def closeEvent(self, event):
        if self.recorder.proc or self.recorder.aproc:
            msg = (
                "Há gravações em andamento.\n"
                "Encerrar agora encerrará TODAS as capturas.\nDeseja sair?"
            )
        else:
            msg = "Tem certeza de que deseja sair?"
        reply = QMessageBox.question(
            self,
            "Confirmar saída",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        logger.info("Encerrando aplicação")
        self.t_stats.stop()
        self.t_live.stop()
        self.t_queue.stop()
        self.t_manual_check.stop()

        try:
            EXEC_CONV.shutdown(wait=False)
            EXEC_LIVE.shutdown(wait=False)
        except Exception:  # pragma: no cover - errors unlikely during shutdown
            pass
        super().closeEvent(event)


# ---------------- MAIN -------------------------------------------------
if __name__ == "__main__":
    logger.info("Aplicação iniciada")
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
