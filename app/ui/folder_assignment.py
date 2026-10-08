"""Read-only folder badge with an explicit assignment action."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QLabel, QToolButton, QComboBox, QHBoxLayout, QMenu
from app.ui.icons import icon


class FolderAssignment(QWidget):
    currentIndexChanged = Signal(int)
    create_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = QComboBox(self)
        self.model.hide()
        self.label = QLabel('Без папки')
        self.label.setTextFormat(Qt.PlainText)
        self.label.setStyleSheet('background:#fff0df;color:#b66012;border-radius:12px;padding:9px 12px;')
        self.button = QToolButton()
        self.button.setIcon(icon('plus', '#e77613', 18))
        self.button.setFixedSize(32, 32)
        self.button.setAccessibleName('Назначить или изменить папку')
        self.button.setToolTip('Назначить или изменить папку')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(self.label)
        layout.addWidget(self.button)
        self.button.clicked.connect(self.choose)
        self.model.currentIndexChanged.connect(self.changed)

    def changed(self, index):
        self.label.setText(self.model.currentText() or 'Без папки')
        self.label.setToolTip(self.label.text())
        self.currentIndexChanged.emit(index)

    def choose(self):
        menu = QMenu(self)
        menu.setStyleSheet('QMenu {background:white;color:#283343;border:1px solid #e9e3dc;padding:6px;} QMenu::item {padding:8px 16px;} QMenu::item:selected {background:#fff0df;}')
        for index in range(self.model.count()):
            action = menu.addAction(self.model.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == self.model.currentIndex())
            action.triggered.connect(lambda checked=False, i=index: self.setCurrentIndex(i))
        menu.addSeparator()
        menu.addAction(icon('plus', '#e77613'), 'Создать папку…', self.create_requested.emit)
        menu.exec(self.button.mapToGlobal(self.button.rect().bottomLeft()))

    def clear(self):
        self.model.clear()

    def addItem(self, *args):
        self.model.addItem(*args)

    def findData(self, *args):
        return self.model.findData(*args)

    def currentData(self):
        return self.model.currentData()

    def currentIndex(self):
        return self.model.currentIndex()

    def setCurrentIndex(self, index):
        self.model.setCurrentIndex(index)

