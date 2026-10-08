"""Accessible application menu with the same visual language as the editor."""
from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QMenu, QToolButton, QWidgetAction

from app.ui.icons import icon


MENU_STYLE = '''
QMenu#applicationMenu {
    background:#ffffff; color:#283343; border:1px solid #e9e3dc;
    border-radius:14px; padding:8px; font-family:"Segoe UI"; font-size:13px;
}
QMenu#applicationMenu::item {
    background:transparent; border-radius:8px; padding:10px 18px 10px 12px;
    margin:2px 0; min-height:20px;
}
QMenu#applicationMenu::item:selected {background:#fff0df; color:#a95008;}
QMenu#applicationMenu::item:disabled {color:#a3aab3;}
QMenu#applicationMenu::icon {padding-left:8px; padding-right:8px;}
QMenu#applicationMenu::separator {
    height:22px; background:transparent; color:#9299a2;
    margin:5px 8px 1px 8px;
}
'''

BUTTON_STYLE = '''
QToolButton#applicationMenuButton {
    background:transparent; border:1px solid transparent; border-radius:10px; padding:0;
}
QToolButton#applicationMenuButton:hover,
QToolButton#applicationMenuButton:pressed {background:#ffe9d4; border-color:#ffd3ac;}
QToolButton#applicationMenuButton:focus {border-color:#e77613; background:#fff3e7;}
QToolButton#applicationMenuButton::menu-indicator {image:none; width:0; height:0;}
'''


def _more_icon():
    svg=QSvgRenderer(QByteArray(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="#687383"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></g></svg>'))
    pixmap=QPixmap(48,48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap)
    svg.render(painter,QRectF(0,0,24,24))
    painter.end()
    return QIcon(pixmap)


class ApplicationMenu(QMenu):
    def __init__(self,window,parent):
        super().__init__(parent)
        self.window=window
        self.setObjectName('applicationMenu')
        self.setAccessibleName('Меню приложения')
        # Set the complete flags: QMenu ignores individual shadow hint updates.
        self.setWindowFlags(Qt.Popup|Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground,True)
        self.setStyleSheet(MENU_STYLE)
        self.setMinimumWidth(282)
        self.setSeparatorsCollapsible(False)
        self.add_heading('Справка')
        from app.utils.shortcuts import shortcut_label
        self.addAction(icon('search','#6d7786',18),'Быстрые действия · '+shortcut_label('Ctrl+K'),window.show_command_palette)
        self.help_action=self.addAction(icon('book','#6d7786',18),'Помощь и горячие клавиши',window.show_help)
        self.status_action=self.addAction(icon('bell','#6d7786',18),'Состояние напоминаний',window.reminder_status)
        self.add_heading('Данные')
        self.export_action=self.addAction(icon('share','#6d7786',18),'Экспорт заметки',window.export_note)
        self.backup_action=self.addAction(icon('archive','#6d7786',18),'Резервная копия базы',window.backup_database)
        self.restore_action=self.addAction(icon('archive','#6d7786',18),'Восстановить базу из копии',window.restore_database)
        self.aboutToShow.connect(self.update_actions)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        # Clip the native popup itself, not only its stylesheet background.
        path=QPainterPath();path.addRoundedRect(QRectF(self.rect()),14,14)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def add_heading(self,text):
        # Qt's stylesheet menu style omits addSection labels on Windows.
        action=QWidgetAction(self)
        action.setText(text)
        action.setEnabled(False)
        label=QLabel(text)
        label.setStyleSheet('background:transparent;color:#9299a2;font:600 11px "Segoe UI";padding:8px 12px 4px;')
        action.setDefaultWidget(label)
        self.addAction(action)

    def update_actions(self):
        self.export_action.setEnabled(bool(self.window.current))


def create_app_menu_button(window):
    button=QToolButton()
    button.setObjectName('applicationMenuButton')
    button.setFixedSize(36,36)
    button.setIcon(_more_icon())
    button.setIconSize(QSize(22,22))
    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
    button.setPopupMode(QToolButton.InstantPopup)
    button.setToolTip('Меню приложения')
    button.setAccessibleName('Меню приложения')
    button.setFocusPolicy(Qt.StrongFocus)
    button.setStyleSheet(BUTTON_STYLE)
    menu=ApplicationMenu(window,button)
    button.setMenu(menu)
    return button
